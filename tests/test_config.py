import json
import tomllib
from pathlib import Path

from click.testing import CliRunner

from cross_review.cli import main
from cross_review.config import load_config
from cross_review.pipeline import ReviewPipeline


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_load_config_reads_cross_review_toml(tmp_path):
    (tmp_path / "cross-review.toml").write_text(
        "[review]\n"
        "top_k = 1\n"
        "lite = true\n"
        "auto_lite_file_threshold = 2\n"
        "targeted_scan_file_threshold = 1000\n"
        "enabled_analyzers = [\"python\", \"sql\"]\n"
        "\n"
        "[context]\n"
        "max_context_lines = 25\n"
        "max_consumer_files = 2\n"
        "\n"
        "ignored_paths = [\"generated/**\"]\n"
        "known_dynamic_boundaries = [\"webhook:stripe\"]\n"
        "\n"
        "[project_graph]\n"
        "external_graph_path = \".codegraph/cross-review.json\"\n"
        "\n"
        "[module_aliases]\n"
        "billing = [\"billing_api\", \"billing_core\"]\n"
        "\n"
        "[path_aliases]\n"
        "\"#billing/\" = \"src/billing/\"\n",
        encoding="utf-8",
    )

    config = load_config(str(tmp_path))

    assert config.review.top_k == 1
    assert config.review.lite is True
    assert config.review.auto_lite_file_threshold == 2
    assert config.review.targeted_scan_file_threshold == 1000
    assert config.review.enabled_analyzers == ["python", "sql"]
    assert config.context.max_diff_lines == 25
    assert config.context.max_consumer_files == 2
    assert config.project_graph.external_graph_path == ".codegraph/cross-review.json"
    assert config.ignored_paths == ["generated/**"]
    assert config.known_dynamic_boundaries == ["webhook:stripe"]
    assert config.module_aliases == {"billing": ["billing_api", "billing_core"]}
    assert config.path_aliases == {"#billing/": "src/billing/"}


def test_default_config_ignores_build_output_paths():
    config = load_config(".")

    assert "dist/**" in config.ignored_paths
    assert "build/**" in config.ignored_paths
    assert "node_modules/**" in config.ignored_paths
    assert "tests/**" in config.ignored_paths
    assert config.review.targeted_scan_file_threshold == 2000


def test_pyproject_uses_pep_621_metadata_and_declares_extras():
    with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
        pyproject = tomllib.load(f)

    project = pyproject["project"]
    assert project["name"] == "cross-review-skill"
    assert project["requires-python"] == ">=3.10"
    assert "tool" not in pyproject or "poetry" not in pyproject["tool"]
    assert pyproject["build-system"]["build-backend"] == "setuptools.build_meta"
    assert pyproject["project"]["scripts"]["cross-review"] == "cross_review.cli:main"
    assert "pytest>=8,<10" in project["optional-dependencies"]["dev"]
    assert "tree-sitter>=0.24,<0.25" in project["optional-dependencies"]["js-ast"]


def test_prepare_reports_optional_js_ast_parser_status(tmp_path, monkeypatch):
    from cross_review.contracts import js_ast

    monkeypatch.setattr(js_ast, "parser_status", lambda: "available")
    app_dir = tmp_path / "src" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "main.ts").write_text("export function render() { return true; }\n", encoding="utf-8")

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["src/app/main.ts"],
        lite=True,
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    assert pack["analysis_config"]["optional_js_ast_parser"] == "available"


