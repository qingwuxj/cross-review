# Cross-Review

Cross-Review is an open, Agent-native code review skill for finding cross-module contract breaks. It prepares local dependency, contract, and review-assignment context for host agents such as Codex or Claude Code without requiring separate model API keys in Agent mode.

Status: `v0.1-alpha`

## Quickstart

Install in editable development mode:

```powershell
python -m pip install -e ".[dev]"
```

Optional parser-backed TypeScript/JavaScript import extraction:

```powershell
python -m pip install -e ".[js-ast]"
```

`requirements.txt` and `requirements-dev.txt` are pinned convenience files for reproducible local environments; package metadata lives in standard PEP 621 fields in `pyproject.toml`.

Prepare a lightweight Agent review pack:

```powershell
python -m cross_review.cli prepare --root examples/toy_api_break --files src/billing/client.py --lite
```

Validate the generated pack:

```powershell
python -m cross_review.cli validate-pack --pack examples/toy_api_break/.cross-review/agent_review_pack.json
```

Read:

```text
examples/toy_api_break/.cross-review/agent_review_pack.json
examples/toy_api_break/.cross-review/agent_review_instructions.md
```

Then let the host Agent follow `agent_review_instructions.md`.

In Codex, select the Cross-Review skill's bundled default prompt when you want subagent review. That prompt explicitly asks Codex to delegate effective assignments, satisfying hosts that require user-authored subagent authorization without making users remember extra wording. If Cross-Review is invoked implicitly from a request that does not authorize delegation, the Agent asks one concise authorization question and pauses instead of silently downgrading to same-agent review. Hosts that do not require separate authorization can delegate immediately.

## What It Generates

`prepare` writes an Agent review pack containing:

- `agent_assignments`: one reviewer task per changed module.
- `execution_policy`: requests real subagents by default while deferring authorization to the user request and host policy; semantic split `effective_assignments` drive one reviewer per assignment, and missing authorization triggers one question rather than silent fallback.
- `cross_review_targets`: downstream modules to inspect in risk order.
- `handoff_artifact`: structured module-review memory.
- `memory_handoff`: instructions for carrying module findings into cross-review.
- `semantic_module_splitter`: host-Agent protocol for optional semantic grouping.
- `contract_graph`: changed contracts and downstream call-sites when full analysis is enabled.
- `context_budget`: estimated context size, configured limits, truncation markers, and Top-K policy.
- `prepare_diagnostics`: stage timings and source/module counts for large-repo timeout diagnosis.

`prepare` also refreshes `.cross-review/prepare_diagnostics.json` while it runs. If a large repository times out before `agent_review_pack.json` is written, inspect that standalone file to see the last active stage and partial timings.

Short pack excerpt:

```json
{
  "mode": "agent",
  "requires_external_api_key": false,
  "execution_policy": {
    "subagents_default_when_available": true,
    "subagents_requested_by_cross_review": true,
    "subagents_required_when_authorized_and_available": true,
    "authorization_source": "user_request_or_host_policy",
    "ask_once_if_host_requires_explicit_authorization": true,
    "missing_authorization_action": "ask_once_and_pause"
  },
  "changed_files": ["src/billing/client.py"],
  "agent_assignments": [
    {
      "agent_id": "module-billing-reviewer",
      "primary_module": "billing",
      "cross_review_targets": [
        {
          "target_module": "admin",
          "review_question": "Will changes in billing break admin?"
        }
      ]
    }
  ],
  "context_budget": {
    "estimated_context_tokens": 419,
    "top_k_policy": {"configured_top_k": 3, "actual_edges": 1}
  },
  "prepare_diagnostics": {
    "analysis_profile": "lite",
    "scanned_file_count": 18,
    "timings_ms": {
      "scan_files_ms": 5,
      "contract_graph_ms": 0,
      "total_prepare_ms": 42
    }
  }
}
```

Example final finding shape from a host-Agent review:

