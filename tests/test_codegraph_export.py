import json
import os
import tomllib

from click.testing import CliRunner

from cross_review.cli import main
from cross_review.external_graph import load_external_project_graph
from cross_review.integrations.codegraph import CommandResult
from cross_review.integrations.codegraph_export import CodeGraphGraphExporter


def test_codegraph_exporter_builds_simplified_external_graph(tmp_path, monkeypatch):
    (tmp_path / ".codegraph").mkdir()
    (tmp_path / "src" / "billing").mkdir(parents=True)
    (tmp_path / "src" / "admin").mkdir(parents=True)
    (tmp_path / "src" / "billing" / "client.py").write_text(
        "def charge_user(user_id):\n    return True\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "admin" / "panel.py").write_text(
        "from src.billing.client import charge_user\n",
        encoding="utf-8",
    )

    files_payload = [
        {"path": "src/billing/client.py", "language": "python", "nodeCount": 2, "size": 44},
        {"path": "src/admin/panel.py", "language": "python", "nodeCount": 1, "size": 43},
    ]
    node_outputs = {
        "src/billing/client.py": (
            "**src/billing/client.py** — 1 symbols, used by 1 files: src/admin/panel.py\n\n"
            "**Symbols**\n"
            "- `charge_user` (function) (user_id) — :1\n"
        ),
        "src/admin/panel.py": (
            "**src/admin/panel.py** — 1 symbols, used by 0 files\n\n"
            "**Symbols**\n"
            "- `run` (function) () — :1\n"
        ),
    }

    def fake_run(self, args):
        if args[0] == "status":
            return CommandResult(args=["codegraph", *args], returncode=0, stdout="Indexed 2 files", stderr="")
        if args[0] == "files":
            return CommandResult(args=["codegraph", *args], returncode=0, stdout=json.dumps(files_payload), stderr="")
        if args[0] == "node":
            file_path = args[args.index("--file") + 1]
            return CommandResult(args=["codegraph", *args], returncode=0, stdout=node_outputs[file_path], stderr="")
        if args[0] == "query":
            return CommandResult(args=["codegraph", *args], returncode=0, stdout="[]", stderr="")
        if args[0] == "callers":
            return CommandResult(
                args=["codegraph", *args],
                returncode=0,
                stdout=json.dumps({"symbol": args[3], "callers": []}),
                stderr="",
            )
        raise AssertionError(f"Unexpected args: {args}")

    monkeypatch.setattr(CodeGraphGraphExporter, "_run", fake_run)

    graph = CodeGraphGraphExporter(str(tmp_path)).build()

    assert graph["name"] == tmp_path.name
    assert {module["name"] for module in graph["modules"]} == {"billing", "admin"}
    billing = next(module for module in graph["modules"] if module["name"] == "billing")
    assert billing["files"] == ["src/billing/client.py"]
    assert "charge_user" in billing["exports"]
    assert graph["dependencies"] == [
        {
            "from": "billing",
            "to": "admin",
            "type": "static_import",
            "details": "CodeGraph usage: src/admin/panel.py uses src/billing/client.py",
            "consumer_files": ["src/admin/panel.py"],
            "provider_files": ["src/billing/client.py"],
        }
    ]
    assert graph["metadata"]["codegraph"]["dependency_source"] == "node_symbols_only"


