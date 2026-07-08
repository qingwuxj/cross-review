from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from cross_review.config import CodeGraphIntegrationConfig


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class CodeGraphIntegration:
    """Collect optional CodeGraph CLI context without depending on its internals."""

    def __init__(self, root_dir: str, config: CodeGraphIntegrationConfig):
        self.root_dir = os.path.abspath(root_dir)
        self.config = config

    def collect(self, changed_files: list[str]) -> dict[str, Any]:
        context = self._base_context(changed_files)
        enabled_mode = self.config.enabled
        if enabled_mode == "false":
            context["status"] = "disabled"
            context["reason"] = "disabled_by_config"
            return context

        index_present = os.path.isdir(os.path.join(self.root_dir, ".codegraph"))
        context["index_present"] = index_present
        if enabled_mode == "auto" and not index_present:
            context["status"] = "skipped"
            context["reason"] = "no_codegraph_index"
            return context

        command_parts = self._command_parts()
        command_path = shutil.which(command_parts[0])
        if command_path is None:
            context["status"] = "skipped" if enabled_mode == "auto" else "error"
            context["reason"] = "codegraph_command_not_found"
            return context

        context["available"] = True
        context["command_path"] = command_path

        status = self._run(["status", self.root_dir])
        context["commands"]["status"] = self._command_payload(status)
        if status.returncode != 0:
            context["status"] = "skipped" if enabled_mode == "auto" else "error"
            context["reason"] = "codegraph_status_failed"
            return context

        context["enabled"] = True
        context["status"] = "enabled"

        if changed_files:
            affected = self._run(
                [
                    "affected",
                    *changed_files,
                    "--depth",
                    str(self.config.affected_depth),
                    "--json",
                ]
            )
            context["commands"]["affected"] = self._command_payload(affected)
            context["affected"] = self._parse_json_output(affected.stdout)

            explore = self._run(["explore", self._build_explore_query(changed_files)])
            context["commands"]["explore"] = self._command_payload(explore)
            context["explore"] = self._truncate(explore.stdout, self.config.max_explore_chars)

        return context

    def _base_context(self, changed_files: list[str]) -> dict[str, Any]:
        return {
            "enabled": False,
            "available": False,
            "index_present": False,
            "status": "unknown",
            "reason": None,
            "source": "codegraph-cli",
            "mode": self.config.enabled,
            "command": self.config.command,
            "changed_files": list(changed_files),
            "affected": None,
            "explore": "",
            "commands": {},
            "usage_notes": [
                "Use CodeGraph context as supplemental routing and blast-radius evidence.",
                "Do not treat CodeGraph summaries as final findings without checking cited source lines.",
                "High or blocking findings still need concrete file/line evidence from the review pack or repository.",
            ],
        }

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
            return CommandResult(
                args=full_args,
                returncode=127,
                stdout="",
                stderr=str(exc),
            )

    def _command_payload(self, result: CommandResult) -> dict[str, Any]:
        return {
            "args": result.args,
            "returncode": result.returncode,
            "stdout_excerpt": self._truncate(result.stdout, 2000),
            "stderr_excerpt": self._truncate(result.stderr, 2000),
            "timed_out": result.timed_out,
        }

    def _parse_json_output(self, stdout: str) -> Any:
        if not stdout.strip():
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {
                "parse_error": "invalid_json",
                "raw_excerpt": self._truncate(stdout, 4000),
            }

    def _build_explore_query(self, changed_files: list[str]) -> str:
        files = ", ".join(changed_files[:12])
        overflow = "" if len(changed_files) <= 12 else f", plus {len(changed_files) - 12} more changed files"
        return (
            "Cross-review blast radius, callers, callees, runtime routes, and affected tests for changed files: "
            f"{files}{overflow}"
        )

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

    def _command_parts(self) -> list[str]:
        parts = shlex.split(self.config.command, posix=False)
        return parts or ["codegraph"]


__all__ = ["CodeGraphIntegration", "CommandResult"]