```json
{
  "severity": "blocking",
  "file": "src/admin/panel.py",
  "line": 6,
  "changed_contract_id": "python:function:src/billing/client.py:charge_user",
  "callsite_id": "python:call:src/admin/panel.py:trigger_billing_override:6",
  "evidence": "billing changed charge_user(...) while admin still calls the old contract.",
  "suggested_fix": "Update admin to call charge_user with the new argument name and add a boundary test."
}
```

Default full mode:

```powershell
python -m cross_review.cli prepare --root <repo-root> --worktree
```

Lightweight mode:

```powershell
python -m cross_review.cli prepare --root <repo-root> --worktree --lite
```

Use `--lite` for first-run skill usage or very large repositories. It skips detailed contract graph evidence but still creates assignments and review instructions.

## Configuration

Optional local configuration lives in `cross-review.toml`, `.cross-review.toml`, or `.cross-review/config.toml`. It uses only local parsing and does not require API keys.

```toml
[review]
top_k = 2
lite = false
auto_lite_file_threshold = 1000
targeted_scan_file_threshold = 2000
enabled_analyzers = ["python", "sql", "typescript", "graphql", "protobuf"]
# If top_k is configured, it is treated as a hard token-budget cap by default.
# Set this true to let critical modules expand the edge count.
expand_critical_top_k = false
# Static tooling or aggregation modules are demoted unless runtime evidence is present.
low_value_modules = ["scripts", "exports", "dist", "build", "generated", "coverage"]

[context]
max_context_lines = 120
max_diff_lines = 120
max_consumer_files = 2
target_context_tokens = 10000
token_estimate_chars_per_token = 4
ignored_paths = ["generated/**", "vendor/**"]
known_dynamic_boundaries = ["webhook:stripe"]

[module_aliases]
billing = ["billing_api", "billing_core"]

[path_aliases]
"#billing/" = "src/billing/"

[integrations.codegraph]
# auto uses CodeGraph when the CLI is installed and .codegraph/ exists.
enabled = "auto"
command = "codegraph"
timeout_seconds = 20
max_explore_chars = 12000
affected_depth = 5

[project_semantics]
# Fill these with repository-specific invariants. Empty values are reported
# as a configuration gap because static graph extraction cannot infer them.
review_gates = ["review-gate"]
forbidden_semantics = ["Forbidden rows must not render as allowed fallback states."]
negative_probes = ["Create a forbidden review-gate fixture and verify it remains blocked."]
```

The generated pack reports token budget choices under `context_budget`, so host Agents can choose `--lite`, targeted full mode, or fewer downstream edges before spending review tokens. Low-value static edges that fall below the risk threshold are recorded in `context_budget.omitted_low_risk_edges` instead of being silently mixed into Top-K.

`project_semantics` is the escape hatch for repository-specific review rules that static graph extraction cannot infer. Host Agents must preserve those entries during semantic splitting and final review, especially negative probes for forbidden states, review gates, or product-specific invariants. If this section is empty, the pack reports a `configuration_gaps` warning and the Agent must state that project-specific forbidden/review-gate semantics were not configured.

When `project_semantics` is empty, `prepare` also scans bounded project docs such as `AGENTS.md`, `README.md`, `docs/**/*.md`, and `.github/**/*.md` for `candidate_project_semantics`. These are doc-derived hints only: the host Agent should cite them as candidates and ask the user to confirm or move them into config before treating them as mandatory review obligations.

For large repositories, `prepare` detects changed files first, then switches to targeted scout when the supported source-file count exceeds `review.targeted_scan_file_threshold`. Targeted scout parses changed modules and direct textual consumers instead of every source file. `prepare` also switches to `auto-lite` when source-file count exceeds `review.auto_lite_file_threshold`; auto-lite keeps module and edge routing, but skips full contract graph extraction to avoid large-repo timeouts. Set either threshold to `0` to disable that guard.