def test_codegraph_exporter_adds_symbol_edges_from_callers_json(tmp_path, monkeypatch):
    (tmp_path / ".codegraph").mkdir()
    (tmp_path / "src" / "billing").mkdir(parents=True)
    (tmp_path / "src" / "admin").mkdir(parents=True)
    (tmp_path / "src" / "billing" / "client.py").write_text(
        "def charge_user(user_id):\n    return True\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "admin" / "panel.py").write_text(
        "from src.billing.client import charge_user\n",
        encoding="utf-8",
    )

    files_payload = [
        {"path": "src/billing/client.py", "language": "python", "nodeCount": 2, "size": 44},
        {"path": "src/admin/panel.py", "language": "python", "nodeCount": 1, "size": 43},
    ]
    node_outputs = {
        "src/billing/client.py": (
            "**src/billing/client.py** — 1 symbols, used by 1 files: src/admin/panel.py\n\n"
            "**Symbols**\n"
            "- `charge_user` (function) (user_id) — :1\n"
        ),
        "src/admin/panel.py": (
            "**src/admin/panel.py** — 1 symbols, used by 0 files\n\n"
            "**Symbols**\n"
            "- `render` (function) () — :1\n"
        ),
    }
    callers_payload = {
        "symbol": "charge_user",
        "callers": [
            {
                "name": "render",
                "kind": "function",
                "filePath": "src/admin/panel.py",
                "startLine": 1,
            }
        ],
    }

    def fake_run(self, args):
        if args[0] == "status":
            return CommandResult(args=["codegraph", *args], returncode=0, stdout="Indexed 2 files", stderr="")
        if args[0] == "files":
            return CommandResult(args=["codegraph", *args], returncode=0, stdout=json.dumps(files_payload), stderr="")
        if args[0] == "node":
            file_path = args[args.index("--file") + 1]
            return CommandResult(args=["codegraph", *args], returncode=0, stdout=node_outputs[file_path], stderr="")
        if args[0] == "query":
            return CommandResult(args=["codegraph", *args], returncode=0, stdout="[]", stderr="")
        if args[0] == "callers":
            assert args[3] == "charge_user"
            return CommandResult(args=["codegraph", *args], returncode=0, stdout=json.dumps(callers_payload), stderr="")
        raise AssertionError(f"Unexpected args: {args}")

    monkeypatch.setattr(CodeGraphGraphExporter, "_run", fake_run)

    graph = CodeGraphGraphExporter(str(tmp_path)).build()

    dependency = graph["dependencies"][0]
    assert dependency["details"] == (
        "CodeGraph usage: src/admin/panel.py uses src/billing/client.py; symbols: charge_user"
    )
    assert dependency["symbol_edges"] == [
        {
            "symbol": "charge_user",
            "kind": "function",
            "provider_file": "src/billing/client.py",
            "provider_line": 1,
            "consumer_file": "src/admin/panel.py",
            "caller": "render",
            "caller_kind": "function",
            "caller_line": 1,
        }
    ]
    assert graph["metadata"]["codegraph"]["symbol_edges"] == dependency["symbol_edges"]


def test_codegraph_exporter_uses_query_json_for_precise_symbol_callers(tmp_path, monkeypatch):
    (tmp_path / ".codegraph").mkdir()
    (tmp_path / "src" / "billing").mkdir(parents=True)
    (tmp_path / "src" / "admin").mkdir(parents=True)
    (tmp_path / "src" / "billing" / "client.py").write_text(
        "def charge_user(user_id):\n    return True\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "admin" / "panel.py").write_text(
        "from src.billing.client import charge_user\n",
        encoding="utf-8",
    )

    files_payload = [
        {"path": "src/billing/client.py", "language": "python", "nodeCount": 2, "size": 44},
        {"path": "src/admin/panel.py", "language": "python", "nodeCount": 1, "size": 43},
    ]
    query_payload = [
        {
            "node": {
                "name": "charge_user",
                "qualifiedName": "src.billing.client.charge_user",
                "kind": "function",
                "filePath": "src/billing/client.py",
                "startLine": 1,
            }
        },
        {
            "node": {
                "name": "charge_user",
                "qualifiedName": "src.legacy.client.charge_user",
                "kind": "function",
                "filePath": "src/legacy/client.py",
                "startLine": 9,
            }
        },
    ]
    callers_payload = {
        "symbol": "src.billing.client.charge_user",
        "callers": [
            {
                "name": "render",
                "kind": "function",
                "filePath": "src/admin/panel.py",
                "startLine": 3,
            }
        ],
    }
    calls = []

    def fake_run(self, args):
        calls.append(args)
        if args[0] == "status":
            return CommandResult(args=["codegraph", *args], returncode=0, stdout="Indexed 2 files", stderr="")
        if args[0] == "files":
            return CommandResult(args=["codegraph", *args], returncode=0, stdout=json.dumps(files_payload), stderr="")
        if args[0] == "node":
            file_path = args[args.index("--file") + 1]
            stdout = (
                "**src/billing/client.py** — 1 symbols, used by 1 files: src/admin/panel.py\n\n"
                "**Symbols**\n"
                "- `charge_user` (function) (user_id) — :1\n"
            ) if file_path == "src/billing/client.py" else (
                "**src/admin/panel.py** — 1 symbols, used by 0 files\n\n"
                "**Symbols**\n"
                "- `render` (function) () — :1\n"
            )
            return CommandResult(args=["codegraph", *args], returncode=0, stdout=stdout, stderr="")
        if args[0] == "query":
            assert args[3] == "charge_user"
            return CommandResult(args=["codegraph", *args], returncode=0, stdout=json.dumps(query_payload), stderr="")
        if args[0] == "callers":
            assert args[3] == "src.billing.client.charge_user"
            return CommandResult(args=["codegraph", *args], returncode=0, stdout=json.dumps(callers_payload), stderr="")
        raise AssertionError(f"Unexpected args: {args}")

    monkeypatch.setattr(CodeGraphGraphExporter, "_run", fake_run)

    graph = CodeGraphGraphExporter(str(tmp_path), query_limit=10).build()

    symbol_edge = graph["dependencies"][0]["symbol_edges"][0]
    assert symbol_edge["qualified_name"] == "src.billing.client.charge_user"
    assert symbol_edge["match_source"] == "query_json"
    assert symbol_edge["caller_line"] == 3
    assert [args[0] for args in calls].count("query") == 1


