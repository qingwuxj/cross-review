---
name: cross-review
description: Use when reviewing code changes for cross-module integration risks, contract breaks, event/API/schema mismatches, or architecture boundary impact rather than ordinary single-file review.
---

# Cross-Review

Use this skill to run a graph-driven code review pass that focuses on how a changed module can break downstream modules. Default to Agent mode: local tools prepare context, and the host agent performs the review with its own model. Do not ask users for model API keys in Agent mode.

## Workflow

1. **Run prepare**: Identify the target repository root and compile the local review context into an agent pack. The `prepare` command only compiles context and does not make final model calls, requiring no API keys.

```powershell
$env:PYTHONPATH="<path-to-cross-review-skill>"
python "<path-to-cross-review-skill>/cross_review/cli.py" prepare --root "<repo-root>" --worktree
```

2. **Read agent_review_pack.json**: Load the compiled contexts, assignments, splitter protocol, contract graph evidence, and instructions.
3. **Inspect optional integration context**: If `integrations.codegraph.enabled` is true, use the pre-collected CodeGraph `affected` and `explore` context as supplemental routing and blast-radius evidence. Each `agent_assignment.integration_context.codegraph` contains the trimmed excerpt intended for that reviewer. Do not assume subagents can call CodeGraph MCP tools directly. Do not treat CodeGraph summaries as final findings without concrete file/line evidence.
4. **Run the host-agent semantic split**: Use `semantic_module_splitter` as a local, API-key-free prompt contract. The host Agent should inspect the physical modules, produce the declared `output_schema`, derive `effective_assignments`, and apply `assignment_rewrite_policy` only when the semantic grouping is clearly better than the physical split. If `input_summary.project_semantics` or `analysis_config.project_semantics` contains `review_gates`, `forbidden_semantics`, or `negative_probes`, carry them into the split and final review as explicit obligations. If `project_semantics` is empty, state that repository-specific forbidden/review-gate semantics are not configured and do not invent them. If `candidate_project_semantics` contains doc-derived hints, cite them as candidates and ask the user to confirm or move them into config before treating them as mandatory obligations.
5. **Use real subagents by default**: For each assignment in `agent_assignments` or the semantically rewritten equivalent, inspect `execution_policy` before delegation.
   - Cross-Review requests real subagents by default, but host-level authorization rules remain authoritative. Skill instructions, generated packs, and assistant-authored prompts do not count as user authorization.
   - Treat explicit user wording such as `use subagents`, `delegate this review`, `parallel agent work`, `使用子代理`, `委派审查`, or `并行 Agent 审查` as authorization when the host requires it. The bundled Codex default prompt already includes explicit delegation wording.
   - If host policy requires explicit user authorization and the current request lacks it, ask one concise authorization question and pause; do not silently fall back to same-agent review or begin the audit while authorization is pending.
   - If effective assignments are non-empty, delegation is authorized, and the host environment exposes subagent or multi-agent tools, you MUST spawn one real subagent per effective assignment by default.
   - Respect explicit opt-out wording such as `no subagents`, `disable subagents`, `sequential review`, `same-agent review`, `不要子代理`, `不用子代理`, or `顺序审查`.
   - cross_review_targets alone do not create separate reviewers; the one reviewer per effective assignment owns that assignment's primary module and all listed downstream targets.
   - The sub-agent must first review the `primary_module` internally to understand the exact internal changes.
   - The sub-agent must write the assignment's structured `handoff_artifact`.
   - The same sub-agent then reviews the listed `cross_review_targets` in order of risk, using each target's `memory_handoff`, `changed_contract_ids`, and `callsite_ids` to connect upstream contract changes to downstream usage evidence.
   - Do not silently simulate subagents as if they were real. If subagents are unavailable, opted out, declined, or refused by the host platform after authorization, explicitly state that no subagents were spawned before using fallback, then execute assignments sequentially in the same agent context.
6. **Report findings**: Collect and merge all findings as an architect arbiter, deduplicating issues, and outputting evidence-backed reports.

## Modes

