# Contributing to Cross-Review

Cross-Review is an Agent-native skill for finding cross-module contract breaks. Contributions should preserve the core design: local deterministic preparation first, host Agent semantic review second.

## Development Rules

- Install pinned local dependencies with `python -m pip install -r requirements-dev.txt`.
- Run `python -m pytest tests/` before submitting changes.
- Run the skill validator before release-facing changes.
- Add or update a regression case for every new analyzer capability.
- Do not add external model calls to Agent mode.
- Do not present mock findings as real audit output.
- Keep `prepare` deterministic and API-key-free.
- Keep generated benchmark expected files intentional and reviewable.

## Analyzer Changes

When adding scanner or contract graph behavior:

1. Add a fixture under `examples/regression_cases/<case-name>/`.
2. Add `expected.json` with expected changed files, impact edges, changed contract ids, and call-site prefixes.
3. Run `python -m cross_review.cli benchmark --cases examples/regression_cases`.
4. Document new limitations if the analyzer is heuristic.

## Validation Changes

When changing pack or report schema:

- Update `validate-pack` or `validate-report`.
- Add tests in `tests/test_validation.py`.
- Update README examples when CLI behavior changes.

## API Key Policy

Agent mode must not require `OPENAI_API_KEY`, `GEMINI_API_KEY`, or other provider keys. Standalone `review` may use provider keys only when the user explicitly chooses that mode.

## Release Checks

Before release, run:

```powershell
python -m pytest tests/
$skillValidator = if ($env:CODEX_HOME) {
  Join-Path $env:CODEX_HOME "skills/.system/skill-creator/scripts/quick_validate.py"
} else {
  Join-Path $HOME ".codex/skills/.system/skill-creator/scripts/quick_validate.py"
}
$env:PYTHONUTF8 = "1"
python $skillValidator "."
python -m cross_review.cli benchmark --cases examples/regression_cases
python -m cross_review.cli prepare --root examples/toy_api_break --files src/billing/client.py --lite
python -m cross_review.cli validate-pack --pack examples/toy_api_break/.cross-review/agent_review_pack.json
```
