import os
import ast
import re
import fnmatch
import time
import json
import hashlib
from cross_review.graph import ProjectGraph

class ScoutScanner:
    def __init__(self, root_dir: str, ignored_paths: list[str] | None = None):
        self.root_dir = os.path.abspath(root_dir)
        self.project_name = os.path.basename(self.root_dir)
        self.graph = ProjectGraph(self.project_name)
        self.ignored_paths = ignored_paths or []
        self.diagnostics = {
            "scan_files_ms": 0,
            "scout_analyze_ms": 0,
            "scout_total_ms": 0,
            "scanned_file_count": 0,
            "module_count": 0,
            "scout_cache_status": "disabled",
        }

    def scan(self, target_files: list[str] | None = None, targeted_file_threshold: int = 0) -> ProjectGraph:
        """
        扫描整个项目，识别模块、文件、静态 import 依赖、API 路由和事件监听。
        """
        # 1. 扫描所有可用物理文件并按目录结构进行模块自动划分
        scan_start = time.perf_counter()
        source_file_map = self._scan_files_and_group_modules()
        source_file_count = sum(len(files) for files in source_file_map.values())
        use_targeted = bool(target_files) and targeted_file_threshold > 0 and source_file_count > targeted_file_threshold
        file_map = self._targeted_file_map(source_file_map, target_files) if use_targeted else source_file_map
        scan_ms = self._elapsed_ms(scan_start)
        
        # 2. 将每个模块加入图中
        for mod_name, files in file_map.items():
            criticality = "medium"
            # 特殊高危模块标签
            if mod_name in ["auth", "security"]:
                criticality = "high"
            elif mod_name in ["billing", "payment", "db", "database"]:
                criticality = "critical"
            self.graph.add_module(mod_name, files, criticality)

        # 3. 解析各模块内部文件以提取 import、api 路由和事件
        analyze_start = time.perf_counter()
        self._analyze_modules_internals(file_map)
        analyze_ms = self._elapsed_ms(analyze_start)
        self.diagnostics = {
            "scan_files_ms": scan_ms,
            "scout_analyze_ms": analyze_ms,
            "scout_total_ms": scan_ms + analyze_ms,
            "scanned_file_count": sum(len(files) for files in file_map.values()),
            "source_file_count": source_file_count,
            "module_count": len(file_map),
            "scan_mode": "targeted" if use_targeted else "full",
            "targeted_file_count": sum(len(files) for files in file_map.values()) if use_targeted else 0,
            "skipped_file_count": max(0, source_file_count - sum(len(files) for files in file_map.values())),
            "scout_cache_status": "miss",
        }

        return self.graph

    def source_fingerprint(self) -> dict:
        file_map = self._scan_files_and_group_modules()
        entries = []
        for file_path in sorted(file_path for files in file_map.values() for file_path in files):
            file_abs = os.path.join(self.root_dir, file_path.replace("/", os.sep))
            try:
                stat = os.stat(file_abs)
            except OSError:
                continue
            entries.append({
                "path": file_path,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            })
        payload = json.dumps(entries, sort_keys=True, separators=(",", ":"))
        return {
            "fingerprint": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            "source_file_count": len(entries),
        }

    def _elapsed_ms(self, start: float) -> int:
        return int((time.perf_counter() - start) * 1000)

    def _scan_files_and_group_modules(self) -> dict[str, list[str]]:
        """
        深度遍历项目，跳过忽略目录，并按照一级子目录进行逻辑模块自动切分。
        """
        ignored_dirs = {
            ".git",
            "venv",
            ".venv",
            "__pycache__",
            "node_modules",
            ".cross-review",
            "tests",
            "dist",
            "build",
            "coverage",
            "generated",
            ".next",
            "out",
        }
        file_map = {}

        for root, dirs, files in os.walk(self.root_dir):
            # 原地修改 dirs 以跳过忽略目录
            dirs[:] = [
                d
                for d in dirs
                if (
                    d not in ignored_dirs
                    and not d.startswith(".")
                    and not self._is_ignored(
                        os.path.relpath(os.path.join(root, d), self.root_dir).replace(os.sep, "/")
                    )
                )
            ]

            for file in files:
                if not file.endswith((".py", ".sql", ".ts", ".tsx", ".js", ".jsx", ".graphql", ".gql", ".proto")):
                    continue
                
                rel_path = os.path.relpath(os.path.join(root, file), self.root_dir)
                rel_normalized = rel_path.replace(os.sep, "/")
                if self._is_ignored(rel_normalized):
                    continue
                parts = rel_path.split(os.sep)

                module_name = self._infer_module_name(parts, file)

                if module_name not in file_map:
                    file_map[module_name] = []
                file_map[module_name].append(rel_normalized)

        return file_map

    def _targeted_file_map(self, file_map: dict[str, list[str]], target_files: list[str] | None) -> dict[str, list[str]]:
        if not target_files:
            return file_map

        normalized_targets = {
            file_path.replace("\\", "/").strip("/")
            for file_path in target_files
            if file_path and not self._is_ignored(file_path)
        }
        if not normalized_targets:
            return {}

        file_to_module = {
            file_path: module_name
            for module_name, files in file_map.items()
            for file_path in files
        }
        target_modules = {
            file_to_module[file_path]
            for file_path in normalized_targets
            if file_path in file_to_module
        }
        if not target_modules:
            return file_map

        tokens = self._target_tokens(normalized_targets, target_modules)
        selected: dict[str, set[str]] = {module_name: set() for module_name in target_modules}
        for target in normalized_targets:
            module_name = file_to_module.get(target)
            if module_name:
                selected.setdefault(module_name, set()).add(target)

        for module_name in target_modules:
            selected.setdefault(module_name, set()).update(file_map.get(module_name, []))

        for module_name, files in file_map.items():
            if module_name in target_modules:
                continue
            for file_path in files:
                if self._file_matches_tokens(file_path, tokens):
                    selected.setdefault(module_name, set()).add(file_path)

        return {
            module_name: sorted(files)
            for module_name, files in selected.items()
            if files
        }

    def _target_tokens(self, target_files: set[str], target_modules: set[str]) -> set[str]:
        tokens = set(target_modules)
        for file_path in target_files:
            parts = [part for part in file_path.split("/") if part]
            tokens.update(os.path.splitext(part)[0] for part in parts if part and part != "src")
            file_abs = os.path.join(self.root_dir, file_path.replace("/", os.sep))
            try:
                with open(file_abs, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue
            tokens.update(re.findall(r"\b(?:function|class|interface|type|def)\s+([A-Za-z_][A-Za-z0-9_]*)", content))
            tokens.update(re.findall(r"['\"](/[A-Za-z0-9_./:{}-]+)['\"]", content))
        return {token for token in tokens if token and len(token) >= 2}

    def _file_matches_tokens(self, file_path: str, tokens: set[str]) -> bool:
        file_abs = os.path.join(self.root_dir, file_path.replace("/", os.sep))
        try:
            with open(file_abs, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return False
        normalized = content.replace("\\", "/")
        return any(token in normalized for token in tokens)

    def _is_ignored(self, rel_path: str) -> bool:
        normalized = rel_path.replace("\\", "/").strip("/")
        for pattern in self.ignored_paths:
            clean_pattern = pattern.replace("\\", "/").strip("/")
            if fnmatch.fnmatch(normalized, clean_pattern):
                return True
            if clean_pattern.endswith("/**") and normalized == clean_pattern[:-3].rstrip("/"):
                return True
        return False

    def _infer_module_name(self, parts: list[str], filename: str) -> str:
        """
        Infer a useful review module from common Python project layouts.
        Prefer the directory directly under any src segment, support common
        monorepo containers, and avoid collapsing flat files into common.
        """
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

    def _analyze_modules_internals(self, file_map: dict[str, list[str]]):
        """
        读取并解析各模块文件中的 AST 和文本正则，提取契约依赖
        """
        # 建立文件路径到模块名的反向映射
        file_to_module = {}
        for mod_name, files in file_map.items():
            for f in files:
                file_to_module[f] = mod_name

        # 定义数据库表的 SQL 匹配
        db_table_pattern = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z0-9_]+)", re.IGNORECASE)
        event_publishers = {}
        event_listeners = {}

        for mod_name, files in file_map.items():
            module_obj = self.graph.model.modules[mod_name]
            
            for file_rel in files:
                file_abs = os.path.join(self.root_dir, file_rel.replace("/", os.sep))
                if not os.path.exists(file_abs):
                    continue

                # 1. 扫描 SQL 迁移文件提取表名
                if file_rel.endswith(".sql"):
                    try:
                        with open(file_abs, "r", encoding="utf-8") as f:
                            content = f.read()
                        tables = db_table_pattern.findall(content)
                        module_obj.db_tables.extend(tables)
                        module_obj.db_tables = sorted(list(set(module_obj.db_tables)))
                    except Exception:
                        pass
                    continue

                if not file_rel.endswith(".py"):
                    continue

                # 2. AST 解析 Python 文件
                try:
                    with open(file_abs, "r", encoding="utf-8") as f:
                        content = f.read()

                    tree = ast.parse(content, filename=file_abs)
                    
                    # 提取 exports (定义的所有类名和函数名)
                    for node in tree.body:
                        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                            module_obj.exports.append(node.name)

                    # 提取 AST 静态 imports
                    for node in ast.walk(tree):
                        imported_modules = []
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                imported_modules.append(alias.name)
                        elif isinstance(node, ast.ImportFrom):
                            if node.module:
                                imported_modules.append(node.module)

                        # 对比被导入的模块包名是否与我们切分出的本地模块重合，从而画出物理依赖边
                        for imp_mod in imported_modules:
                            # 转换路径分隔符并检查
                            # 如 import src.auth.service 匹配 auth
                            parts = imp_mod.split('.')
                            target_mod = None
                            if len(parts) > 1 and parts[0] == "src":
                                target_mod = parts[1]
                            elif len(parts) > 0:
                                target_mod = parts[0]

                            if target_mod and target_mod in file_map and target_mod != mod_name:
                                self.graph.add_dependency(
                                    from_mod=target_mod,
                                    to_mod=mod_name,
                                    dep_type="static_import",
                                    details=f"{file_rel} imports {imp_mod}",
                                    consumer_files=[file_rel],
                                    provider_files=file_map.get(target_mod, [])
                                )

                    routes, emitted_events, listened_events = self._extract_contracts_from_tree(tree)
                    module_obj.routes.extend(routes)
                    module_obj.routes = sorted(list(set(module_obj.routes)))
                    module_obj.events.extend(emitted_events)
                    module_obj.events = sorted(list(set(module_obj.events)))
                    for event in emitted_events:
                        event_publishers.setdefault(event, []).append((mod_name, file_rel))
                    for event in listened_events:
                        event_listeners.setdefault(event, []).append((mod_name, file_rel))
                except Exception:
                    pass  # AST 报错直接略过

            # 最终去重
            module_obj.exports = sorted(list(set(module_obj.exports)))

        # 4. 运行时事件契约依赖边画图 (provider -> consumer)
        for event, consumers in event_listeners.items():
            for provider_mod, provider_file in event_publishers.get(event, []):
                for consumer_mod, consumer_file in consumers:
                    if provider_mod == consumer_mod:
                        continue
                    self.graph.add_dependency(
                        from_mod=provider_mod,
                        to_mod=consumer_mod,
                        dep_type="event_contract",
                        details=f"{consumer_mod} listens to event '{event}' published by {provider_mod}",
                        consumer_files=[consumer_file],
                        provider_files=[provider_file]
                    )

    def _extract_contracts_from_tree(self, tree: ast.AST) -> tuple[list[str], list[str], list[str]]:
        routes = []
        emitted_events = []
        listened_events = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call_name = self._call_name(node.func)
                first_arg = self._first_string_arg(node)
                if first_arg and call_name in {"emit", "publish", "trigger"}:
                    emitted_events.append(first_arg)
                elif first_arg and call_name in {"on", "subscribe", "listen"}:
                    listened_events.append(first_arg)

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    route = self._route_from_decorator(decorator)
                    if route:
                        routes.append(route)

        return (
            sorted(list(set(routes))),
            sorted(list(set(emitted_events))),
            sorted(list(set(listened_events))),
        )

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

    def _route_from_decorator(self, decorator: ast.AST) -> str | None:
        if not isinstance(decorator, ast.Call):
            return None
        if not isinstance(decorator.func, ast.Attribute):
            return None
        if decorator.func.attr not in {"get", "post", "put", "delete", "patch"}:
            return None
        target = decorator.func.value
        if not isinstance(target, ast.Name) or target.id not in {"app", "router", "blueprint"}:
            return None
        return self._first_string_arg(decorator)
