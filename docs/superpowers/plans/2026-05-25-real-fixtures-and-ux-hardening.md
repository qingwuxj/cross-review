# Real Fixtures And UX Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Cross-Review from toy-only confidence toward open-source usability by adding realistic fixtures, richer benchmark metrics, broader config, stricter report validation, and small CLI inspection commands.

**Architecture:** Keep default analysis local and dependency-free. Add larger fixtures under the existing benchmark runner, extend the runner with aggregate metrics, pass config into scanner/contract graph, and add read-only CLI helpers over generated packs.

**Tech Stack:** Python 3.10+, pytest, Click, GitPython, standard-library TOML parsing.

---

### Task 1: Real-Project Benchmark Fixtures

**Files:**
- Create: `examples/regression_cases/real_monorepo_packages/**`
- Create: `examples/regression_cases/real_fastapi_sqlalchemy/**`
- Create: `examples/regression_cases/real_express_service/**`
- Create: `examples/regression_cases/real_graphql_frontend/**`
- Create: `examples/regression_cases/real_proto_wrapper/**`
- Test: `tests/test_benchmark.py`

- [x] **Step 1: Add a failing benchmark count test**

Assert the benchmark contains at least 25 cases and includes all five real fixtures.

- [x] **Step 2: Add realistic fixtures**

Each fixture includes `expected.json`, provider code, and downstream consumer code.

- [x] **Step 3: Run benchmark**

Run: `python -m cross_review.cli benchmark --cases examples/regression_cases`
Expected: all cases pass.

### Task 2: Benchmark Metrics

**Files:**
- Modify: `cross_review/benchmark/runner.py`
- Modify: `cross_review/cli.py`
- Test: `tests/test_benchmark.py`

- [x] **Step 1: Add failing metrics tests**

Assert `BenchmarkSummary.metrics` includes expected edge hit rate, unexpected edge count, changed-contract hit rate, callsite hit rate, context tokens, and runtime.

- [x] **Step 2: Implement metrics**

Collect expected/actual counts while checking each case.

- [x] **Step 3: Print metrics in CLI**

Keep pass/fail output, append concise metric lines.

### Task 3: Config Coverage

**Files:**
- Modify: `cross_review/config.py`
- Modify: `cross_review/scout.py`
- Modify: `cross_review/contracts/contract_graph.py`
- Modify: `cross_review/pipeline.py`
- Test: `tests/test_config.py`

- [x] **Step 1: Add failing config tests**

Cover ignored paths, enabled analyzers, known dynamic boundaries, and package path aliases.

- [x] **Step 2: Wire ignored paths into scanner and changed files**

Ignored paths must not enter module graph or review changed files.

- [x] **Step 3: Wire enabled analyzers into contract graph**

Disabled analyzers must skip their contract surfaces/call-sites.

- [x] **Step 4: Wire path aliases into TypeScript import matching**

Alias imports should resolve through configured path prefixes.

- [x] **Step 5: Emit dynamic boundaries in pack**

Expose configured dynamic boundaries for host Agent/report validation context.

### Task 4: Report Quality Validation

**Files:**
- Modify: `cross_review/validation/report_validator.py`
- Test: `tests/test_validation.py`

- [x] **Step 1: Add failing validation tests**

Reject high/blocking findings whose evidence is absent from pack evidence, whose callsite file/line do not match the cited callsite, or whose suggested fix is generic.

- [x] **Step 2: Implement evidence index checks**

Use pack contexts, changed contracts, and call-sites as the evidence source.

- [x] **Step 3: Run validation tests**

Run: `python -m pytest tests/test_validation.py -q`
Expected: PASS.

### Task 5: CLI UX

**Files:**
- Modify: `cross_review/cli.py`
- Test: `tests/test_pipeline.py`

- [x] **Step 1: Add failing CLI tests**

Cover `doctor`, `list-cases`, `explain-pack`, and `summarize`.

- [x] **Step 2: Implement read-only commands**

Commands should inspect existing files and exit non-zero on missing required artifacts.

- [x] **Step 3: Run CLI tests**

Run: `python -m pytest tests/test_pipeline.py -q`
Expected: PASS.

### Task 6: Documentation and Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/release-checklist.md`

- [x] **Step 1: Document implemented scope**

Update benchmark count, config schema, metrics, and CLI helpers.

- [x] **Step 2: Full verification**

Run:

```powershell
python -m pytest tests/
python -m cross_review.cli benchmark --cases examples/regression_cases
python -m cross_review.cli prepare --root examples/toy_api_break --files src/billing/client.py
python -m cross_review.cli validate-pack --pack examples/toy_api_break/.cross-review/agent_review_pack.json
python C:\Users\86193\.codex\skills\.system\skill-creator\scripts\quick_validate.py "C:\Users\86193\Desktop\cross review\cross-review-skill"
```

Expected: all commands pass.