For SQL migrations, DB edges with concrete downstream writer evidence stay high priority. Static-only DB import edges without `db_shared` call-site evidence are demoted into omitted low-risk edges, which prevents indirect API imports from displacing the actual writer module.

Generate a conservative large-repo config:

```powershell
python -m cross_review.cli init-config --root <repo-root> --large-repo
```

This writes the following starting point:

```toml
[review]
top_k = 1
auto_lite_file_threshold = 500
targeted_scan_file_threshold = 500
enabled_analyzers = ["python", "sql"]
low_value_modules = ["scripts", "exports", "dist", "build", "generated", "coverage"]

[context]
max_context_lines = 80
max_consumer_files = 1
ignored_paths = ["generated/**", "dist/**", "build/**", "vendor/**", "coverage/**", "node_modules/**", "tests/**", "test/**", "__tests__/**"]

[project_semantics]
# Fill these before relying on forbidden/review-gate semantics.
review_gates = []
forbidden_semantics = []
negative_probes = []
```

Use `--lite` for the fastest first pass:

```powershell
python -m cross_review.cli prepare --root <repo-root> --worktree --lite
```

If `prepare` is still slow or times out, inspect `.cross-review/prepare_diagnostics.json`. It reports `scan_mode`, `source_file_count`, `scanned_file_count`, and skipped file counts. High `scan_files_ms` usually means ignored paths should be tighter; high `contract_graph_ms` means use `--lite`, lower `auto_lite_file_threshold`, or narrow `enabled_analyzers`.

### Optional CodeGraph context

Cross-Review can collect supplemental context from CodeGraph when the CodeGraph CLI is installed and the target repository has been initialized with `codegraph init`. This is an optional peer-tool integration; do not vendor CodeGraph or commit `.codegraph/codegraph.db` into this repository.

```powershell
npx @colbymchenry/codegraph
cd <repo-root>
codegraph init
python -m cross_review.cli prepare --root . --worktree
```

With the default config, `prepare` runs in auto mode:

```toml
[integrations.codegraph]
enabled = "auto"
command = "codegraph"
timeout_seconds = 20
max_explore_chars = 12000
affected_depth = 5
```

Prefer a globally available `codegraph` command for repeated exports. If CodeGraph is not installed globally, point `command` at the package runner:

```toml
[integrations.codegraph]
enabled = "auto"
command = "npx -y @colbymchenry/codegraph"
```

When available, generated packs include `integrations.codegraph` and `.cross-review/codegraph_context.json` with `codegraph status`, `codegraph affected --json`, and `codegraph explore` output. Each `agent_assignment` also gets `integration_context.codegraph` with a trimmed reviewer-local excerpt. Host Agents should use this as supplemental assignment routing and blast-radius context, while still grounding final findings in concrete files, lines, contract ids, and call-site ids.

Set `enabled = false` to disable the integration, or `enabled = true` to record an error if CodeGraph is expected but unavailable.

To use CodeGraph as the project graph source instead of only supplemental context, export a Cross-Review-compatible graph:

```powershell
cross-review codegraph-export `
  --root . `
  --out .codegraph/cross-review.json `
  --command "codegraph" `
  --symbol-limit-per-file 2 `
  --caller-limit 20 `
  --query-limit 10
```

Then configure:

```toml
[project_graph]
external_graph_path = ".codegraph/cross-review.json"
```

`codegraph-export` uses CodeGraph `files --json`, `node --file --symbols-only`, `query --json`, and bounded `callers --json` output to create Cross-Review's simplified `{modules, dependencies}` graph. Dependency entries can include `symbol_edges` with provider symbol, qualified name, provider file/line, consumer file, and caller line evidence. Caller and query results are cached inside one export run, and exports run through `npx` record a performance warning because every CodeGraph subcommand pays package-runner startup cost. Use `--max-files`, `--symbol-limit-per-file`, `--caller-limit`, and `--query-limit` to cap export time on large repositories; set `--symbol-limit-per-file 0` for file-only edges. When this file is configured as `[project_graph].external_graph_path`, `prepare` carries `symbol_edges` into `impact_edges`, `cross_review_contexts`, and assignment targets.

