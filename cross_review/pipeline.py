import os
import json
import re
import fnmatch
import time
from cross_review.config import load_config
from cross_review.contracts.contract_graph import ContractGraphBuilder
from cross_review.external_graph import load_external_project_graph
from cross_review.scout import ScoutScanner
from cross_review.diff import GitDiffParser
from cross_review.impact_scorer import ImpactScorer
from cross_review.integrations.codegraph import CodeGraphIntegration
from cross_review.context_pack import ContextPackager
from cross_review.llm import LLMClient
from cross_review.graph import ProjectGraph
from cross_review.schemas.models import (
    ProjectGraphModel, 
    ModuleReviewModel, 
    CrossReviewModel, 
    FinalReportModel
)

class ReviewPipeline:
    def __init__(self, root_dir: str = ".", cache_dir: str = ".cross-review", config_path: str = None):
        self.root_dir = os.path.abspath(root_dir)
        self.cache_dir = os.path.join(self.root_dir, cache_dir)
        os.makedirs(self.cache_dir, exist_ok=True)
        self.config = load_config(self.root_dir, config_path=config_path)
        self.llm = None

    def run(self, base_branch: str = "main", head_branch: str = "HEAD", manual_files: list[str] = None, diff_mode: str = None) -> str:
        """
        运行完整的 Cross-Review 审查管道
        """
        llm = self._get_llm()

        # Step 1: Scout Project
        print("[1/9] Scouting project structure and dependencies...")
        scanner = ScoutScanner(self.root_dir, ignored_paths=self.config.ignored_paths)
        graph = scanner.scan()
        scanned_file_count = sum(len(module.files) for module in graph.model.modules.values())
        graph_path = os.path.join(self.cache_dir, "project_graph.json")
        graph.save_to_file(graph_path)
        print(f"      Saved project graph to {graph_path}")

        # Step 2: Detect Changed Files
        print("[2/9] Detecting changed files...")
        changed_files = self._detect_changed_files(base_branch, head_branch, manual_files, diff_mode)

        changed_path = os.path.join(self.cache_dir, "changed_files.json")
        with open(changed_path, "w", encoding="utf-8") as f:
            json.dump({"changed_files": changed_files}, f, indent=2)
        print(f"      Changed files: {changed_files}")

        # Step 3: Score Impact Edges
        print("[3/9] Building impact graph and scoring edges...")
        scorer = ImpactScorer(
            graph.model,
            changed_files,
            self.root_dir,
            diff_mode=diff_mode,
            low_value_modules=self.config.review.low_value_modules,
        )
        top_edges = scorer.calculate_scores(
            K=self.config.review.top_k,
            expand_critical_top_k=self.config.review.expand_critical_top_k,
        )
        
        edges_path = os.path.join(self.cache_dir, "impact_edges.json")
        with open(edges_path, "w", encoding="utf-8") as f:
            f.write(json.dumps([e.model_dump() for e in top_edges], indent=2))
        print(f"      Top-K Impact Edges selected: {len(top_edges)}")
        for idx, edge in enumerate(top_edges):
            print(f"      - Edge {idx+1}: {edge.from_module} -> {edge.to_module} (Score: {edge.risk_score})")

        # Step 4: Build Context Packs
        print("[4/9] Packaging contexts for review...")
        packager = ContextPackager(
            self.root_dir,
            graph.model,
            diff_mode=diff_mode,
            max_diff_lines=self.config.context.max_diff_lines,
            max_consumer_files=self.config.context.max_consumer_files,
        )
        diff_parser = GitDiffParser(self.root_dir) if scorer.diff_parser else None

        # Step 5: Module Review execution loop
        print("[5/9] Running local Module Review Agents...")
        changed_modules = scorer.get_changed_modules()
        module_reviews = []

        # 载入 Module Review Prompt 模版
        mr_prompt_template = self._load_prompt("module_review.txt")

        for mod_name in changed_modules:
            if mod_name not in graph.model.modules:
                continue
            print(f"      Auditing module: {mod_name}...")
            context_pack = packager.build_module_pack(mod_name, changed_files, diff_parser)
            
            prompt = (
                mr_prompt_template
                .replace("[MODULE_NAME]", mod_name)
                .replace("[DIFF_CONTENT]", context_pack)
            )

            # 调用大模型执行主审
            review_obj = llm.call_json(
                prompt=prompt,
                schema=ModuleReviewModel,
                system_instruction="You are a strict, facts-only static audit agent."
            )
            module_reviews.append(review_obj)

        reviews_path = os.path.join(self.cache_dir, "module_reviews.json")
        with open(reviews_path, "w", encoding="utf-8") as f:
            f.write(json.dumps([r.model_dump() for r in module_reviews], indent=2))

        # Step 6: Cross-Review execution loop
        print("[6/9] Running Cross-Review Agents for high-risk edges...")
        cross_reviews = []
        cr_prompt_template = self._load_prompt("cross_review.txt")

        for edge in top_edges:
            print(f"      Cross-Reviewing boundary: {edge.from_module} -> {edge.to_module}...")
            # 建立交叉包
            cross_pack = packager.build_cross_pack(edge.from_module, edge.to_module, changed_files, diff_parser)
            
            # P0-3: 修复占位符替换
            prompt = (
                cr_prompt_template
                .replace("[FROM_MODULE]", edge.from_module)
                .replace("[TO_MODULE]", edge.to_module)
                .replace("[EDGE_TYPE]", edge.edge_type)
                .replace("[RISK_SCORE]", str(edge.risk_score))
                .replace("[FROM_MODULE_DIFF]", cross_pack)
            )

            cross_obj = llm.call_json(
                prompt=prompt,
                schema=CrossReviewModel,
                system_instruction=f"Determine strictly if changes in {edge.from_module} will break {edge.to_module}."
            )
            # 补齐分值
            cross_obj.risk_score = edge.risk_score
            cross_reviews.append(cross_obj)

        cross_path = os.path.join(self.cache_dir, "cross_reviews.json")
        with open(cross_path, "w", encoding="utf-8") as f:
            f.write(json.dumps([c.model_dump() for c in cross_reviews], indent=2))

        # Step 7: Arbiter & Deduplication
        print("[7/9] Merging and Synthesizing findings via Architect Arbiter...")
        arb_prompt_template = self._load_prompt("arbiter.txt")

        # 汇聚所有子代理返回结果
        all_reviews_data = {
            "module_reviews": [r.model_dump() for r in module_reviews],
            "cross_reviews": [c.model_dump() for c in cross_reviews]
        }

        arbiter_prompt = (
            arb_prompt_template
            .replace("[ALL_INPUT_JSONS]", json.dumps(all_reviews_data, indent=2))
        )

        final_report = llm.call_json(
            prompt=arbiter_prompt,
            schema=FinalReportModel,
            system_instruction="Filter duplicate findings and prioritize integration risks using only provided evidence."
        )

        # 标记是否为 Mock 模式
        if not llm.gemini_key and not llm.openai_key:
            final_report.is_mock = True

        # Step 8: Save Final structured report
        print("[8/9] Persisting final report...")
        report_json_path = os.path.join(self.cache_dir, "final_report.json")
        with open(report_json_path, "w", encoding="utf-8") as f:
            f.write(final_report.model_dump_json(indent=2))
        print(f"      Saved structured JSON report to {report_json_path}")

        # Step 9: Render markdown report
        print("[9/9] Generating final report artifacts...")
        markdown_content = self.render_markdown(final_report, top_edges)
        report_md_path = os.path.join(self.cache_dir, "final_report.md")
        with open(report_md_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)
        print(f"      Saved Markdown report to {report_md_path}")
        print("\nCross-Review completed successfully.")

        return report_json_path

    def prepare(self, base_branch: str = "main", head_branch: str = "HEAD", manual_files: list[str] = None, diff_mode: str = None, lite: bool = False) -> str:
        """
        Prepare deterministic review context for a host Agent. This mode never
        calls external LLM APIs and does not require user-managed API keys.
        """
        prepare_start = time.perf_counter()
        diagnostics_path = os.path.join(self.cache_dir, "prepare_diagnostics.json")
        timings = {
            "scan_files_ms": 0,
            "scout_analyze_ms": 0,
            "detect_changed_files_ms": 0,
            "codegraph_ms": 0,
            "contract_graph_ms": 0,
            "impact_score_ms": 0,
            "context_pack_ms": 0,
            "write_pack_ms": 0,
            "total_prepare_ms": 0,
        }
        scanner = None
        graph = None
        scanned_file_count = 0
        source_file_count = 0
        analysis_profile = "unknown"
        completed_stages: list[str] = []
        current_stage = "starting"
        current_stage_start = prepare_start
        codegraph_context = None
        codegraph_context_path = os.path.join(self.cache_dir, "codegraph_context.json")
        stage_timing_keys = {
            "detect_changed_files": "detect_changed_files_ms",
            "codegraph": "codegraph_ms",
            "contract_graph": "contract_graph_ms",
            "impact_score": "impact_score_ms",
            "context_pack": "context_pack_ms",
            "write_pack": "write_pack_ms",
        }

        def write_live_diagnostics(status: str, failed_stage: str | None = None, error: Exception | None = None) -> dict:
            timings["total_prepare_ms"] = self._elapsed_ms(prepare_start)
            diagnostics = self._build_prepare_diagnostics(
                scanner,
                graph.model if graph is not None else None,
                scanned_file_count,
                analysis_profile,
                timings,
                status=status,
                current_stage=current_stage,
                completed_stages=completed_stages,
                failed_stage=failed_stage,
                error=error,
                diagnostics_path=diagnostics_path,
            )
            self._write_prepare_diagnostics_file(diagnostics_path, diagnostics)
            return diagnostics

        try:
            print("[1/6] Detecting changed files...")
            current_stage = "detect_changed_files"
            current_stage_start = time.perf_counter()
            write_live_diagnostics("running")
            changed_files = self._detect_changed_files(base_branch, head_branch, manual_files, diff_mode)
            changed_path = os.path.join(self.cache_dir, "changed_files.json")
            with open(changed_path, "w", encoding="utf-8") as f:
                json.dump({"changed_files": changed_files}, f, indent=2)
            timings["detect_changed_files_ms"] = self._elapsed_ms(current_stage_start)
            completed_stages.append(current_stage)
            write_live_diagnostics("running")
            print(f"      Changed files: {changed_files}")

            print("[2/6] Checking optional CodeGraph integration...")
            current_stage = "codegraph"
            current_stage_start = time.perf_counter()
            write_live_diagnostics("running")
            codegraph_context = self._collect_codegraph_context(changed_files)
            with open(codegraph_context_path, "w", encoding="utf-8") as f:
                json.dump(codegraph_context, f, indent=2, ensure_ascii=False)
            timings["codegraph_ms"] = self._elapsed_ms(current_stage_start)
            completed_stages.append(current_stage)
            write_live_diagnostics("running")
            if codegraph_context.get("enabled"):
                print(f"      CodeGraph context saved to {codegraph_context_path}")
            else:
                print(f"      CodeGraph skipped: {codegraph_context.get('reason')}")

            print("[3/6] Scouting project structure and dependencies...")
            current_stage = "scan_project"
            current_stage_start = time.perf_counter()
            scanner = ScoutScanner(self.root_dir, ignored_paths=self.config.ignored_paths)
            write_live_diagnostics("running")
            if self.config.project_graph.external_graph_path:
                graph = load_external_project_graph(self.root_dir, self.config.project_graph.external_graph_path)
                self._set_external_graph_diagnostics(scanner, graph)
            else:
                scout_cache_key = self._build_scout_cache_key(scanner, changed_files)
                graph = self._load_scout_cache(scout_cache_key, scanner)
                if graph is None:
                    graph = scanner.scan(
                        target_files=changed_files,
                        targeted_file_threshold=self.config.review.targeted_scan_file_threshold,
                    )
                    self._write_scout_cache(scout_cache_key, graph, scanner)
            scout_elapsed_ms = self._elapsed_ms(current_stage_start)
            scout_diagnostics = getattr(scanner, "diagnostics", {}) or {}
            timings["scan_files_ms"] = int(scout_diagnostics.get("scan_files_ms", scout_elapsed_ms))
            timings["scout_analyze_ms"] = int(scout_diagnostics.get("scout_analyze_ms", 0))
            scanned_file_count = sum(len(module.files) for module in graph.model.modules.values())
            source_file_count = int(scout_diagnostics.get("source_file_count") or scanned_file_count)
            graph_path = os.path.join(self.cache_dir, "project_graph.json")
            graph.save_to_file(graph_path)
            completed_stages.append(current_stage)
            write_live_diagnostics("running")
            print(f"      Saved project graph to {graph_path}")

            auto_lite = self._should_auto_lite(source_file_count, explicit_lite=lite)
            effective_lite = lite or self.config.review.lite or auto_lite
            analysis_profile = "lite" if lite or self.config.review.lite else "auto-lite" if auto_lite else "full"
            if auto_lite:
                print(
                    "      Auto-lite enabled: "
                    f"{source_file_count} supported files exceeds threshold "
                    f"{self.config.review.auto_lite_file_threshold}."
                )

            current_stage = "contract_graph"
            current_stage_start = time.perf_counter()
            write_live_diagnostics("running")
            if effective_lite:
                contract_graph = self._empty_contract_graph()
            else:
                previous_source_provider = self._build_previous_source_provider(base_branch, diff_mode)
                contract_graph = ContractGraphBuilder(
                    self.root_dir,
                    graph.model,
                    previous_source_provider=previous_source_provider,
                    enabled_analyzers=self.config.review.enabled_analyzers,
                    path_aliases=self.config.path_aliases,
                ).build(changed_files)
                self._add_contract_graph_dependencies(graph, contract_graph)
                graph.save_to_file(graph_path)
            timings["contract_graph_ms"] = self._elapsed_ms(current_stage_start)
            completed_stages.append(current_stage)
            write_live_diagnostics("running")

            print("[4/6] Scoring impact edges...")
            current_stage = "impact_score"
            current_stage_start = time.perf_counter()
            write_live_diagnostics("running")
            scorer = ImpactScorer(
                graph.model,
                changed_files,
                self.root_dir,
                diff_mode=diff_mode,
                low_value_modules=self.config.review.low_value_modules,
            )
            top_edges = scorer.calculate_scores(
                K=self.config.review.top_k,
                expand_critical_top_k=self.config.review.expand_critical_top_k,
            )
            self._annotate_edges_with_contract_evidence(top_edges, contract_graph)
            edges_path = os.path.join(self.cache_dir, "impact_edges.json")
            with open(edges_path, "w", encoding="utf-8") as f:
                f.write(json.dumps([e.model_dump() for e in top_edges], indent=2))
            timings["impact_score_ms"] = self._elapsed_ms(current_stage_start)
            completed_stages.append(current_stage)
            write_live_diagnostics("running")
            print(f"      Top-K Impact Edges selected: {len(top_edges)}")

            print("[5/6] Building agent review contexts...")
            current_stage = "context_pack"
            current_stage_start = time.perf_counter()
            write_live_diagnostics("running")
            packager = ContextPackager(
                self.root_dir,
                graph.model,
                diff_mode=diff_mode,
                max_diff_lines=self.config.context.max_diff_lines,
                max_consumer_files=self.config.context.max_consumer_files,
            )
            diff_parser = GitDiffParser(self.root_dir) if scorer.diff_parser else None
            changed_modules = scorer.get_changed_modules()

            module_contexts = []
            for mod_name in changed_modules:
                if mod_name not in graph.model.modules:
                    continue
                module_contexts.append({
                    "module_name": mod_name,
                    "prompt_template": "module_review.txt",
                    "context": packager.build_module_pack(mod_name, changed_files, diff_parser),
                })

            cross_review_contexts = []
            for edge in top_edges:
                edge_changed_contracts = [
                    changed.model_dump()
                    for changed in contract_graph.changed_contracts
                    if changed.contract_id in edge.changed_contract_ids
                ]
                edge_call_sites = [
                    callsite.model_dump()
                    for callsite in contract_graph.call_sites
                    if callsite.callsite_id in edge.callsite_ids
                ]
                base_context = packager.build_cross_pack(edge.from_module, edge.to_module, changed_files, diff_parser)
                evidence_summary = self._render_contract_evidence_summary(edge_changed_contracts, edge_call_sites)
                context = f"{base_context}\n\n{evidence_summary}" if evidence_summary else base_context
                cross_review_contexts.append({
                    "from_module": edge.from_module,
                    "to_module": edge.to_module,
                    "edge_type": edge.edge_type,
                    "risk_score": edge.risk_score,
                    "force_triggered": edge.force_triggered,
                    "reasons": edge.reasons,
                    "changed_contract_ids": edge.changed_contract_ids,
                    "callsite_ids": edge.callsite_ids,
                    "symbol_edges": edge.symbol_edges,
                    "changed_contracts": edge_changed_contracts,
                    "downstream_call_sites": edge_call_sites,
                    "prompt_template": "cross_review.txt",
                    "context": context,
                })

            agent_assignments = self._build_agent_assignments(
                graph.model,
                changed_files,
                module_contexts,
                top_edges,
                cross_review_contexts,
                codegraph_context,
            )
            candidate_project_semantics = self._extract_candidate_project_semantics()
            configuration_gaps = self._build_configuration_gaps(candidate_project_semantics)
            semantic_module_splitter = self._build_semantic_module_splitter(
                graph.model,
                changed_files,
                graph_path,
                agent_assignments,
                candidate_project_semantics=candidate_project_semantics,
                configuration_gaps=configuration_gaps,
            )
            context_budget = self._build_context_budget(
                module_contexts,
                cross_review_contexts,
                top_edges,
                auto_lite=auto_lite,
                omitted_low_risk_edges=scorer.omitted_low_risk_edges,
            )
            timings["context_pack_ms"] = self._elapsed_ms(current_stage_start)
            completed_stages.append(current_stage)
            prepare_diagnostics = write_live_diagnostics("running")

            agent_pack = {
                "mode": "agent",
                "analysis_profile": analysis_profile,
                "requires_external_api_key": False,
                "execution_policy": self._build_execution_policy(),
                "project_root": self.root_dir,
                "cache_dir": self.cache_dir,
                "changed_files": changed_files,
                "project_graph_path": graph_path,
                "impact_edges_path": edges_path,
                "impact_edges": [edge.model_dump() for edge in top_edges],
                "contract_graph": contract_graph.model_dump(),
                "module_contexts": module_contexts,
                "cross_review_contexts": cross_review_contexts,
                "agent_assignments": agent_assignments,
                "semantic_module_splitter": semantic_module_splitter,
                "integrations": {
                    "codegraph": codegraph_context or self._empty_codegraph_context(changed_files),
                },
                "integration_context_paths": {
                    "codegraph": codegraph_context_path,
                },
                "configuration_gaps": configuration_gaps,
                "context_budget": context_budget,
                "analysis_config": self._analysis_config(
                    scanned_file_count=scanned_file_count,
                    source_file_count=source_file_count,
                    candidate_project_semantics=candidate_project_semantics,
                ),
                "prepare_diagnostics": prepare_diagnostics,
                "prepare_diagnostics_path": diagnostics_path,
                "agent_instructions_path": os.path.join(self.cache_dir, "agent_review_instructions.md"),
            }

            print("[6/6] Writing agent review pack...")
            current_stage = "write_pack"
            current_stage_start = time.perf_counter()
            write_live_diagnostics("running")
            pack_path = os.path.join(self.cache_dir, "agent_review_pack.json")
            with open(pack_path, "w", encoding="utf-8") as f:
                json.dump(agent_pack, f, indent=2, ensure_ascii=False)

            instructions = self._render_agent_instructions(agent_pack)
            with open(agent_pack["agent_instructions_path"], "w", encoding="utf-8") as f:
                f.write(instructions)
            timings["write_pack_ms"] = self._elapsed_ms(current_stage_start)
            completed_stages.append(current_stage)
            current_stage = "completed"
            agent_pack["prepare_diagnostics"] = write_live_diagnostics("completed")
            with open(pack_path, "w", encoding="utf-8") as f:
                json.dump(agent_pack, f, indent=2, ensure_ascii=False)

            print(f"      Saved agent review pack to {pack_path}")
            print("External API keys are not required for prepare mode.")
            return pack_path
        except Exception as exc:
            timing_key = stage_timing_keys.get(current_stage)
            if timing_key:
                timings[timing_key] = max(timings.get(timing_key, 0), self._elapsed_ms(current_stage_start))
            timings["total_prepare_ms"] = self._elapsed_ms(prepare_start)
            write_live_diagnostics("failed", failed_stage=current_stage, error=exc)
            raise

    def _collect_codegraph_context(self, changed_files: list[str]) -> dict:
        try:
            return CodeGraphIntegration(self.root_dir, self.config.integrations.codegraph).collect(changed_files)
        except Exception as exc:
            context = self._empty_codegraph_context(changed_files)
            context.update(
                {
                    "status": "error",
                    "reason": "codegraph_integration_exception",
                    "error": str(exc),
                }
            )
            return context

    def _empty_codegraph_context(self, changed_files: list[str]) -> dict:
        return {
            "enabled": False,
            "available": False,
            "index_present": os.path.isdir(os.path.join(self.root_dir, ".codegraph")),
            "status": "skipped",
            "reason": "not_collected",
            "source": "codegraph-cli",
            "mode": self.config.integrations.codegraph.enabled,
            "command": self.config.integrations.codegraph.command,
            "changed_files": list(changed_files),
            "affected": None,
            "explore": "",
            "commands": {},
            "usage_notes": [
                "CodeGraph context was not collected; use built-in project graph and repository files.",
            ],
        }

    def _empty_contract_graph(self):
        from cross_review.schemas.models import ContractGraphModel

        return ContractGraphModel()

    def _analysis_config(
        self,
        scanned_file_count: int | None = None,
        source_file_count: int | None = None,
        candidate_project_semantics: dict | None = None,
    ) -> dict:
        return {
            "config_path": self.config.source_path,
            "enabled_analyzers": self.config.review.enabled_analyzers,
            "ignored_paths": self.config.ignored_paths,
            "known_dynamic_boundaries": self.config.known_dynamic_boundaries,
            "path_aliases": self.config.path_aliases,
            "low_value_modules": self.config.review.low_value_modules,
            "external_project_graph_path": self.config.project_graph.external_graph_path,
            "integrations": {
                "codegraph": {
                    "enabled": self.config.integrations.codegraph.enabled,
                    "command": self.config.integrations.codegraph.command,
                    "timeout_seconds": self.config.integrations.codegraph.timeout_seconds,
                    "max_explore_chars": self.config.integrations.codegraph.max_explore_chars,
                    "affected_depth": self.config.integrations.codegraph.affected_depth,
                },
            },
            "project_semantics": self.config.project_semantics.as_dict(),
            "candidate_project_semantics": candidate_project_semantics or self._empty_candidate_project_semantics(),
            "optional_js_ast_parser": self._optional_js_ast_parser_status(),
            "auto_lite_file_threshold": self.config.review.auto_lite_file_threshold,
            "targeted_scan_file_threshold": self.config.review.targeted_scan_file_threshold,
            "source_file_count": source_file_count,
            "scanned_file_count": scanned_file_count,
        }

    def _optional_js_ast_parser_status(self) -> str:
        from cross_review.contracts import js_ast

        return js_ast.parser_status()

    def _build_prepare_diagnostics(
        self,
        scanner: ScoutScanner | None,
        graph_model: ProjectGraphModel | None,
        scanned_file_count: int,
        analysis_profile: str,
        timings: dict,
        status: str = "completed",
        current_stage: str = "completed",
        completed_stages: list[str] | None = None,
        failed_stage: str | None = None,
        error: Exception | None = None,
        diagnostics_path: str | None = None,
    ) -> dict:
        scanner_diagnostics = getattr(scanner, "diagnostics", {}) or {}
        module_count = len(graph_model.modules) if graph_model is not None else 0
        timing_keys = [
            "scan_files_ms",
            "scout_analyze_ms",
            "detect_changed_files_ms",
            "codegraph_ms",
            "contract_graph_ms",
            "impact_score_ms",
            "context_pack_ms",
            "write_pack_ms",
            "total_prepare_ms",
        ]
        timing_values = {}
        for key in timing_keys:
            value = timings.get(key, scanner_diagnostics.get(key, 0))
            timing_values[key] = max(0, int(value or 0))

        return {
            "status": status,
            "current_stage": current_stage,
            "completed_stages": list(completed_stages or []),
            "failed_stage": failed_stage,
            "error": self._format_prepare_error(error),
            "diagnostics_path": diagnostics_path,
            "analysis_profile": analysis_profile,
            "scan_mode": scanner_diagnostics.get("scan_mode", "unknown" if scanner is None else "full"),
            "scout_cache_status": scanner_diagnostics.get("scout_cache_status", "unknown"),
            "source_file_count": int(scanner_diagnostics.get("source_file_count") or scanned_file_count),
            "scanned_file_count": int(scanner_diagnostics.get("scanned_file_count") or scanned_file_count),
            "targeted_file_count": int(scanner_diagnostics.get("targeted_file_count") or 0),
            "skipped_file_count": int(scanner_diagnostics.get("skipped_file_count") or 0),
            "module_count": int(scanner_diagnostics.get("module_count") or module_count),
            "timings_ms": timing_values,
        }

    def _format_prepare_error(self, error: Exception | None) -> dict | None:
        if error is None:
            return None
        return {
            "type": error.__class__.__name__,
            "message": str(error),
        }

    def _write_prepare_diagnostics_file(self, diagnostics_path: str, diagnostics: dict):
        os.makedirs(os.path.dirname(diagnostics_path), exist_ok=True)
        with open(diagnostics_path, "w", encoding="utf-8") as f:
            json.dump(diagnostics, f, indent=2, ensure_ascii=False)

    def _elapsed_ms(self, start: float) -> int:
        return int((time.perf_counter() - start) * 1000)

    def _set_external_graph_diagnostics(self, scanner: ScoutScanner, graph: ProjectGraph):
        scanned_file_count = sum(len(module.files) for module in graph.model.modules.values())
        scanner.diagnostics = {
            "scan_mode": "external-graph",
            "scout_cache_status": "bypassed",
            "source_file_count": scanned_file_count,
            "scanned_file_count": scanned_file_count,
            "targeted_file_count": 0,
            "skipped_file_count": 0,
            "module_count": len(graph.model.modules),
            "scan_files_ms": 0,
            "scout_analyze_ms": 0,
        }

    def _build_scout_cache_key(self, scanner: ScoutScanner, changed_files: list[str]) -> dict:
        fingerprint = scanner.source_fingerprint()
        return {
            "version": 1,
            "source_fingerprint": fingerprint["fingerprint"],
            "source_file_count": fingerprint["source_file_count"],
            "changed_files": sorted(changed_files),
            "ignored_paths": sorted(self.config.ignored_paths),
            "targeted_scan_file_threshold": self.config.review.targeted_scan_file_threshold,
        }

    def _load_scout_cache(self, cache_key: dict, scanner: ScoutScanner) -> ProjectGraph | None:
        meta_path = os.path.join(self.cache_dir, "project_graph.cache_meta.json")
        graph_path = os.path.join(self.cache_dir, "project_graph.cache.json")
        if not os.path.exists(meta_path) or not os.path.exists(graph_path):
            return None
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("cache_key") != cache_key:
                return None
            graph = ProjectGraph.load_from_file(graph_path)
        except Exception:
            return None
        diagnostics = dict(meta.get("diagnostics") or {})
        diagnostics["scan_mode"] = "cache-hit"
        diagnostics["scout_cache_status"] = "hit"
        scanner.diagnostics = diagnostics
        return graph

    def _write_scout_cache(self, cache_key: dict, graph: ProjectGraph, scanner: ScoutScanner):
        graph_path = os.path.join(self.cache_dir, "project_graph.cache.json")
        meta_path = os.path.join(self.cache_dir, "project_graph.cache_meta.json")
        try:
            graph.save_to_file(graph_path)
            diagnostics = dict(getattr(scanner, "diagnostics", {}) or {})
            diagnostics["scout_cache_status"] = "miss"
            scanner.diagnostics = diagnostics
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "cache_key": cache_key,
                        "diagnostics": diagnostics,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
        except Exception:
            diagnostics = dict(getattr(scanner, "diagnostics", {}) or {})
            diagnostics["scout_cache_status"] = "write_failed"
            scanner.diagnostics = diagnostics

    def _should_auto_lite(self, scanned_file_count: int, explicit_lite: bool = False) -> bool:
        threshold = self.config.review.auto_lite_file_threshold
        return (
            not explicit_lite
            and not self.config.review.lite
            and threshold > 0
            and scanned_file_count > threshold
        )

    def _build_execution_policy(self) -> dict:
        return {
            "subagents_default_when_available": True,
            "subagents_requested_by_cross_review": True,
            "subagents_required_when_authorized_and_available": True,
            "authorization_source": "user_request_or_host_policy",
            "ask_once_if_host_requires_explicit_authorization": True,
            "missing_authorization_action": "ask_once_and_pause",
            "respect_user_opt_out": True,
            "simulation_allowed_only_if_subagents_unavailable": True,
            "simulation_requires_explicit_note": True,
            "fallback_execution_mode": "sequential_same_agent",
            "preflight_prompt_policy": {
                "assignment_basis": "effective_assignments_after_semantic_split",
                "ask_before_spawning": "when_host_requires_explicit_authorization_and_request_lacks_it",
                "spawn_when_effective_assignments_gt": 0,
                "cross_review_targets_do_not_alone_trigger_subagents": True,
                "fallback_effective_assignment_source": "raw_agent_assignments_when_semantic_split_uncertain",
                "if_user_declines_or_disables": "sequential_same_agent_with_explicit_note",
            },
            "explicit_authorization_examples": [
                "use subagents",
                "delegate to subagents",
                "enable subagent review",
                "parallel agent work",
                "parallel review",
                "delegation",
                "开启子代理审查",
                "使用子代理",
                "并行审查",
            ],
            "opt_out_examples": [
                "no subagents",
                "disable subagents",
                "sequential review",
                "same-agent review",
                "不要子代理",
                "不用子代理",
                "顺序审查",
            ],
            "instructions": [
                "Run semantic_module_splitter first and derive effective assignments before applying subagent preflight policy.",
                "Cross-review requests real subagents by default, but host-level authorization rules remain authoritative.",
                "Skill instructions, generated packs, and assistant-authored prompts do not count as user authorization.",
                "If host policy requires explicit user authorization and the current request lacks it, ask one concise authorization question and pause; do not silently fall back to same-agent review.",
                "If effective assignments are non-empty, delegation is authorized, and the host exposes subagent or multi-agent tools, spawn one real subagent per effective assignment by default.",
                "If subagents are unavailable, opted out, declined, or refused after authorization, explicitly state that no subagents were spawned before running assignments sequentially.",
            ],
        }

    def _build_configuration_gaps(self, candidate_project_semantics: dict | None = None) -> list[dict]:
        if not self._project_semantics_empty():
            return []
        candidates = candidate_project_semantics or self._empty_candidate_project_semantics()
        return [
            {
                "field": "project_semantics",
                "severity": "warning",
                "message": (
                    "project_semantics is empty; forbidden semantics, review-gate obligations, "
                    "and negative probes cannot be inferred safely from the static graph."
                ),
                "host_agent_action": (
                    "State this limitation in the review and ask the user or project docs for "
                    "repo-specific forbidden/review-gate semantics before treating that surface as complete. "
                    "Use candidate_project_semantics only as doc-derived hints until the user configures them."
                ),
                "candidate_project_semantics": candidates,
                "suggested_config": (
                    "[project_semantics]\n"
                    "review_gates = [\"review-gate\"]\n"
                    "forbidden_semantics = [\"Forbidden rows must not render as allowed fallback states.\"]\n"
                    "negative_probes = [\"Create a forbidden review-gate fixture and verify it remains blocked.\"]"
                ),
            }
        ]

    def _project_semantics_empty(self) -> bool:
        semantics = self.config.project_semantics
        return (
            not semantics.review_gates
            and not semantics.forbidden_semantics
            and not semantics.negative_probes
        )

    def _empty_candidate_project_semantics(self) -> dict:
        return {
            "review_gates": [],
            "forbidden_semantics": [],
            "negative_probes": [],
            "evidence_refs": [],
        }

    def _extract_candidate_project_semantics(self) -> dict:
        candidates = self._empty_candidate_project_semantics()
        seen_values = {key: set() for key in candidates}

        def add_candidate(key: str, value: str, evidence_ref: str):
            value = value.strip()
            if not value or value in seen_values[key]:
                return
            if len(candidates[key]) >= 12:
                return
            seen_values[key].add(value)
            candidates[key].append(value)
            if evidence_ref not in seen_values["evidence_refs"] and len(candidates["evidence_refs"]) < 24:
                seen_values["evidence_refs"].add(evidence_ref)
                candidates["evidence_refs"].append(evidence_ref)

        for rel_path in self._candidate_project_semantics_doc_paths():
            abs_path = os.path.join(self.root_dir, rel_path.replace("/", os.sep))
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except Exception:
                continue

            for line_number, raw_line in enumerate(lines[:400], start=1):
                clean_line = self._clean_doc_rule_line(raw_line)
                if not clean_line or len(clean_line) > 500:
                    continue
                lower_line = clean_line.lower()
                evidence_ref = f"{rel_path}:{line_number}"

                if re.search(r"\breview[-_\s]?gate\b", lower_line):
                    add_candidate("review_gates", "review-gate", evidence_ref)

                if "forbidden" in lower_line and re.search(
                    r"\b(must not|never|should not|cannot|must remain impossible)\b",
                    lower_line,
                ):
                    add_candidate("forbidden_semantics", clean_line, evidence_ref)

                if (
                    "negative probe" in lower_line
                    or ("forbidden" in lower_line and "verify" in lower_line and re.search(r"\b(test|fixture|probe)\b", lower_line))
                ):
                    probe = re.sub(r"(?i)^negative\s+probe\s*:\s*", "", clean_line).strip()
                    add_candidate("negative_probes", probe, evidence_ref)

        return candidates

    def _candidate_project_semantics_doc_paths(self) -> list[str]:
        roots = ["AGENTS.md", "README.md", "CONTRIBUTING.md"]
        paths = []
        seen = set()

        def add_path(rel_path: str):
            normalized = rel_path.replace("\\", "/").strip("/")
            if not normalized or normalized in seen or self._is_ignored_path(normalized):
                return
            if os.path.exists(os.path.join(self.root_dir, normalized.replace("/", os.sep))):
                seen.add(normalized)
                paths.append(normalized)

        for rel_path in roots:
            add_path(rel_path)

        for base in ["docs", ".github"]:
            base_abs = os.path.join(self.root_dir, base)
            if not os.path.isdir(base_abs):
                continue
            for dirpath, dirnames, filenames in os.walk(base_abs):
                dirnames[:] = [
                    dirname
                    for dirname in sorted(dirnames)
                    if not self._is_ignored_path(os.path.relpath(os.path.join(dirpath, dirname), self.root_dir))
                ]
                for filename in sorted(filenames):
                    if not filename.lower().endswith((".md", ".mdx", ".txt")):
                        continue
                    rel_path = os.path.relpath(os.path.join(dirpath, filename), self.root_dir)
                    add_path(rel_path)
                    if len(paths) >= 40:
                        return paths

        return paths

    def _clean_doc_rule_line(self, line: str) -> str:
        clean = line.strip()
        clean = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", clean)
        clean = re.sub(r"^\s{0,3}#+\s*", "", clean)
        return clean.strip()

    def _build_previous_source_provider(self, base_branch: str, diff_mode: str = None):
        try:
            parser = GitDiffParser(self.root_dir)
        except Exception:
            return None

        def provider(file_path: str) -> str | None:
            return parser.get_previous_file_content(file_path, base=base_branch, mode=diff_mode)

        return provider

    def _annotate_edges_with_contract_evidence(self, edges: list, contract_graph):
        for edge in edges:
            changed_contract_ids = [
                changed.contract_id
                for changed in contract_graph.changed_contracts
                if changed.module == edge.from_module
            ]
            callsite_ids = [
                callsite.callsite_id
                for callsite in contract_graph.call_sites
                if (
                    callsite.provider_module == edge.from_module
                    and callsite.consumer_module == edge.to_module
                    and callsite.contract_id in changed_contract_ids
                )
            ]
            edge.changed_contract_ids = sorted(changed_contract_ids)
            edge.callsite_ids = sorted(callsite_ids)

    def _add_contract_graph_dependencies(self, graph, contract_graph):
        surfaces_by_id = {
            surface.contract_id: surface
            for surface in contract_graph.contract_surfaces
        }
        for callsite in contract_graph.call_sites:
            surface = surfaces_by_id.get(callsite.contract_id)
            if surface is None:
                continue
            dep_type = self._dependency_type_for_contract(surface.contract_id)
            if dep_type is None:
                continue
            graph.add_dependency(
                from_mod=callsite.provider_module,
                to_mod=callsite.consumer_module,
                dep_type=dep_type,
                details=f"{callsite.file}:{callsite.line} consumes {surface.contract_id}",
                consumer_files=[callsite.file],
                provider_files=[surface.file],
            )

    def _render_contract_evidence_summary(self, changed_contracts: list[dict], call_sites: list[dict]) -> str:
        if not changed_contracts and not call_sites:
            return ""
        lines = ["--- 4. Changed Contract Evidence ---"]
        for contract in changed_contracts:
            lines.append(
                "- "
                f"id={contract.get('contract_id')} "
                f"type={contract.get('change_type')} "
                f"file={contract.get('file')}:{contract.get('line')} "
                f"signature={contract.get('current_signature') or contract.get('signature') or ''}"
            )
            if contract.get("diff_summary"):
                lines.append(f"  summary={contract.get('diff_summary')}")
        lines.append("")
        lines.append("--- 5. Downstream Call-Site Evidence ---")
        for callsite in call_sites:
            lines.append(
                "- "
                f"id={callsite.get('callsite_id')} "
                f"contract={callsite.get('contract_id')} "
                f"file={callsite.get('file')}:{callsite.get('line')} "
                f"usage={callsite.get('usage')}"
            )
        return "\n".join(lines)

    def _build_context_budget(
        self,
        module_contexts: list[dict],
        cross_review_contexts: list[dict],
        top_edges: list,
        auto_lite: bool = False,
        omitted_low_risk_edges: list[dict] | None = None,
    ) -> dict:
        context_items = []
        for idx, context in enumerate(module_contexts):
            context_items.append(
                {
                    "kind": "module",
                    "index": idx,
                    "name": context.get("module_name"),
                    "text": context.get("context", ""),
                }
            )
        for idx, context in enumerate(cross_review_contexts):
            context_items.append(
                {
                    "kind": "cross_review",
                    "index": idx,
                    "name": f"{context.get('from_module')}->{context.get('to_module')}",
                    "text": context.get("context", ""),
                }
            )

        total_chars = sum(len(item["text"]) for item in context_items)
        chars_per_token = max(1, self.config.context.token_estimate_chars_per_token)
        estimated_tokens = (total_chars + chars_per_token - 1) // chars_per_token
        truncated_contexts = [
            {
                "kind": item["kind"],
                "index": item["index"],
                "name": item["name"],
                "reason": "context contains folded lines",
            }
            for item in context_items
            if "folded" in item["text"]
        ]
        truncated_files = [
            {
                "kind": item["kind"],
                "index": item["index"],
                "name": item["name"],
                "file": item["name"],
                "reason": "context contains folded lines",
            }
            for item in context_items
            if "folded" in item["text"]
        ]
        evidence_count = sum(
            len(context.get("changed_contracts", [])) + len(context.get("downstream_call_sites", []))
            for context in cross_review_contexts
        )

        budget = {
            "estimated_context_chars": total_chars,
            "estimated_context_tokens": estimated_tokens,
            "target_context_tokens": self.config.context.target_context_tokens,
            "over_budget": estimated_tokens > self.config.context.target_context_tokens,
            "module_context_count": len(module_contexts),
            "cross_review_context_count": len(cross_review_contexts),
            "contract_evidence_count": evidence_count,
            "evidence_density": round(evidence_count / max(1, estimated_tokens), 6),
            "top_k_policy": {
                "configured_top_k": self.config.review.top_k,
                "actual_edges": len(top_edges),
                "expand_critical_top_k": self.config.review.expand_critical_top_k,
            },
            "limits": {
                "max_diff_lines": self.config.context.max_diff_lines,
                "max_consumer_files": self.config.context.max_consumer_files,
                "token_estimate_chars_per_token": chars_per_token,
            },
            "truncated_contexts": truncated_contexts,
            "truncated_files": truncated_files,
            "omitted_low_risk_edges": list(omitted_low_risk_edges or []),
        }
        if auto_lite:
            budget["auto_lite_reason"] = "scanned_file_count_exceeded_threshold"
        return budget

    def _dependency_type_for_contract(self, contract_id: str) -> str | None:
        if contract_id.startswith("python:route:"):
            return "api_call"
        if contract_id.startswith("typescript:route:"):
            return "api_call"
        if contract_id.startswith("typescript:function:"):
            return "static_import"
        if contract_id.startswith("typescript:class:"):
            return "static_import"
        if contract_id.startswith("python:event:"):
            return "event_contract"
        if contract_id.startswith("graphql:"):
            return "api_call"
        if contract_id.startswith("protobuf:"):
            return "api_call"
        if contract_id.startswith("sql:"):
            return "db_shared"
        return None

    def _build_agent_id(self, module_name: str) -> str:
        cleaned = re.sub(r"[^a-z0-9]+", "-", module_name.lower()).strip("-")
        return f"module-{cleaned or 'unnamed'}-reviewer"

    def _find_cross_review_context_index(
        self,
        cross_review_contexts: list[dict],
        from_module: str,
        to_module: str,
        edge_type: str,
    ) -> int:
        for idx, context in enumerate(cross_review_contexts):
            if (
                context["from_module"] == from_module
                and context["to_module"] == to_module
                and context["edge_type"] == edge_type
            ):
                return idx
        raise ValueError(
            f"Missing cross review context for {from_module} -> {to_module} ({edge_type})."
        )

    def _build_handoff_artifact(self, module_name: str, agent_id: str) -> dict:
        required_fields = [
            "summary",
            "changed_contracts",
            "public_api_changes",
            "data_schema_changes",
            "event_contract_changes",
            "route_changes",
            "internal_findings",
            "assumptions",
            "evidence_refs",
            "downstream_questions",
        ]
        return {
            "artifact_id": f"module-review-memory:{module_name}",
            "producer_agent_id": agent_id,
            "purpose": (
                "Persist the primary module review result so the same reviewer "
                "can carry exact evidence into downstream cross-review tasks."
            ),
            "required_fields": required_fields,
            "schema": {
                "summary": "Short factual summary of what changed in the primary module.",
                "changed_contracts": "List of public functions/classes/routes/events/schema fields whose contract may have changed.",
                "public_api_changes": "List of changed call signatures, return shapes, exceptions, or route parameters.",
                "data_schema_changes": "List of changed SQL tables, columns, migrations, or persistence assumptions.",
                "event_contract_changes": "List of emitted/listened event name or payload changes.",
                "route_changes": "List of HTTP route, method, parameter, or response shape changes.",
                "internal_findings": "Evidence-backed issues found inside the primary module.",
                "assumptions": "Explicit assumptions that downstream review must re-check.",
                "evidence_refs": "File/line references or context snippets supporting each claim.",
                "downstream_questions": "Questions each target-module review must answer using this memory.",
            },
        }

    def _build_assignment_codegraph_context(self, codegraph_context: dict | None) -> dict:
        if not isinstance(codegraph_context, dict):
            return self._empty_assignment_codegraph_context()
        explore = codegraph_context.get("explore")
        explore_excerpt = self._truncate_text(explore if isinstance(explore, str) else "", 1200)
        return {
            "enabled": bool(codegraph_context.get("enabled")),
            "status": codegraph_context.get("status") or "unknown",
            "reason": codegraph_context.get("reason"),
            "affected": codegraph_context.get("affected"),
            "explore_excerpt": explore_excerpt,
            "usage_notes": list(codegraph_context.get("usage_notes") or []),
        }

    def _empty_assignment_codegraph_context(self) -> dict:
        return {
            "enabled": False,
            "status": "skipped",
            "reason": "not_collected",
            "affected": None,
            "explore_excerpt": "",
            "usage_notes": [
                "CodeGraph context was not available for this assignment.",
            ],
        }

    def _truncate_text(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[:limit] + "[truncated]"

    def _build_agent_assignments(
        self,
        graph: ProjectGraphModel,
        changed_files: list[str],
        module_contexts: list[dict],
        top_edges: list,
        cross_review_contexts: list[dict],
        codegraph_context: dict | None = None,
    ) -> list[dict]:
        agent_assignments = []
        assignment_codegraph_context = self._build_assignment_codegraph_context(codegraph_context)
        for context_idx, mod_ctx in enumerate(module_contexts):
            mod_name = mod_ctx["module_name"]
            mod_obj = graph.modules.get(mod_name)
            primary_files = [f for f in changed_files if mod_obj and f in mod_obj.files]
            agent_id = self._build_agent_id(mod_name)
            handoff_artifact = self._build_handoff_artifact(mod_name, agent_id)

            cross_review_targets = []
            for edge in top_edges:
                if edge.from_module != mod_name:
                    continue

                cross_ctx_idx = self._find_cross_review_context_index(
                    cross_review_contexts,
                    edge.from_module,
                    edge.to_module,
                    edge.edge_type,
                )
                cross_review_targets.append({
                    "target_module": edge.to_module,
                    "edge_type": edge.edge_type,
                    "risk_score": edge.risk_score,
                    "force_triggered": edge.force_triggered,
                    "cross_review_context_index": cross_ctx_idx,
                    "changed_contract_ids": edge.changed_contract_ids,
                    "callsite_ids": edge.callsite_ids,
                    "symbol_edges": edge.symbol_edges,
                    "review_question": f"Will changes in {mod_name} break {edge.to_module}?",
                    "memory_handoff": {
                        "source_artifact_id": handoff_artifact["artifact_id"],
                        "required_fields": [
                            "changed_contracts",
                            "public_api_changes",
                            "data_schema_changes",
                            "event_contract_changes",
                            "route_changes",
                            "evidence_refs",
                            "downstream_questions",
                        ],
                        "consumer_instruction": (
                            f"Before judging {mod_name} -> {edge.to_module}, read the "
                            f"{handoff_artifact['artifact_id']} artifact and connect each "
                            "finding to concrete downstream usage evidence."
                        ),
                    },
                    "focus_questions": [
                        f"Which changed {mod_name} contracts are consumed by {edge.to_module}?",
                        f"Does {edge.to_module} still call old signatures, fields, routes, or event payloads?",
                        "Is there an integration or contract test covering this boundary?",
                    ],
                })

            cross_review_targets.sort(key=lambda t: t["risk_score"], reverse=True)
            execution_order = ["module_review"] + [
                f"cross_review:{target['target_module']}" for target in cross_review_targets
            ]

            handoff_notes = [
                f"First review {mod_name} internally and write {handoff_artifact['artifact_id']}.",
            ]
            if cross_review_targets:
                targets_str = ", ".join(f"{mod_name} -> {t['target_module']}" for t in cross_review_targets)
                handoff_notes.append(f"Then consume that memory artifact while reviewing {targets_str}.")
            else:
                handoff_notes.append("No downstream cross-review required.")

            agent_assignments.append({
                "agent_id": agent_id,
                "primary_module": mod_name,
                "primary_files": primary_files,
                "module_context_index": context_idx,
                "cross_review_targets": cross_review_targets,
                "execution_order": execution_order,
                "handoff_artifact": handoff_artifact,
                "handoff_notes": handoff_notes,
                "integration_context": {
                    "codegraph": assignment_codegraph_context,
                },
            })

        return agent_assignments

    def _build_semantic_module_splitter(
        self,
        graph: ProjectGraphModel,
        changed_files: list[str],
        project_graph_path: str,
        agent_assignments: list[dict] | None = None,
        candidate_project_semantics: dict | None = None,
        configuration_gaps: list[dict] | None = None,
    ) -> dict:
        detected_modules = sorted(graph.modules.keys())
        aliases = self._suggest_semantic_aliases(graph)
        project_semantics = self.config.project_semantics.as_dict()
        candidate_project_semantics = candidate_project_semantics or self._empty_candidate_project_semantics()
        configuration_gaps = configuration_gaps if configuration_gaps is not None else self._build_configuration_gaps(candidate_project_semantics)
        deterministic_effective_assignments = self._build_deterministic_effective_assignments(
            agent_assignments or [],
            aliases,
        )
        return {
            "enabled_for_host_agent": True,
            "requires_external_api_key": False,
            "execution_location": "host_agent",
            "requires_host_agent_reasoning": True,
            "input_summary": {
                "project_root": self.root_dir,
                "project_graph_path": project_graph_path,
                "detected_modules": detected_modules,
                "changed_files": changed_files,
                "project_semantics": project_semantics,
                "candidate_project_semantics": candidate_project_semantics,
                "configuration_gaps": configuration_gaps,
                "deterministic_effective_assignment_count": len(deterministic_effective_assignments),
                "physical_modules": [
                    {
                        "name": mod_name,
                        "files": graph.modules[mod_name].files,
                        "criticality": graph.modules[mod_name].criticality,
                        "exports": graph.modules[mod_name].exports,
                        "routes": graph.modules[mod_name].routes,
                        "events": graph.modules[mod_name].events,
                        "db_tables": graph.modules[mod_name].db_tables,
                    }
                    for mod_name in detected_modules
                ],
            },
            "host_agent_instructions": [
                "Quickly inspect physical_modules and project_graph_path before dispatching reviewers.",
                "Produce a semantic module split using output_schema; do not ask for API keys because the host Agent is the reasoning engine.",
                "Use suggested_alias_schema as deterministic hints, not as final truth.",
                "Apply input_summary.project_semantics during semantic split and final review focus; preserve configured review_gates, forbidden_semantics, and negative_probes as explicit review obligations.",
                "If input_summary.project_semantics is empty, state that project_semantics is empty and do not invent forbidden semantics or review-gate rules.",
                "If input_summary.candidate_project_semantics has doc-derived hints, cite them as candidates and ask the user to confirm or move them into config before treating them as mandatory obligations.",
                "Start from deterministic_effective_assignments when deriving effective assignments; revise only with concrete evidence from physical_modules or project_graph_path.",
                "Return effective assignments after semantic split; subagent preflight decisions must use those effective assignments, not raw agent_assignments.",
                "If semantic aliases merge physical modules, apply assignment_rewrite_policy before running subagents.",
                "Do not rewrite project_graph.json unless the user explicitly asks.",
            ],
            "deterministic_effective_assignments": deterministic_effective_assignments,
            "output_schema": {
                "required_fields": [
                    "semantic_modules",
                    "module_aliases",
                    "effective_assignments",
                    "assignment_rewrite_decisions",
                    "rationale",
                    "confidence",
                ],
                "semantic_modules_item": {
                    "name": "Stable semantic module name.",
                    "physical_modules": "Physical modules included in this semantic module.",
                    "responsibility": "One-sentence domain responsibility.",
                    "contract_surfaces": "Exports, routes, events, db tables, or external API surfaces.",
                    "evidence_refs": "Files or graph entries that justify this split.",
                },
                "assignment_rewrite_decisions_item": {
                    "semantic_module": "Semantic module that should own a review task.",
                    "source_agent_assignment_ids": "Original agent_assignments to merge or keep.",
                    "module_context_indexes": "Original module_context indexes that must remain valid.",
                    "cross_review_context_indexes": "Original cross_review_context indexes that must remain valid.",
                    "reason": "Why this rewrite improves review fidelity.",
                },
                "effective_assignments_item": {
                    "review_unit": "Semantic review unit name used for preflight and execution.",
                    "source_agent_assignment_ids": "Original agent_assignments represented by this effective assignment.",
                    "primary_modules": "Physical or semantic modules included in this review unit.",
                    "module_context_indexes": "Original module_context indexes that must remain valid.",
                    "cross_review_context_indexes": "Original cross_review_context indexes that must remain valid.",
                    "cross_review_targets": "Downstream targets to review inside this effective assignment.",
                    "reason": "Why this is the effective review unit.",
                },
            },
            "assignment_rewrite_policy": {
                "may_merge_assignments": True,
                "may_split_assignments": False,
                "must_preserve_context_indexes": True,
                "must_preserve_primary_files": True,
                "must_preserve_cross_review_targets": True,
                "fallback": "If uncertain, use the physical-module agent_assignments unchanged.",
            },
            "suggested_alias_schema": {
                "aliases": aliases,
            },
        }

    def _build_deterministic_effective_assignments(
        self,
        agent_assignments: list[dict],
        aliases: list[dict],
    ) -> list[dict]:
        module_to_alias = {}
        alias_basis = {}
        for alias in aliases:
            confidence = alias.get("confidence")
            if confidence not in {"configured", "candidate"}:
                continue
            semantic_module = alias.get("semantic_module")
            if not semantic_module:
                continue
            basis = "configured_module_alias" if confidence == "configured" else "candidate_prefix_alias"
            for physical_module in alias.get("physical_modules", []):
                module_to_alias.setdefault(physical_module, semantic_module)
                alias_basis.setdefault(semantic_module, basis)

        grouped: dict[str, dict] = {}
        for assignment in agent_assignments:
            primary_module = assignment.get("primary_module")
            review_unit = module_to_alias.get(primary_module, primary_module)
            if not review_unit:
                continue
            item = grouped.setdefault(
                review_unit,
                {
                    "review_unit": review_unit,
                    "basis": alias_basis.get(review_unit, "physical_module"),
                    "source_agent_assignment_ids": [],
                    "primary_modules": [],
                    "module_context_indexes": [],
                    "cross_review_context_indexes": [],
                    "cross_review_targets": [],
                    "reason": "",
                },
            )
            item["source_agent_assignment_ids"].append(assignment.get("agent_id"))
            item["primary_modules"].append(primary_module)
            item["module_context_indexes"].append(assignment.get("module_context_index"))
            targets = assignment.get("cross_review_targets", [])
            item["cross_review_targets"].extend(targets)
            item["cross_review_context_indexes"].extend(
                target.get("cross_review_context_index")
                for target in targets
                if isinstance(target, dict)
            )

        result = []
        for review_unit, item in sorted(grouped.items()):
            item["source_agent_assignment_ids"] = sorted(
                value for value in set(item["source_agent_assignment_ids"]) if value
            )
            item["primary_modules"] = sorted(value for value in set(item["primary_modules"]) if value)
            item["module_context_indexes"] = sorted(
                value for value in set(item["module_context_indexes"]) if isinstance(value, int)
            )
            item["cross_review_context_indexes"] = sorted(
                value for value in set(item["cross_review_context_indexes"]) if isinstance(value, int)
            )
            if item["basis"] == "configured_module_alias":
                item["reason"] = f"Configured module_aliases groups these physical modules into {review_unit}."
            elif item["basis"] == "candidate_prefix_alias":
                item["reason"] = f"Physical modules share a domain prefix and are a deterministic merge candidate for {review_unit}."
            else:
                item["reason"] = f"Physical module {review_unit} remains its own effective review unit."
            result.append(item)
        return result

    def _suggest_semantic_aliases(self, graph: ProjectGraphModel) -> list[dict]:
        modules = sorted(graph.modules.keys())
        prefix_groups: dict[str, list[str]] = {}
        generic_prefixes = {"api", "app", "apps", "core", "common", "lib", "service", "services", "shared", "src", "utils"}
        aliases = []
        configured_semantic_modules = set()

        for semantic_module, physical_modules in sorted(self.config.module_aliases.items()):
            existing_modules = [module for module in physical_modules if module in graph.modules]
            if not existing_modules:
                continue
            configured_semantic_modules.add(semantic_module)
            aliases.append({
                "semantic_module": semantic_module,
                "physical_modules": existing_modules,
                "reason": "Configured in cross-review.toml module_aliases.",
                "confidence": "configured",
            })

        for module_name in modules:
            tokens = [token for token in re.split(r"[^a-zA-Z0-9]+", module_name.lower()) if token]
            if len(tokens) < 2 or tokens[0] in generic_prefixes:
                continue
            prefix_groups.setdefault(tokens[0], []).append(module_name)

        grouped_modules = set()
        for prefix, physical_modules in sorted(prefix_groups.items()):
            if len(physical_modules) < 2 or prefix in configured_semantic_modules:
                continue
            grouped_modules.update(physical_modules)
            aliases.append({
                "semantic_module": prefix,
                "physical_modules": physical_modules,
                "reason": f"Physical modules share the '{prefix}' domain prefix.",
                "confidence": "candidate",
            })

        for module_name in modules:
            confidence = "physical_fallback"
            reason = f"Physical module '{module_name}' is available as an unmerged review boundary."
            if module_name in grouped_modules:
                reason = f"Fallback boundary if the host Agent rejects the broader semantic group for '{module_name}'."
            aliases.append({
                "semantic_module": module_name,
                "physical_modules": [module_name],
                "reason": reason,
                "confidence": confidence,
            })

        return aliases

    def _get_llm(self) -> LLMClient:
        if self.llm is None:
            self.llm = LLMClient()
        return self.llm

    def _detect_changed_files(self, base_branch: str, head_branch: str, manual_files: list[str] = None, diff_mode: str = None) -> list[str]:
        if manual_files:
            return self._filter_ignored_files([self._normalize_manual_file(f) for f in manual_files])

        try:
            parser = GitDiffParser(self.root_dir)
            return self._filter_ignored_files(parser.get_changed_files(base_branch, head_branch, diff_mode))
        except Exception as e:
            raise RuntimeError(
                "Unable to detect changed files from Git. Run inside a Git "
                "repository or pass explicit review targets with --files."
            ) from e

    def _filter_ignored_files(self, files: list[str]) -> list[str]:
        return [file_path for file_path in files if not self._is_ignored_path(file_path)]

    def _is_ignored_path(self, file_path: str) -> bool:
        normalized = file_path.replace("\\", "/").strip("/")
        for pattern in self.config.ignored_paths:
            clean_pattern = pattern.replace("\\", "/").strip("/")
            if fnmatch.fnmatch(normalized, clean_pattern):
                return True
            if clean_pattern.endswith("/**") and normalized == clean_pattern[:-3].rstrip("/"):
                return True
        return False

    def _render_agent_instructions(self, agent_pack: dict) -> str:
        lines = [
            "# Cross-Review Agent Instructions",
            "",
            "This pack was prepared locally. Do not ask for model API keys.",
            "",
            "Please execute the cross-module review using the following dynamic assignment and context handoff protocol:",
            "",
            "Step 0: run the host-agent semantic split step described by semantic_module_splitter. Produce its output_schema, then apply assignment_rewrite_policy or keep the physical assignments unchanged if uncertain.",
            "",
            "Step 0a: read analysis_config.project_semantics and semantic_module_splitter.input_summary.project_semantics. Treat configured review_gates, forbidden_semantics, and negative_probes as mandatory review obligations, even when static impact edges look low risk.",
            "",
            "Step 0b: if configuration_gaps says project_semantics is not configured, state that limitation in the review and do not invent forbidden semantics, review-gate rules, or negative probes.",
            "",
            "Step 0b.1: if analysis_config.candidate_project_semantics or semantic_module_splitter.input_summary.candidate_project_semantics contains doc-derived hints, cite them as candidates and ask the user to confirm or move them into config before treating them as mandatory obligations.",
            "",
            "Step 0c: derive effective assignments after semantic split. Use effective assignments for subagent preflight decisions; raw agent_assignments are only the fallback when semantic split is uncertain.",
            "",
            "Step 0d: inspect integrations.codegraph when present. If enabled, treat its affected/explore output as supplemental routing and blast-radius context for assignments; each assignment also carries a trimmed integration_context.codegraph excerpt for subagents. Do not treat CodeGraph summaries as final findings without concrete file/line evidence.",
            "",
            "Step 1: check execution_policy and the user's request for any subagent opt-out before spawning subagents.",
            "",
            "Step 1a: Cross-review requests real subagents by default, but host-level authorization rules remain authoritative. Skill instructions, generated packs, and assistant-authored prompts do not count as user authorization.",
            "",
            "Step 1b: If host policy requires explicit user authorization and the current request lacks it, ask one concise authorization question and pause; do not silently fall back to same-agent review or begin the audit while authorization is pending.",
            "",
            "Step 1c: If effective assignments are non-empty, delegation is authorized, and the host exposes subagent or multi-agent tools, spawn one real subagent per effective assignment by default; this is the one reviewer per effective assignment rule.",
            "",
            "Step 1d: Treat opt-out wording such as no subagents, disable subagents, sequential review, same-agent review, 不要子代理, 不用子代理, or 顺序审查 as a request to run sequentially in the same agent context.",
            "",
            "Step 1e: If subagents are unavailable, opted out, declined, or refused after authorization, explicitly state that no subagents were spawned before running assignments sequentially.",
            "",
            "Step 2: each reviewer first performs module_review on their primary module using primary_files and module_context_index, then writes the assignment's handoff_artifact.",
            "",
            "Step 3: the same reviewer then performs listed cross_review targets, carrying forward module_review findings through each target's memory_handoff to capture cascading contract breaks.",
            "",
            "Step 4: merge findings as arbiter to deduplicate issues and finalize the architectural impact report.",
            "",
            "Do not silently simulate subagents as if they were real.",
            "If subagents are unavailable, opted out, or refused by the host platform, explicitly state that no subagents were spawned before using fallback.",
            "If fallback is required, execute assignments sequentially in the same agent context.",
            "",
            f"Changed files: {', '.join(agent_pack['changed_files']) if agent_pack['changed_files'] else 'none'}",
            f"Impact edges: {len(agent_pack['impact_edges'])}",
            f"Agent assignments: {len(agent_pack['agent_assignments'])}",
            "",
            "If there are no evidence-backed issues, say so and mention any residual test or static-analysis gaps.",
        ]
        return "\n".join(lines)

    def _normalize_manual_file(self, file_path: str) -> str:
        if os.path.isabs(file_path):
            abs_path = os.path.abspath(file_path)
        else:
            root_relative = os.path.abspath(os.path.join(self.root_dir, file_path))
            cwd_relative = os.path.abspath(file_path)
            abs_path = root_relative if os.path.exists(root_relative) or not os.path.exists(cwd_relative) else cwd_relative

        rel_path = os.path.relpath(abs_path, self.root_dir)
        if rel_path.startswith(".."):
            raise ValueError(f"Manual review file '{file_path}' is outside review root '{self.root_dir}'.")
        return rel_path.replace(os.sep, "/")

    def render_markdown(self, report: FinalReportModel, edges: list) -> str:
        """
        将 FinalReportModel 渲染成极具现代美感和清晰层次的 Markdown 报告
        """
        lines = []
        # P0-2: 为 mock/dry-run 模式增加显式高亮 Banner 警告
        if report.is_mock:
            lines.extend([
                "> [!WARNING]",
                "> **MOCK / DRY-RUN 模式警告**：本报告是因本地未配置 API Key 而触发的本地仿真报告，所含漏洞与打分为系统内置 Mock 样本，不代表对您真实代码库的 AI 审计结果。",
                ""
            ])

        lines.extend([
            f"# Cross-Review 代码交叉审查报告",
            f"**系统总体风险等级**: `{report.overall_risk.upper()}`",
            "",
            "## 架构变更摘要",
            report.summary,
            "",
            "## 模块影响路径拓扑 (Top Impact Paths)",
        ])

        if not edges:
            lines.append("未发现显著的跨模块高危影响路径。")
        else:
            lines.append("| 触发源模块 | 受波及目标模块 | 依赖耦合类型 | 风险打分 | 强制触发？ |")
            lines.append("| :--- | :--- | :--- | :--- | :--- |")
            for edge in edges:
                force_str = "YES" if edge.force_triggered else "NO"
                lines.append(f"| `{edge.from_module}` | `{edge.to_module}` | `{edge.edge_type}` | **{edge.risk_score:.2f}** | {force_str} |")
        lines.append("")

        lines.append("## 深度审查发现 (Merged Findings)")

        categories = ["blocking", "high", "medium", "low", "needs_human_review"]
        headers = {
            "blocking": "阻断级缺陷 (Blocking)",
            "high": "高风险缺陷 (High)",
            "medium": "中风险缺陷 (Medium)",
            "low": "低风险/建议项 (Low)",
            "needs_human_review": "需要人工复核 (Needs Human Review)"
        }

        total_findings = 0
        for cat in categories:
            findings_list = report.findings.get(cat, [])
            if not findings_list:
                continue

            lines.append(f"### {headers[cat]}")
            for idx, f in enumerate(findings_list):
                total_findings += 1
                lines.append(f"#### {idx+1}. 缺陷：{f.evidence}")
                lines.append(f"* **位置**: [`{f.file}:L{f.line}`](file:///{self.root_dir}/{f.file}#L{f.line})")
                lines.append(f"* **AI 置信度**: `{f.confidence:.2%}`")
                lines.append(f"* **💡 修复建议**:")
                lines.append(f"  ```text")
                lines.append(f"  {f.suggested_fix}")
                lines.append(f"  ```")
                lines.append("")

        if total_findings == 0:
            lines.append("> ✅ **优秀！主审与交叉审查均未发现明显的系统缺陷，契约与一致性良好。**")
            lines.append("")

        lines.append("> [!TIP]")
        lines.append("> 本报告基于静态拓扑图进行高风险边双向交叉复审，重点挖掘隐式契约破坏，辅助提升系统架构一致性。")

        return "\n".join(lines)

    def _load_prompt(self, filename: str) -> str:
        """
        优先加载本地项目的 prompt，不存在则回退默认模板
        """
        custom_path = os.path.join(self.root_dir, "cross_review", "prompts", filename)
        if os.path.exists(custom_path):
            with open(custom_path, "r", encoding="utf-8") as f:
                return f.read()
        
        # 兜底内置模板
        fallback_dir = os.path.dirname(__file__)
        fallback_path = os.path.join(fallback_dir, "prompts", filename)
        if os.path.exists(fallback_path):
            with open(fallback_path, "r", encoding="utf-8") as f:
                return f.read()

        raise FileNotFoundError(f"Prompt template '{filename}' not found.")
