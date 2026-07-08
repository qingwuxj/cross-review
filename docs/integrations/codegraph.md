# CodeGraph Integration

Cross-Review treats CodeGraph as an optional peer tool. Do not vendor CodeGraph source, generated `.codegraph/` indexes, or agent-specific MCP config into this repository.

## User Setup

```powershell
npx @colbymchenry/codegraph
cd <repo-root>
codegraph init
python -m cross_review.cli prepare --root . --worktree
```

## Configuration

```toml
[integrations.codegraph]
enabled = "auto"
command = "codegraph"
timeout_seconds = 20
max_explore_chars = 12000
affected_depth = 5
```

If `codegraph` is not on PATH, use the package runner:

```toml
[integrations.codegraph]
enabled = "auto"
command = "npx -y @colbymchenry/codegraph"
```

- `auto`: collect CodeGraph context only when the configured command is available and `.codegraph/` exists.
- `true`: expect CodeGraph and record an integration error when it is unavailable.
- `false`: disable CodeGraph integration.

## Pack Contract

`prepare` writes `.cross-review/codegraph_context.json` and embeds the same object at `agent_review_pack.json -> integrations.codegraph`.

Each `agent_assignment` also receives `integration_context.codegraph`, which contains a trimmed `explore_excerpt`, affected-test payload, status, and usage notes. Subagents should prefer that assignment-local excerpt instead of assuming they can call CodeGraph MCP tools directly.

Agents should use this context for assignment routing and blast-radius hints only. Final findings still need concrete file/line evidence from the repository or the deterministic Cross-Review pack, and high/blocking findings should cite contract and call-site ids when available.

## External Graph Export

Use `codegraph-export` when CodeGraph should provide the project graph used by `prepare`. Prefer a globally available `codegraph` command for repeated exports; `npx -y @colbymchenry/codegraph` works as a fallback but is slower because each CodeGraph subcommand starts through the package runner.

```powershell
cross-review codegraph-export `
  --root . `
  --out .codegraph/cross-review.json `
  --command "codegraph" `
  --symbol-limit-per-file 2 `
  --caller-limit 20 `
  --query-limit 10
```

Then set:

```toml
[project_graph]
external_graph_path = ".codegraph/cross-review.json"
```

The exporter reads CodeGraph `files --json` for indexed files, `node --file --symbols-only` for per-file dependents, `query --json` for provider file/line/qualified-name matching, and bounded `callers --json` output for symbol-level evidence. Query and caller results are cached inside one export run. It emits Cross-Review's simplified external graph format:

```json
{
  "name": "repo-name",
  "modules": [
    {"name": "billing", "files": ["src/billing/client.py"], "exports": ["charge_user"]}
  ],
  "dependencies": [
    {
      "from": "billing",
      "to": "admin",
      "type": "static_import",
      "details": "CodeGraph usage: src/admin/panel.py uses src/billing/client.py; symbols: charge_user",
      "consumer_files": ["src/admin/panel.py"],
      "provider_files": ["src/billing/client.py"],
      "symbol_edges": [
        {
          "symbol": "charge_user",
          "qualified_name": "src.billing.client.charge_user",
          "kind": "function",
          "provider_file": "src/billing/client.py",
          "provider_line": 1,
          "consumer_file": "src/admin/panel.py",
          "caller": "render",
          "caller_kind": "function",
          "caller_line": 1,
          "match_source": "query_json"
        }
      ]
    }
  ]
}
```

Use `--max-files`, `--symbol-limit-per-file`, `--caller-limit`, and `--query-limit` to cap export time on large repositories, especially when running CodeGraph through `npx`. Set `--symbol-limit-per-file 0` for a faster file-only export. When the exported file is configured as `[project_graph].external_graph_path`, `prepare` carries `symbol_edges` into `impact_edges`, `cross_review_contexts`, and assignment targets so reviewers can see concrete caller evidence without reopening the raw export JSON.
