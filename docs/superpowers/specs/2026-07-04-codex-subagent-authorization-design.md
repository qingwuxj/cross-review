# Codex Subagent Authorization Compatibility

## Problem

Cross-Review currently says that invoking the skill authorizes real subagents. Codex's
`spawn_agent` policy has higher priority and requires the user's request to explicitly ask
for subagents, delegation, or parallel agent work. The host therefore rejects the skill's
implicit-authorization claim and may silently fall back to same-agent review.

## Design

Keep subagent review as the Cross-Review default without claiming that a skill can override
host authorization:

1. The Codex UI default prompt explicitly asks for subagent delegation. Selecting the skill
   therefore produces a user request that satisfies Codex's authorization rule without
   requiring the user to remember extra wording.
2. The skill and generated pack distinguish preference from authorization. They require real
   subagents when the user has authorized them and the host exposes the tools.
3. When a host requires explicit authorization and the current user request lacks it, the
   agent asks one concise authorization question and pauses review. It must not silently
   downgrade merely because authorization is missing.
4. Same-agent sequential review is used only when the user opts out, declines the authorization
   question, or the host has no usable subagent tools.

## Artifact Changes

- `agents/openai.yaml`: include explicit subagent delegation in `default_prompt`.
- `SKILL.md`: replace the invalid "invocation itself is authorization" claim with the host-aware
  authorization gate.
- `cross_review/pipeline.py`: emit an execution policy that records default preference,
  authorization requirements, one-question preflight behavior, and valid fallback reasons.
- `cross_review/validation/pack_validator.py`: validate the revised execution-policy contract.
- `README.md`: document the Codex limitation and the no-memory-required UI path.

## Verification

- Add failing tests for the Codex default prompt and the revised pack/instruction contract.
- Verify generated instructions require one authorization question instead of silent fallback.
- Run targeted tests, the full test suite, and the skill validator.
- Synchronize the verified files into the installed Codex skill.

## Non-Goals

- Bypassing or changing Codex's host-level `spawn_agent` policy.
- Treating skill text, generated packs, or assistant-authored prompts as user authorization.
- Requiring explicit authorization on hosts whose own tool policy permits automatic delegation.