def test_prepare_can_use_external_project_graph_from_config(tmp_path, monkeypatch):
    from cross_review.scout import ScoutScanner

    (tmp_path / ".codegraph").mkdir()
    (tmp_path / "cross-review.toml").write_text(
        "[review]\n"
        "top_k = 1\n"
        "\n"
        "[project_graph]\n"
        "external_graph_path = \".codegraph/cross-review.json\"\n",
        encoding="utf-8",
    )
    (tmp_path / ".codegraph" / "cross-review.json").write_text(
        json.dumps({
            "name": "external-fixture",
            "modules": [
                {"name": "billing", "files": ["src/billing/client.py"]},
                {"name": "admin", "files": ["src/admin/panel.py"]},
            ],
            "dependencies": [
                {
                    "from": "billing",
                    "to": "admin",
                    "type": "static_import",
                    "details": "external graph import edge",
                    "consumer_files": ["src/admin/panel.py"],
                    "provider_files": ["src/billing/client.py"],
                }
            ],
        }),
        encoding="utf-8",
    )
    billing_dir = tmp_path / "src" / "billing"
    admin_dir = tmp_path / "src" / "admin"
    billing_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)
    (billing_dir / "client.py").write_text("def charge_user(user_id):\n    return True\n", encoding="utf-8")
    (admin_dir / "panel.py").write_text(
        "from src.billing.client import charge_user\n",
        encoding="utf-8",
    )

    def fail_scan(*args, **kwargs):
        raise AssertionError("built-in scout should not run when external graph is configured")

    monkeypatch.setattr(ScoutScanner, "scan", fail_scan)

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["src/billing/client.py"],
        lite=True,
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    assert pack["analysis_config"]["external_project_graph_path"] == ".codegraph/cross-review.json"
    assert pack["prepare_diagnostics"]["scan_mode"] == "external-graph"
    assert pack["prepare_diagnostics"]["scout_cache_status"] == "bypassed"
    assert pack["project_graph_path"].endswith(".cross-review\\project_graph.json") or pack["project_graph_path"].endswith(".cross-review/project_graph.json")
    assert any(edge["from_module"] == "billing" and edge["to_module"] == "admin" for edge in pack["impact_edges"])


