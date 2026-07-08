# Phase 1 Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Phase 1 validation layer from `docs/rigorous-cross-review-design.md`: pack validation, report validation, and CLI commands that reject structurally invalid Agent review artifacts.

**Architecture:** Add a small `cross_review.validation` package with pure-Python validators returning structured results. Keep validation separate from `pipeline.py` so `prepare` stays deterministic and existing standalone review behavior remains unchanged. Expose validation through `cross_review.cli validate-pack` and `cross_review.cli validate-report`.

**Tech Stack:** Python 3.11, Click, pytest, existing JSON/Pydantic models where useful.

---

### Task 1: Pack Validator

**Files:**
- Create: `cross_review/validation/__init__.py`
- Create: `cross_review/validation/pack_validator.py`
- Test: `tests/test_validation.py`

- [ ] **Step 1: Write failing tests**

Add tests that build a real pack with `ReviewPipeline.prepare`, then assert:

```python
from cross_review.validation.pack_validator import validate_pack

def test_validate_pack_accepts_prepare_output(tmp_path):
    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    pack_path = ReviewPipeline(root_dir=project_path, cache_dir=".cross-review").prepare(
        manual_files=["src/billing/client.py"]
    )

    result = validate_pack(pack_path)

    assert result.valid is True
    assert result.errors == []

def test_validate_pack_rejects_missing_cross_review_context_index(tmp_path):
    pack = _load_prepare_pack()
    pack["agent_assignments"][0]["cross_review_targets"][0]["cross_review_context_index"] = 99
    path = _write_json(tmp_path, "bad-pack.json", pack)

    result = validate_pack(path)

    assert result.valid is False
    assert any("cross_review_context_index" in error for error in result.errors)

def test_validate_pack_rejects_missing_handoff_artifact(tmp_path):
    pack = _load_prepare_pack()
    del pack["agent_assignments"][0]["handoff_artifact"]
    path = _write_json(tmp_path, "bad-pack.json", pack)

    result = validate_pack(path)

    assert result.valid is False
    assert any("handoff_artifact" in error for error in result.errors)
```

- [ ] **Step 2: Verify tests fail**

Run:

```powershell
python -m pytest tests/test_validation.py -q
```

Expected: import failure because `cross_review.validation.pack_validator` does not exist.

- [ ] **Step 3: Implement minimal pack validator**

Create `ValidationResult` and `validate_pack(pack_path)` with these checks:

- JSON file loads.
- Required top-level keys exist.
- `project_root` exists.
- `project_graph_path` exists.
- every `module_context_index` is within `module_contexts`.
- every `cross_review_context_index` is within `cross_review_contexts`.
- every assignment has `handoff_artifact`.
- every target with `memory_handoff.source_artifact_id` matches the assignment artifact id.
- every `primary_files` item exists under `project_root`; missing files are errors.

- [ ] **Step 4: Verify tests pass**

Run:

```powershell
python -m pytest tests/test_validation.py -q
```

Expected: pack validator tests pass.

### Task 2: Report Validator

**Files:**
- Create: `cross_review/validation/report_validator.py`
- Modify: `tests/test_validation.py`

- [ ] **Step 1: Write failing tests**

Add tests for `validate_report(pack_path, report_path)`:

```python
from cross_review.validation.report_validator import validate_report

def test_validate_report_rejects_high_finding_without_evidence(tmp_path):
    pack_path = _prepare_pack_path()
    report = {
        "overall_risk": "high",
        "summary": "bad report",
        "is_mock": False,
        "findings": {
            "blocking": [],
            "high": [{
                "severity": "high",
                "confidence": 0.9,
                "file": "src/admin/panel.py",
                "line": 1,
                "evidence": "",
                "suggested_fix": "fix it"
            }],
            "medium": [],
            "low": [],
            "needs_human_review": []
        }
    }
    report_path = _write_json(tmp_path, "bad-report.json", report)

    result = validate_report(pack_path, report_path)

    assert result.valid is False
    assert any("evidence" in error for error in result.errors)

def test_validate_report_rejects_nonexistent_finding_file(tmp_path):
    pack_path = _prepare_pack_path()
    report = _valid_report_with_file("src/missing.py")
    report_path = _write_json(tmp_path, "bad-report.json", report)

    result = validate_report(pack_path, report_path)

    assert result.valid is False
    assert any("does not exist" in error for error in result.errors)
```

