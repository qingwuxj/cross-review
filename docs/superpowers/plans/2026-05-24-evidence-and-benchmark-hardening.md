# Evidence and Benchmark Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand low-token regression coverage and improve deterministic evidence so host Agents read less irrelevant code while receiving stronger contract/call-site signals.

**Architecture:** Keep `SKILL.md` and default `prepare --lite` lean. Add benchmark cases as filesystem fixtures, then improve `contract_graph` with before/after-aware changed contracts, tighter call-site matching, and optional TypeScript/JavaScript structural parsing without forcing host Agents to ingest large docs.

**Tech Stack:** Python 3.11, pytest, Click, Pydantic, stdlib `ast`, deterministic text/brace parsing for TS/JS until a maintained optional parser is introduced.

---

### Task 1: Regression Coverage 6 to 10

**Files:**
- Create: `examples/regression_cases/python_alias_import_signature_break/**`
- Create: `examples/regression_cases/python_class_constructor_break/**`
- Create: `examples/regression_cases/ts_arrow_export_break/**`
- Create: `examples/regression_cases/ts_import_alias_call_break/**`
- Modify: `tests/test_benchmark.py`

- [x] Add four focused fixtures with `expected.json` including `changed_files`, `expected_edges`, changed contract ids, and call-site prefixes.
- [x] Run `python -m pytest tests/test_benchmark.py -q` and confirm failure while total cases still expect 10.
- [x] Implement only scanner/contract behavior required by the new fixtures.
- [x] Run `python -m pytest tests/test_benchmark.py -q` and confirm all 10 pass.

### Task 2: Before/After Changed Contract Classification

**Files:**
- Modify: `cross_review/schemas/models.py`
- Modify: `cross_review/contracts/contract_graph.py`
- Test: `tests/test_contract_graph.py`

- [x] Add optional `previous_signature`, `current_signature`, and `diff_summary` fields to `ChangedContractModel`.
- [x] Teach `ContractGraphBuilder` to read before snapshots from `.cross-review-before/<changed-file>` when present.
- [x] Classify changed exports as `signature_changed` when before/current signatures differ, otherwise keep `changed_file_contains_export`.
- [x] Add a regression case with `.cross-review-before` proving the stricter classification.

### Task 3: Tighter Downstream Call-Site Evidence

**Files:**
- Modify: `cross_review/contracts/contract_graph.py`
- Modify: `cross_review/context_pack.py`
- Test: `tests/test_contract_graph.py`

- [x] Preserve call-site ids and line evidence for imported aliases and route/event/db consumers.
- [x] Include contract evidence ids in cross-review context metadata while keeping source snippets folded.
- [x] Add tests for alias imports and class constructor calls.

### Task 4: TypeScript/JavaScript Structural Parsing

**Files:**
- Modify: `cross_review/contracts/contract_graph.py`
- Test: `tests/test_contract_graph.py`
- Test: `tests/test_benchmark.py`

- [x] Replace narrow TS function regex with a small brace-aware structural parser for exported function declarations, exported const arrow functions, exported classes, named imports, and import aliases.
- [x] Keep the parser dependency-free in default install.
- [x] Add benchmark cases for exported arrow functions and aliased imports.

### Task 5: Documentation and Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/release-checklist.md`

- [x] Update benchmark count and remaining limitations.
- [x] Confirm CI, requirements, and release checklist mention the same commands.
- [x] Run:

```powershell
python -m pytest tests/
python <codex-home>\skills\.system\skill-creator\scripts\quick_validate.py "<cross-review-skill-root>"
python -m cross_review.cli benchmark --cases examples/regression_cases
python -m cross_review.cli prepare --root examples/toy_api_break --files src/billing/client.py --lite
python -m cross_review.cli validate-pack --pack examples/toy_api_break/.cross-review/agent_review_pack.json
```
