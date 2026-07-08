# Agent Native Prepare Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an API-key-free prepare mode that generates deterministic review packs for host agents such as Codex and Claude Code.

**Architecture:** Keep `review` as the standalone model-calling mode. Add `prepare` as a local-only pipeline path that scans, scores, builds context packs, and writes `agent_review_pack.json` plus a Markdown instruction file for the host agent.

**Tech Stack:** Python 3.10, Click, Pydantic, pytest.

---

### Task 1: Regression Tests

**Files:**
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing tests**

```python
def test_prepare_generates_agent_review_pack_without_api_keys(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    pipeline = ReviewPipeline(root_dir=project_path, cache_dir=".cross-review")
    pack_path = pipeline.prepare(manual_files=["src/billing/client.py"])
    with open(pack_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["mode"] == "agent"
    assert data["requires_external_api_key"] is False
    assert data["impact_edges"][0]["from_module"] == "billing"
```

- [ ] **Step 2: Verify tests fail**

Run: `python -m pytest tests/test_pipeline.py::test_prepare_generates_agent_review_pack_without_api_keys -q`

Expected: fail because `ReviewPipeline.prepare` does not exist.

### Task 2: Prepare Pipeline

**Files:**
- Modify: `cross_review/pipeline.py`

- [ ] **Step 1: Implement `prepare`**

Add a method that reuses scanner, diff parser, scorer, and context packager without calling `LLMClient.call_json`.

- [ ] **Step 2: Write artifacts**

Write `.cross-review/agent_review_pack.json` and `.cross-review/agent_review_instructions.md`.

- [ ] **Step 3: Verify tests pass**

Run: `python -m pytest tests/test_pipeline.py::test_prepare_generates_agent_review_pack_without_api_keys -q`

Expected: pass.

### Task 3: CLI Command

**Files:**
- Modify: `cross_review/cli.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Add CLI test**

Use `CliRunner().invoke(main, ["prepare", "--root", project_path, "--files", "src/billing/client.py"])`.

- [ ] **Step 2: Add `prepare` command**

Expose `--root`, `--base`, `--head`, `--files`, `--worktree`, and `--staged`, matching `review` options.

- [ ] **Step 3: Verify CLI test passes**

Run: `python -m pytest tests/test_pipeline.py::test_cli_prepare_supports_explicit_root -q`

Expected: pass.

### Task 4: Documentation

**Files:**
- Modify: `SKILL.md`
- Modify: `README.md`

- [ ] **Step 1: Update skill workflow**

Make `prepare` the default Agent Skill mode and describe `review` as optional standalone mode.

- [ ] **Step 2: Update README**

Document that API keys are optional and only needed for standalone model-calling mode.

### Task 5: Verification

**Files:**
- No source changes.

- [ ] **Step 1: Run full tests**

Run: `python -m pytest tests/`

Expected: all tests pass.

- [ ] **Step 2: Validate skill**

Run: `python <codex-home>\skills\.system\skill-creator\scripts\quick_validate.py "<cross-review-skill-root>"`

Expected: `Skill is valid!`

- [ ] **Step 3: Smoke test CLI**

Run: `python cross_review/cli.py prepare --root examples/toy_api_break --files src/billing/client.py`

Expected: writes `examples/toy_api_break/.cross-review/agent_review_pack.json`.