- [ ] **Step 2: Verify tests fail**

Run:

```powershell
python -m pytest tests/test_validation.py -q
```

Expected: import failure because `cross_review.validation.report_validator` does not exist.

- [ ] **Step 3: Implement minimal report validator**

Implement:

- Load and validate pack first using `validate_pack`.
- Load report JSON.
- Validate with `FinalReportModel`.
- For every finding in every severity bucket:
  - evidence must be non-empty.
  - suggested_fix must be non-empty.
  - line must be positive.
  - file must exist under pack `project_root`.
  - high/blocking findings should warn if they lack `changed_contract_id` or `callsite_id`, but not fail yet because current report schema does not require those fields.

- [ ] **Step 4: Verify tests pass**

Run:

```powershell
python -m pytest tests/test_validation.py -q
```

Expected: report validator tests pass.

### Task 3: CLI Commands

**Files:**
- Modify: `cross_review/cli.py`
- Modify: `tests/test_validation.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing CLI tests**

Add tests using `CliRunner`:

```python
def test_validate_pack_cli_returns_zero_for_valid_pack():
    pack_path = _prepare_pack_path()

    result = CliRunner().invoke(main, ["validate-pack", "--pack", pack_path])

    assert result.exit_code == 0
    assert "Pack is valid" in result.output

def test_validate_report_cli_returns_nonzero_for_invalid_report(tmp_path):
    pack_path = _prepare_pack_path()
    report = _valid_report_with_file("src/missing.py")
    report_path = _write_json(tmp_path, "bad-report.json", report)

    result = CliRunner().invoke(main, ["validate-report", "--pack", pack_path, "--report", report_path])

    assert result.exit_code != 0
    assert "does not exist" in result.output
```

- [ ] **Step 2: Verify tests fail**

Run:

```powershell
python -m pytest tests/test_validation.py -q
```

Expected: Click reports missing commands.

- [ ] **Step 3: Implement CLI commands**

Add:

```python
@main.command("validate-pack")
@click.option("--pack", "pack_path", required=True, type=click.Path(dir_okay=False))
def validate_pack_command(pack_path):
    ...

@main.command("validate-report")
@click.option("--pack", "pack_path", required=True, type=click.Path(dir_okay=False))
@click.option("--report", "report_path", required=True, type=click.Path(dir_okay=False))
def validate_report_command(pack_path, report_path):
    ...
```

Behavior:

- Exit 0 when valid.
- Raise `click.ClickException` with joined errors when invalid.
- Print warnings without failing.

- [ ] **Step 4: Update README**

Document:

```powershell
python -m cross_review.cli validate-pack --pack .cross-review/agent_review_pack.json
python -m cross_review.cli validate-report --pack .cross-review/agent_review_pack.json --report .cross-review/final_report.json
```

- [ ] **Step 5: Verify tests pass**

Run:

```powershell
python -m pytest tests/test_validation.py tests/test_pipeline.py -q
```

Expected: validation and existing pipeline tests pass.

### Task 4: Full Verification

**Files:**
- No new files.

- [ ] **Step 1: Run all tests**

```powershell
python -m pytest tests/
```

Expected: all tests pass.

- [ ] **Step 2: Run skill validator**

```powershell
python <codex-home>\skills\.system\skill-creator\scripts\quick_validate.py "<cross-review-skill-root>"
```

Expected: `Skill is valid!`

- [ ] **Step 3: Run prepare smoke test**

```powershell
python -m cross_review.cli prepare --root examples/toy_api_break --files src/billing/client.py
```

Expected: agent pack is generated.

- [ ] **Step 4: Run validation smoke test**

```powershell
python -m cross_review.cli validate-pack --pack examples/toy_api_break/.cross-review/agent_review_pack.json
```

Expected: `Pack is valid.`

---

## Self-Review

- Spec coverage: This plan implements Phase 1 from the design doc: pack validator, report validator, CLI exposure, and docs. It intentionally does not implement Phase 2 contract graph or benchmark.
- Placeholder scan: No placeholder steps remain; each task names files, commands, and expected results.
- Type consistency: `ValidationResult`, `validate_pack`, and `validate_report` are used consistently across tests and CLI.
