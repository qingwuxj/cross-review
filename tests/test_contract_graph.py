import json
import os
import shutil
import importlib

import git

from cross_review.contracts.contract_graph import ContractGraphBuilder
from cross_review.contracts import js_ast
from cross_review.pipeline import ReviewPipeline
from cross_review.scout import ScoutScanner


TEST_DIR = os.path.dirname(os.path.abspath(__file__))
EXAMPLES_DIR = os.path.join(os.path.dirname(TEST_DIR), "examples")


def test_contract_analyzers_are_split_by_language_module():
    builder_module = importlib.import_module("cross_review.contracts.builder")

    assert builder_module.ContractGraphBuilder is ContractGraphBuilder

    expected_exports = {
        "cross_review.contracts.python": ["parse_python_files", "extract_python_surfaces", "extract_python_call_sites"],
        "cross_review.contracts.typescript": ["parse_text_files", "extract_typescript_surfaces", "extract_typescript_call_sites"],
        "cross_review.contracts.sql": ["extract_sql_surfaces"],
        "cross_review.contracts.graphql": ["extract_graphql_surfaces", "extract_graphql_call_sites"],
        "cross_review.contracts.protobuf": ["extract_proto_surfaces", "extract_proto_call_sites"],
        "cross_review.contracts.js_ast": ["parser_available", "parser_status", "extract_imports"],
    }

    for module_name, export_names in expected_exports.items():
        module = importlib.import_module(module_name)
        for export_name in export_names:
            assert hasattr(module, export_name), f"{module_name} missing {export_name}"


def test_contract_graph_extracts_python_contracts_and_callsites(tmp_path):
    billing_dir = tmp_path / "src" / "billing"
    admin_dir = tmp_path / "src" / "admin"
    billing_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)
    (billing_dir / "client.py").write_text(
        "def charge_user(user_id: str, amount: int) -> bool:\n"
        "    return True\n",
        encoding="utf-8",
    )
    (admin_dir / "panel.py").write_text(
        "from src.billing.client import charge_user\n\n"
        "def trigger_billing_override(user_id):\n"
        "    return charge_user(user_id, 100)\n",
        encoding="utf-8",
    )

    graph = ScoutScanner(str(tmp_path)).scan().model
    contract_graph = ContractGraphBuilder(str(tmp_path), graph).build(
        changed_files=["src/billing/client.py"]
    )

    contract_id = "python:function:src/billing/client.py:charge_user"
    assert contract_id in {surface.contract_id for surface in contract_graph.contract_surfaces}
    assert contract_id in {changed.contract_id for changed in contract_graph.changed_contracts}

    callsite = next(site for site in contract_graph.call_sites if site.contract_id == contract_id)
    assert callsite.consumer_module == "admin"
    assert callsite.provider_module == "billing"
    assert callsite.file == "src/admin/panel.py"
    assert callsite.usage == "charge_user(user_id, 100)"


def test_contract_graph_resolves_module_alias_callsites(tmp_path):
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
        "from src.billing import client\n\n"
        "def trigger_billing_override(user_id):\n"
        "    return client.charge_user(user_id)\n",
        encoding="utf-8",
    )

    graph = ScoutScanner(str(tmp_path)).scan().model
    contract_graph = ContractGraphBuilder(str(tmp_path), graph).build(
        changed_files=["src/billing/client.py"]
    )

    contract_id = "python:function:src/billing/client.py:charge_user"
    callsite = next(site for site in contract_graph.call_sites if site.contract_id == contract_id)
    assert callsite.usage == "client.charge_user(user_id)"


def test_contract_graph_resolves_class_constructor_callsites(tmp_path):
    billing_dir = tmp_path / "src" / "billing"
    admin_dir = tmp_path / "src" / "admin"
    billing_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)
    (billing_dir / "models.py").write_text(
        "class BillingPlan:\n"
        "    def __init__(self, user_id: str, plan_tier: str):\n"
        "        self.user_id = user_id\n",
        encoding="utf-8",
    )
    (admin_dir / "panel.py").write_text(
        "from src.billing.models import BillingPlan\n\n"
        "def create_default_plan(user_id):\n"
        "    return BillingPlan(user_id)\n",
        encoding="utf-8",
    )

    graph = ScoutScanner(str(tmp_path)).scan().model
    contract_graph = ContractGraphBuilder(str(tmp_path), graph).build(
        changed_files=["src/billing/models.py"]
    )

    contract_id = "python:class:src/billing/models.py:BillingPlan"
    callsite = next(site for site in contract_graph.call_sites if site.contract_id == contract_id)
    assert callsite.consumer_module == "admin"
    assert callsite.usage == "BillingPlan(user_id)"