def test_prepare_includes_external_symbol_edges_in_reviewer_pack(tmp_path, monkeypatch):
    from cross_review.scout import ScoutScanner

    (tmp_path / ".codegraph").mkdir()
    (tmp_path / "cross-review.toml").write_text(
        "[review]\n"
        "top_k = 1\n"
        "\n"
        "[project_graph]\n"
        "external_graph_path = \".codegraph/cross-review.json\"\n",
        encoding="utf-8",
    )
    symbol_edge = {
        "symbol": "charge_user",
        "qualified_name": "src.billing.client.charge_user",
        "kind": "function",
        "provider_file": "src/billing/client.py",
        "provider_line": 1,
        "consumer_file": "src/admin/panel.py",
        "caller": "render",
        "caller_kind": "function",
        "caller_line": 3,
        "match_source": "query_json",
    }
    (tmp_path / ".codegraph" / "cross-review.json").write_text(
        json.dumps(
            {
                "name": "external-fixture",
                "modules": [
                    {"name": "billing", "files": ["src/billing/client.py"], "exports": ["charge_user"]},
                    {"name": "admin", "files": ["src/admin/panel.py"], "exports": ["render"]},
                ],
                "dependencies": [
                    {
                        "from": "billing",
                        "to": "admin",
                        "type": "static_import",
                        "details": "CodeGraph usage: src/admin/panel.py uses src/billing/client.py; symbols: charge_user",
                        "consumer_files": ["src/admin/panel.py"],
                        "provider_files": ["src/billing/client.py"],
                        "symbol_edges": [symbol_edge],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    billing_dir = tmp_path / "src" / "billing"
    admin_dir = tmp_path / "src" / "admin"
    billing_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)
    (billing_dir / "client.py").write_text("def charge_user(user_id):\n    return True\n", encoding="utf-8")
    (admin_dir / "panel.py").write_text(
        "from src.billing.client import charge_user\n\n"
        "def render():\n"
        "    return charge_user('u_1')\n",
        encoding="utf-8",
    )

    def fail_scan(*args, **kwargs):
        raise AssertionError("built-in scout should not run when external graph is configured")

    monkeypatch.setattr(ScoutScanner, "scan", fail_scan)

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["src/billing/client.py"],
        lite=True,
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    edge = next(item for item in pack["impact_edges"] if item["from_module"] == "billing" and item["to_module"] == "admin")
    assert edge["symbol_edges"] == [symbol_edge]

    cross_context = next(item for item in pack["cross_review_contexts"] if item["from_module"] == "billing" and item["to_module"] == "admin")
    assert cross_context["symbol_edges"] == [symbol_edge]
    assert "--- 4. CODEGRAPH SYMBOL-LEVEL CALLER EVIDENCE ---" in cross_context["context"]
    assert "src.billing.client.charge_user" in cross_context["context"]
    assert "src/admin/panel.py:3" in cross_context["context"]

    target = pack["agent_assignments"][0]["cross_review_targets"][0]
    assert target["symbol_edges"] == [symbol_edge]


def test_prepare_uses_configured_top_k(tmp_path):
    (tmp_path / "cross-review.toml").write_text(
        "[review]\n"
        "top_k = 1\n",
        encoding="utf-8",
    )
    billing_dir = tmp_path / "src" / "billing"
    admin_dir = tmp_path / "src" / "admin"
    support_dir = tmp_path / "src" / "support"
    billing_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)
    support_dir.mkdir(parents=True)
    (billing_dir / "client.py").write_text(
        "def charge_user(user_id):\n"
        "    return True\n",
        encoding="utf-8",
    )
    (admin_dir / "panel.py").write_text(
        "from src.billing.client import charge_user\n\n"
        "def admin_override(user_id):\n"
        "    return charge_user(user_id)\n",
        encoding="utf-8",
    )
    (support_dir / "dashboard.py").write_text(
        "from src.billing.client import charge_user\n\n"
        "def support_override(user_id):\n"
        "    return charge_user(user_id)\n",
        encoding="utf-8",
    )

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["src/billing/client.py"],
        lite=True,
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    assert len(pack["impact_edges"]) == 1


def test_prepare_includes_configured_module_aliases(tmp_path):
    (tmp_path / "cross-review.toml").write_text(
        "[module_aliases]\n"
        "billing = [\"billing_api\", \"billing_core\"]\n",
        encoding="utf-8",
    )
    billing_api_dir = tmp_path / "src" / "billing_api"
    billing_core_dir = tmp_path / "src" / "billing_core"
    admin_dir = tmp_path / "src" / "admin"
    billing_api_dir.mkdir(parents=True)
    billing_core_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)
    (billing_api_dir / "routes.py").write_text(
        "def charge_user(user_id):\n"
        "    return True\n",
        encoding="utf-8",
    )
    (billing_core_dir / "service.py").write_text(
        "def calculate_invoice(user_id):\n"
        "    return 100\n",
        encoding="utf-8",
    )
    (admin_dir / "panel.py").write_text(
        "from src.billing_api.routes import charge_user\n",
        encoding="utf-8",
    )

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["src/billing_api/routes.py"],
        lite=True,
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    aliases = {
        alias["semantic_module"]: alias
        for alias in pack["semantic_module_splitter"]["suggested_alias_schema"]["aliases"]
    }
    assert aliases["billing"]["physical_modules"] == ["billing_api", "billing_core"]
    assert aliases["billing"]["confidence"] == "configured"


def test_prepare_builds_deterministic_effective_assignments_from_module_aliases(tmp_path):
    (tmp_path / "cross-review.toml").write_text(
        "[module_aliases]\n"
        "billing = [\"billing_api\", \"billing_core\"]\n",
        encoding="utf-8",
    )
    billing_api_dir = tmp_path / "src" / "billing_api"
    billing_core_dir = tmp_path / "src" / "billing_core"
    billing_api_dir.mkdir(parents=True)
    billing_core_dir.mkdir(parents=True)
    (billing_api_dir / "routes.ts").write_text(
        "export function chargeUser(userId: string) { return true; }\n",
        encoding="utf-8",
    )
    (billing_core_dir / "service.ts").write_text(
        "export function calculateInvoice(userId: string) { return 100; }\n",
        encoding="utf-8",
    )

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["src/billing_api/routes.ts", "src/billing_core/service.ts"],
        lite=True,
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    effective = {
        assignment["review_unit"]: assignment
        for assignment in pack["semantic_module_splitter"]["deterministic_effective_assignments"]
    }
    assert "billing" in effective
    assert sorted(effective["billing"]["primary_modules"]) == ["billing_api", "billing_core"]
    assert len(effective["billing"]["source_agent_assignment_ids"]) == 2
    assert effective["billing"]["basis"] == "configured_module_alias"


def test_config_ignored_paths_exclude_graph_and_changed_files(tmp_path):
    (tmp_path / "cross-review.toml").write_text(
        "ignored_paths = [\"generated/**\"]\n",
        encoding="utf-8",
    )
    generated_dir = tmp_path / "generated" / "billing"
    billing_dir = tmp_path / "src" / "billing"
    generated_dir.mkdir(parents=True)
    billing_dir.mkdir(parents=True)
    (generated_dir / "client.py").write_text("def generated_client():\n    return True\n", encoding="utf-8")
    (billing_dir / "client.py").write_text("def charge_user(user_id):\n    return True\n", encoding="utf-8")

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["generated/billing/client.py", "src/billing/client.py"],
        lite=True,
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    assert pack["changed_files"] == ["src/billing/client.py"]
    assert "generated" not in pack["semantic_module_splitter"]["input_summary"]["detected_modules"]


def test_default_ignored_paths_exclude_dist_modules(tmp_path):
    src_dir = tmp_path / "src" / "app"
    dist_dir = tmp_path / "dist"
    src_dir.mkdir(parents=True)
    dist_dir.mkdir(parents=True)
    (src_dir / "main.ts").write_text("export function run() { return true; }\n", encoding="utf-8")
    (dist_dir / "bundle.js").write_text("export function bundled() { return true; }\n", encoding="utf-8")

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["src/app/main.ts"],
        lite=True,
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    detected_modules = pack["semantic_module_splitter"]["input_summary"]["detected_modules"]
    assert "app" in detected_modules
    assert "dist" not in detected_modules


def test_default_ignored_paths_exclude_test_files_from_changed_files(tmp_path):
    app_dir = tmp_path / "src" / "app"
    test_dir = tmp_path / "tests"
    app_dir.mkdir(parents=True)
    test_dir.mkdir(parents=True)
    (app_dir / "main.ts").write_text("export function render() { return true; }\n", encoding="utf-8")
    (test_dir / "app.test.ts").write_text("import { render } from '../src/app/main';\n", encoding="utf-8")

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["tests/app.test.ts", "src/app/main.ts"],
        lite=True,
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    assert pack["changed_files"] == ["src/app/main.ts"]
    assert all(assignment["primary_module"] != "common" for assignment in pack["agent_assignments"])


def test_low_value_tooling_edges_are_omitted_from_top_edges(tmp_path):
    scripts_dir = tmp_path / "scripts"
    exports_dir = tmp_path / "exports"
    scripts_dir.mkdir(parents=True)
    exports_dir.mkdir(parents=True)
    (scripts_dir / "build.ts").write_text(
        "export function emitReviewGateSnapshot(): boolean {\n"
        "  return true;\n"
        "}\n",
        encoding="utf-8",
    )
    (exports_dir / "index.ts").write_text(
        "import { emitReviewGateSnapshot } from '../scripts/build';\n"
        "export function packageExports() {\n"
        "  return emitReviewGateSnapshot();\n"
        "}\n",
        encoding="utf-8",
    )

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["scripts/build.ts"],
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    assert all(
        not (edge["from_module"] == "scripts" and edge["to_module"] == "exports")
        for edge in pack["impact_edges"]
    )
    assert any(
        edge["from_module"] == "scripts" and edge["to_module"] == "exports"
        for edge in pack["context_budget"]["omitted_low_risk_edges"]
    )


def test_project_semantics_are_included_for_host_agent_review(tmp_path):
    (tmp_path / "cross-review.toml").write_text(
        "[project_semantics]\n"
        "review_gates = [\"review-gate\"]\n"
        "forbidden_semantics = [\"Forbidden rows must not render as allowed fallback states.\"]\n"
        "negative_probes = [\"Create a forbidden review-gate fixture and verify it remains blocked.\"]\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "src" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "main.ts").write_text("export function render() { return true; }\n", encoding="utf-8")

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["src/app/main.ts"],
        lite=True,
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    expected_semantics = {
        "review_gates": ["review-gate"],
        "forbidden_semantics": ["Forbidden rows must not render as allowed fallback states."],
        "negative_probes": ["Create a forbidden review-gate fixture and verify it remains blocked."],
    }
    assert pack["analysis_config"]["project_semantics"] == expected_semantics
    assert pack["semantic_module_splitter"]["input_summary"]["project_semantics"] == expected_semantics
    assert any(
        "project_semantics" in item
        for item in pack["semantic_module_splitter"]["host_agent_instructions"]
    )


def test_prepare_extracts_candidate_project_semantics_from_project_docs(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        "# Review rules\n\n"
        "- review-gate must block forbidden rows.\n"
        "- Forbidden rows must not render as allowed fallback states.\n"
        "- Negative probe: create a forbidden review-gate fixture and verify it remains blocked.\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "src" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "main.ts").write_text("export function render() { return true; }\n", encoding="utf-8")

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["src/app/main.ts"],
        lite=True,
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    candidates = pack["analysis_config"]["candidate_project_semantics"]
    assert "review-gate" in candidates["review_gates"]
    assert "Forbidden rows must not render as allowed fallback states." in candidates["forbidden_semantics"]
    assert any("forbidden review-gate fixture" in probe for probe in candidates["negative_probes"])
    assert pack["semantic_module_splitter"]["input_summary"]["candidate_project_semantics"] == candidates
    project_gap = next(gap for gap in pack["configuration_gaps"] if gap["field"] == "project_semantics")
    assert project_gap["candidate_project_semantics"] == candidates


def test_config_enabled_analyzers_can_disable_typescript_contracts(tmp_path):
    (tmp_path / "cross-review.toml").write_text(
        "[review]\n"
        "enabled_analyzers = [\"python\"]\n",
        encoding="utf-8",
    )
    billing_dir = tmp_path / "src" / "billing"
    admin_dir = tmp_path / "src" / "admin"
    billing_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)
    (billing_dir / "client.ts").write_text(
        "export function chargeUser(userId: string): boolean {\n"
        "  return true;\n"
        "}\n",
        encoding="utf-8",
    )
    (admin_dir / "panel.ts").write_text(
        "import { chargeUser } from '../billing/client';\n"
        "export function run(userId: string) { return chargeUser(userId); }\n",
        encoding="utf-8",
    )

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["src/billing/client.ts"]
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    assert pack["contract_graph"]["changed_contracts"] == []
    assert pack["analysis_config"]["enabled_analyzers"] == ["python"]


def test_config_path_aliases_resolve_typescript_imports(tmp_path):
    (tmp_path / "cross-review.toml").write_text(
        "[path_aliases]\n"
        "\"#billing/\" = \"src/billing/\"\n",
        encoding="utf-8",
    )
    billing_dir = tmp_path / "src" / "billing"
    admin_dir = tmp_path / "src" / "admin"
    billing_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)
    (billing_dir / "client.ts").write_text(
        "export function chargeUser(userId: string): boolean {\n"
        "  return true;\n"
        "}\n",
        encoding="utf-8",
    )
    (admin_dir / "panel.ts").write_text(
        "import { chargeUser } from '#billing/client';\n"
        "export function run(userId: string) { return chargeUser(userId); }\n",
        encoding="utf-8",
    )

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["src/billing/client.ts"]
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    edge = pack["impact_edges"][0]
    assert edge["from_module"] == "billing"
    assert edge["to_module"] == "admin"
    assert edge["callsite_ids"]
    assert pack["analysis_config"]["path_aliases"] == {"#billing/": "src/billing/"}


def test_prepare_includes_known_dynamic_boundaries(tmp_path):
    (tmp_path / "cross-review.toml").write_text(
        "known_dynamic_boundaries = [\"webhook:stripe\"]\n",
        encoding="utf-8",
    )
    billing_dir = tmp_path / "src" / "billing"
    billing_dir.mkdir(parents=True)
    (billing_dir / "client.py").write_text("def charge_user(user_id):\n    return True\n", encoding="utf-8")

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["src/billing/client.py"],
        lite=True,
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    assert pack["analysis_config"]["known_dynamic_boundaries"] == ["webhook:stripe"]


def test_prepare_auto_lite_skips_contract_graph_on_large_file_count(tmp_path):
    (tmp_path / "cross-review.toml").write_text(
        "[review]\n"
        "auto_lite_file_threshold = 2\n",
        encoding="utf-8",
    )
    billing_dir = tmp_path / "src" / "billing"
    admin_dir = tmp_path / "src" / "admin"
    support_dir = tmp_path / "src" / "support"
    billing_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)
    support_dir.mkdir(parents=True)
    (billing_dir / "client.py").write_text("def charge_user():\n    return True\n", encoding="utf-8")
    (admin_dir / "panel.py").write_text(
        "from src.billing.client import charge_user\n"
        "def run():\n"
        "    return charge_user()\n",
        encoding="utf-8",
    )
    (support_dir / "dashboard.py").write_text("def show():\n    return True\n", encoding="utf-8")

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["src/billing/client.py"]
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    assert pack["analysis_profile"] == "auto-lite"
    assert pack["analysis_config"]["auto_lite_file_threshold"] == 2
    assert pack["analysis_config"]["scanned_file_count"] == 3
    assert pack["contract_graph"] == {
        "contract_surfaces": [],
        "changed_contracts": [],
        "call_sites": [],
    }
    assert pack["context_budget"]["auto_lite_reason"] == "scanned_file_count_exceeded_threshold"


def test_prepare_uses_targeted_scan_after_diff_on_large_repositories(tmp_path):
    (tmp_path / "cross-review.toml").write_text(
        "[review]\n"
        "top_k = 3\n"
        "auto_lite_file_threshold = 0\n"
        "targeted_scan_file_threshold = 5\n",
        encoding="utf-8",
    )
    billing_dir = tmp_path / "src" / "billing"
    admin_dir = tmp_path / "src" / "admin"
    billing_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)
    (billing_dir / "client.ts").write_text(
        "export function chargeUser(userId: string): boolean {\n"
        "  return true;\n"
        "}\n",
        encoding="utf-8",
    )
    (admin_dir / "panel.ts").write_text(
        "import { chargeUser } from '../billing/client';\n"
        "export function override(userId: string) {\n"
        "  return chargeUser(userId);\n"
        "}\n",
        encoding="utf-8",
    )
    for idx in range(12):
        unrelated_dir = tmp_path / "src" / f"unrelated_{idx}"
        unrelated_dir.mkdir(parents=True)
        (unrelated_dir / "index.ts").write_text(
            f"export function unrelated{idx}() {{ return {idx}; }}\n",
            encoding="utf-8",
        )

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["src/billing/client.ts"],
    )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    diagnostics = pack["prepare_diagnostics"]
    assert diagnostics["scan_mode"] == "targeted"
    assert diagnostics["source_file_count"] > diagnostics["scanned_file_count"]
    detected_modules = pack["semantic_module_splitter"]["input_summary"]["detected_modules"]
    assert "billing" in detected_modules
    assert "admin" in detected_modules
    assert all(not module.startswith("unrelated_") for module in detected_modules)
    assert any(
        edge["from_module"] == "billing" and edge["to_module"] == "admin"
        for edge in pack["impact_edges"]
    )


