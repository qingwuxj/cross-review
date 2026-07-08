import os
import re
from cross_review.schemas.models import ProjectGraphModel, EdgeModel, DependencyModel
from cross_review.diff import GitDiffParser

class ImpactScorer:
    def __init__(
        self,
        graph: ProjectGraphModel,
        changed_files: list[str],
        repo_path: str = ".",
        diff_mode: str = None,
        low_value_modules: list[str] | None = None,
    ):
        self.graph = graph
        self.changed_files = changed_files
        self.repo_path = repo_path
        self.diff_mode = diff_mode
        self.low_value_modules = {module.lower() for module in (low_value_modules or [])}
        self.omitted_low_risk_edges = []
        self.diff_parser = None
        try:
            self.diff_parser = GitDiffParser(repo_path)
        except Exception:
            pass  # 如果没有有效的 Git 仓库，回退到降级策略

    def get_changed_modules(self) -> list[str]:
        """
        找出发生变更的文件所属的模块列表
        """
        changed_mods = []
        for file in self.changed_files:
            # 查找文件所属的模块
            found = False
            for mod_name, mod_obj in self.graph.modules.items():
                if file in mod_obj.files:
                    changed_mods.append(mod_name)
                    found = True
                    break
            if not found:
                # 兜底：如果是 common 或根目录文件，归为 common 模块
                changed_mods.append("common")
        return sorted(list(set(changed_mods)))

    def calculate_scores(self, K: int = 3, expand_critical_top_k: bool = True) -> list[EdgeModel]:
        """
        计算所有受变动影响的模块对(A -> B)的风险得分，并过滤 Top-K 高风险边。
        """
        changed_modules = self.get_changed_modules()
        all_modules = list(self.graph.modules.keys())
        self.omitted_low_risk_edges = []
        
        edges = []

        # A 是发生变更的模块，B 是系统中可能被波及的相邻模块
        for from_mod in changed_modules:
            if from_mod not in self.graph.modules:
                continue

            for to_mod in all_modules:
                if from_mod == to_mod:
                    continue

                risk_score, reasons, force_triggered = self._evaluate_edge(from_mod, to_mod)
                
                # 低分边过滤阈值 (0.4)
                if risk_score >= 0.4 or force_triggered:
                    # 如果发生变更的模块本身是高危资金或安全模块，动态提升其 K 额度或置信度
                    mod_obj = self.graph.modules[from_mod]
                    if expand_critical_top_k and mod_obj.criticality == "critical" and K < 5:
                        K = 5
                    
                    edges.append(
                        EdgeModel(
                            from_module=from_mod,
                            to_module=to_mod,
                            edge_type=self._determine_edge_type(from_mod, to_mod),
                            risk_score=risk_score,
                            force_triggered=force_triggered,
                            reasons=reasons,
                            symbol_edges=self._symbol_edges_for_edge(from_mod, to_mod),
                        )
                    )
                elif any(reason.startswith("Deprioritized") for reason in reasons):
                    self.omitted_low_risk_edges.append({
                        "from_module": from_mod,
                        "to_module": to_mod,
                        "edge_type": self._determine_edge_type(from_mod, to_mod),
                        "risk_score": risk_score,
                        "reason": self._omission_reason(reasons),
                    })

        # 按 Risk Score 降序排序，如果是 Force-Triggered 则优先级最高
        edges = sorted(edges, key=lambda e: (e.force_triggered, e.risk_score), reverse=True)
        
        # 裁剪保留 Top-K 边
        return edges[:K]

    def _evaluate_edge(self, from_mod: str, to_mod: str) -> tuple[float, list[str], bool]:
        """
        计算 A -> B 的风险数值，并检测是否触发 Force-Trigger。
        """
        reasons = []
        force_triggered = False

        # --- 1. 强制触发 (Force-Trigger) 检测 ---
        # 规则 1.1：权限边界安全触发 (Permission Boundary)
        if from_mod in ["auth", "security"] or to_mod in ["auth", "security"]:
            force_triggered = True
            reasons.append("🔐 Force-Trigger: Permission Boundary module modified.")

        # 规则 1.2：资金核心交易触发 (Billing/Payment)
        if from_mod in ["billing", "payment"] or to_mod in ["billing", "payment"]:
            force_triggered = True
            reasons.append("💳 Force-Trigger: Critical Billing/Payment module modified.")

        # 规则 1.3：数据库迁移触发 (DB Migration)
        has_db_migration = any(f.endswith(".sql") or "migrations/" in f for f in self.changed_files)
        if has_db_migration and (from_mod in ["db", "database"] or to_mod in ["db", "database"]):
            if self._has_dependency_type(from_mod, to_mod, {"db_shared"}):
                force_triggered = True
                reasons.append("🗄️ Force-Trigger: DB Migration file modified.")
            else:
                reasons.append("DB migration file modified; no downstream DB call-site evidence on this edge.")

        has_changed_provider_file = any(f in self.graph.modules[from_mod].files for f in self.changed_files)
        if has_changed_provider_file:
            for dep in self.graph.dependencies:
                if (
                    dep.from_module == from_mod
                    and dep.to_module == to_mod
                    and dep.type in ["api_call", "event_contract", "db_shared"]
                ):
                    force_triggered = True
                    reasons.append(f"Force-Trigger: Runtime contract boundary modified ({dep.type}).")
                    break

        # --- 2. 五大维度打分算法 ---
        # (1) 静态依赖得分 S
        S = 0.0
        for dep in self.graph.dependencies:
            if (dep.from_module == from_mod and dep.to_module == to_mod) or \
               (dep.from_module == to_mod and dep.to_module == from_mod):
                if dep.type == "static_import":
                    S = 1.0
                    reasons.append(f"Static dependency found: {dep.details}")
                    break

        # (2) 契约变更得分 C
        C = 0.0
        if self.diff_parser:
            for file in self.changed_files:
                # 检查此文件是否属于 from_mod
                if file in self.graph.modules[from_mod].files:
                    diff_payload = self.diff_parser.get_file_diff_payload(file, mode=self.diff_mode)
                    # 匹配是否有新增/删除函数签名或类定义
                    if re.search(r"^\+(?:def |class )", diff_payload, re.MULTILINE):
                        C = 1.0
                        reasons.append(f"Contract changes detected: Function/Class definition added in {file}.")
                        break

        # (3) Git 协同修改得分 G (历史上 A 和 B 一起改的概率)
        G = 0.0
        if self.diff_parser:
            try:
                G = self._calculate_git_cochange_rate(from_mod, to_mod)
                if G > 0.0:
                    reasons.append(f"Historical git co-change rate: {G:.2%}")
            except Exception:
                # 降级：如果历史上都有静态依赖，则判定有少量协同修改概率
                G = 0.3 if S > 0.0 else 0.0

        # (4) 运行时耦合得分 R
        R = 0.0
        for dep in self.graph.dependencies:
            if dep.from_module == from_mod and dep.to_module == to_mod:
                if dep.type in ["event_contract", "api_call", "db_shared"]:
                    R = 1.0
                    reasons.append(f"Runtime contract connection: {dep.details}")
                    break

        # (5) 测试缺口得分 T (寻找是否有 B 与 A 的集成测试)
        test_path = self._find_cross_module_test(from_mod, to_mod)
        T = 0.0 if test_path else 1.0
        if test_path:
            reasons.append(f"Integration test covers both: {test_path}")
        
        if T == 1.0:
            reasons.append(f"Test Gap: No cross-module integration test found between {from_mod} and {to_mod}.")

        # 评分公式加权
        risk_score = 0.25 * S + 0.25 * C + 0.20 * G + 0.15 * R + 0.15 * T

        if force_triggered:
            risk_score = 1.0
        elif has_db_migration and self._is_static_only_db_edge(from_mod, to_mod):
            risk_score *= 0.35
            reasons.append("Deprioritized static-only DB migration edge without downstream DB call-site evidence.")
        elif self._is_low_value_static_edge(from_mod, to_mod):
            risk_score *= 0.35
            reasons.append("Deprioritized low-value static edge between tooling/aggregation modules.")

        return round(risk_score, 4), reasons, force_triggered

    def _omission_reason(self, reasons: list[str]) -> str:
        for reason in reasons:
            if reason.startswith("Deprioritized static-only DB migration"):
                return "static_only_db_migration_edge"
            if reason.startswith("Deprioritized low-value"):
                return "low_value_static_edge"
        return "deprioritized_edge"

    def _has_dependency_type(self, from_mod: str, to_mod: str, dep_types: set[str]) -> bool:
        return any(
            dep.from_module == from_mod
            and dep.to_module == to_mod
            and dep.type in dep_types
            for dep in self.graph.dependencies
        )

    def _is_static_only_db_edge(self, from_mod: str, to_mod: str) -> bool:
        if from_mod not in {"db", "database"} and to_mod not in {"db", "database"}:
            return False
        related_deps = [
            dep
            for dep in self.graph.dependencies
            if (
                (dep.from_module == from_mod and dep.to_module == to_mod)
                or (dep.from_module == to_mod and dep.to_module == from_mod)
            )
        ]
        if not related_deps:
            return True
        return all(dep.type == "static_import" for dep in related_deps)

    def _is_low_value_static_edge(self, from_mod: str, to_mod: str) -> bool:
        if not self.low_value_modules:
            return False
        if from_mod.lower() not in self.low_value_modules and to_mod.lower() not in self.low_value_modules:
            return False
        related_deps = [
            dep
            for dep in self.graph.dependencies
            if (
                (dep.from_module == from_mod and dep.to_module == to_mod)
                or (dep.from_module == to_mod and dep.to_module == from_mod)
            )
        ]
        if not related_deps:
            return False
        return all(dep.type == "static_import" for dep in related_deps)

    def _calculate_git_cochange_rate(self, from_mod: str, to_mod: str, max_count: int = 100) -> float:
        repo = self.diff_parser.repo
        raw_log = repo.git.log(f"-n{max_count}", "--name-only", "--pretty=format:commit %H")
        commits = self._parse_name_only_log(raw_log)
        both_count = 0
        from_count = 0

        for changed_paths in commits:
            has_from = any(f in self.graph.modules[from_mod].files for f in changed_paths)
            has_to = any(f in self.graph.modules[to_mod].files for f in changed_paths)
            if has_from:
                from_count += 1
                if has_to:
                    both_count += 1

        if from_count == 0:
            return 0.0
        return float(both_count) / float(from_count)

    def _parse_name_only_log(self, raw_log: str) -> list[list[str]]:
        commits = []
        current = []

        for raw_line in raw_log.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("commit "):
                if current:
                    commits.append(current)
                    current = []
                continue
            current.append(line.replace("\\", "/"))

        if current:
            commits.append(current)

        return commits

    def _find_cross_module_test(self, from_mod: str, to_mod: str) -> str | None:
        test_candidates = []

        for mod_obj in self.graph.modules.values():
            test_candidates.extend(f for f in mod_obj.files if self._looks_like_test_path(f))

        for dirname in ["tests", "test", "specs", "__tests__"]:
            test_root = os.path.join(self.repo_path, dirname)
            if not os.path.isdir(test_root):
                continue
            for root, dirs, files in os.walk(test_root):
                dirs[:] = [d for d in dirs if d not in {"__pycache__", "node_modules", ".git"}]
                for filename in files:
                    rel_path = os.path.relpath(os.path.join(root, filename), self.repo_path).replace(os.sep, "/")
                    if self._looks_like_test_path(rel_path):
                        test_candidates.append(rel_path)

        from_key = from_mod.lower()
        to_key = to_mod.lower()
        for path in sorted(set(test_candidates)):
            lower_path = path.lower()
            if from_key in lower_path and to_key in lower_path:
                return path

            abs_path = os.path.join(self.repo_path, path.replace("/", os.sep))
            if not os.path.isfile(abs_path):
                continue
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    content = f.read(200_000).lower()
                if from_key in content and to_key in content:
                    return path
            except Exception:
                continue

        return None

    def _looks_like_test_path(self, path: str) -> bool:
        normalized = path.replace("\\", "/").lower()
        filename = os.path.basename(normalized)
        return (
            filename.startswith("test_")
            or filename.endswith("_test.py")
            or filename.endswith(".test.py")
            or filename.endswith(".spec.py")
            or "/tests/" in f"/{normalized}"
            or "/__tests__/" in f"/{normalized}"
        )

    def _determine_edge_type(self, from_mod: str, to_mod: str) -> str:
        """
        判断两条模块关联边的主导类型 (API, Event, DB, Static)
        """
        for dep in self.graph.dependencies:
            if (dep.from_module == from_mod and dep.to_module == to_mod) or \
               (dep.from_module == to_mod and dep.to_module == from_mod):
                if dep.type in ["api_call", "event_contract", "db_shared"]:
                    return dep.type
        return "static_import"

    def _symbol_edges_for_edge(self, from_mod: str, to_mod: str) -> list[dict]:
        symbol_edges = []
        for dep in self.graph.dependencies:
            if dep.from_module == from_mod and dep.to_module == to_mod:
                symbol_edges.extend(dep.symbol_edges)
        return symbol_edges
