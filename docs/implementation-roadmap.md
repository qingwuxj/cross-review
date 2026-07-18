# Cross-Review Implementation Roadmap

## Current State

Cross-Review is now an Agent-native cross-module review skill with these implemented foundations:

- Agent mode `prepare` does not require user-managed model API keys.
- `agent_review_pack.json` contains module contexts, cross-review contexts, semantic splitter protocol, agent assignments, handoff artifacts, and contract graph evidence.
- `execution_policy` directly authorizes real subagents through Cross-Review skill use. The Codex default prompt explicitly authorizes delegation; same-agent sequential review remains limited to opt-out, unavailable tools, or host refusal.
- Large-repo `prepare` avoids common timeout paths by skipping Python AST parsing for non-Python files and by switching to `auto-lite` when supported source-file count exceeds `review.auto_lite_file_threshold`.
- `prepare_diagnostics` records stage timings and source/module counts in the Agent pack; `.cross-review/prepare_diagnostics.json` is refreshed during prepare so timeout failures still leave a current-stage snapshot.
- `init-config --large-repo` writes conservative first-run settings for large repositories.
- `validate-pack` checks structure, context indexes, file references, handoff protocol, and contract/call-site references.
- `validate-report` checks final report structure, evidence fields, file paths, line numbers, and basic finding quality.
- Python contract graph v1 extracts top-level function/class contracts, changed contracts, and downstream call-sites.
- `benchmark` runs JSON-defined regression cases and compares expected edges, changed contracts, change types, and call-sites.
- Optional `cross-review.toml` configuration can cap Top-K, context size, ignored paths, analyzers, dynamic boundaries, module aliases, and package path aliases locally before host Agents spend tokens.
- `context_budget` in the Agent pack reports estimated context size, truncation markers, and configured limits.
- Full `prepare` can classify before/after signatures from Git previous-file content or `.cross-review-before` snapshots.
- `benchmark` reports aggregate hit-rate, unexpected-edge, token, and runtime metrics.
- Read-only CLI helpers can inspect environment, cases, and generated packs.
- Contract graph dispatch is split through `contracts/builder.py` and language analyzer entrypoints in `contracts/python.py`, `typescript.py`, `sql.py`, `graphql.py`, and `protobuf.py`.
- README includes short, non-exhaustive examples of an Agent review pack excerpt and final finding shape.

Historical implementation plans are kept under `docs/superpowers/` for contributor auditability. They are not required user documentation, and the current project state is authoritative in this roadmap, `README.md`, and `SKILL.md`.

Implemented phase plan archive:

- `docs/superpowers/plans/2026-05-24-phase-1-validation.md`
- `docs/superpowers/plans/2026-05-24-phase-2-contract-graph.md`
- `docs/superpowers/plans/2026-05-24-phase-3-regression-benchmark.md`
- `docs/superpowers/plans/2026-05-24-phase-4-python-regression-coverage.md`
- `docs/superpowers/plans/2026-05-24-phase-5-typescript-support.md`
- `docs/superpowers/plans/2026-05-24-phase-6-strict-report-evidence.md`
- `docs/superpowers/plans/2026-05-24-phase-7-open-source-hardening.md`
- `docs/superpowers/plans/2026-05-24-evidence-and-benchmark-hardening.md`
- `docs/superpowers/plans/2026-05-24-20-case-boundary-and-prompt-hardening.md`
- `docs/superpowers/plans/2026-05-25-usability-hardening.md`
- `docs/superpowers/plans/2026-05-25-real-fixtures-and-ux-hardening.md`
- Phase 12 analyzer modularization follow-up: direct implementation from the P1 roadmap item.

## Roadmap Principles

1. Do not add broad language support before the Python evidence model is stable.
2. Every new analysis capability must add at least one regression case.
3. Every high-confidence finding must move toward the evidence chain:

```text
changed provider contract -> downstream call-site or consumer evidence -> failure mode -> suggested fix
```

4. Agent mode must remain local and deterministic during `prepare`; semantic judgment stays with the host Agent.
5. Validation and benchmark must reject regressions before open-source release.

## Phase Status

| Phase | Name | Status | Main Output |
|---|---|---|---|
| Phase 1 | Validation Layer | Done | `validate-pack`, `validate-report` |
| Phase 2 | Contract Graph v1 | Done | Python changed contracts + call-sites |
| Phase 3 | Regression Benchmark v1 | Done | Benchmark runner + first case |
| Phase 4 | Python Regression Coverage | Done | Route, event, SQL cases |
| Phase 5 | TypeScript/JavaScript Support | Done | ES import/export and route/client evidence |
| Phase 6 | Strict Report Evidence | Done | High/blocking evidence requirements |
| Phase 7 | Open Source Hardening | Done | Docs, contributing, release checklist |
| Phase 8 | Evidence and Benchmark Hardening | Done | 10 cases, before/after signatures, tighter call-sites |
| Phase 9 | 20-Case Boundary and Prompt Hardening | Done | 20 cases, GraphQL/protobuf shallow evidence, neutral prompts |
| Phase 10 | Usability Hardening | Done | Local config, context budget metadata, Git previous-source evidence |
| Phase 11 | Real Fixtures and UX Hardening | Done | 26 cases, realistic fixtures, benchmark metrics, stricter report validation, CLI helpers |
| Phase 12 | Analyzer Modularization | Done | Compatibility wrapper, builder/language analyzer module entrypoints, README output examples |
| Phase 13 | Optional JS AST Import Adapter | Done | Parser-backed import extraction when `js-ast` is installed |
| Phase 14 | Packaging Cleanup | Done | PEP 621 metadata, editable-install CI, pinned requirements as convenience files |
| Phase 15 | External Codegraph Adapter | Done | Configured external graph import to skip built-in scout on large repos |

