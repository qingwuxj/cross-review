import json
import os

from cross_review.config import CodeGraphIntegrationConfig, load_config
from cross_review.integrations.codegraph import CodeGraphIntegration, CommandResult
from cross_review.pipeline import ReviewPipeline


def test_codegraph_auto_skips_without_index(tmp_path):
    context = CodeGraphIntegration(
        str(tmp_path),
        CodeGraphIntegrationConfig(enabled="auto"),
    ).collect(["src/app.py"])

    assert context["enabled"] is False
    assert context["status"] == "skipped"
    assert context["reason"] == "no_codegraph_index"


def test_codegraph_collects_cli_context(tmp_path, monkeypatch):
    (tmp_path / ".codegraph").mkdir()
    monkeypatch.setattr("cross_review.integrations.codegraph.shutil.which", lambda command: command)

    calls = []

    def fake_run(self, args):
        calls.append(args)
        if args[0] == "status":
            return CommandResult(args=["codegraph", *args], returncode=0, stdout="Indexed files: 2\n", stderr="")
        if args[0] == "affected":
            return CommandResult(
                args=["codegraph", *args],
                returncode=0,
                stdout=json.dumps({"tests": ["tests/test_app.py"]}),
                stderr="",
            )
        if args[0] == "explore":
            return CommandResult(
                args=["codegraph", *args],
                returncode=0,
                stdout="src/app.py:1 calls src/api.py:1",
                stderr="",
            )
        raise AssertionError(f"Unexpected args: {args}")

    monkeypatch.setattr(CodeGraphIntegration, "_run", fake_run)

    context = CodeGraphIntegration(
        str(tmp_path),
        CodeGraphIntegrationConfig(enabled="true", affected_depth=2),
    ).collect(["src/app.py"])

    assert context["enabled"] is True
    assert context["status"] == "enabled"
    assert context["affected"] == {"tests": ["tests/test_app.py"]}
    assert "src/app.py:1" in context["explore"]
    assert calls[0][0] == "status"
    assert calls[1] == ["affected", "src/app.py", "--depth", "2", "--json"]
    assert calls[2][0] == "explore"


def test_codegraph_command_may_include_arguments(tmp_path, monkeypatch):
    (tmp_path / ".codegraph").mkdir()
    monkeypatch.setattr("cross_review.integrations.codegraph.shutil.which", lambda command: f"C:/bin/{command}.cmd")

    captured = []

    def fake_subprocess_run(args, **kwargs):
        captured.append(args)

        class Completed:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return Completed()

    monkeypatch.setattr("cross_review.integrations.codegraph.subprocess.run", fake_subprocess_run)

    CodeGraphIntegration(
        str(tmp_path),
        CodeGraphIntegrationConfig(enabled="true", command="npx -y @colbymchenry/codegraph"),
    ).collect([])

    assert captured[0][:4] == ["C:/bin/npx.cmd", "-y", "@colbymchenry/codegraph", "status"]


def test_config_loads_codegraph_integration_table(tmp_path):
    (tmp_path / "cross-review.toml").write_text(
        """
[integrations.codegraph]
enabled = false
command = "cg"
timeout_seconds = 7
max_explore_chars = 99
affected_depth = 4
""",
        encoding="utf-8",
    )

    config = load_config(str(tmp_path))

    assert config.integrations.codegraph.enabled == "false"
    assert config.integrations.codegraph.command == "cg"
    assert config.integrations.codegraph.timeout_seconds == 7
    assert config.integrations.codegraph.max_explore_chars == 99
    assert config.integrations.codegraph.affected_depth == 4


def test_prepare_writes_codegraph_context_into_pack(tmp_path, monkeypatch):
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "main.py").write_text("def run():\n    return True\n", encoding="utf-8")
    (tmp_path / ".codegraph").mkdir()

    def fake_collect(self, changed_files):
        return {
            "enabled": True,
            "available": True,
            "index_present": True,
            "status": "enabled",
            "reason": None,
            "source": "codegraph-cli",
            "mode": "auto",
            "command": "codegraph",
            "changed_files": changed_files,
            "affected": {"tests": []},
            "explore": "blast radius context",
            "commands": {},
            "usage_notes": [],
        }

    monkeypatch.setattr(CodeGraphIntegration, "collect", fake_collect)

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(manual_files=["src/app/main.py"], lite=True)

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    context_path = os.path.join(str(tmp_path), ".cross-review", "codegraph_context.json")
    assert os.path.exists(context_path)
    assert pack["integrations"]["codegraph"]["enabled"] is True
    assert pack["integrations"]["codegraph"]["explore"] == "blast radius context"
    assert pack["integration_context_paths"]["codegraph"] == context_path
    assert pack["prepare_diagnostics"]["timings_ms"]["codegraph_ms"] >= 0


def test_prepare_reports_codegraph_as_numbered_step(tmp_path, monkeypatch, capsys):
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "main.py").write_text("def run():\n    return True\n", encoding="utf-8")

    monkeypatch.setattr(
        CodeGraphIntegration,
        "collect",
        lambda self, changed_files: {
            "enabled": False,
            "available": False,
            "index_present": False,
            "status": "skipped",
            "reason": "no_codegraph_index",
            "source": "codegraph-cli",
            "mode": "auto",
            "command": "codegraph",
            "changed_files": changed_files,
            "affected": None,
            "explore": "",
            "commands": {},
            "usage_notes": [],
        },
    )

    ReviewPipeline(root_dir=str(tmp_path)).prepare(manual_files=["src/app/main.py"], lite=True)

    output = capsys.readouterr().out
    assert "[1/6] Detecting changed files..." in output
    assert "[2/6] Checking optional CodeGraph integration..." in output
    assert "[3/6] Scouting project structure and dependencies..." in output
    assert "[6/6] Writing agent review pack..." in output
    assert "[1/5]" not in output


def test_prepare_attaches_trimmed_codegraph_context_to_assignments(tmp_path, monkeypatch):
    (tmp_path / "src" / "billing").mkdir(parents=True)
    (tmp_path / "src" / "admin").mkdir(parents=True)
    (tmp_path / "src" / "billing" / "client.py").write_text(
        "def charge_user(user_id):\n    return True\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "admin" / "panel.py").write_text(
        "from src.billing.client import charge_user\n\n"
        "def run():\n"
        "    return charge_user('u1')\n",
        encoding="utf-8",
    )

    def fake_collect(self, changed_files):
        return {
            "enabled": True,
            "available": True,
            "index_present": True,
            "status": "enabled",
            "reason": None,
            "source": "codegraph-cli",
            "mode": "auto",
            "command": "codegraph",
            "changed_files": changed_files,
            "affected": {"tests": ["tests/test_billing.py"]},
            "explore": "billing client callers include src/admin/panel.py\n" * 200,
            "commands": {"explore": {"returncode": 0}},
            "usage_notes": ["supplemental only"],
        }

    monkeypatch.setattr(CodeGraphIntegration, "collect", fake_collect)

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["src/billing/client.py"],
        lite=True,
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    assignment = next(item for item in pack["agent_assignments"] if item["primary_module"] == "billing")
    codegraph_context = assignment["integration_context"]["codegraph"]

    assert codegraph_context["enabled"] is True
    assert codegraph_context["affected"] == {"tests": ["tests/test_billing.py"]}
    assert codegraph_context["explore_excerpt"].endswith("[truncated]")
    assert len(codegraph_context["explore_excerpt"]) <= 1212
    assert "supplemental only" in codegraph_context["usage_notes"]