def test_contract_graph_maps_sqlalchemy_tablename_constructor_to_sql_column_callsite(tmp_path):
    migration_dir = tmp_path / "src" / "db" / "migrations"
    billing_dir = tmp_path / "src" / "billing"
    migration_dir.mkdir(parents=True)
    billing_dir.mkdir(parents=True)
    (migration_dir / "2026_add_plan_code.sql").write_text(
        "ALTER TABLE subscriptions ADD COLUMN plan_code TEXT NOT NULL;\n",
        encoding="utf-8",
    )
    (billing_dir / "models.py").write_text(
        "class Subscription(Base):\n"
        "    __tablename__ = 'subscriptions'\n"
        "    user_id = Column(String)\n",
        encoding="utf-8",
    )
    (billing_dir / "repository.py").write_text(
        "from src.billing.models import Subscription\n\n"
        "def create_subscription(user_id):\n"
        "    return Subscription(user_id=user_id)\n",
        encoding="utf-8",
    )

    graph = ScoutScanner(str(tmp_path)).scan().model
    contract_graph = ContractGraphBuilder(str(tmp_path), graph).build(
        changed_files=["src/db/migrations/2026_add_plan_code.sql"]
    )

    contract_id = "sql:column:src/db/migrations/2026_add_plan_code.sql:subscriptions.plan_code"
    callsite = next(site for site in contract_graph.call_sites if site.contract_id == contract_id)
    assert callsite.consumer_module == "billing"
    assert callsite.provider_module == "db"
    assert callsite.usage == "Subscription(user_id=user_id)"


def test_contract_graph_extracts_typescript_arrow_exports_and_callsites(tmp_path):
    billing_dir = tmp_path / "src" / "billing"
    admin_dir = tmp_path / "src" / "admin"
    billing_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)
    (billing_dir / "client.ts").write_text(
        "export const chargeUser = (userId: string, amount: number): boolean => {\n"
        "  return true;\n"
        "};\n",
        encoding="utf-8",
    )
    (admin_dir / "panel.ts").write_text(
        "import { chargeUser } from '../billing/client';\n\n"
        "export function triggerBillingOverride(userId: string) {\n"
        "  return chargeUser(userId, 100);\n"
        "}\n",
        encoding="utf-8",
    )

    graph = ScoutScanner(str(tmp_path)).scan().model
    contract_graph = ContractGraphBuilder(str(tmp_path), graph).build(
        changed_files=["src/billing/client.ts"]
    )

    contract_id = "typescript:function:src/billing/client.ts:chargeUser"
    assert contract_id in {surface.contract_id for surface in contract_graph.contract_surfaces}
    callsite = next(site for site in contract_graph.call_sites if site.contract_id == contract_id)
    assert callsite.usage == "return chargeUser(userId, 100);"


def test_contract_graph_extracts_fastify_routes_and_frontend_fetch_callsites(tmp_path):
    api_dir = tmp_path / "src" / "api"
    web_dir = tmp_path / "src" / "web"
    api_dir.mkdir(parents=True)
    web_dir.mkdir(parents=True)
    (api_dir / "orders.ts").write_text(
        "import Fastify from 'fastify';\n"
        "const fastify = Fastify();\n\n"
        "fastify.get('/orders/:orderId', async (request, reply) => {\n"
        "  return { ok: true };\n"
        "});\n",
        encoding="utf-8",
    )
    (web_dir / "orders.tsx").write_text(
        "export async function loadOrder(orderId: string) {\n"
        "  return fetch(`/orders/${orderId}`);\n"
        "}\n",
        encoding="utf-8",
    )

    graph = ScoutScanner(str(tmp_path)).scan().model
    contract_graph = ContractGraphBuilder(str(tmp_path), graph).build(
        changed_files=["src/api/orders.ts"]
    )

    contract_id = "typescript:route:src/api/orders.ts:/orders/:orderId"
    assert contract_id in {surface.contract_id for surface in contract_graph.contract_surfaces}
    assert any(
        callsite.contract_id == contract_id
        and callsite.consumer_module == "web"
        and callsite.provider_module == "api"
        for callsite in contract_graph.call_sites
    )