## Execution Order

### Phase 4: Python Regression Coverage

Expand the benchmark suite before broadening language support. Add fixed cases for:

- FastAPI-style route parameter contract break
- Event payload field rename
- SQL migration with NOT NULL column risk

Status: completed. `benchmark` now covers Python import/call-site, route, event, and DB migration boundaries.

Plan file:

- `docs/superpowers/plans/2026-05-24-phase-4-python-regression-coverage.md`

### Phase 5: TypeScript/JavaScript Support

Add first TypeScript/JavaScript static evidence support after Python scenarios are benchmarked. Start narrow:

- ES import/export
- named function export
- Express route handler
- fetch/axios call-site detection

Status: completed. Benchmark now includes TypeScript export and Express route cases with changed contract and downstream call-site evidence.

Plan file:

- `docs/superpowers/plans/2026-05-24-phase-5-typescript-support.md`

### Phase 6: Strict Report Evidence

Tighten final report validation after contract graph evidence is available. High/blocking findings should require structured evidence:

- `changed_contract_id`
- `callsite_id` or explicit dynamic-boundary exception
- valid downstream file reference

Status: completed. Vague high/blocking findings now fail `validate-report` unless they cite `changed_contract_id` and `callsite_id`, or provide a dynamic-boundary exception.

Plan file:

- `docs/superpowers/plans/2026-05-24-phase-6-strict-report-evidence.md`

### Phase 7: Open Source Hardening

Prepare the skill for public release:

- README quickstart
- API key/privacy statement
- CONTRIBUTING
- limitations
- benchmark status
- release checklist

Status: completed. README, contribution guide, privacy/API key policy, and release checklist now document the public workflow and limitations.

Plan file:

- `docs/superpowers/plans/2026-05-24-phase-7-open-source-hardening.md`

### Phase 8: Evidence and Benchmark Hardening

Raise low-token deterministic evidence quality without making `SKILL.md` heavier:

- benchmark coverage expanded from 6 to 10 cases,
- expected files can assert changed contract `change_type`,
- `.cross-review-before/<file>` snapshots enable `signature_changed` classification,
- Python class constructor and alias call-sites are tracked,
- TypeScript/JavaScript structural parsing now covers exported arrow functions and import aliases,
- cross-review context includes concise contract/call-site id evidence summaries.

Status: completed. Phase 9 has since expanded the benchmark suite to 20 cases.

Plan file:

- `docs/superpowers/plans/2026-05-24-evidence-and-benchmark-hardening.md`

### Phase 9: 20-Case Boundary and Prompt Hardening

Expand benchmark breadth while keeping default skill usage light:

- benchmark coverage expanded from 10 to 20 cases,
- Python module import aliases, keyword-only signatures, requests route calls, and SQLAlchemy-style inserts are covered,
- TypeScript default exports, path alias imports, class constructors, and axios template route calls are covered,
- shallow GraphQL field and protobuf RPC evidence extraction is covered,
- prompt templates and retry prompts use neutral audit language without alert emoji or ceremonial role wording.

Status: completed. This improves alpha confidence, but GraphQL/protobuf remain shallow extractors rather than full schema/compiler integrations.

Plan file:

- `docs/superpowers/plans/2026-05-24-20-case-boundary-and-prompt-hardening.md`

### Phase 10: Usability Hardening

Keep the open-source skill usable without increasing default token spend:

- `cross-review.toml` can configure Top-K, lite default, context line limits, consumer-file count, and semantic module aliases,
- configured `top_k` is treated as a hard cap unless `expand_critical_top_k = true`,
- Agent packs include `context_budget` with estimated tokens, truncation markers, limits, and Top-K policy,
- full `prepare` can use Git previous-file content for before/after signature classification without requiring `.cross-review-before` snapshots.

Status: completed. This improves first-run predictability and lets host Agents make cheaper routing decisions before reading all context.

Plan file:

- `docs/superpowers/plans/2026-05-25-usability-hardening.md`

### Phase 11: Real Fixtures and UX Hardening

Raise open-source credibility and day-to-day usability without increasing default analysis weight:

