# Release Checklist

Run this checklist before publishing Cross-Review.

## Required Commands

```powershell
python -m pip install -e ".[dev]"
cross-review doctor --root examples/toy_api_break
python -m pytest tests/
$skillValidator = if ($env:CODEX_HOME) {
  Join-Path $env:CODEX_HOME "skills/.system/skill-creator/scripts/quick_validate.py"
} else {
  Join-Path $HOME ".codex/skills/.system/skill-creator/scripts/quick_validate.py"
}
$env:PYTHONUTF8 = "1"
python $skillValidator "."
python -m cross_review.cli benchmark --cases examples/regression_cases
python -m cross_review.cli prepare --root examples/toy_api_break --files src/billing/client.py
python -m cross_review.cli validate-pack --pack examples/toy_api_break/.cross-review/agent_review_pack.json
```

Run the commands from the repository root after installing the package in editable mode. The skill validator path depends on the local Codex installation; the PowerShell snippet above uses `CODEX_HOME` when it is set and falls back to the default `$HOME/.codex` location.

## Portable Contributor Checks

Contributors without Codex installed can run the portable checks below for local development and pull requests:

```powershell
python -m pip install -e ".[dev]"
cross-review doctor --root examples/toy_api_break
python -m pytest tests/
python -m cross_review.cli benchmark --cases examples/regression_cases
python -m cross_review.cli prepare --root examples/toy_api_break --files src/billing/client.py --lite
python -m cross_review.cli validate-pack --pack examples/toy_api_break/.cross-review/agent_review_pack.json
```

Official Codex skill releases should still include the Codex `quick_validate.py` check from the required command list above.

## Documentation

- README quickstart works.
- Agent mode vs standalone mode is clearly explained.
- API key behavior is clearly documented.
- Known limitations are current.
- Benchmark coverage is current.
- `cross-review.toml` configuration behavior is documented.
- `context_budget` pack metadata is documented.
- Benchmark remains at or above 26 fixed cases.
- Benchmark includes realistic project-layout fixtures.
- Benchmark metrics are documented.
- CLI helper commands are documented.
- Benchmark expected files include changed contracts, call-site prefixes, and change types when before/after evidence is available.
- Prompt templates use neutral audit language.
- Privacy statement is current.
- `pyproject.toml` uses standard PEP 621 metadata and CI tests editable install.

## Safety

- No real API keys are committed.
- No generated mock finding is described as real audit output.
- `.cross-review` generated files are not accidentally packaged as source examples unless intentional.
- Regression expected files are reviewed.

## Quality Bar

- Tests pass.
- Skill validator passes.
- Benchmark passes.
- High/blocking report findings require structured pack-grounded evidence.
- New analyzer capabilities have regression cases.