def test_contract_graph_resolves_typescript_import_alias_callsites(tmp_path):
    billing_dir = tmp_path / "src" / "billing"
    admin_dir = tmp_path / "src" / "admin"
    billing_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)
    (billing_dir / "client.ts").write_text(
        "export function chargeUser(userId: string, amount: number): boolean {\n"
        "  return true;\n"
        "}\n",
        encoding="utf-8",
    )
    (admin_dir / "panel.ts").write_text(
        "import { chargeUser as billUser } from '../billing/client';\n\n"
        "export function triggerBillingOverride(userId: string) {\n"
        "  return billUser(userId, 100);\n"
        "}\n",
        encoding="utf-8",
    )

    graph = ScoutScanner(str(tmp_path)).scan().model
    contract_graph = ContractGraphBuilder(str(tmp_path), graph).build(
        changed_files=["src/billing/client.ts"]
    )

    contract_id = "typescript:function:src/billing/client.ts:chargeUser"
    callsite = next(site for site in contract_graph.call_sites if site.contract_id == contract_id)
    assert callsite.usage == "return billUser(userId, 100);"


def test_optional_js_ast_import_statement_parser_handles_combined_imports():
    imports = js_ast._imports_from_statement(
        "import billingClient, { chargeUser as billUser, type BillingPlan } from '../billing/client';"
    )

    assert imports["billingClient"] == {
        "imported_name": "default",
        "import_path": "../billing/client",
    }
    assert imports["billUser"] == {
        "imported_name": "chargeUser",
        "import_path": "../billing/client",
    }
    assert imports["BillingPlan"] == {
        "imported_name": "BillingPlan",
        "import_path": "../billing/client",
    }


def test_contract_graph_uses_optional_js_ast_imports_for_combined_import_callsites(tmp_path, monkeypatch):
    billing_dir = tmp_path / "src" / "billing"
    admin_dir = tmp_path / "src" / "admin"
    billing_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)
    (billing_dir / "client.ts").write_text(
        "export function chargeUser(userId: string, amount: number): boolean {\n"
        "  return true;\n"
        "}\n",
        encoding="utf-8",
    )
    (admin_dir / "panel.ts").write_text(
        "import billingClient, { chargeUser as billUser } from '../billing/client';\n\n"
        "export function triggerBillingOverride(userId: string) {\n"
        "  return billUser(userId, 100);\n"
        "}\n",
        encoding="utf-8",
    )

    def fake_extract_imports(source, source_type="typescript"):
        if "chargeUser as billUser" not in source:
            return {}
        return {
            "billingClient": {
                "imported_name": "default",
                "import_path": "../billing/client",
            },
            "billUser": {
                "imported_name": "chargeUser",
                "import_path": "../billing/client",
            },
        }

    monkeypatch.setattr(js_ast, "extract_imports", fake_extract_imports)

    graph = ScoutScanner(str(tmp_path)).scan().model
    contract_graph = ContractGraphBuilder(str(tmp_path), graph).build(
        changed_files=["src/billing/client.ts"]
    )

    contract_id = "typescript:function:src/billing/client.ts:chargeUser"
    callsite = next(site for site in contract_graph.call_sites if site.contract_id == contract_id)
    assert callsite.consumer_module == "admin"
    assert callsite.usage == "return billUser(userId, 100);"


def test_contract_graph_marks_signature_changed_from_before_snapshot(tmp_path):
    billing_dir = tmp_path / "src" / "billing"
    before_dir = tmp_path / ".cross-review-before" / "src" / "billing"
    billing_dir.mkdir(parents=True)
    before_dir.mkdir(parents=True)
    (before_dir / "client.py").write_text(
        "def charge_user(user_id: int, amount: float) -> bool:\n"
        "    return True\n",
        encoding="utf-8",
    )
    (billing_dir / "client.py").write_text(
        "def charge_user(user_id: int, amount: float, currency: str = 'USD') -> bool:\n"
        "    return True\n",
        encoding="utf-8",
    )

    graph = ScoutScanner(str(tmp_path)).scan().model
    contract_graph = ContractGraphBuilder(str(tmp_path), graph).build(
        changed_files=["src/billing/client.py"]
    )

    changed = next(
        item
        for item in contract_graph.changed_contracts
        if item.contract_id == "python:function:src/billing/client.py:charge_user"
    )
    assert changed.change_type == "signature_changed"
    assert changed.previous_signature == "charge_user(user_id: int, amount: float) -> bool"
    assert changed.current_signature == "charge_user(user_id: int, amount: float, currency: str='USD') -> bool"
    assert "currency" in changed.diff_summary


