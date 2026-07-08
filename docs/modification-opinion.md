# Archived Initial Modification Opinion

This is a historical review note from the early prototype stage. It is kept only to preserve design history; the P1/P2 items below are not the current project status. Current user-facing status lives in `README.md`, `SKILL.md`, and `docs/implementation-roadmap.md`.

## Positioning

At the time this note was written, Cross-Review was a Python CLI and library prototype for dependency-graph-driven cross-module code review. It was not yet a complete Codex skill because the package had no `SKILL.md` entry point or agent-facing metadata.

The strongest path is to stabilize the CLI/tool kernel first, then wrap it as a skill. The LLM layer should only consume compact, evidence-bearing context produced by deterministic local tools.

## Main Issues

### P1: Skill packaging is missing

The repository name and documentation describe a skill, but the folder cannot be discovered by Codex as a skill. A valid skill package needs a `SKILL.md` with clear trigger metadata and usage instructions. Agent UI metadata should live under `agents/openai.yaml`.

### P1: CLI review target is ambiguous

`review` always constructs `ReviewPipeline(root_dir=".")`. This makes the README example scan the package root instead of the intended toy project. Manual files are also normalized relative to the current shell instead of the selected review root.

Expected behavior:

- `cross-review review --root examples/toy_api_break --files src/billing/client.py`
- changed file normalizes to `src/billing/client.py`
- impact edges include `billing -> admin`

### P1: Prompt templates are not strict JSON-safe

Prompt examples contain `//` comments while instructing the model to return valid JSON. This increases validation failures with real LLMs. `cross_review.txt` also hardcodes `risk_score: 0.85` instead of using the runtime score placeholder.

Expected behavior:

- prompt examples contain valid JSON only
- all runtime values use placeholders
- evidence instructions require findings to cite concrete symbols, fields, files, or call paths from the provided context

### P1: Diff mode does not reach all consumers

`--worktree` and `--staged` influence changed-file detection, but `ContextPackager` is created without `diff_mode`, and `ImpactScorer` calls diff payload extraction without the mode. This can review the wrong diff content.

Expected behavior:

- `diff_mode` is passed through pipeline, scorer, and context packager
- staged/worktree mode affects both changed-file list and diff payloads

### P1: CLI can report failure with exit code 0

The CLI catches exceptions and prints an error without making Click return a failure code. On Windows GBK consoles, emoji output can also raise encoding errors after artifacts are written.

Expected behavior:

- operational failures use `click.ClickException` or a non-zero exit
- terminal status output is ASCII-safe
- Markdown artifacts can retain rich Unicode because they are written as UTF-8 files

### P2: Test-gap signal cannot see tests

`ScoutScanner` intentionally ignores the `tests` directory, but `ImpactScorer` only searches graph files for `test_`. This makes the test-gap score usually act as if no integration tests exist.

Expected behavior:

- graph scanning can still ignore tests
- scorer separately scans common test directories and filenames for module-pair coverage signals

### P2: Dependency declarations are incomplete

`llm.py` imports `openai`, but `pyproject.toml` does not declare it. Users with only `OPENAI_API_KEY` will hit a missing dependency.

Expected behavior:

- runtime dependencies match imported optional clients

## Execution Plan

1. Add regression tests for root-relative manual files, prompt JSON safety, scorer test-gap detection, and CLI root support.
2. Fix path normalization and add `--root` support to the CLI.
3. Pass `diff_mode` through the pipeline into context packaging and impact scoring.
4. Rewrite bundled prompt templates as strict JSON-safe templates and make `init` copy bundled prompts instead of duplicating prompt text.
5. Make CLI errors return non-zero status and remove terminal emoji output.
6. Add skill packaging files: `SKILL.md` and `agents/openai.yaml`.
7. Update README usage and dependency declarations.
8. Run the full pytest suite and skill validation where available.

## Non-Goals

- No dashboard or web UI.
- No MCP server in this pass.
- No multi-language parser expansion in this pass.
- No real LLM accuracy benchmark in this pass.
