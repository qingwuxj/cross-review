# Phase 7 Open Source Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare Cross-Review for public release as an open-source Agent-native skill.

**Architecture:** Improve documentation, examples, contribution process, release checklist, and public positioning without changing core analysis behavior.

**Tech Stack:** Markdown docs, existing CLI, pytest.

---

### Task 1: README Restructure

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Rewrite README sections**

Ensure README contains:

- one-sentence project positioning
- quickstart
- Agent mode vs standalone mode
- API key policy
- generated files
- validation workflow
- benchmark workflow
- limitations
- roadmap

- [ ] **Step 2: Verify commands in README**

Run every quickstart command from README against toy examples.

### Task 2: Contributing Guide

**Files:**
- Create: `CONTRIBUTING.md`

- [ ] **Step 1: Add contribution rules**

Include:

- run tests before PR
- add regression case for every new analyzer capability
- do not add external model calls to Agent mode
- no mock findings as real findings
- update benchmark expected files intentionally

### Task 3: Privacy And API Key Statement

**Files:**
- Create: `docs/privacy-and-api-keys.md`
- Modify: `README.md`

- [ ] **Step 1: Document data flow**

Explain:

- `prepare` runs locally
- no external LLM API calls in Agent mode
- standalone `review` may use configured provider keys
- generated pack may contain source snippets
- users should review pack contents before sharing externally

### Task 4: Release Checklist

**Files:**
- Create: `docs/release-checklist.md`

- [ ] **Step 1: Add release checklist**

Checklist must include:

- tests pass
- skill validator pass
- benchmark pass
- README commands verified
- no accidental API keys
- examples are intentional
- limitations updated

### Task 5: Full Verification

Run:

```powershell
python -m pytest tests/
python <codex-home>\skills\.system\skill-creator\scripts\quick_validate.py "<cross-review-skill-root>"
python -m cross_review.cli benchmark --cases examples/regression_cases
python -m cross_review.cli prepare --root examples/toy_api_break --files src/billing/client.py
python -m cross_review.cli validate-pack --pack examples/toy_api_break/.cross-review/agent_review_pack.json
```

Expected:

- all tests pass
- skill is valid
- benchmark passes
- quickstart commands work

---

## Non-Goals

- Do not add new analyzers in this phase.
- Do not change benchmark expected outputs unless a docs command exposes a real bug.
- Do not publish or push to GitHub from this phase unless explicitly requested.

## Self-Review

- Spec coverage: Covers open-source docs, privacy/API key policy, contribution flow, and release checklist.
- Placeholder scan: No placeholders remain.
- Type consistency: Refers to existing CLI commands and docs consistently.
