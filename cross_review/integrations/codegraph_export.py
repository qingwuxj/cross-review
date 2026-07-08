from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from collections import defaultdict
from typing import Any

from cross_review.config import CodeGraphIntegrationConfig
from cross_review.integrations.codegraph import CommandResult


SOURCE_EXTENSIONS = (".py", ".sql", ".ts", ".tsx", ".js", ".jsx", ".graphql", ".gql", ".proto")


class CodeGraphGraphExporter:
    """Export CodeGraph CLI data into Cross-Review's simplified external graph format."""

    def __init__(
        self,
        root_dir: str,
        command: str = "codegraph",
        timeout_seconds: int = 60,
        max_files: int = 500,
        symbol_limit_per_file: int = 2,
        caller_limit: int = 20,
        query_limit: int = 10,
    ):
        self.root_dir = os.path.abspath(root_dir)
        self.config = CodeGraphIntegrationConfig(
            enabled="true",
            command=command,
            timeout_seconds=timeout_seconds,
        )
        self.max_files = max(1, max_files)
        self.symbol_limit_per_file = max(0, symbol_limit_per_file)
        self.caller_limit = max(1, caller_limit)
        self.query_limit = max(0, query_limit)
        self._query_cache: dict[tuple[str, int], tuple[list[dict[str, Any]], CommandResult]] = {}
        self._caller_cache: dict[tuple[str, int], tuple[list[dict[str, Any]], CommandResult]] = {}
        self._query_cache_hits = 0
        self._caller_cache_hits = 0

    def build(self) -> dict[str, Any]:
        self._query_cache = {}
        self._caller_cache = {}
        self._query_cache_hits = 0
        self._caller_cache_hits = 0
        diagnostics: dict[str, Any] = {
            "enabled": True,
            "source": "codegraph-cli",
            "command": self.config.command,
            "dependency_source": "node_symbols_only",
            "symbol_dependency_source": "callers_json",
            "symbol_limit_per_file": self.symbol_limit_per_file,
            "caller_limit": self.caller_limit,
            "query_limit": self.query_limit,
            "errors": [],
            "warnings": [],
        }
        if self._uses_npx_command():
            diagnostics["warnings"].append("codegraph_npx_command_slower_than_global_command")
            diagnostics["command_recommendation"] = (
                "Install CodeGraph as a global command or set command = \"codegraph\" to avoid per-call npx startup cost."
            )

        status = self._run(["status", self.root_dir])
        diagnostics["status"] = self._command_diagnostics(status)
        if status.returncode != 0:
            diagnostics["errors"].append("codegraph_status_failed")

        files_result = self._run(["files", "--path", self.root_dir, "--format", "flat", "--json"])
        diagnostics["files"] = self._command_diagnostics(files_result)
        file_entries = self._parse_json_array(files_result.stdout)
        if files_result.returncode != 0:
            diagnostics["errors"].append("codegraph_files_failed")
        if not file_entries:
            diagnostics["warnings"].append("codegraph_files_empty_or_unparseable")

        source_files = self._source_files_from_entries(file_entries)
        if len(source_files) > self.max_files:
            diagnostics["warnings"].append("codegraph_export_file_limit_applied")
        selected_files = source_files[: self.max_files]

        modules = self._build_modules(selected_files)
        file_to_module = {
            file_path: module["name"]
            for module in modules.values()
            for file_path in module["files"]
        }
        dependencies, node_stats = self._build_dependencies(selected_files, file_to_module)
        for module_name, exports in node_stats.get("module_exports", {}).items():
            if module_name in modules:
                modules[module_name]["exports"] = exports
        diagnostics["node"] = node_stats
        diagnostics["symbol_edges"] = node_stats.get("symbol_edges", [])

        return {
            "name": os.path.basename(self.root_dir) or "codegraph-project",
            "modules": [modules[name] for name in sorted(modules)],
            "dependencies": dependencies,
            "metadata": {
                "codegraph": diagnostics,
            },
        }

    def _build_modules(self, files: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        modules: dict[str, dict[str, Any]] = {}
        for entry in files:
            file_path = self._normalize_path(entry.get("path"))
            if not file_path:
                continue
            module_name = self._infer_module_name(file_path)
            module = modules.setdefault(
                module_name,
                {
                    "name": module_name,
                    "files": [],
                    "criticality": self._criticality(module_name),
                    "exports": [],
                    "routes": [],
                    "events": [],
                    "db_tables": [],
                },
            )
            module["files"].append(file_path)
        for module in modules.values():
            module["files"] = sorted(set(module["files"]))
        return modules

    def _build_dependencies(
        self,
        files: list[dict[str, Any]],
        file_to_module: dict[str, str],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        edge_map: dict[tuple[str, str, str], dict[str, Any]] = {}
        module_exports: dict[str, set[str]] = defaultdict(set)
        parsed_files = 0
        failed_files = []
        query_queries = 0
        failed_queries = []
        caller_queries = 0
        failed_callers = []
        ambiguous_symbols = []
        all_symbol_edges: list[dict[str, Any]] = []

        for entry in files:
            provider_file = self._normalize_path(entry.get("path"))
            if not provider_file:
                continue
            result = self._run(["node", "--path", self.root_dir, "--file", provider_file, "--symbols-only"])
            if result.returncode != 0:
                failed_files.append(provider_file)
                continue
            parsed_files += 1
            provider_module = file_to_module.get(provider_file)
            symbols = self._parse_symbols(result.stdout)
            if provider_module:
                module_exports[provider_module].update(symbol["name"] for symbol in symbols)
            cross_module_consumers = []
            for consumer_file in self._parse_used_by_files(result.stdout):
                consumer_module = file_to_module.get(consumer_file)
                if not provider_module or not consumer_module or provider_module == consumer_module:
                    continue
                cross_module_consumers.append(consumer_file)
                self._edge_for(edge_map, provider_module, consumer_module, provider_file, consumer_file)

            if not provider_module or not cross_module_consumers or self.symbol_limit_per_file <= 0:
                continue

            for symbol in symbols[: self.symbol_limit_per_file]:
                symbol_ref, query_diag = self._resolve_symbol_reference(symbol, provider_file)
                if query_diag.get("queried"):
                    query_queries += 1
                if query_diag.get("failed"):
                    failed_queries.append(query_diag["failed"])
                if symbol_ref.get("ambiguous"):
                    ambiguous_symbols.append(
                        {
                            "provider_file": provider_file,
                            "symbol": symbol["name"],
                            "query_result_count": symbol_ref.get("query_result_count", 0),
                        }
                    )
                callers, caller_result, was_cache_hit = self._callers_for_symbol(symbol_ref["query"])
                if not was_cache_hit:
                    caller_queries += 1
                if caller_result.returncode != 0:
                    failed_callers.append(
                        {
                            "provider_file": provider_file,
                            "symbol": symbol["name"],
                            "query": symbol_ref["query"],
                            "returncode": caller_result.returncode,
                            "stderr_excerpt": self._truncate(caller_result.stderr, 1000),
                        }
                    )
                    continue
                for caller in callers:
                    consumer_file = self._normalize_path(caller.get("filePath"))
                    consumer_module = file_to_module.get(consumer_file or "")
                    if not consumer_file or not consumer_module or consumer_module == provider_module:
                        continue
                    edge = self._edge_for(edge_map, provider_module, consumer_module, provider_file, consumer_file)
                    symbol_edge = self._make_symbol_edge(provider_file, symbol, caller, consumer_file, symbol_ref)
                    edge.setdefault("symbol_edges", []).append(symbol_edge)
                    all_symbol_edges.append(symbol_edge)

        for edge in edge_map.values():
            edge["consumer_files"] = sorted(set(edge["consumer_files"]))
            edge["provider_files"] = sorted(set(edge["provider_files"]))
            if edge["consumer_files"] and edge["provider_files"]:
                edge["details"] = (
                    f"CodeGraph usage: {edge['consumer_files'][0]} uses {edge['provider_files'][0]}"
                )
            if edge.get("symbol_edges"):
                edge["symbol_edges"] = self._dedupe_symbol_edges(edge["symbol_edges"])
                symbols = sorted({item["symbol"] for item in edge["symbol_edges"] if item.get("symbol")})
                if symbols:
                    suffix = ", ".join(symbols[:5])
                    if len(symbols) > 5:
                        suffix += f", +{len(symbols) - 5} more"
                    edge["details"] = f"{edge['details']}; symbols: {suffix}"

        dependencies = sorted(edge_map.values(), key=lambda item: (item["from"], item["to"], item["type"]))
        return dependencies, {
            "parsed_files": parsed_files,
            "failed_files": failed_files,
            "query_queries": query_queries,
            "failed_queries": failed_queries,
            "query_cache_hits": self._query_cache_hits,
            "caller_queries": caller_queries,
            "failed_callers": failed_callers,
            "caller_cache_hits": self._caller_cache_hits,
            "ambiguous_symbols": ambiguous_symbols,
            "symbol_edges": self._dedupe_symbol_edges(all_symbol_edges),
            "module_exports": {module: sorted(exports) for module, exports in module_exports.items()},
        }

    def _edge_for(
        self,
        edge_map: dict[tuple[str, str, str], dict[str, Any]],
        provider_module: str,
        consumer_module: str,
        provider_file: str,
        consumer_file: str,
    ) -> dict[str, Any]:
        key = (provider_module, consumer_module, "static_import")
        edge = edge_map.setdefault(
            key,
            {
                "from": provider_module,
                "to": consumer_module,
                "type": "static_import",
                "details": f"CodeGraph usage: {consumer_file} uses {provider_file}",
                "consumer_files": [],
                "provider_files": [],
            },
        )
        edge["consumer_files"].append(consumer_file)
        edge["provider_files"].append(provider_file)
        return edge

    def _parse_json_array(self, stdout: str) -> list[dict[str, Any]]:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _source_files_from_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        source_entries = []
        for entry in entries:
            file_path = self._normalize_path(entry.get("path"))
            if file_path and file_path.endswith(SOURCE_EXTENSIONS):
                copy = dict(entry)
                copy["path"] = file_path
                source_entries.append(copy)
        return sorted(source_entries, key=lambda item: item["path"])

    def _parse_used_by_files(self, stdout: str) -> list[str]:
        first_line = stdout.splitlines()[0] if stdout.splitlines() else ""
        match = re.search(r"used by\s+\d+\s+files?:\s*(.+)$", first_line)
        if not match:
            return []
        raw_files = match.group(1)
        if not raw_files.strip():
            return []
        return [
            normalized
            for item in raw_files.split(",")
            if (normalized := self._normalize_path(item.strip()))
        ]

    def _parse_symbol_names(self, stdout: str) -> list[str]:
        return sorted({symbol["name"] for symbol in self._parse_symbols(stdout)})

    def _parse_symbols(self, stdout: str) -> list[dict[str, Any]]:
        symbols = []
        seen = set()
        for line in stdout.splitlines():
            match = re.match(r"- `([^`]+)` \(([^)]+)\)(.*)$", line.strip())
            if not match:
                continue
            name = match.group(1).strip()
            kind = match.group(2).strip()
            if not name or name in seen:
                continue
            line_match = re.search(r":(\d+)\b", match.group(3))
            symbol: dict[str, Any] = {"name": name, "kind": kind}
            if line_match:
                symbol["line"] = int(line_match.group(1))
            symbols.append(symbol)
            seen.add(name)
        return symbols

    def _parse_callers(self, stdout: str) -> list[dict[str, Any]]:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return []
        if isinstance(payload, dict):
            callers = payload.get("callers")
        elif isinstance(payload, list):
            callers = payload
        else:
            callers = None
        if not isinstance(callers, list):
            return []
        return [item for item in callers if isinstance(item, dict)]

    def _resolve_symbol_reference(
        self,
        symbol: dict[str, Any],
        provider_file: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        fallback = {
            "query": symbol["name"],
            "match_source": "name_fallback",
            "query_result_count": 0,
        }
        if self.query_limit <= 0:
            return fallback, {"queried": False}

        candidates, result, cache_hit = self._query_symbol(symbol["name"])
        diag: dict[str, Any] = {"queried": not cache_hit}
        if result.returncode != 0:
            diag["failed"] = {
                "provider_file": provider_file,
                "symbol": symbol["name"],
                "returncode": result.returncode,
                "stderr_excerpt": self._truncate(result.stderr, 1000),
            }
            return fallback, diag

        selected, ambiguous = self._select_query_node(candidates, symbol, provider_file)
        if not selected:
            fallback["query_result_count"] = len(candidates)
            return fallback, diag

        qualified_name = self._clean_string(selected.get("qualifiedName"))
        reference = {
            "query": qualified_name or selected.get("name") or symbol["name"],
            "match_source": "query_json",
            "query_result_count": len(candidates),
            "ambiguous": ambiguous,
        }
        if qualified_name:
            reference["qualified_name"] = qualified_name
        start_line = self._positive_int(selected.get("startLine") or selected.get("line"))
        if start_line is not None:
            reference["provider_line"] = start_line
        return reference, diag

    def _query_symbol(self, symbol_name: str) -> tuple[list[dict[str, Any]], CommandResult, bool]:
        cache_key = (symbol_name, self.query_limit)
        if cache_key in self._query_cache:
            self._query_cache_hits += 1
            candidates, result = self._query_cache[cache_key]
            return candidates, result, True
        result = self._run(
            [
                "query",
                "--path",
                self.root_dir,
                symbol_name,
                "--json",
                "--limit",
                str(self.query_limit),
            ]
        )
        candidates = self._parse_query_nodes(result.stdout)
        self._query_cache[cache_key] = (candidates, result)
        return candidates, result, False

    def _parse_query_nodes(self, stdout: str) -> list[dict[str, Any]]:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        nodes = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            node = item.get("node", item)
            if isinstance(node, dict):
                nodes.append(node)
        return nodes

    def _select_query_node(
        self,
        candidates: list[dict[str, Any]],
        symbol: dict[str, Any],
        provider_file: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        symbol_name = symbol.get("name")
        symbol_line = self._positive_int(symbol.get("line"))
        file_matches = []
        for candidate in candidates:
            candidate_file = self._normalize_path(candidate.get("filePath"))
            if candidate_file != provider_file:
                continue
            candidate_name = self._clean_string(candidate.get("name"))
            qualified_name = self._clean_string(candidate.get("qualifiedName"))
            if candidate_name != symbol_name and not (qualified_name and qualified_name.endswith(f".{symbol_name}")):
                continue
            file_matches.append(candidate)
        if not file_matches:
            return None, False
        if symbol_line is not None:
            line_matches = [
                candidate
                for candidate in file_matches
                if self._positive_int(candidate.get("startLine") or candidate.get("line")) == symbol_line
            ]
            if line_matches:
                return line_matches[0], len(line_matches) > 1
        return file_matches[0], len(file_matches) > 1

    def _callers_for_symbol(self, query: str) -> tuple[list[dict[str, Any]], CommandResult, bool]:
        cache_key = (query, self.caller_limit)
        if cache_key in self._caller_cache:
            self._caller_cache_hits += 1
            callers, result = self._caller_cache[cache_key]
            return callers, result, True
        result = self._run(
            [
                "callers",
                "--path",
                self.root_dir,
                query,
                "--json",
                "--limit",
                str(self.caller_limit),
            ]
        )
        callers = self._parse_callers(result.stdout)
        self._caller_cache[cache_key] = (callers, result)
        return callers, result, False

    def _make_symbol_edge(
        self,
        provider_file: str,
        symbol: dict[str, Any],
        caller: dict[str, Any],
        consumer_file: str,
        symbol_ref: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        symbol_ref = symbol_ref or {}
        edge: dict[str, Any] = {
            "symbol": symbol["name"],
            "kind": symbol.get("kind") or "symbol",
            "provider_file": provider_file,
            "consumer_file": consumer_file,
        }
        qualified_name = self._clean_string(symbol_ref.get("qualified_name"))
        if qualified_name:
            edge["qualified_name"] = qualified_name
        if symbol_ref.get("match_source") == "query_json":
            edge["match_source"] = "query_json"
        provider_line = self._positive_int(symbol.get("line") or symbol_ref.get("provider_line"))
        if provider_line is not None:
            edge["provider_line"] = provider_line
        caller_name = self._clean_string(caller.get("name"))
        if caller_name:
            edge["caller"] = caller_name
        caller_kind = self._clean_string(caller.get("kind"))
        if caller_kind:
            edge["caller_kind"] = caller_kind
        caller_line = self._positive_int(caller.get("startLine") or caller.get("line"))
        if caller_line is not None:
            edge["caller_line"] = caller_line
        return edge

    def _dedupe_symbol_edges(self, edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique = {}
        for edge in edges:
            key = (
                edge.get("symbol"),
                edge.get("kind"),
                edge.get("provider_file"),
                edge.get("provider_line"),
                edge.get("consumer_file"),
                edge.get("caller"),
                edge.get("caller_kind"),
                edge.get("caller_line"),
                edge.get("qualified_name"),
            )
            unique[key] = edge
        return sorted(
            unique.values(),
            key=lambda item: (
                item.get("provider_file") or "",
                item.get("symbol") or "",
                item.get("consumer_file") or "",
                item.get("caller") or "",
                item.get("caller_line") or 0,
            ),
        )

    def _infer_module_name(self, file_path: str) -> str:
        parts = file_path.split("/")
        filename = parts[-1] if parts else file_path
        if "src" in parts:
            src_index = parts.index("src")
            if len(parts) > src_index + 2:
                return parts[src_index + 1]
        if len(parts) > 1:
            if parts[0] in {"apps", "packages", "services"} and len(parts) > 2:
                return parts[1]
            return parts[0]
        if filename.endswith(".py"):
            return os.path.splitext(filename)[0]
        return "common"

    def _criticality(self, module_name: str) -> str:
        if module_name in {"auth", "security"}:
            return "high"
        if module_name in {"billing", "payment", "db", "database"}:
            return "critical"
        return "medium"

    def _normalize_path(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.replace("\\", "/").strip().strip("/")
        return normalized or None

    def _clean_string(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    def _positive_int(self, value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.isdigit():
            parsed = int(value)
            if parsed > 0:
                return parsed
        return None

    def _run(self, args: list[str]) -> CommandResult:
        command_parts = self._command_parts()
        executable = shutil.which(command_parts[0]) or command_parts[0]
        full_args = [executable, *command_parts[1:], *args]
        try:
            completed = subprocess.run(
                full_args,
                cwd=self.root_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.timeout_seconds,
                check=False,
            )
            return CommandResult(
                args=full_args,
                returncode=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                args=full_args,
                returncode=124,
                stdout=self._coerce_text(exc.stdout),
                stderr=self._coerce_text(exc.stderr) or "CodeGraph command timed out.",
                timed_out=True,
            )
        except OSError as exc:
            return CommandResult(args=full_args, returncode=127, stdout="", stderr=str(exc))

    def _command_diagnostics(self, result: CommandResult) -> dict[str, Any]:
        return {
            "args": result.args,
            "returncode": result.returncode,
            "stderr_excerpt": self._truncate(result.stderr, 2000),
            "timed_out": result.timed_out,
        }

    def _command_parts(self) -> list[str]:
        parts = shlex.split(self.config.command, posix=False)
        return parts or ["codegraph"]

    def _uses_npx_command(self) -> bool:
        parts = self._command_parts()
        return bool(parts) and os.path.basename(parts[0]).lower() in {"npx", "npx.cmd", "npx.exe"}

    def _truncate(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[:limit] + "\n[truncated]"

    def _coerce_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)


__all__ = ["CodeGraphGraphExporter"]
