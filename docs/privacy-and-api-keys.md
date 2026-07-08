# Privacy And API Key Policy

## Agent Mode

Agent mode uses:

```powershell
python -m cross_review.cli prepare --root <repo-root> --worktree
```

This mode performs local static analysis and writes `.cross-review/agent_review_pack.json`. It does not call external LLM APIs and does not require user-managed model API keys.

The host Agent, such as Codex or Claude Code, performs the semantic review with its own runtime. Cross-Review only prepares deterministic context and protocols.

## Standalone Review Mode

Standalone mode uses:

```powershell
python -m cross_review.cli review --root <repo-root>
```

This mode may read provider keys such as:

- `OPENAI_API_KEY`
- `GEMINI_API_KEY`

If no supported key is present, the local LLM client may fall back to mock output. Mock reports are marked with `"is_mock": true` and must not be presented as real audit findings.

## Generated Files

Generated `.cross-review` files can include:

- changed file paths
- source snippets
- diff snippets
- function names
- routes
- event names
- SQL table or column names
- downstream call-site evidence

Review generated packs before sharing them outside your local environment.

## No Hidden Uploads

The `prepare`, `validate-pack`, `validate-report`, and `benchmark` commands run locally. They do not upload code to a remote service.

## User Responsibility

Users are responsible for their host Agent environment. If a host Agent sends context to a model provider, that behavior is controlled by the host Agent, not by Cross-Review's local `prepare` command.
