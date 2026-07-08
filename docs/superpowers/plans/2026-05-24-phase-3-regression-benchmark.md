# Phase 3 Regression Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic regression benchmark runner that checks whether prepare-mode output matches expected impact edges, changed contracts, and downstream call-sites for fixture cases.

**Architecture:** Add `cross_review.benchmark` with a small JSON-driven runner. Each regression case is a folder containing source files and `expected.json`; the runner calls `ReviewPipeline.prepare`, reads `agent_review_pack.json`, compares expected ids against actual pack data, and returns structured pass/fail metrics. Expose it through `cross_review.cli benchmark`.

**Tech Stack:** Python 3.11, Click, pytest, JSON fixtures.

---

### Task 1: Regression Fixture And Runner

**Files:**
- Create: `examples/regression_cases/python_signature_break/src/billing/client.py`
- Create: `examples/regression_cases/python_signature_break/src/admin/panel.py`
- Create: `examples/regression_cases/python_signature_break/expected.json`
- Create: `cross_review/benchmark/__init__.py`
- Create: `cross_review/benchmark/runner.py`
- Test: `tests/test_benchmark.py`

- [ ] **Step 1: Write failing tests**

Add tests that run `BenchmarkRunner` against `examples/regression_cases` and assert:

- total cases is 1
- passed cases is 1
- expected edge `billing -> admin` is found
- expected changed contract id is found
- expected callsite id prefix is found

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_benchmark.py -q
```

Expected: import failure because `cross_review.benchmark.runner` does not exist.

- [ ] **Step 3: Implement runner**

Implement:

- `BenchmarkCaseResult`
- `BenchmarkSummary`
- `BenchmarkRunner(cases_dir).run()`

Expected JSON format:

```json
{
  "name": "python_signature_break",
  "changed_files": ["src/billing/client.py"],
  "expected_edges": [
    {
      "from_module": "billing",
      "to_module": "admin",
      "changed_contract_ids": ["python:function:src/billing/client.py:charge_user"],
      "callsite_id_prefixes": ["python:call:src/admin/panel.py:trigger_billing_override:"]
    }
  ]
}
```

- [ ] **Step 4: Verify tests pass**

Run:

```powershell
python -m pytest tests/test_benchmark.py -q
```

Expected: benchmark tests pass.

### Task 2: Benchmark CLI

**Files:**
- Modify: `cross_review/cli.py`
- Modify: `tests/test_benchmark.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing CLI test**

Add a `CliRunner` test:

```python
def test_benchmark_cli_reports_passes():
    result = CliRunner().invoke(main, ["benchmark", "--cases", REGRESSION_CASES_DIR])
    assert result.exit_code == 0
    assert "1/1 cases passed" in result.output
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_benchmark.py -q
```

Expected: missing `benchmark` command.

- [ ] **Step 3: Implement CLI command**

Add:

```python
@main.command("benchmark")
@click.option("--cases", "cases_dir", default="examples/regression_cases", type=click.Path(file_okay=False, dir_okay=True))
def benchmark_command(cases_dir):
    ...
```

Behavior:

- Print summary.
- Print each failed case and failure reason.
- Exit non-zero if any case fails.

- [ ] **Step 4: Update README**

Document:

```powershell
python -m cross_review.cli benchmark --cases examples/regression_cases
```

### Task 3: Full Verification

Run:

```powershell
python -m pytest tests/
python <codex-home>\skills\.system\skill-creator\scripts\quick_validate.py "<cross-review-skill-root>"
python -m cross_review.cli benchmark --cases examples/regression_cases
```

Expected:

- all tests pass
- skill is valid
- benchmark reports `1/1 cases passed`

---

## Self-Review

- Spec coverage: This implements the Phase 3 foundation: fixture layout, benchmark runner, CLI, and metrics. It intentionally starts with one regression case rather than the design doc's eventual 20-case target.
- Placeholder scan: No placeholders remain.
- Type consistency: `BenchmarkRunner`, `BenchmarkSummary`, and CLI output are used consistently.
