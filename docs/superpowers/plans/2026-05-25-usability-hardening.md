# Usability Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add low-token, open-source-friendly hardening: local configuration, pack budget metadata, and stronger before/after contract evidence.

**Architecture:** Keep defaults lightweight and deterministic. Add a small standard-library TOML config loader, let `prepare` use config limits and aliases, and let the contract graph read previous source from Git or `.cross-review-before` snapshots without requiring external LLM APIs.

**Tech Stack:** Python 3.10+, `tomllib` when available, GitPython already in dependencies, pytest, Click CLI.

---

### Task 1: Local Configuration

**Files:**
- Create: `cross_review/config.py`
- Modify: `cross_review/pipeline.py`
- Modify: `cross_review/cli.py`
- Test: `tests/test_config.py`

- [x] **Step 1: Write failing config tests**

```python
def test_load_config_reads_cross_review_toml(tmp_path):
    (tmp_path / "cross-review.toml").write_text("[review]\ntop_k = 1\n", encoding="utf-8")
    config = load_config(str(tmp_path))
    assert config.review.top_k == 1
```

Run: `python -m pytest tests/test_config.py -q`
Expected: FAIL because `cross_review.config` does not exist.

- [x] **Step 2: Implement config loader**

Add `CrossReviewConfig`, `ReviewConfig`, `ContextConfig`, and `load_config(root_dir)`.

- [x] **Step 3: Wire config into prepare**

`ReviewPipeline` loads config once, `prepare` uses `review.top_k`, `review.lite`, `context.max_*`, and `module_aliases`.

- [x] **Step 4: Verify config tests**

Run: `python -m pytest tests/test_config.py -q`
Expected: PASS.

### Task 2: Context Budget Metadata

**Files:**
- Modify: `cross_review/pipeline.py`
- Modify: `cross_review/context_pack.py`
- Test: `tests/test_pipeline.py`

- [x] **Step 1: Write failing budget metadata test**

```python
assert "context_budget" in pack
assert pack["context_budget"]["estimated_context_tokens"] > 0
```

Run: `python -m pytest tests/test_pipeline.py::test_prepare_pack_includes_context_budget_metadata -q`
Expected: FAIL because `context_budget` is missing.

- [x] **Step 2: Implement budget metadata**

Estimate tokens from generated context strings using conservative character counts, report context counts, truncation markers, configured limits, and top-K policy.

- [x] **Step 3: Verify budget test**

Run: `python -m pytest tests/test_pipeline.py::test_prepare_pack_includes_context_budget_metadata -q`
Expected: PASS.

### Task 3: Git Before/After Contract Evidence

**Files:**
- Modify: `cross_review/diff.py`
- Modify: `cross_review/contracts/contract_graph.py`
- Modify: `cross_review/pipeline.py`
- Test: `tests/test_contract_graph.py`

- [x] **Step 1: Write failing before-source provider test**

```python
builder = ContractGraphBuilder(str(tmp_path), graph, previous_source_provider=lambda path: old_sources.get(path))
```

Run: `python -m pytest tests/test_contract_graph.py::test_contract_graph_marks_signature_changed_from_previous_source_provider -q`
Expected: FAIL because the builder does not accept `previous_source_provider`.

- [x] **Step 2: Implement provider support**

Let `ContractGraphBuilder` prefer `previous_source_provider(file)` over `.cross-review-before`.

- [x] **Step 3: Add Git previous source helper**

Expose `GitDiffParser.get_previous_file_content(file, base, mode)` and pass it from full `prepare` where Git is available.

- [x] **Step 4: Verify contract graph tests**

Run: `python -m pytest tests/test_contract_graph.py -q`
Expected: PASS.

### Task 4: Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/implementation-roadmap.md`

- [x] **Step 1: Document config and budget fields**

Add a short TOML example and explain that config lowers token cost by limiting context and top-K locally.

- [x] **Step 2: Run verification**

Run:

```powershell
python -m pytest tests/
python -m cross_review.cli benchmark --cases examples/regression_cases
python -m cross_review.cli prepare --root examples/toy_api_break --files src/billing/client.py --lite
python -m cross_review.cli validate-pack --pack examples/toy_api_break/.cross-review/agent_review_pack.json
python <codex-home>\skills\.system\skill-creator\scripts\quick_validate.py .
```

Expected: all pass; skill validator prints `Skill is valid!`.