### External graph import

If your repository already has an exported graph, point cross-review at that JSON graph to skip the built-in scout stage:

```toml
[project_graph]
external_graph_path = ".codegraph/cross-review.json"
```

Supported JSON shapes are the native `.cross-review/project_graph.json` format, or a simplified export:

```json
{
  "name": "my-service",
  "modules": [
    { "name": "billing", "files": ["src/billing/client.py"] },
    { "name": "admin", "files": ["src/admin/panel.py"] }
  ],
  "dependencies": [
    {
      "from": "billing",
      "to": "admin",
      "type": "static_import",
      "details": "import edge",
      "consumer_files": ["src/admin/panel.py"],
      "provider_files": ["src/billing/client.py"]
    }
  ]
}
```

When this is active, `prepare_diagnostics.scan_mode` is `external-graph` and `analysis_config.external_project_graph_path` records the configured path.

## Agent Mode vs Standalone Mode

Agent mode is the recommended mode:

```powershell
python -m cross_review.cli prepare --root <repo-root> --worktree
```

It runs locally and does not require `OPENAI_API_KEY`, `GEMINI_API_KEY`, or other model provider keys. The host Agent performs the semantic review with its own runtime.

Standalone mode is optional:

```powershell
python -m cross_review.cli review --root <repo-root> --worktree
```

Standalone mode may use `OPENAI_API_KEY` or `GEMINI_API_KEY`. If no supported key is configured, the local LLM client may fall back to mock output. Mock output is marked with `"is_mock": true` and must not be treated as a real audit.

More detail: [docs/privacy-and-api-keys.md](docs/privacy-and-api-keys.md).

## Validation

Validate a pack before handing it to a host Agent:

```powershell
python -m cross_review.cli validate-pack --pack <repo-root>/.cross-review/agent_review_pack.json
```

Validate a final report:

```powershell
python -m cross_review.cli validate-report `
  --pack <repo-root>/.cross-review/agent_review_pack.json `
  --report <repo-root>/.cross-review/final_report.json
```

High or blocking findings must cite `changed_contract_id` and `callsite_id`, unless they include a concrete `dynamic_boundary_exception`. `validate-report` also checks that cited evidence comes from the pack, file/line is close to the cited call-site, the contract/call-site pair belongs to the same impact edge, and the suggested fix is specific enough to act on.

## Benchmark

Run fixed regression cases:

```powershell
python -m cross_review.cli benchmark --cases examples/regression_cases
```

Current benchmark coverage:

- Python signature break
- Python alias import call-site
- Python module import alias call-site
- Python class constructor call-site
- Python keyword-only signature break
- Python route boundary
- Python requests route call-site
- Python event boundary
- SQL NOT NULL migration
- SQLAlchemy-style `insert("table")` writer
- TypeScript named function export call-site
- TypeScript exported arrow function call-site
- TypeScript default export/import call-site
- TypeScript aliased import call-site
- TypeScript path alias import call-site
- TypeScript class constructor call-site
- Express route / fetch boundary
- Axios template route call-site
- GraphQL field read
- Protobuf RPC method call-site
- Realistic monorepo `apps/` + `packages/`
- Realistic FastAPI + SQLAlchemy-style persistence
- Realistic Express route + service layer
- Realistic Fastify route + React/fetch frontend
- Realistic GraphQL schema + frontend query
- Realistic protobuf service + generated-client wrapper

Benchmark output includes aggregate quality metrics:

- `expected_edge_hit_rate`
- `changed_contract_hit_rate`
- `callsite_hit_rate`
- `unexpected_edges_count`
- `estimated_context_tokens`
- `runtime_ms`

## CLI Inspection

Read-only helper commands:

