import os
import re
from cross_review.schemas.models import ProjectGraphModel
from cross_review.diff import GitDiffParser

class ContextPackager:
    def __init__(
        self,
        root_dir: str,
        graph: ProjectGraphModel,
        diff_mode: str = None,
        max_diff_lines: int = 150,
        max_consumer_files: int = 3,
    ):
        self.root_dir = os.path.abspath(root_dir)
        self.graph = graph
        self.diff_mode = diff_mode
        self.max_diff_lines = max_diff_lines
        self.max_consumer_files = max_consumer_files

    def build_module_pack(self, module_name: str, changed_files: list[str], diff_parser: GitDiffParser) -> str:
        """
        汇总该模块内所有被修改文件的 diff 及其自身元数据，拼装紧凑上下文
        """
        if module_name not in self.graph.modules:
            return f"Module '{module_name}' not found in project graph."

        mod_obj = self.graph.modules[module_name]
        pack_lines = [
            f"=== MODULE CONTEXT: {module_name} ===",
            f"Criticality: {mod_obj.criticality}",
            f"Files in Module: {', '.join(mod_obj.files)}",
            f"Public Exports: {', '.join(mod_obj.exports)}",
            f"Declared API Routes: {', '.join(mod_obj.routes)}",
            f"Published Events: {', '.join(mod_obj.events)}",
            "",
            "--- CHANGED FILES DIFFS ---"
        ]

        # 找出该模块下具体被修改的文件
        mod_changed_files = [f for f in changed_files if f in mod_obj.files]
        if not mod_changed_files:
            pack_lines.append("No files modified in this module.")
        else:
            for file in mod_changed_files:
                diff_payload = ""
                if diff_parser:
                    # Point 1: 将 diff_mode 传递到 get_file_diff_payload
                    diff_payload = diff_parser.get_file_diff_payload(file, mode=self.diff_mode)
                else:
                    # Fallback: 读取物理文件全文作为审查上下文
                    file_abs = os.path.join(self.root_dir, file.replace("/", os.sep))
                    if os.path.exists(file_abs):
                        try:
                            with open(file_abs, "r", encoding="utf-8") as f:
                                diff_payload = f.read()
                        except Exception:
                            pass

                # 使用折叠算法防止 diff/文件全文过长
                folded_diff = self._fold_text(diff_payload, max_lines=self.max_diff_lines)
                pack_lines.append(f"File: {file}")
                if diff_parser:
                    pack_lines.append("```diff")
                else:
                    pack_lines.append("```python")
                pack_lines.append(folded_diff)
                pack_lines.append("```")
                pack_lines.append("")

        return "\n".join(pack_lines)

    def build_cross_pack(self, from_mod: str, to_mod: str, changed_files: list[str], diff_parser: GitDiffParser) -> str:
        """
        提取 from_mod 的 diff 和公共协议，以及 downstream (to_mod) 真实的消费/连接代码文件。
        """
        if from_mod not in self.graph.modules or to_mod not in self.graph.modules:
            return f"Error: '{from_mod}' or '{to_mod}' not found in project graph."

        from_obj = self.graph.modules[from_mod]
        to_obj = self.graph.modules[to_mod]

        # 1. 组装 From 模块变更情况
        from_changed = [f for f in changed_files if f in from_obj.files]
        from_diffs = []
        for file in from_changed:
            diff_payload = ""
            if diff_parser:
                # Point 1: 将 diff_mode 传递到 get_file_diff_payload
                diff_payload = diff_parser.get_file_diff_payload(file, mode=self.diff_mode)
            else:
                file_abs = os.path.join(self.root_dir, file.replace("/", os.sep))
                if os.path.exists(file_abs):
                    try:
                        with open(file_abs, "r", encoding="utf-8") as f:
                            diff_payload = f.read()
                    except Exception:
                        pass
            
            if diff_parser:
                from_diffs.append(f"File: {file}\n```diff\n{self._fold_text(diff_payload, self.max_diff_lines)}\n```")
            else:
                from_diffs.append(f"File: {file}\n```python\n{self._fold_text(diff_payload, self.max_diff_lines)}\n```")

        from_diff_content = "\n\n".join(from_diffs) if from_diffs else "No modified files in from_module."

        # 2. 查找 downstream 模块 (to_mod) 真实与 from_mod 交互的消费源文件
        consumer_files = []
        symbol_edges = []
        for dep in self.graph.dependencies:
            if dep.from_module == from_mod and dep.to_module == to_mod:
                consumer_files.extend(dep.consumer_files)
                symbol_edges.extend(dep.symbol_edges)
            elif dep.from_module == to_mod and dep.to_module == from_mod:
                match = re.search(r"(\S+\.py)\s+imports", dep.details)
                if match:
                    consumer_files.append(match.group(1))

        if not consumer_files:
            for file in to_obj.files:
                file_abs = os.path.join(self.root_dir, file.replace("/", os.sep))
                if os.path.exists(file_abs) and not file.endswith(".sql"):
                    try:
                        with open(file_abs, "r", encoding="utf-8") as f:
                            content = f.read()
                        if from_mod in content or any(e in content for e in from_obj.events):
                            consumer_files.append(file)
                    except Exception:
                        pass

        consumer_files = sorted(list(set(consumer_files)))[: self.max_consumer_files]

        consumer_code_blocks = []
        for file in consumer_files:
            file_abs = os.path.join(self.root_dir, file.replace("/", os.sep))
            if os.path.exists(file_abs):
                try:
                    with open(file_abs, "r", encoding="utf-8") as f:
                        content = f.read()
                    folded_code = self._fold_source_code(content, [from_mod] + from_obj.events + from_obj.exports)
                    consumer_code_blocks.append(f"File: {file}\n```python\n{folded_code}\n```")
                except Exception as e:
                    consumer_code_blocks.append(f"File: {file} (Failed to load: {e})")

        consumer_content = "\n\n".join(consumer_code_blocks) if consumer_code_blocks else "No direct consumer files detected in to_module."
        symbol_evidence = self._render_symbol_edges(symbol_edges)

        pack_lines = [
            f"=== CROSS-REVIEW CONTEXT: {from_mod} -> {to_mod} ===",
            f"Edge Type: {self._determine_edge_type(from_mod, to_mod)}",
            "",
            "--- 1. CHANGES IN UPSTREAM MODULE (FROM_MODULE) ---",
            from_diff_content,
            "",
            f"--- 2. INTERFACE / CONTRACT CONTRACTS OF {from_mod} ---",
            f"Public Exports: {', '.join(from_obj.exports)}",
            f"API Routes: {', '.join(from_obj.routes)}",
            f"Published Events: {', '.join(from_obj.events)}",
            "",
            "--- 3. CONSUMER CODE IN DOWNSTREAM MODULE (TO_MODULE) ---",
            consumer_content,
            "",
            "--- 4. CODEGRAPH SYMBOL-LEVEL CALLER EVIDENCE ---",
            symbol_evidence,
        ]

        return "\n".join(pack_lines)

    def _determine_edge_type(self, from_mod: str, to_mod: str) -> str:
        for dep in self.graph.dependencies:
            if (dep.from_module == from_mod and dep.to_module == to_mod) or \
               (dep.from_module == to_mod and dep.to_module == from_mod):
                if dep.type in ["api_call", "event_contract", "db_shared"]:
                    return dep.type
        return "static_import"

    def _render_symbol_edges(self, symbol_edges: list[dict]) -> str:
        if not symbol_edges:
            return "No CodeGraph symbol-level caller evidence was provided for this boundary."

        lines = []
        for edge in symbol_edges[:10]:
            symbol = edge.get("qualified_name") or edge.get("symbol") or "unknown_symbol"
            kind = edge.get("kind") or "symbol"
            provider = self._format_file_line(edge.get("provider_file"), edge.get("provider_line"))
            consumer = self._format_file_line(edge.get("consumer_file"), edge.get("caller_line"))
            caller = edge.get("caller") or "unknown caller"
            match_source = edge.get("match_source") or "codegraph"
            lines.append(
                f"- {symbol} ({kind}) provider {provider} -> consumer {consumer} via {caller}; source={match_source}"
            )
        if len(symbol_edges) > 10:
            lines.append(f"- ... {len(symbol_edges) - 10} additional symbol edge(s) omitted from context.")
        return "\n".join(lines)

    def _format_file_line(self, file_path: str | None, line: int | str | None) -> str:
        if not file_path:
            return "unknown file"
        if isinstance(line, int) and line > 0:
            return f"{file_path}:{line}"
        if isinstance(line, str) and line.isdigit():
            return f"{file_path}:{line}"
        return file_path

    def _fold_text(self, text: str, max_lines: int = 150) -> str:
        """
        折叠长文本的简单行数截断与滑动窗口提示
        """
        lines = text.splitlines()
        if len(lines) <= max_lines:
            return text
        
        half = max_lines // 2
        folded = lines[:half] + [f"\n... [{len(lines) - max_lines} lines folded dynamically] ...\n"] + lines[-half:]
        return "\n".join(folded)

    def _fold_source_code(self, code: str, keywords: list[str]) -> str:
        """
        智能折叠消费者源文件。
        保留头部 import 区，以及所有包含关键词的函数体，折叠其他不相干函数。
        """
        lines = code.splitlines()
        if len(lines) < 120:  # 小文件无需折叠
            return code

        # 筛选核心保留行
        keep_lines = set()
        
        # 1. 始终保留头部 30 行
        for i in range(min(30, len(lines))):
            keep_lines.add(i)

        # 2. 匹配包含关键字的行，并保留该行前 15 行及后 15 行 (滑动关联窗口)
        for i, line in enumerate(lines):
            if any(kw in line for kw in keywords if kw):
                for offset in range(-15, 16):
                    idx = i + offset
                    if 0 <= idx < len(lines):
                        keep_lines.add(idx)

        # 3. 始终保留尾部 10 行
        for i in range(max(0, len(lines)-10), len(lines)):
            keep_lines.add(i)

        # 4. 组装折叠文本
        folded_lines = []
        in_fold = False
        
        for i, line in enumerate(lines):
            if i in keep_lines:
                if in_fold:
                    folded_lines.append("    # ... [unrelated function body folded to preserve context tokens] ...")
                    in_fold = False
                folded_lines.append(line)
            else:
                in_fold = True

        if in_fold:
            folded_lines.append("    # ... [unrelated code folded] ...")

        return "\n".join(folded_lines)