def test_contract_graph_marks_signature_changed_from_previous_source_provider(tmp_path):
    billing_dir = tmp_path / "src" / "billing"
    billing_dir.mkdir(parents=True)
    (billing_dir / "client.py").write_text(
        "def charge_user(user_id: int, amount: float, currency: str = 'USD') -> bool:\n"
        "    return True\n",
        encoding="utf-8",
    )

    graph = ScoutScanner(str(tmp_path)).scan().model
    old_sources = {
        "src/billing/client.py": (
            "def charge_user(user_id: int, amount: float) -> bool:\n"
            "    return True\n"
        )
    }
    contract_graph = ContractGraphBuilder(
        str(tmp_path),
        graph,
        previous_source_provider=lambda path: old_sources.get(path),
    ).build(changed_files=["src/billing/client.py"])

    changed = next(
        item
        for item in contract_graph.changed_contracts
        if item.contract_id == "python:function:src/billing/client.py:charge_user"
    )
    assert changed.change_type == "signature_changed"
    assert changed.previous_signature == "charge_user(user_id: int, amount: float) -> bool"
    assert changed.current_signature == "charge_user(user_id: int, amount: float, currency: str='USD') -> bool"


def test_prepare_uses_git_head_content_for_signature_changed(tmp_path):
    billing_dir = tmp_path / "src" / "billing"
    admin_dir = tmp_path / "src" / "admin"
    billing_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)
    (billing_dir / "client.py").write_text(
        "def charge_user(user_id: int, amount: float) -> bool:\n"
        "    return True\n",
        encoding="utf-8",
    )
    (admin_dir / "panel.py").write_text(
        "from src.billing.client import charge_user\n\n"
        "def trigger_billing_override(user_id):\n"
        "    return charge_user(user_id, 100)\n",
        encoding="utf-8",
    )

    repo = git.Repo.init(str(tmp_path))
    repo.index.add(["src/billing/client.py", "src/admin/panel.py"])
    actor = git.Actor("Cross Review Test", "cross-review@example.com")
    repo.index.commit("initial", author=actor, committer=actor)

    (billing_dir / "client.py").write_text(
        "def charge_user(user_id: int, amount: float, currency: str = 'USD') -> bool:\n"
        "    return True\n",
        encoding="utf-8",
    )

    pack_path = ReviewPipeline(root_dir=str(tmp_path)).prepare(
        base_branch="HEAD",
        manual_files=["src/billing/client.py"],
        diff_mode="worktree",
    )
    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    changed = next(
        item
        for item in pack["contract_graph"]["changed_contracts"]
        if item["contract_id"] == "python:function:src/billing/client.py:charge_user"
    )
    assert changed["change_type"] == "signature_changed"
    assert changed["previous_signature"] == "charge_user(user_id: int, amount: float) -> bool"
    assert changed["current_signature"] == "charge_user(user_id: int, amount: float, currency: str='USD') -> bool"


def test_prepare_pack_includes_contract_graph_edge_evidence():
    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

    pack_path = ReviewPipeline(root_dir=project_path, cache_dir=".cross-review").prepare(
        manual_files=["src/billing/client.py"]
    )
    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    assert "contract_graph" in pack
    assert pack["contract_graph"]["changed_contracts"]
    assert pack["contract_graph"]["call_sites"]

    edge = pack["impact_edges"][0]
    assert edge["changed_contract_ids"] == ["python:function:src/billing/client.py:charge_user"]
    assert edge["callsite_ids"]

    context = pack["cross_review_contexts"][0]
    assert context["changed_contracts"]
    assert context["downstream_call_sites"]
    assert "Changed Contract Evidence" in context["context"]
    assert edge["changed_contract_ids"][0] in context["context"]
    assert edge["callsite_ids"][0] in context["context"]

    target = pack["agent_assignments"][0]["cross_review_targets"][0]
    assert target["changed_contract_ids"] == edge["changed_contract_ids"]
    assert target["callsite_ids"] == edge["callsite_ids"]