```powershell
python -m cross_review.cli doctor --root <repo-root>
python -m cross_review.cli list-cases --cases examples/regression_cases
python -m cross_review.cli explain-pack --pack <repo-root>/.cross-review/agent_review_pack.json
python -m cross_review.cli summarize --pack <repo-root>/.cross-review/agent_review_pack.json
```

## Current Capabilities

- Path-based physical module splitting.
- Python import dependency detection.
- Python route, event, and SQL migration evidence for selected patterns.
- SQLAlchemy-style `__tablename__` class mapping for constructor call-sites that write rows affected by SQL column changes.
- Before/after signature classification when Git previous-file content or `.cross-review-before/<file>` snapshots are available.
- Narrow TypeScript/JavaScript structural evidence for named/default exports, arrow exports, import aliases, class constructors, Express/Fastify routes, and fetch/axios calls.
- Optional `js-ast` parser-backed import statement extraction for combined default + named imports before falling back to dependency-free regex parsing.
- Configured TypeScript package path aliases for import matching.
- Shallow GraphQL field and protobuf RPC evidence extraction for direct source/query usage.
- Optional external project graph import for repositories that already maintain a codegraph index.
- Agent assignment generation.
- Structured handoff between module review and downstream cross-review.
- Pack and report validation.
- Regression benchmark runner with 26 fixed cases, including 6 realistic project-layout fixtures and aggregate quality metrics.
- Modular contract analyzer entrypoints under `cross_review/contracts/` for Python, TypeScript, optional JS AST imports, SQL, GraphQL, and protobuf evidence.

## Known Limitations

- This is alpha software.
- Default TypeScript/JavaScript support is dependency-free structural parsing. The optional `js-ast` extra improves import statement extraction, but is not a complete TypeScript compiler or type checker.
- GraphQL and protobuf/RPC support is shallow evidence extraction, not schema validation or code generation analysis.
- Go, Rust, Java, complex tsconfig path alias matrices, and full ORM query semantics are not supported yet.
- Before/after contract classification requires a Git-backed review or `.cross-review-before` snapshots; without that, changed contracts fall back to changed-file evidence.
- Module splitting is primarily path-based; semantic grouping is performed by the host Agent using the included protocol.
- Large repositories may need `--lite` first, then targeted full-mode runs.
- Benchmark coverage has reached 26 cases with 6 realistic fixtures, but is not broad enough for production-grade recall claims. The next target is broader real-project validation and deeper framework coverage.

## Development

Run tests:

```powershell
python -m pytest tests/
```

Run skill validation:

```powershell
$skillValidator = if ($env:CODEX_HOME) {
  Join-Path $env:CODEX_HOME "skills/.system/skill-creator/scripts/quick_validate.py"
} else {
  Join-Path $HOME ".codex/skills/.system/skill-creator/scripts/quick_validate.py"
}
$env:PYTHONUTF8 = "1"
python $skillValidator "."
```

The skill validator is a Codex-specific maintainer check. If you do not have Codex installed, run the portable local checks instead:

```powershell
python -m pytest tests/
python -m cross_review.cli benchmark --cases examples/regression_cases
python -m cross_review.cli prepare --root examples/toy_api_break --files src/billing/client.py --lite
python -m cross_review.cli validate-pack --pack examples/toy_api_break/.cross-review/agent_review_pack.json
```

Run release checks:

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
python -m cross_review.cli prepare --root examples/toy_api_break --files src/billing/client.py --lite
python -m cross_review.cli validate-pack --pack examples/toy_api_break/.cross-review/agent_review_pack.json
```

Documentation index: [docs/README.md](docs/README.md)

Contribution rules: [CONTRIBUTING.md](CONTRIBUTING.md)

Release checklist: [docs/release-checklist.md](docs/release-checklist.md)

Implementation roadmap: [docs/implementation-roadmap.md](docs/implementation-roadmap.md)

## License

MIT. See [LICENSE](LICENSE).