def test_codegraph_exporter_caches_duplicate_fallback_caller_queries(tmp_path, monkeypatch):
    (tmp_path / ".codegraph").mkdir()
    (tmp_path / "src" / "billing").mkdir(parents=True)
    (tmp_path / "src" / "admin").mkdir(parents=True)
    (tmp_path / "src" / "billing" / "client_a.py").write_text("def shared_name():\n    return True\n", encoding="utf-8")
    (tmp_path / "src" / "billing" / "client_b.py").write_text("def shared_name():\n    return True\n", encoding="utf-8")
    (tmp_path / "src" / "admin" / "panel.py").write_text("from src.billing.client_a import shared_name\n", encoding="utf-8")

    files_payload = [
        {"path": "src/admin/panel.py", "language": "python", "nodeCount": 1, "size": 43},
        {"path": "src/billing/client_a.py", "language": "python", "nodeCount": 1, "size": 34},
        {"path": "src/billing/client_b.py", "language": "python", "nodeCount": 1, "size": 34},
    ]
    node_outputs = {
        "src/admin/panel.py": (
            "**src/admin/panel.py** — 1 symbols, used by 0 files\n\n"
            "**Symbols**\n"
            "- `render` (function) () — :1\n"
        ),
        "src/billing/client_a.py": (
            "**src/billing/client_a.py** — 1 symbols, used by 1 files: src/admin/panel.py\n\n"
            "**Symbols**\n"
            "- `shared_name` (function) () — :1\n"
        ),
        "src/billing/client_b.py": (
            "**src/billing/client_b.py** — 1 symbols, used by 1 files: src/admin/panel.py\n\n"
            "**Symbols**\n"
            "- `shared_name` (function) () — :1\n"
        ),
    }
    caller_queries = []

    def fake_run(self, args):
        if args[0] == "status":
            return CommandResult(args=["codegraph", *args], returncode=0, stdout="Indexed 3 files", stderr="")
        if args[0] == "files":
            return CommandResult(args=["codegraph", *args], returncode=0, stdout=json.dumps(files_payload), stderr="")
        if args[0] == "node":
            file_path = args[args.index("--file") + 1]
            return CommandResult(args=["codegraph", *args], returncode=0, stdout=node_outputs[file_path], stderr="")
        if args[0] == "query":
            return CommandResult(args=["codegraph", *args], returncode=0, stdout="[]", stderr="")
        if args[0] == "callers":
            caller_queries.append(args[3])
            return CommandResult(
                args=["codegraph", *args],
                returncode=0,
                stdout=json.dumps(
                    {
                        "symbol": args[3],
                        "callers": [{"name": "render", "kind": "function", "filePath": "src/admin/panel.py", "startLine": 4}],
                    }
                ),
                stderr="",
            )
        raise AssertionError(f"Unexpected args: {args}")

    monkeypatch.setattr(CodeGraphGraphExporter, "_run", fake_run)

    graph = CodeGraphGraphExporter(str(tmp_path), query_limit=10).build()

    assert caller_queries == ["shared_name"]
    assert graph["metadata"]["codegraph"]["node"]["caller_cache_hits"] == 1
    assert len(graph["metadata"]["codegraph"]["symbol_edges"]) == 2


def test_codegraph_exporter_records_npx_command_performance_warning(tmp_path, monkeypatch):
    (tmp_path / ".codegraph").mkdir()

    def fake_run(self, args):
        if args[0] == "status":
            return CommandResult(args=["npx", *args], returncode=0, stdout="Indexed 0 files", stderr="")
        if args[0] == "files":
            return CommandResult(args=["npx", *args], returncode=0, stdout="[]", stderr="")
        raise AssertionError(f"Unexpected args: {args}")

    monkeypatch.setattr(CodeGraphGraphExporter, "_run", fake_run)

    graph = CodeGraphGraphExporter(str(tmp_path), command="npx -y @colbymchenry/codegraph").build()

    assert "codegraph_npx_command_slower_than_global_command" in graph["metadata"]["codegraph"]["warnings"]
    assert graph["metadata"]["codegraph"]["command_recommendation"] == "Install CodeGraph as a global command or set command = \"codegraph\" to avoid per-call npx startup cost."


