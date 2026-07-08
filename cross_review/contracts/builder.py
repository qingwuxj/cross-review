import ast
import os
import re
from dataclasses import dataclass
from typing import Callable

from cross_review.contracts import graphql as graphql_analyzer
from cross_review.contracts import js_ast as js_ast_analyzer
from cross_review.contracts import protobuf as protobuf_analyzer
from cross_review.contracts import python as python_analyzer
from cross_review.contracts import sql as sql_analyzer
from cross_review.contracts import typescript as typescript_analyzer
from cross_review.schemas.models import (
    CallSiteModel,
    ChangedContractModel,
    ContractGraphModel,
    ContractSurfaceModel,
    ProjectGraphModel,
)


@dataclass
class _FileAnalysis:
    module_name: str
    file_path: str
    tree: ast.AST
    source: str
    import_bindings: dict[str, str]


@dataclass
class _TextAnalysis:
    module_name: str
    file_path: str
    source: str


class ContractGraphBuilder:
    def __init__(
        self,
        root_dir: str,
        graph: ProjectGraphModel,
        previous_source_provider: Callable[[str], str | None] | None = None,
        enabled_analyzers: list[str] | None = None,
        path_aliases: dict[str, str] | None = None,
    ):
        self.root_dir = os.path.abspath(root_dir)
        self.graph = graph
        self.previous_source_provider = previous_source_provider
        self.enabled_analyzers = set(enabled_analyzers or ["python", "sql", "typescript", "graphql", "protobuf"])
        self.path_aliases = path_aliases or {}

    def build(self, changed_files: list[str]) -> ContractGraphModel:
        analyses = python_analyzer.parse_python_files(self) if self._enabled("python") else []
        text_analyses = (
            typescript_analyzer.parse_text_files(self, (".ts", ".tsx", ".js", ".jsx"))
            if self._enabled("typescript")
            else []
        )
        schema_analyses = (
            typescript_analyzer.parse_text_files(self, (".graphql", ".gql", ".proto"))
            if self._enabled("graphql") or self._enabled("protobuf")
            else []
        )
        consumer_text_analyses = (
            typescript_analyzer.parse_text_files(self, (".py", ".ts", ".tsx", ".js", ".jsx"))
            if self._enabled("graphql") or self._enabled("protobuf")
            else []
        )
        surfaces = []
        if self._enabled("python"):
            surfaces.extend(python_analyzer.extract_python_surfaces(self, analyses))
        if self._enabled("sql"):
            surfaces.extend(sql_analyzer.extract_sql_surfaces(self))
        if self._enabled("typescript"):
            surfaces.extend(typescript_analyzer.extract_typescript_surfaces(self, text_analyses))
        if self._enabled("graphql"):
            surfaces.extend(graphql_analyzer.extract_graphql_surfaces(self, schema_analyses))
        if self._enabled("protobuf"):
            surfaces.extend(protobuf_analyzer.extract_proto_surfaces(self, schema_analyses))
        surfaces = self._dedupe_surfaces(surfaces)
        changed_contracts = self._changed_contracts(surfaces, set(changed_files))
        call_sites = []
        if self._enabled("python"):
            call_sites.extend(python_analyzer.extract_python_call_sites(self, analyses, surfaces))
        if self._enabled("typescript"):
            call_sites.extend(typescript_analyzer.extract_typescript_call_sites(self, text_analyses, surfaces))
        if self._enabled("graphql"):
            call_sites.extend(graphql_analyzer.extract_graphql_call_sites(self, consumer_text_analyses, surfaces))
        if self._enabled("protobuf"):
            call_sites.extend(protobuf_analyzer.extract_proto_call_sites(self, consumer_text_analyses, surfaces))
        call_sites = self._dedupe_call_sites(call_sites)
        return ContractGraphModel(
            contract_surfaces=surfaces,
            changed_contracts=changed_contracts,
            call_sites=call_sites,
        )

    def _enabled(self, analyzer: str) -> bool:
        return analyzer in self.enabled_analyzers

    def _parse_python_files(self) -> list[_FileAnalysis]:
        analyses: list[_FileAnalysis] = []
        for module_name, module in self.graph.modules.items():
            for file_path in module.files:
                if not file_path.endswith(".py"):
                    continue
                file_abs = os.path.join(self.root_dir, file_path.replace("/", os.sep))
                if not os.path.exists(file_abs):
                    continue
                try:
                    with open(file_abs, "r", encoding="utf-8") as f:
                        source = f.read()
                    tree = ast.parse(source, filename=file_abs)
                except Exception:
                    continue
                analyses.append(
                    _FileAnalysis(
                        module_name=module_name,
                        file_path=file_path,
                        tree=tree,
                        source=source,
                        import_bindings=self._import_bindings(tree),
                    )
                )
        return analyses

    def _parse_text_files(self, suffixes: tuple[str, ...]) -> list[_TextAnalysis]:
        analyses: list[_TextAnalysis] = []
        for module_name, module in self.graph.modules.items():
            for file_path in module.files:
                if not file_path.endswith(suffixes):
                    continue
                file_abs = os.path.join(self.root_dir, file_path.replace("/", os.sep))
                if not os.path.exists(file_abs):
                    continue
                try:
                    with open(file_abs, "r", encoding="utf-8") as f:
                        source = f.read()
                except Exception:
                    continue
                analyses.append(_TextAnalysis(module_name=module_name, file_path=file_path, source=source))
        return analyses

    def _extract_surfaces(self, analyses: list[_FileAnalysis]) -> list[ContractSurfaceModel]:
        surfaces: list[ContractSurfaceModel] = []
        seen_runtime_contracts: set[str] = set()
        for analysis in analyses:
            for node in analysis.tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    surfaces.append(
                        ContractSurfaceModel(
                            contract_id=self._contract_id("function", analysis.file_path, node.name),
                            module=analysis.module_name,
                            kind="function",
                            name=node.name,
                            file=analysis.file_path,
                            line=node.lineno,
                            signature=self._function_signature(node),
                            return_shape=self._unparse(node.returns) if node.returns else "",
                            evidence=self._line_at(analysis.source, node.lineno),
                        )
                    )
                    for method, route_path, line in self._route_decorators(node):
                        contract_id = self._contract_id("route", analysis.file_path, route_path)
                        surfaces.append(
                            ContractSurfaceModel(
                                contract_id=contract_id,
                                module=analysis.module_name,
                                kind="route",
                                name=route_path,
                                file=analysis.file_path,
                                line=line,
                                signature=f"{method.upper()} {route_path}",
                                evidence=self._line_at(analysis.source, line),
                            )
                        )
                elif isinstance(node, ast.ClassDef):
                    surfaces.append(
                        ContractSurfaceModel(
                            contract_id=self._contract_id("class", analysis.file_path, node.name),
                            module=analysis.module_name,
                            kind="class",
                            name=node.name,
                            file=analysis.file_path,
                            line=node.lineno,
                            signature=self._class_signature(node),
                            evidence=self._line_at(analysis.source, node.lineno),
                        )
                    )
            for node in ast.walk(analysis.tree):
                if not isinstance(node, ast.Call):
                    continue
                call_name = self._call_name(node.func)
                event_name = self._first_string_arg(node)
                if call_name not in {"emit", "publish", "trigger"} or not event_name:
                    continue
                contract_id = self._contract_id("event", analysis.file_path, event_name)
                if contract_id in seen_runtime_contracts:
                    continue
                seen_runtime_contracts.add(contract_id)
                surfaces.append(
                    ContractSurfaceModel(
                        contract_id=contract_id,
                        module=analysis.module_name,
                        kind="event",
                        name=event_name,
                        file=analysis.file_path,
                        line=node.lineno,
                        signature=f"event {event_name}",
                        evidence=self._line_at(analysis.source, node.lineno),
                    )
                )
        return sorted(surfaces, key=lambda surface: surface.contract_id)

    def _extract_sql_surfaces(self) -> list[ContractSurfaceModel]:
        surfaces: list[ContractSurfaceModel] = []
        add_not_null_pattern = re.compile(
            r"ALTER\s+TABLE\s+([a-zA-Z_][\w]*)\s+ADD\s+COLUMN\s+([a-zA-Z_][\w]*)\b[^;]*\bNOT\s+NULL\b",
            re.IGNORECASE,
        )
        for module_name, module in self.graph.modules.items():
            for file_path in module.files:
                if not file_path.endswith(".sql"):
                    continue
                file_abs = os.path.join(self.root_dir, file_path.replace("/", os.sep))
                if not os.path.exists(file_abs):
                    continue
                try:
                    with open(file_abs, "r", encoding="utf-8") as f:
                        source = f.read()
                except Exception:
                    continue
                for match in add_not_null_pattern.finditer(source):
                    table, column = match.group(1), match.group(2)
                    name = f"{table}.{column}"
                    line = source[: match.start()].count("\n") + 1
                    surfaces.append(
                        ContractSurfaceModel(
                            contract_id=f"sql:column:{file_path}:{name}",
                            module=module_name,
                            kind="db_column",
                            name=name,
                            file=file_path,
                            line=line,
                            signature=f"ALTER TABLE {table} ADD COLUMN {column} NOT NULL",
                            evidence=self._line_at(source, line),
                        )
                    )
        return sorted(surfaces, key=lambda surface: surface.contract_id)

    def _extract_typescript_surfaces(self, analyses: list[_TextAnalysis]) -> list[ContractSurfaceModel]:
        surfaces: list[ContractSurfaceModel] = []
        function_pattern = re.compile(r"\bexport\s+function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)")
        default_function_pattern = re.compile(r"\bexport\s+default\s+function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)")
        arrow_function_pattern = re.compile(
            r"\bexport\s+const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*(?::\s*([^=]+?))?\s*=>",
            re.MULTILINE,
        )
        class_pattern = re.compile(r"\bexport\s+class\s+([A-Za-z_$][\w$]*)\b")
        route_pattern = re.compile(
            r"\b(?:router|app|fastify|server)\.(get|post|put|delete|patch|options|head)\s*\(\s*([\"'`])([^\"'`]+)\2",
            re.MULTILINE,
        )
        for analysis in analyses:
            for match in function_pattern.finditer(analysis.source):
                name = match.group(1)
                line = self._line_number(analysis.source, match.start())
                surfaces.append(
                    ContractSurfaceModel(
                        contract_id=f"typescript:function:{analysis.file_path}:{name}",
                        module=analysis.module_name,
                        kind="function",
                        name=name,
                        file=analysis.file_path,
                        line=line,
                        signature=f"{name}({match.group(2).strip()})",
                        evidence=self._line_at(analysis.source, line),
                    )
                )
            for match in default_function_pattern.finditer(analysis.source):
                name = match.group(1)
                line = self._line_number(analysis.source, match.start())
                surfaces.append(
                    ContractSurfaceModel(
                        contract_id=f"typescript:function:{analysis.file_path}:{name}",
                        module=analysis.module_name,
                        kind="function",
                        name=name,
                        file=analysis.file_path,
                        line=line,
                        signature=f"{name}({match.group(2).strip()})",
                        evidence=self._line_at(analysis.source, line),
                    )
                )
            for match in arrow_function_pattern.finditer(analysis.source):
                name = match.group(1)
                args = match.group(2).strip()
                return_type = (match.group(3) or "").strip()
                line = self._line_number(analysis.source, match.start())
                signature = f"{name}({args})"
                if return_type:
                    signature = f"{signature} -> {return_type}"
                surfaces.append(
                    ContractSurfaceModel(
                        contract_id=f"typescript:function:{analysis.file_path}:{name}",
                        module=analysis.module_name,
                        kind="function",
                        name=name,
                        file=analysis.file_path,
                        line=line,
                        signature=signature,
                        evidence=self._line_at(analysis.source, line),
                    )
                )
            for match in class_pattern.finditer(analysis.source):
                name = match.group(1)
                line = self._line_number(analysis.source, match.start())
                surfaces.append(
                    ContractSurfaceModel(
                        contract_id=f"typescript:class:{analysis.file_path}:{name}",
                        module=analysis.module_name,
                        kind="class",
                        name=name,
                        file=analysis.file_path,
                        line=line,
                        signature=f"class {name}",
                        evidence=self._line_at(analysis.source, line),
                    )
                )
            for match in route_pattern.finditer(analysis.source):
                method, route_path = match.group(1), match.group(3)
                line = self._line_number(analysis.source, match.start())
                surfaces.append(
                    ContractSurfaceModel(
                        contract_id=f"typescript:route:{analysis.file_path}:{route_path}",
                        module=analysis.module_name,
                        kind="route",
                        name=route_path,
                        file=analysis.file_path,
                        line=line,
                        signature=f"{method.upper()} {route_path}",
                        evidence=self._line_at(analysis.source, line),
                    )
                )
        return sorted(surfaces, key=lambda surface: surface.contract_id)

    def _extract_graphql_surfaces(self, analyses: list[_TextAnalysis]) -> list[ContractSurfaceModel]:
        surfaces: list[ContractSurfaceModel] = []
        type_pattern = re.compile(r"\btype\s+([A-Za-z_][\w]*)\s*\{(?P<body>.*?)\}", re.DOTALL)
        field_pattern = re.compile(r"^\s*([A-Za-z_][\w]*)\s*:", re.MULTILINE)
        for analysis in analyses:
            if not analysis.file_path.endswith((".graphql", ".gql")):
                continue
            for type_match in type_pattern.finditer(analysis.source):
                type_name = type_match.group(1)
                body = type_match.group("body")
                body_start = type_match.start("body")
                for field_match in field_pattern.finditer(body):
                    field_name = field_match.group(1)
                    name = f"{type_name}.{field_name}"
                    line = self._line_number(analysis.source, body_start + field_match.start(1))
                    surfaces.append(
                        ContractSurfaceModel(
                            contract_id=f"graphql:field:{analysis.file_path}:{name}",
                            module=analysis.module_name,
                            kind="graphql_field",
                            name=name,
                            file=analysis.file_path,
                            line=line,
                            signature=name,
                            evidence=self._line_at(analysis.source, line),
                        )
                    )
        return sorted(surfaces, key=lambda surface: surface.contract_id)

    def _extract_proto_surfaces(self, analyses: list[_TextAnalysis]) -> list[ContractSurfaceModel]:
        surfaces: list[ContractSurfaceModel] = []
        service_pattern = re.compile(r"\bservice\s+([A-Za-z_][\w]*)\s*\{(?P<body>.*?)\}", re.DOTALL)
        rpc_pattern = re.compile(r"\brpc\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)\s+returns\s+\(([^)]*)\)")
        for analysis in analyses:
            if not analysis.file_path.endswith(".proto"):
                continue
            for service_match in service_pattern.finditer(analysis.source):
                service_name = service_match.group(1)
                body = service_match.group("body")
                body_start = service_match.start("body")
                for rpc_match in rpc_pattern.finditer(body):
                    method_name = rpc_match.group(1)
                    name = f"{service_name}.{method_name}"
                    line = self._line_number(analysis.source, body_start + rpc_match.start(1))
                    surfaces.append(
                        ContractSurfaceModel(
                            contract_id=f"protobuf:rpc:{analysis.file_path}:{name}",
                            module=analysis.module_name,
                            kind="rpc_method",
                            name=name,
                            file=analysis.file_path,
                            line=line,
                            signature=f"rpc {method_name}({rpc_match.group(2).strip()}) returns ({rpc_match.group(3).strip()})",
                            evidence=self._line_at(analysis.source, line),
                        )
                    )
        return sorted(surfaces, key=lambda surface: surface.contract_id)

    def _changed_contracts(
        self,
        surfaces: list[ContractSurfaceModel],
        changed_files: set[str],
    ) -> list[ChangedContractModel]:
        changed = []
        for surface in surfaces:
            if surface.file not in changed_files:
                continue
            previous_surface = self._previous_surface_for(surface)
            change_type = "changed_file_contains_export"
            previous_signature = None
            current_signature = None
            diff_summary = None
            if previous_surface and previous_surface.signature != surface.signature:
                change_type = "signature_changed"
                previous_signature = previous_surface.signature
                current_signature = surface.signature
                diff_summary = (
                    f"Signature changed from `{previous_surface.signature}` "
                    f"to `{surface.signature}`."
                )
            changed.append(
                ChangedContractModel(
                    contract_id=surface.contract_id,
                    module=surface.module,
                    change_type=change_type,
                    file=surface.file,
                    line=surface.line,
                    previous_signature=previous_signature,
                    current_signature=current_signature,
                    diff_summary=diff_summary,
                    risk_reason=(
                        diff_summary
                        or f"Changed file {surface.file} contains exported {surface.kind} '{surface.name}'."
                    ),
                )
            )
        return sorted(changed, key=lambda item: item.contract_id)

    def _previous_surface_for(self, surface: ContractSurfaceModel) -> ContractSurfaceModel | None:
        source = self._previous_source_for(surface.file)
        if source is None:
            return None

        previous_surfaces: list[ContractSurfaceModel] = []
        if surface.file.endswith(".py"):
            try:
                tree = ast.parse(source, filename=surface.file)
            except Exception:
                return None
            previous_surfaces = self._extract_surfaces([
                _FileAnalysis(
                    module_name=surface.module,
                    file_path=surface.file,
                    tree=tree,
                    source=source,
                    import_bindings={},
                )
            ])
        elif surface.file.endswith((".ts", ".tsx", ".js", ".jsx")):
            previous_surfaces = self._extract_typescript_surfaces([
                _TextAnalysis(
                    module_name=surface.module,
                    file_path=surface.file,
                    source=source,
                )
            ])

        return next(
            (
                previous
                for previous in previous_surfaces
                if previous.contract_id == surface.contract_id
            ),
            None,
        )

    def _previous_source_for(self, file_path: str) -> str | None:
        if self.previous_source_provider:
            try:
                source = self.previous_source_provider(file_path)
            except Exception:
                source = None
            if source is not None:
                return source

        before_abs = os.path.join(
            self.root_dir,
            ".cross-review-before",
            file_path.replace("/", os.sep),
        )
        if not os.path.exists(before_abs):
            return None
        try:
            with open(before_abs, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None

    def _extract_call_sites(
        self,
        analyses: list[_FileAnalysis],
        surfaces: list[ContractSurfaceModel],
    ) -> list[CallSiteModel]:
        surfaces_by_binding = self._surfaces_by_binding(surfaces)
        orm_table_classes = self._orm_table_classes(analyses)
        call_sites: list[CallSiteModel] = []
        seen_ids: set[str] = set()

        for analysis in analyses:
            for node in ast.walk(analysis.tree):
                if not isinstance(node, ast.Call):
                    continue
                binding_key, usage = self._call_binding(node)
                if binding_key:
                    provider_key = self._provider_key_for_binding(binding_key, analysis.import_bindings)
                    surface = surfaces_by_binding.get(provider_key or "")
                    if surface and surface.module != analysis.module_name:
                        self._append_callsite(
                            call_sites,
                            seen_ids,
                            CallSiteModel(
                                callsite_id=(
                                    f"python:call:{analysis.file_path}:"
                                    f"{self._enclosing_function_name(analysis.tree, node)}:{node.lineno}"
                                ),
                                consumer_module=analysis.module_name,
                                provider_module=surface.module,
                                contract_id=surface.contract_id,
                                file=analysis.file_path,
                                line=node.lineno,
                                usage=usage,
                                evidence=self._line_at(analysis.source, node.lineno),
                            ),
                        )

                for surface in surfaces:
                    if surface.module == analysis.module_name:
                        continue
                    if surface.kind == "route" and self._call_matches_route(node, surface.name):
                        self._append_callsite(
                            call_sites,
                            seen_ids,
                            CallSiteModel(
                                callsite_id=(
                                    f"python:route-call:{analysis.file_path}:"
                                    f"{self._enclosing_function_name(analysis.tree, node)}:{node.lineno}"
                                ),
                                consumer_module=analysis.module_name,
                                provider_module=surface.module,
                                contract_id=surface.contract_id,
                                file=analysis.file_path,
                                line=node.lineno,
                                usage=usage,
                                evidence=self._line_at(analysis.source, node.lineno),
                            ),
                        )
                    elif surface.kind == "event" and self._call_listens_to_event(node, surface.name):
                        self._append_callsite(
                            call_sites,
                            seen_ids,
                            CallSiteModel(
                                callsite_id=(
                                    f"python:event-listener:{analysis.file_path}:"
                                    f"{self._enclosing_function_name(analysis.tree, node)}:{node.lineno}"
                                ),
                                consumer_module=analysis.module_name,
                                provider_module=surface.module,
                                contract_id=surface.contract_id,
                                file=analysis.file_path,
                                line=node.lineno,
                                usage=usage,
                                evidence=self._line_at(analysis.source, node.lineno),
                            ),
                        )
                    elif surface.kind == "db_column" and self._call_writes_db_table(node, surface.name, orm_table_classes):
                        self._append_callsite(
                            call_sites,
                            seen_ids,
                            CallSiteModel(
                                callsite_id=(
                                    f"python:sql-write:{analysis.file_path}:"
                                    f"{self._enclosing_function_name(analysis.tree, node)}:{node.lineno}"
                                ),
                                consumer_module=analysis.module_name,
                                provider_module=surface.module,
                                contract_id=surface.contract_id,
                                file=analysis.file_path,
                                line=node.lineno,
                                usage=usage,
                                evidence=self._line_at(analysis.source, node.lineno),
                            ),
                        )

        return sorted(call_sites, key=lambda site: site.callsite_id)

    def _extract_graphql_call_sites(
        self,
        analyses: list[_TextAnalysis],
        surfaces: list[ContractSurfaceModel],
    ) -> list[CallSiteModel]:
        call_sites: list[CallSiteModel] = []
        graphql_surfaces = [
            surface
            for surface in surfaces
            if surface.contract_id.startswith("graphql:field:")
        ]
        for analysis in analyses:
            if analysis.file_path.endswith((".graphql", ".gql", ".proto")):
                continue
            for surface in graphql_surfaces:
                if surface.module == analysis.module_name:
                    continue
                field_name = surface.name.split(".", 1)[-1]
                match = re.search(rf"\b{re.escape(field_name)}\b", analysis.source)
                if not match:
                    continue
                line = self._line_number(analysis.source, match.start())
                call_sites.append(
                    CallSiteModel(
                        callsite_id=(
                            f"graphql:field-read:{analysis.file_path}:"
                            f"{self._text_enclosing_function(analysis.source, match.start())}:{line}"
                        ),
                        consumer_module=analysis.module_name,
                        provider_module=surface.module,
                        contract_id=surface.contract_id,
                        file=analysis.file_path,
                        line=line,
                        usage=self._line_at(analysis.source, line),
                        evidence=self._line_at(analysis.source, line),
                    )
                )
        return sorted(call_sites, key=lambda site: site.callsite_id)

    def _extract_proto_call_sites(
        self,
        analyses: list[_TextAnalysis],
        surfaces: list[ContractSurfaceModel],
    ) -> list[CallSiteModel]:
        call_sites: list[CallSiteModel] = []
        proto_surfaces = [
            surface
            for surface in surfaces
            if surface.contract_id.startswith("protobuf:rpc:")
        ]
        for analysis in analyses:
            if analysis.file_path.endswith((".graphql", ".gql", ".proto")):
                continue
            for surface in proto_surfaces:
                if surface.module == analysis.module_name:
                    continue
                method_name = surface.name.split(".", 1)[-1]
                candidates = {method_name, self._lower_camel(method_name)}
                match = next(
                    (
                        found
                        for candidate in candidates
                        if (found := re.search(rf"\b{re.escape(candidate)}\s*\(", analysis.source))
                    ),
                    None,
                )
                if not match:
                    continue
                line = self._line_number(analysis.source, match.start())
                call_sites.append(
                    CallSiteModel(
                        callsite_id=(
                            f"protobuf:rpc-call:{analysis.file_path}:"
                            f"{self._text_enclosing_function(analysis.source, match.start())}:{line}"
                        ),
                        consumer_module=analysis.module_name,
                        provider_module=surface.module,
                        contract_id=surface.contract_id,
                        file=analysis.file_path,
                        line=line,
                        usage=self._line_at(analysis.source, line),
                        evidence=self._line_at(analysis.source, line),
                    )
                )
        return sorted(call_sites, key=lambda site: site.callsite_id)

    def _extract_typescript_call_sites(
        self,
        analyses: list[_TextAnalysis],
        surfaces: list[ContractSurfaceModel],
    ) -> list[CallSiteModel]:
        call_sites: list[CallSiteModel] = []
        function_surfaces = [
            surface
            for surface in surfaces
            if surface.contract_id.startswith(("typescript:function:", "typescript:class:"))
        ]
        route_surfaces = [
            surface
            for surface in surfaces
            if surface.contract_id.startswith("typescript:route:")
        ]

        for analysis in analyses:
            imports = self._typescript_named_imports(analysis.source, analysis.file_path)
            for surface in function_surfaces:
                if surface.module == analysis.module_name:
                    continue
                local_names = [
                    local_name
                    for local_name, import_info in imports.items()
                    if (
                        import_info["imported_name"] in {surface.name, "default"}
                        and self._typescript_import_path_mentions_module(import_info["import_path"], surface.module)
                    )
                ]
                if not local_names:
                    continue

                for local_name in local_names:
                    call_match = re.search(rf"\b{re.escape(local_name)}\s*\(", analysis.source)
                    if not call_match:
                        continue
                    line = self._line_number(analysis.source, call_match.start())
                    call_sites.append(
                        CallSiteModel(
                            callsite_id=(
                                f"typescript:call:{analysis.file_path}:"
                                f"{self._typescript_enclosing_function(analysis.source, call_match.start())}:{line}"
                            ),
                            consumer_module=analysis.module_name,
                            provider_module=surface.module,
                            contract_id=surface.contract_id,
                            file=analysis.file_path,
                            line=line,
                            usage=self._line_at(analysis.source, line),
                            evidence=self._line_at(analysis.source, line),
                        )
                    )

            for surface in route_surfaces:
                if surface.module == analysis.module_name:
                    continue
                route_prefix = self._route_prefix(surface.name)
                fetch_match = self._typescript_fetch_match(analysis.source, route_prefix)
                if not fetch_match:
                    continue
                line = self._line_number(analysis.source, fetch_match.start())
                call_sites.append(
                    CallSiteModel(
                        callsite_id=(
                            f"typescript:route-call:{analysis.file_path}:"
                            f"{self._typescript_enclosing_function(analysis.source, fetch_match.start())}:{line}"
                        ),
                        consumer_module=analysis.module_name,
                        provider_module=surface.module,
                        contract_id=surface.contract_id,
                        file=analysis.file_path,
                        line=line,
                        usage=self._line_at(analysis.source, line),
                        evidence=self._line_at(analysis.source, line),
                    )
                )

        return sorted(call_sites, key=lambda site: site.callsite_id)

    def _append_callsite(
        self,
        call_sites: list[CallSiteModel],
        seen_ids: set[str],
        callsite: CallSiteModel,
    ):
        if callsite.callsite_id in seen_ids:
            return
        seen_ids.add(callsite.callsite_id)
        call_sites.append(callsite)

    def _dedupe_call_sites(self, call_sites: list[CallSiteModel]) -> list[CallSiteModel]:
        deduped = {}
        for callsite in call_sites:
            deduped.setdefault(callsite.callsite_id, callsite)
        return sorted(deduped.values(), key=lambda site: site.callsite_id)

    def _surfaces_by_binding(self, surfaces: list[ContractSurfaceModel]) -> dict[str, ContractSurfaceModel]:
        mapping = {}
        for surface in surfaces:
            if surface.kind not in {"function", "class"}:
                continue
            module_path = surface.file[:-3].replace("/", ".")
            mapping[f"{module_path}.{surface.name}"] = surface
            module_parts = module_path.split(".")
            if module_parts:
                mapping[f"{module_parts[-1]}.{surface.name}"] = surface
            if surface.module:
                mapping[f"{surface.module}.{surface.name}"] = surface
        return mapping

    def _orm_table_classes(self, analyses: list[_FileAnalysis]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for analysis in analyses:
            for node in ast.walk(analysis.tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                table_name = self._tablename_for_class(node)
                if table_name:
                    mapping.setdefault(node.name, table_name)
        return mapping

    def _tablename_for_class(self, node: ast.ClassDef) -> str | None:
        for item in node.body:
            if isinstance(item, ast.Assign):
                if not any(self._assignment_target_name(target) == "__tablename__" for target in item.targets):
                    continue
                return self._string_constant(item.value)
            if isinstance(item, ast.AnnAssign):
                if self._assignment_target_name(item.target) != "__tablename__":
                    continue
                return self._string_constant(item.value)
        return None

    def _assignment_target_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    def _string_constant(self, node: ast.AST | None) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def _route_decorators(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, str, int]]:
        routes = []
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not isinstance(decorator.func, ast.Attribute):
                continue
            method = decorator.func.attr
            if method not in {"get", "post", "put", "delete", "patch"}:
                continue
            route_path = self._first_string_arg(decorator)
            if route_path:
                routes.append((method, route_path, getattr(decorator, "lineno", node.lineno)))
        return routes

    def _import_bindings(self, tree: ast.AST) -> dict[str, str]:
        bindings: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    full_name = f"{node.module}.{alias.name}"
                    bindings[local_name] = full_name
                    bindings[f"{local_name}.*"] = full_name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local_name = alias.asname or alias.name.split(".")[0]
                    bindings[local_name] = alias.name
                    bindings[f"{local_name}.*"] = alias.name
        return bindings

    def _call_binding(self, node: ast.Call) -> tuple[str | None, str]:
        usage = self._unparse(node)
        if isinstance(node.func, ast.Name):
            return node.func.id, usage
        if isinstance(node.func, ast.Attribute):
            attr_chain = self._attribute_chain(node.func)
            if attr_chain:
                return attr_chain, usage
        return None, usage

    def _call_matches_route(self, node: ast.Call, route_path: str) -> bool:
        route_prefix = self._route_prefix(route_path)
        if not route_prefix:
            return False
        for value in self._string_values(node):
            if value == route_path or value.startswith(route_prefix) or route_prefix in value:
                return True
        return False

    def _call_listens_to_event(self, node: ast.Call, event_name: str) -> bool:
        if self._call_name(node.func) not in {"on", "subscribe", "listen"}:
            return False
        return self._first_string_arg(node) == event_name

    def _call_writes_db_table(
        self,
        node: ast.Call,
        db_column_name: str,
        orm_table_classes: dict[str, str] | None = None,
    ) -> bool:
        table = db_column_name.split(".", 1)[0]
        call_name = self._call_name(node.func)
        if call_name and (orm_table_classes or {}).get(call_name) == table:
            return True
        pattern = re.compile(rf"\b(?:INSERT\s+INTO|UPDATE)\s+{re.escape(table)}\b", re.IGNORECASE)
        string_values = self._string_values(node)
        if any(pattern.search(value) for value in string_values):
            return True
        if call_name in {"insert", "table"} and table in string_values:
            return True
        return False

    def _route_prefix(self, route_path: str) -> str:
        match = re.search(r"[{:<]", route_path)
        if match:
            return route_path[: match.start()]
        if route_path.endswith("/"):
            return route_path
        return route_path.rstrip("/") + "/"

    def _typescript_named_imports(self, source: str, file_path: str = "") -> dict[str, dict[str, str]]:
        imports = self._typescript_regex_imports(source)
        source_type = "tsx" if file_path.endswith(".tsx") else "typescript"
        imports.update(js_ast_analyzer.extract_imports(source, source_type=source_type))
        return imports

    def _typescript_regex_imports(self, source: str) -> dict[str, dict[str, str]]:
        imports: dict[str, dict[str, str]] = {}
        pattern = re.compile(r"import\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]")
        for match in pattern.finditer(source):
            for raw_name in match.group(1).split(","):
                parts = [part.strip() for part in raw_name.strip().split(" as ")]
                if not parts or not parts[0]:
                    continue
                imported_name = parts[0]
                local_name = parts[-1] if len(parts) > 1 else imported_name
                imports[local_name] = {
                    "imported_name": imported_name,
                    "import_path": match.group(2),
                }
        default_pattern = re.compile(r"import\s+([A-Za-z_$][\w$]*)\s+from\s+['\"]([^'\"]+)['\"]")
        for match in default_pattern.finditer(source):
            imports[match.group(1)] = {
                "imported_name": "default",
                "import_path": match.group(2),
            }
        return imports

    def _typescript_import_path_mentions_module(self, import_path: str, module_name: str) -> bool:
        normalized = self._normalize_typescript_import_path(import_path)
        return bool(normalized) and module_name.lower() in normalized.lower().split("/")

    def _normalize_typescript_import_path(self, import_path: str) -> str:
        for alias_prefix, target_prefix in sorted(self.path_aliases.items(), key=lambda item: len(item[0]), reverse=True):
            if import_path.startswith(alias_prefix):
                suffix = import_path[len(alias_prefix):].lstrip("/")
                return f"{target_prefix.rstrip('/')}/{suffix}" if suffix else target_prefix.rstrip("/")
        return import_path

    def _typescript_fetch_match(self, source: str, route_prefix: str):
        if not route_prefix:
            return None
        pattern = re.compile(
            rf"\b(?:fetch|axios\.(?:get|post|put|delete|patch))\s*\(\s*([`'\"]){re.escape(route_prefix)}",
            re.MULTILINE,
        )
        return pattern.search(source)

    def _typescript_enclosing_function(self, source: str, offset: int) -> str:
        return self._text_enclosing_function(source, offset)

    def _fallback_provider_key(self, binding_key: str) -> str | None:
        if "." not in binding_key:
            return None
        base, attr = binding_key.rsplit(".", 1)
        return f"{base}.{attr}"

    def _provider_key_for_binding(self, binding_key: str, import_bindings: dict[str, str]) -> str | None:
        direct = import_bindings.get(binding_key)
        if direct:
            return direct
        parts = binding_key.split(".")
        for index in range(len(parts) - 1, 0, -1):
            base = ".".join(parts[:index])
            remainder = ".".join(parts[index:])
            if base in import_bindings:
                return f"{import_bindings[base]}.{remainder}"
        return self._fallback_provider_key(binding_key)

    def _text_enclosing_function(self, source: str, offset: int) -> str:
        prefix = source[:offset]
        patterns = [
            r"\b(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(",
            r"\bdef\s+([A-Za-z_][\w]*)\s*\(",
            r"\b(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(",
        ]
        matches = []
        for pattern in patterns:
            matches.extend(re.finditer(pattern, prefix))
        if not matches:
            return "<module>"
        return max(matches, key=lambda match: match.start()).group(1)

    def _lower_camel(self, value: str) -> str:
        if not value:
            return value
        return value[0].lower() + value[1:]

    def _attribute_chain(self, node: ast.Attribute) -> str | None:
        parts = [node.attr]
        current = node.value
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            return ".".join(reversed(parts))
        return None

    def _call_name(self, func: ast.AST) -> str | None:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    def _first_string_arg(self, node: ast.Call) -> str | None:
        if not node.args:
            return None
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
        return None

    def _string_values(self, node: ast.AST) -> list[str]:
        values = []
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                values.append(child.value)
            elif isinstance(child, ast.JoinedStr):
                literal = "".join(
                    part.value
                    for part in child.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                )
                if literal:
                    values.append(literal)
        return values

    def _enclosing_function_name(self, tree: ast.AST, target: ast.AST) -> str:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(node):
                    if child is target:
                        return node.name
        return "<module>"

    def _class_signature(self, node: ast.ClassDef) -> str:
        init_node = next(
            (
                item
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__init__"
            ),
            None,
        )
        if init_node is None:
            return f"class {node.name}"
        args = self._function_args_signature(init_node, drop_first_arg=True)
        return f"class {node.name}({args})"

    def _function_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args = self._function_args_signature(node)
        returns = f" -> {self._unparse(node.returns)}" if node.returns else ""
        return f"{node.name}({args}){returns}"

    def _function_args_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef, drop_first_arg: bool = False) -> str:
        positional_args = list(node.args.args)
        positional_defaults: list[ast.AST | None] = [None] * (len(positional_args) - len(node.args.defaults)) + list(node.args.defaults)
        if drop_first_arg and positional_args:
            positional_args = positional_args[1:]
            positional_defaults = positional_defaults[1:]

        args = [
            self._arg_signature(arg, default)
            for arg, default in zip(positional_args, positional_defaults)
        ]
        if node.args.vararg:
            args.append("*" + self._arg_signature(node.args.vararg))
        args.extend(
            self._arg_signature(arg, default)
            for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults)
        )
        if node.args.kwarg:
            args.append("**" + self._arg_signature(node.args.kwarg))
        return ", ".join(args)

    def _arg_signature(self, arg: ast.arg, default: ast.AST | None = None) -> str:
        if arg.annotation:
            signature = f"{arg.arg}: {self._unparse(arg.annotation)}"
        else:
            signature = arg.arg
        if default is not None:
            signature = f"{signature}={self._unparse(default)}"
        return signature

    def _contract_id(self, kind: str, file_path: str, name: str) -> str:
        return f"python:{kind}:{file_path}:{name}"

    def _dedupe_surfaces(self, surfaces: list[ContractSurfaceModel]) -> list[ContractSurfaceModel]:
        deduped = {}
        for surface in surfaces:
            deduped.setdefault(surface.contract_id, surface)
        return sorted(deduped.values(), key=lambda surface: surface.contract_id)

    def _unparse(self, node: ast.AST | None) -> str:
        if node is None:
            return ""
        try:
            return ast.unparse(node)
        except Exception:
            return ""

    def _line_at(self, source: str, lineno: int) -> str:
        lines = source.splitlines()
        if 1 <= lineno <= len(lines):
            return lines[lineno - 1].strip()
        return ""

    def _line_number(self, source: str, offset: int) -> int:
        return source[:offset].count("\n") + 1