- benchmark coverage expanded from 20 to 26 cases,
- 6 realistic fixtures cover monorepo `apps/` + `packages/`, FastAPI + SQLAlchemy-style persistence, Express/Fastify service boundaries, GraphQL schema + frontend query, and protobuf service + generated-client wrapper,
- benchmark output includes expected edge/contract/callsite hit rates, unexpected edge count, estimated context tokens, and runtime,
- `cross-review.toml` now supports ignored paths, enabled analyzers, known dynamic boundaries, semantic module aliases, and TypeScript package path aliases,
- `validate-report` rejects high/blocking findings whose evidence is not grounded in the pack, whose file/line does not match the cited call-site, whose evidence pair does not belong to the same impact edge, or whose suggested fix is generic,
- read-only CLI helpers `doctor`, `list-cases`, `explain-pack`, and `summarize` improve first-run diagnostics and pack inspection.

Status: completed. This improves regression fidelity and user ergonomics while keeping default analyzer behavior dependency-free.

Plan file:

- `docs/superpowers/plans/2026-05-25-real-fixtures-and-ux-hardening.md`

### Phase 12: Analyzer Modularization

Reduce the maintenance risk of the former monolithic contract graph module without changing analyzer behavior:

- `cross_review/contracts/contract_graph.py` is now a compatibility import wrapper,
- `ContractGraphBuilder` lives in `cross_review/contracts/builder.py`,
- language-specific analyzer entrypoints live in `contracts/python.py`, `typescript.py`, `sql.py`, `graphql.py`, and `protobuf.py`,
- existing imports from `cross_review.contracts.contract_graph` continue to work,
- regression coverage checks the module split and all existing contract graph behavior,
- README now shows a compact `agent_review_pack.json` excerpt and a final finding example without dumping full artifacts.

Status: completed as the first P1 maintainability item.

### Phase 13: Optional JS AST Import Adapter

Add a parser-backed path without making the default skill heavier:

- `cross_review/contracts/js_ast.py` lazily uses `tree-sitter` and `tree-sitter-typescript` when the `js-ast` extra is installed,
- TypeScript call-site matching merges dependency-free regex imports with parser-backed import statement extraction,
- combined default + named imports such as `import client, { chargeUser as billUser } from "../billing/client"` are covered by optional AST routing,
- `doctor` and `analysis_config.optional_js_ast_parser` report whether the optional parser path is available,
- default behavior still falls back to dependency-free structural parsing when optional dependencies are absent.

Status: completed as an initial parser-backed import extraction path. Deeper TypeScript compiler semantics, type resolution, and framework-specific AST analysis remain future work.

### Phase 14: Packaging Cleanup

Make the project easier to install and publish:

- `pyproject.toml` now uses standard PEP 621 `[project]` metadata with setuptools,
- `cross-review` is declared in `[project.scripts]`,
- development and optional parser dependencies live in `[project.optional-dependencies]`,
- CI installs with `python -m pip install -e ".[dev]"` and runs `cross-review doctor` to verify the editable console script,
- pinned `requirements.txt` and `requirements-dev.txt` remain as reproducible local-environment convenience files rather than the primary package metadata.

Status: completed. This chooses the PEP 621 route rather than Poetry-only packaging.

### Phase 15: External Codegraph Adapter

Let large repositories reuse an existing graph index before running cross-review:

- `[project_graph] external_graph_path = "...json"` loads an external graph during `prepare`,
- supported inputs include native cross-review `project_graph.json` and a simplified `{modules: [...], dependencies: [...]}` shape,
- external graph mode skips the built-in scout scan and scout cache,
- `prepare_diagnostics.scan_mode` reports `external-graph`,
- `analysis_config.external_project_graph_path` records the configured source path,
- regression coverage proves that `prepare` does not call `ScoutScanner.scan` when the external graph is configured.

Status: completed as a generic adapter layer. A dedicated converter for any specific GitHub codegraph project can be added on top of this JSON contract later.

## Global Verification

After each phase, run:

```powershell
python -m pytest tests/
$skillValidator = if ($env:CODEX_HOME) {
  Join-Path $env:CODEX_HOME "skills/.system/skill-creator/scripts/quick_validate.py"
} else {
  Join-Path $HOME ".codex/skills/.system/skill-creator/scripts/quick_validate.py"
}
$env:PYTHONUTF8 = "1"
python $skillValidator "."
python -m cross_review.cli benchmark --cases examples/regression_cases
python -m cross_review.cli prepare --root examples/toy_api_break --files src/billing/client.py
python -m cross_review.cli validate-pack --pack examples/toy_api_break/.cross-review/agent_review_pack.json
```

## Release Bar

The project has crossed the 26-case alpha regression floor with initial realistic fixtures, but should not be described as production-grade until:

- benchmark coverage includes broader real project fixtures beyond the current 6 synthetic-realistic layouts,
- language/framework support has broader parser-backed and framework-specific coverage for high-volume ecosystems,
- high/blocking findings require structured evidence,
- benchmark is part of routine verification,
- README clearly distinguishes Agent mode from standalone mode,
- unsupported language/framework boundaries are documented,
- mock findings cannot be confused with real audit output.