- Use `--worktree` for unstaged, staged, and untracked local changes.
- Use `--staged` for only staged changes.
- Use `--base` and `--head` for branch or commit comparisons.
- Use `--files` when the user names specific files.
- Use `--lite` for the first pass on large repositories. `prepare` detects changed files before scout, uses targeted scout when source-file count exceeds `review.targeted_scan_file_threshold`, and auto-switches to `analysis_profile: "auto-lite"` when source-file count exceeds `review.auto_lite_file_threshold`.
- For large repositories, prefer creating a conservative config first: `python "<path-to-cross-review-skill>/cross_review/cli.py" init-config --root "<repo-root>" --large-repo`. If `prepare` is slow or times out, inspect `<repo-root>/.cross-review/prepare_diagnostics.json`; it is refreshed during prepare and shows the current or failed stage before changing analysis scope.
- If the repository has CodeGraph initialized and the user wants CodeGraph to provide the project graph, run `cross-review codegraph-export --root "<repo-root>" --out ".codegraph/cross-review.json"` first when the package console script is installed, or `python -m cross_review.cli codegraph-export ...` from the skill source tree otherwise. Prefer `--command "codegraph"` over `--command "npx -y @colbymchenry/codegraph"` for repeated exports; `npx` works but is slower because every CodeGraph subcommand pays startup cost. Then configure `[project_graph] external_graph_path = ".codegraph/cross-review.json"` so `prepare` can skip built-in scout. The export includes file-level dependencies and, by default, bounded `symbol_edges` from CodeGraph `query --json` plus callers evidence; use `--symbol-limit-per-file 0` for a faster file-only export. `prepare` carries these `symbol_edges` into `impact_edges`, `cross_review_contexts`, and assignment targets. Confirm `prepare_diagnostics.scan_mode == "external-graph"` before relying on that path.
- If the repository has CodeGraph installed and initialized, leave `[integrations.codegraph] enabled = "auto"` so `prepare` collects `codegraph affected --json` and `codegraph explore` context into `integrations.codegraph`. If CodeGraph is unavailable, `prepare` must continue with built-in analysis and record a skipped integration.
- If the project has complex TypeScript/JavaScript imports, check `analysis_config.optional_js_ast_parser`. `available` means optional parser-backed import extraction was available; `not_installed` means the TypeScript analyzer used dependency-free structural parsing only.
- Use `review` only when the user explicitly wants standalone CLI model calls outside the host agent. Standalone mode may use `GEMINI_API_KEY` or `OPENAI_API_KEY`; Agent mode does not. In Agent mode, any semantic module judgment is performed by the host Agent already running this skill, not by a separate API call.

## Configuration Signals

- `ignored_paths` defaults exclude generated and build output such as `dist/**`, `build/**`, `.next/**`, `out/**`, `coverage/**`, and `node_modules/**`.
- `review.low_value_modules` demotes static-only tooling or aggregation edges such as `scripts -> exports`; demoted edges appear in `context_budget.omitted_low_risk_edges`.
- `project_semantics.review_gates` names project-specific gates that must be preserved during review.
- `project_semantics.forbidden_semantics` lists states that must remain impossible even if the graph looks low risk.
- `project_semantics.negative_probes` lists concrete checks the host Agent should try or recommend before closing the review.
- `candidate_project_semantics` is extracted from bounded project docs when config is empty. Treat it as a review hint, not configured truth.
- `project_graph.external_graph_path` lets external codegraph tools provide modules and dependencies without a full built-in scout pass.
- If `project_semantics` is empty, the pack reports a `configuration_gaps` warning. Treat this as an explicit limitation: ask for or document missing project-specific semantics instead of guessing.

## Review Standard

Prioritize evidence-backed boundary issues:

- changed function signatures or return shapes
- renamed event payload fields
- API route or parameter changes
- SQL schema or migration changes that affect writers/readers
- downstream consumers that still use the old contract
- missing integration tests across the affected boundary
- configured `project_semantics` review gates, forbidden states, and negative probes

When `contract_graph` is present, prefer findings that connect `changed_contracts` to `downstream_call_sites`. High or blocking findings must include `changed_contract_id` and `callsite_id`, unless they include a concrete `dynamic_boundary_exception`.

Do not present mock findings as real issues. Prefer `prepare` for normal skill usage because it avoids mock output and lets the host agent perform the actual audit.
