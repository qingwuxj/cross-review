# Phase 2 Contract Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first contract graph layer so prepare-mode impact edges carry changed contract ids and downstream call-site ids instead of only module-level risk.

**Architecture:** Introduce `cross_review.contracts` as a deterministic static-analysis package. It extracts Python contract surfaces and call-sites from existing `ProjectGraphModel`, marks changed contracts for changed files, and annotates `EdgeModel` plus `agent_review_pack.json` without changing standalone LLM behavior.

**Tech Stack:** Python 3.11, stdlib `ast`, Pydantic models, pytest.

---

### Task 1: Contract Graph Extraction

**Files:**
- Modify: `cross_review/schemas/models.py`
- Create: `cross_review/contracts/__init__.py`
- Create: `cross_review/contracts/contract_graph.py`
- Test: `tests/test_contract_graph.py`

- [ ] **Step 1: Write failing tests**

Add tests that create a temporary Python project with `src/billing/client.py` and `src/admin/panel.py`. Assert that the contract graph extracts:

- `python:function:src/billing/client.py:charge_user`
- a call-site from `src/admin/panel.py` that references that contract
- changed contracts when `src/billing/client.py` is in `changed_files`

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_contract_graph.py -q
```

Expected: import failure because `cross_review.contracts.contract_graph` does not exist.

- [ ] **Step 3: Implement extractor**

Implement:

- `ContractSurfaceModel`
- `ChangedContractModel`
- `CallSiteModel`
- `ContractGraphModel`
- `ContractGraphBuilder`

Keep first version Python-only:

- Top-level `FunctionDef`, `AsyncFunctionDef`, and `ClassDef` become contract surfaces.
- `from src.billing.client import charge_user` maps `charge_user(...)` calls to the billing contract.
- `from src.billing import client` maps `client.charge_user(...)` calls.
- `import billing` maps `billing.charge_user(...)` calls for flat projects.
- Changed files mark their exported function/class contracts as `changed_file_contains_export`.

- [ ] **Step 4: Verify tests pass**

Run:

```powershell
python -m pytest tests/test_contract_graph.py -q
```

Expected: contract graph tests pass.

### Task 2: Impact Edge Annotation

**Files:**
- Modify: `cross_review/schemas/models.py`
- Modify: `cross_review/pipeline.py`
- Test: `tests/test_contract_graph.py`
- Modify: `tests/test_pipeline.py` only if existing assertions need model field awareness.

- [ ] **Step 1: Write failing tests**

Add a prepare-mode test that runs on `examples/toy_api_break` and asserts:

- `agent_review_pack.json` contains top-level `contract_graph`.
- first impact edge contains `changed_contract_ids`.
- first impact edge contains `callsite_ids`.
- `cross_review_contexts[0]` contains `changed_contracts` and `downstream_call_sites`.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_contract_graph.py -q
```

Expected: missing pack fields.

- [ ] **Step 3: Annotate edges in prepare**

In `ReviewPipeline.prepare`:

- Build contract graph after `top_edges` are computed.
- Annotate each edge where `changed_contract.module == edge.from_module` and `callsite.consumer_module == edge.to_module`.
- Add `contract_graph` to `agent_pack`.
- Add `changed_contracts` and `downstream_call_sites` to each `cross_review_context`.

- [ ] **Step 4: Verify tests pass**

Run:

```powershell
python -m pytest tests/test_contract_graph.py -q
```

Expected: tests pass.

### Task 3: Validation And Docs

**Files:**
- Modify: `cross_review/validation/pack_validator.py`
- Modify: `README.md`
- Test: `tests/test_validation.py`

- [ ] **Step 1: Write failing validator test**

Add a test that corrupts an edge `changed_contract_ids` with an unknown id and expects `validate_pack` to fail.

- [ ] **Step 2: Implement validator check**

`validate_pack` should validate:

- if `contract_graph` exists, all edge `changed_contract_ids` exist in `contract_graph.changed_contracts`.
- all edge `callsite_ids` exist in `contract_graph.call_sites`.

- [ ] **Step 3: Update README**

Document that Phase 2 currently supports Python contract/call-site evidence in prepare output.

- [ ] **Step 4: Verify validation tests pass**

Run:

```powershell
python -m pytest tests/test_validation.py -q
```

Expected: validation tests pass.

### Task 4: Full Verification

Run:

```powershell
python -m pytest tests/
python C:\Users\86193\.codex\skills\.system\skill-creator\scripts\quick_validate.py "C:\Users\86193\Desktop\cross review\cross-review-skill"
python -m cross_review.cli prepare --root examples/toy_api_break --files src/billing/client.py
python -m cross_review.cli validate-pack --pack examples/toy_api_break/.cross-review/agent_review_pack.json
```

Expected:

- all tests pass
- skill is valid
- prepare succeeds
- pack validates

---

## Self-Review

- Spec coverage: Covers the first Phase 2 slice: Python contract surfaces, changed contracts, downstream call-sites, edge annotations, pack validation. It intentionally does not implement TypeScript, SQL migration contract changes, or benchmark.
- Placeholder scan: No placeholder steps remain.
- Type consistency: Model names and pack field names are consistent across tasks.