def test_prepare_reuses_scout_cache_for_same_changed_files(tmp_path):
    billing_dir = tmp_path / "src" / "billing"
    admin_dir = tmp_path / "src" / "admin"
    billing_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)
    (billing_dir / "client.py").write_text(
        "def charge_user(user_id):\n"
        "    return True\n",
        encoding="utf-8",
    )
    (admin_dir / "panel.py").write_text(
        "from src.billing.client import charge_user\n\n"
        "def trigger(user_id):\n"
        "    return charge_user(user_id)\n",
        encoding="utf-8",
    )

    pipeline = ReviewPipeline(root_dir=str(tmp_path))
    first_pack_path = pipeline.prepare(manual_files=["src/billing/client.py"], lite=True)
    with open(first_pack_path, "r", encoding="utf-8") as f:
        first_pack = json.load(f)
    assert first_pack["prepare_diagnostics"]["scout_cache_status"] == "miss"

    second_pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        manual_files=["src/billing/client.py"],
        lite=True,
    )
    with open(second_pack_path, "r", encoding="utf-8") as f:
        second_pack = json.load(f)

    assert second_pack["prepare_diagnostics"]["scout_cache_status"] == "hit"
    assert second_pack["prepare_diagnostics"]["scan_mode"] == "cache-hit"
    assert second_pack["project_graph_path"].endswith("project_graph.json")


def test_cli_init_config_large_repo_writes_tuned_template(tmp_path):
    result = CliRunner().invoke(
        main,
        ["init-config", "--root", str(tmp_path), "--large-repo"],
    )

    assert result.exit_code == 0, result.output
    config_path = tmp_path / "cross-review.toml"
    assert config_path.exists()
    text = config_path.read_text(encoding="utf-8")
    assert "top_k = 1" in text
    assert "auto_lite_file_threshold = 500" in text
    assert "targeted_scan_file_threshold = 500" in text
    assert 'enabled_analyzers = ["python", "sql"]' in text
    assert "max_context_lines = 80" in text
    assert "max_consumer_files = 1" in text
    assert "generated/**" in text
    assert "node_modules/**" in text
    assert 'low_value_modules = ["scripts", "exports", "dist", "build", "generated", "coverage"]' in text
    assert "[project_semantics]" in text
    assert "forbidden_semantics = []" in text
    assert "negative_probes = []" in text