def test_codegraph_export_cli_writes_loadable_external_graph(tmp_path, monkeypatch):
    (tmp_path / ".codegraph").mkdir()
    (tmp_path / "src" / "billing").mkdir(parents=True)
    (tmp_path / "src" / "admin").mkdir(parents=True)
    (tmp_path / "src" / "billing" / "client.py").write_text("def charge_user():\n    return True\n", encoding="utf-8")
    (tmp_path / "src" / "admin" / "panel.py").write_text("from src.billing.client import charge_user\n", encoding="utf-8")

    graph_payload = {
        "name": "fixture",
        "modules": [
            {"name": "billing", "files": ["src/billing/client.py"], "exports": ["charge_user"]},
            {"name": "admin", "files": ["src/admin/panel.py"], "exports": []},
        ],
        "dependencies": [
            {
                "from": "billing",
                "to": "admin",
                "type": "static_import",
                "details": "CodeGraph usage: src/admin/panel.py uses src/billing/client.py",
                "consumer_files": ["src/admin/panel.py"],
                "provider_files": ["src/billing/client.py"],
            }
        ],
        "metadata": {"codegraph": {"enabled": True}},
    }

    monkeypatch.setattr(CodeGraphGraphExporter, "build", lambda self: graph_payload)

    out_path = tmp_path / ".codegraph" / "cross-review.json"
    result = CliRunner().invoke(
        main,
        [
            "codegraph-export",
            "--root",
            str(tmp_path),
            "--out",
            ".codegraph/cross-review.json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Wrote CodeGraph external graph" in result.output
    assert out_path.exists()

    loaded = load_external_project_graph(str(tmp_path), ".codegraph/cross-review.json")
    assert "billing" in loaded.model.modules
    assert any(dep.from_module == "billing" and dep.to_module == "admin" for dep in loaded.model.dependencies)


def test_codegraph_export_cli_creates_parent_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(
        CodeGraphGraphExporter,
        "build",
        lambda self: {"name": "empty", "modules": [], "dependencies": [], "metadata": {}},
    )

    result = CliRunner().invoke(
        main,
        ["codegraph-export", "--root", str(tmp_path), "--out", ".codegraph/cross-review.json"],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".codegraph" / "cross-review.json").exists()


def test_codegraph_export_cli_passes_symbol_query_limits(tmp_path, monkeypatch):
    received = {}

    def fake_init(self, root_dir, command="codegraph", timeout_seconds=60, max_files=500, symbol_limit_per_file=2, caller_limit=20, query_limit=10):
        received.update(
            {
                "root_dir": root_dir,
                "command": command,
                "timeout_seconds": timeout_seconds,
                "max_files": max_files,
                "symbol_limit_per_file": symbol_limit_per_file,
                "caller_limit": caller_limit,
                "query_limit": query_limit,
            }
        )

    monkeypatch.setattr(CodeGraphGraphExporter, "__init__", fake_init)
    monkeypatch.setattr(
        CodeGraphGraphExporter,
        "build",
        lambda self: {"name": "empty", "modules": [], "dependencies": [], "metadata": {}},
    )

    result = CliRunner().invoke(
        main,
        [
            "codegraph-export",
            "--root",
            str(tmp_path),
            "--out",
            ".codegraph/cross-review.json",
            "--command",
            "npx -y @colbymchenry/codegraph",
            "--timeout-seconds",
            "12",
            "--max-files",
            "34",
            "--symbol-limit-per-file",
            "5",
            "--caller-limit",
            "7",
            "--query-limit",
            "9",
        ],
    )

    assert result.exit_code == 0, result.output
    assert received["command"] == "npx -y @colbymchenry/codegraph"
    assert received["timeout_seconds"] == 12
    assert received["max_files"] == 34
    assert received["symbol_limit_per_file"] == 5
    assert received["caller_limit"] == 7
    assert received["query_limit"] == 9


def test_pyproject_exposes_cross_review_console_script():
    pyproject_path = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")
    with open(pyproject_path, "rb") as f:
        pyproject = tomllib.load(f)

    assert pyproject["project"]["scripts"]["cross-review"] == "cross_review.cli:main"
