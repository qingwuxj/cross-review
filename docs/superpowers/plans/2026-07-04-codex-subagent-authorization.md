# Codex Subagent Authorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Cross-Review request subagents by default through Codex's user-facing prompt while respecting host-level explicit authorization rules.

**Architecture:** Replace the impossible "skill invocation authorizes subagents" contract with a host-aware state machine. The Codex default prompt supplies explicit user authorization; other invocation paths ask once when the host requires authorization, and sequential fallback remains limited to opt-out, decline, or unavailable tools.

**Tech Stack:** Python 3.11, pytest, Markdown skill instructions, Codex `agents/openai.yaml`

---

### Task 1: Lock the Revised Authorization Contract

**Files:**
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Replace the old policy assertions with failing assertions for the new contract**

Assert that:

```python
policy["subagents_default_when_available"] is True
policy["subagents_requested_by_cross_review"] is True
policy["subagents_required_when_authorized_and_available"] is True
policy["authorization_source"] == "user_request_or_host_policy"
policy["ask_once_if_host_requires_explicit_authorization"] is True
policy["missing_authorization_action"] == "ask_once_and_pause"
policy["fallback_execution_mode"] == "sequential_same_agent"
```

Also assert that generated instructions contain:

```text
ask one concise authorization question and pause
do not silently fall back
```

and do not contain:

```text
Treat invocation of cross-review itself as authorization
```

- [ ] **Step 2: Add a failing Codex UI prompt test**

Read `agents/openai.yaml` and assert its `default_prompt` explicitly contains `$cross-review`, `subagents`, and `delegate`.

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```powershell
$env:PYTHONUTF8 = "1"
python -m pytest tests/test_pipeline.py -k "subagent or execution_policy" -q
```

Expected: failures for missing new fields, stale implicit-authorization text, and a default prompt that does not request delegation.

### Task 2: Implement Host-Aware Authorization

**Files:**
- Modify: `agents/openai.yaml`
- Modify: `SKILL.md`
- Modify: `cross_review/pipeline.py`
- Modify: `cross_review/validation/pack_validator.py`

- [ ] **Step 1: Update the Codex default prompt**

Set:

```yaml
default_prompt: "Use $cross-review to audit my current changes for cross-module contract and integration risks, delegating each effective assignment to a real subagent when available."
```

- [ ] **Step 2: Update the skill workflow**

Specify this order:

1. Respect explicit opt-out.
2. If host policy permits delegation and the request authorizes it, spawn one subagent per effective assignment.
3. If host policy requires explicit user authorization and the request lacks it, ask one concise question and pause.
4. Never treat skill text, packs, or assistant-authored prompts as user authorization.
5. Use sequential fallback only after opt-out, decline, tool absence, or host refusal after authorization.

- [ ] **Step 3: Revise `_build_execution_policy`**

Emit:

```python
{
    "subagents_default_when_available": True,
    "subagents_requested_by_cross_review": True,
    "subagents_required_when_authorized_and_available": True,
    "authorization_source": "user_request_or_host_policy",
    "ask_once_if_host_requires_explicit_authorization": True,
    "missing_authorization_action": "ask_once_and_pause",
    "respect_user_opt_out": True,
    "simulation_allowed_only_if_subagents_unavailable": True,
    "simulation_requires_explicit_note": True,
    "fallback_execution_mode": "sequential_same_agent",
}
```

Keep the existing effective-assignment and opt-out metadata, but change `preflight_prompt_policy.ask_before_spawning` to `"when_host_requires_explicit_authorization_and_request_lacks_it"`.

- [ ] **Step 4: Update generated reviewer instructions**

Replace implicit authorization with the one-question authorization gate and explicitly prohibit silent fallback while authorization is merely missing.

- [ ] **Step 5: Update pack validation**

Validate the revised boolean and string fields and remove validation of deleted implicit-authorization fields.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run:

```powershell
$env:PYTHONUTF8 = "1"
python -m pytest tests/test_pipeline.py -k "subagent or execution_policy" -q
```

Expected: all selected tests pass.

### Task 3: Document, Validate, and Deploy

**Files:**
- Modify: `README.md`
- Modify: `docs/implementation-roadmap.md`
- Synchronize verified files to: `<codex-home>\skills\cross-review-skill`

- [ ] **Step 1: Document the Codex constraint**

Explain that Codex host policy may require explicit user wording, the bundled default prompt supplies it when the skill is selected, and implicit auto-triggering may require one confirmation.

- [ ] **Step 2: Run all tests**

Run:

```powershell
$env:PYTHONUTF8 = "1"
python -m pytest -q
```

Expected: full suite passes.

- [ ] **Step 3: Validate the skill package**

Run:

```powershell
$env:PYTHONUTF8 = "1"
python "<codex-home>\skills\.system\skill-creator\scripts\quick_validate.py" .
```

Expected: `Skill is valid!`

- [ ] **Step 4: Synchronize the verified files**

Copy only the changed skill/package files into the installed skill directory, preserving unrelated installed files.

- [ ] **Step 5: Verify the installed copy**

Run the same focused tests and skill validator against `<codex-home>\skills\cross-review-skill`.

Expected: tests pass and `Skill is valid!`

> Git commit steps are omitted because this workspace does not contain `.git` metadata.
