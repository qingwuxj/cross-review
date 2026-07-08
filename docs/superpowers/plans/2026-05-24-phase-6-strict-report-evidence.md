# Phase 6 Strict Report Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade high/blocking report evidence from warnings to validation errors when contract graph evidence is available.

**Architecture:** Extend `validate-report` to cross-check finding-level `changed_contract_id` and `callsite_id` against `agent_review_pack.json`. Keep medium/low findings permissive, but require high/blocking findings to cite structured evidence or explicitly mark a dynamic-boundary exception.

**Tech Stack:** Python 3.11, pytest, existing validators.

---

### Task 1: Extend Finding Schema

**Files:**
- Modify: `cross_review/schemas/models.py`
- Modify: `tests/test_validation.py`

- [ ] **Step 1: Add failing tests**

Create a high finding with no `changed_contract_id` or `callsite_id` and assert `validate_report` fails when pack has `contract_graph`.

Create a high finding with valid ids from the pack and assert `validate_report` passes.

- [ ] **Step 2: Add optional fields**

Add optional fields to `FindingModel`:

```python
changed_contract_id: Optional[str] = None
callsite_id: Optional[str] = None
dynamic_boundary_exception: Optional[str] = None
```

- [ ] **Step 3: Verify**

```powershell
python -m pytest tests/test_validation.py -q
```

Expected: schema accepts valid ids and validator still fails until strict checks are implemented.

### Task 2: Strict Validator

**Files:**
- Modify: `cross_review/validation/report_validator.py`
- Modify: `tests/test_validation.py`

- [ ] **Step 1: Implement strict checks**

For high/blocking findings:

- if `dynamic_boundary_exception` exists and evidence is non-empty, allow missing call-site with warning.
- otherwise require `changed_contract_id`.
- otherwise require `callsite_id`.
- verify ids exist in pack `contract_graph`.

- [ ] **Step 2: Verify**

```powershell
python -m pytest tests/test_validation.py -q
```

Expected: validation tests pass.

### Task 3: Prompt And README Update

**Files:**
- Modify: `cross_review/prompts/cross_review.txt`
- Modify: `cross_review/prompts/arbiter.txt`
- Modify: `SKILL.md`
- Modify: `README.md`

- [ ] **Step 1: Update prompt requirements**

Require high/blocking findings to cite:

- `changed_contract_id`
- `callsite_id`
- file/line evidence

- [ ] **Step 2: Update docs**

Explain that high/blocking reports fail validation without structured evidence.

### Task 4: Full Verification

Run:

```powershell
python -m pytest tests/
python C:\Users\86193\.codex\skills\.system\skill-creator\scripts\quick_validate.py "C:\Users\86193\Desktop\cross review\cross-review-skill"
python -m cross_review.cli benchmark --cases examples/regression_cases
```

Expected:

- all tests pass
- skill is valid
- benchmark passes

---

## Non-Goals

- Do not require strict evidence for medium/low findings yet.
- Do not require call-site ids for dynamic systems if an explicit exception is documented.

## Self-Review

- Spec coverage: Converts the design document's evidence-first policy into enforceable report validation.
- Placeholder scan: No placeholders remain.
- Type consistency: Uses `changed_contract_id`, `callsite_id`, and `dynamic_boundary_exception` consistently.
