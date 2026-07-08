import os
import shutil
import json
import inspect
import ast
import pytest
from click.testing import CliRunner
from cross_review.scout import ScoutScanner
from cross_review.impact_scorer import ImpactScorer
from cross_review.pipeline import ReviewPipeline
from cross_review.context_pack import ContextPackager
from cross_review.diff import GitDiffParser
from cross_review.llm import LLMClient, LLMValidationError
from cross_review.graph import ProjectGraph
from cross_review.cli import main
from cross_review.schemas.models import FinalReportModel, ModuleReviewModel, EdgeModel

# 获取当前测试文件的根目录，以便找到 examples 文件夹
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
EXAMPLES_DIR = os.path.join(os.path.dirname(TEST_DIR), "examples")

def test_toy_api_break_scout():
    """
    测试 ScoutScanner 在 toy_api_break 示例上扫描
    是否能正确检测出 billing 和 admin 模块，并画出静态依赖边。
    """
    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    assert os.path.exists(project_path), f"Path not found: {project_path}"

    scanner = ScoutScanner(project_path)
    graph = scanner.scan()

    assert "billing" in graph.model.modules
    assert "admin" in graph.model.modules
    assert "charge_user" in graph.model.modules["billing"].exports
    assert "trigger_billing_override" in graph.model.modules["admin"].exports

    deps = graph.model.dependencies
    assert len(deps) > 0
    has_dep = any(
        d.from_module == "billing"
        and d.to_module == "admin"
        and d.type == "static_import"
        and "src/admin/panel.py" in d.consumer_files
        for d in deps
    )
    assert has_dep

def test_event_contract_dependency_tracks_downstream_consumer_file(tmp_path):
    """
    Event dependencies should use the same provider -> consumer direction as
    static dependencies and should carry structured consumer file metadata.
    """
    (tmp_path / "src" / "order").mkdir(parents=True)
    (tmp_path / "src" / "notification").mkdir(parents=True)
    (tmp_path / "src" / "order" / "events.py").write_text(
        "def publish_order_paid(event):\n    trigger('OrderPaid', event)\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "notification" / "listener.py").write_text(
        "def handler(event):\n    pass\n\nsubscribe('OrderPaid', handler)\n",
        encoding="utf-8",
    )

    graph = ScoutScanner(str(tmp_path)).scan().model
    event_dep = next(dep for dep in graph.dependencies if dep.type == "event_contract")

    assert event_dep.from_module == "order"
    assert event_dep.to_module == "notification"
    assert "src/notification/listener.py" in event_dep.consumer_files

    pack = ContextPackager(str(tmp_path), graph).build_cross_pack(
        "order",
        "notification",
        ["src/order/events.py"],
        diff_parser=None,
    )
    assert "src/notification/listener.py" in pack

def test_scout_splits_flat_root_files_by_file_stem(tmp_path):
    """
    Flat projects should not collapse every root-level file into one common
    module, otherwise cross-review has no useful module boundary.
    """
    (tmp_path / "billing.py").write_text(
        "def charge_user():\n    return True\n",
        encoding="utf-8",
    )
    (tmp_path / "admin.py").write_text(
        "import billing\n\ndef trigger_billing_override():\n    return billing.charge_user()\n",
        encoding="utf-8",
    )

    graph = ScoutScanner(str(tmp_path)).scan().model

    assert "billing" in graph.modules
    assert "admin" in graph.modules
    assert "common" not in graph.modules
    assert any(dep.from_module == "billing" and dep.to_module == "admin" for dep in graph.dependencies)

def test_scout_uses_nearest_src_child_for_nested_monorepo_paths(tmp_path):
    """
    Monorepo layouts such as apps/backend/src/billing should split by the
    module folder under src, not collapse everything into apps.
    """
    billing_dir = tmp_path / "apps" / "backend" / "src" / "billing"
    admin_dir = tmp_path / "apps" / "backend" / "src" / "admin"
    billing_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)
    (billing_dir / "client.py").write_text("def charge_user():\n    return True\n", encoding="utf-8")
    (admin_dir / "panel.py").write_text("from src.billing import client\n", encoding="utf-8")

    graph = ScoutScanner(str(tmp_path)).scan().model

    assert "billing" in graph.modules
    assert "admin" in graph.modules
    assert "apps" not in graph.modules

def test_scout_uses_ast_for_events_and_ignores_commented_calls(tmp_path):
    """
    Event extraction should understand formatted Python calls and avoid false
    positives from comments.
    """
    (tmp_path / "src" / "order").mkdir(parents=True)
    (tmp_path / "src" / "notification").mkdir(parents=True)
    (tmp_path / "src" / "order" / "events.py").write_text(
        "def publish(event):\n"
        "    # trigger('CommentOnly', event)\n"
        "    trigger(\n"
        "        'OrderPaid',\n"
        "        event,\n"
        "    )\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "notification" / "listener.py").write_text(
        "def handler(event):\n"
        "    pass\n\n"
        "subscribe(\n"
        "    'OrderPaid',\n"
        "    handler,\n"
        ")\n",
        encoding="utf-8",
    )

    graph = ScoutScanner(str(tmp_path)).scan().model

    assert graph.modules["order"].events == ["OrderPaid"]
    assert "CommentOnly" not in graph.modules["order"].events
    assert any(dep.type == "event_contract" for dep in graph.dependencies)

def test_scout_uses_ast_for_multiline_fastapi_routes(tmp_path):
    """
    Route extraction should use Python syntax, not line-oriented text matching.
    """
    (tmp_path / "src" / "api").mkdir(parents=True)
    (tmp_path / "src" / "api" / "routes.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n\n"
        "@router.post(\n"
        "    '/orders/{order_id}',\n"
        ")\n"
        "def create_order(order_id: int):\n"
        "    return order_id\n",
        encoding="utf-8",
    )

    graph = ScoutScanner(str(tmp_path)).scan().model

    assert graph.modules["api"].routes == ["/orders/{order_id}"]

def test_scout_does_not_python_ast_parse_non_python_files(tmp_path, monkeypatch):
    """
    Large TS/JS repos should not pay Python AST parse cost for files that can
    never parse as Python.
    """
    (tmp_path / "src" / "billing").mkdir(parents=True)
    (tmp_path / "src" / "admin").mkdir(parents=True)
    (tmp_path / "src" / "billing" / "client.py").write_text(
        "def charge_user():\n    return True\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "admin" / "panel.ts").write_text(
        "export function run() { return true; }\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "admin" / "panel.js").write_text(
        "export function run() { return true; }\n",
        encoding="utf-8",
    )

    original_parse = ast.parse
    parsed_filenames = []

    def tracking_parse(source, filename="<unknown>", mode="exec", **kwargs):
        parsed_filenames.append(str(filename))
        return original_parse(source, filename=filename, mode=mode, **kwargs)

    monkeypatch.setattr("cross_review.scout.ast.parse", tracking_parse)

    ScoutScanner(str(tmp_path)).scan()

    assert parsed_filenames
    assert all(not name.endswith((".ts", ".js")) for name in parsed_filenames)

def test_toy_api_break_scorer():
    """
    测试 ImpactScorer 的打分和 Top-K 裁剪逻辑。
    """
    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    scanner = ScoutScanner(project_path)
    graph = scanner.scan()

    # 模拟修改了 src/billing/client.py
    changed_files = ["src/billing/client.py"]

    scorer = ImpactScorer(graph.model, changed_files, repo_path=project_path)
    changed_mods = scorer.get_changed_modules()
    assert "billing" in changed_mods

    top_edges = scorer.calculate_scores(K=3)
    assert len(top_edges) > 0
    
    target_edge = None
    for edge in top_edges:
        if edge.from_module == "billing" and edge.to_module == "admin":
            target_edge = edge
            break

    assert target_edge is not None
    assert target_edge.force_triggered
    assert target_edge.risk_score == 1.0

def test_pipeline_requires_changed_files_when_git_is_unavailable(tmp_path, monkeypatch):
    """
    Non-Git projects must pass explicit files instead of receiving a demo mock
    changed file that could mislead a real review.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    source_dir = tmp_path / "src" / "billing"
    source_dir.mkdir(parents=True)
    (source_dir / "subscription.py").write_text("def renew_subscription():\n    return True\n", encoding="utf-8")

    pipeline = ReviewPipeline(root_dir=str(tmp_path), cache_dir=".cross-review")

    with pytest.raises(RuntimeError, match="Unable to detect changed files"):
        pipeline.prepare()

    with pytest.raises(RuntimeError, match="Unable to detect changed files"):
        pipeline.run()

def test_pipeline_dry_run_shape_and_warning(monkeypatch):
    """
    Point 2: 验证 Mock 模式的基础形状与免责警告 (is_mock, warning banner, schema shape)。
    不对硬编码 findings 的内容做真 bug 语义断言。
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

    pipeline = ReviewPipeline(root_dir=project_path, cache_dir=".cross-review")
    report_json_path = pipeline.run(
        base_branch="main",
        head_branch="HEAD",
        manual_files=[os.path.join(project_path, "src/billing/client.py")]
    )

    assert os.path.exists(report_json_path)

    with open(report_json_path, "r", encoding="utf-8") as f:
        report_data = json.load(f)

    final_report = FinalReportModel.model_validate(report_data)
    assert final_report.is_mock is True
    assert final_report.overall_risk == "high"

    report_md_path = os.path.join(cache_dir, "final_report.md")
    assert os.path.exists(report_md_path)
    with open(report_md_path, "r", encoding="utf-8") as f:
        markdown_text = f.read()

    assert "MOCK / DRY-RUN 模式警告" in markdown_text
    assert "overall_risk" not in markdown_text
    assert "blocking" in final_report.findings
    assert "high" in final_report.findings
    assert "needs_human_review" in final_report.findings

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

def test_pipeline_accepts_root_relative_manual_files(monkeypatch):
    """
    Manual file paths should be interpreted relative to the selected review root,
    not relative to the shell's current working directory.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

    pipeline = ReviewPipeline(root_dir=project_path, cache_dir=".cross-review")
    pipeline.run(
        base_branch="main",
        head_branch="HEAD",
        manual_files=["src/billing/client.py"],
    )

    with open(os.path.join(cache_dir, "impact_edges.json"), "r", encoding="utf-8") as f:
        edges = [EdgeModel.model_validate(item) for item in json.load(f)]

    assert any(edge.from_module == "billing" and edge.to_module == "admin" for edge in edges)

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

def test_cli_review_supports_explicit_root(monkeypatch):
    """
    The CLI should let users review a target project path without changing into it.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

    result = CliRunner().invoke(
        main,
        ["review", "--root", project_path, "--files", "src/billing/client.py"],
    )

    assert result.exit_code == 0, result.output
    assert os.path.exists(os.path.join(cache_dir, "final_report.json"))

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

def test_prepare_generates_agent_review_pack_without_api_keys(monkeypatch):
    """
    Agent-native mode should prepare deterministic context for the host agent
    without requiring Gemini/OpenAI API keys or calling the LLM client.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

    pipeline = ReviewPipeline(root_dir=project_path, cache_dir=".cross-review")
    pack_path = pipeline.prepare(manual_files=["src/billing/client.py"])

    assert os.path.exists(pack_path)
    with open(pack_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["mode"] == "agent"
    assert data["requires_external_api_key"] is False
    assert data["changed_files"] == ["src/billing/client.py"]
    assert any(edge["from_module"] == "billing" and edge["to_module"] == "admin" for edge in data["impact_edges"])
    assert any(item["module_name"] == "billing" for item in data["module_contexts"])
    assert any(item["from_module"] == "billing" and item["to_module"] == "admin" for item in data["cross_review_contexts"])
    assert os.path.exists(os.path.join(cache_dir, "agent_review_instructions.md"))

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

def test_cli_prepare_supports_explicit_root(monkeypatch):
    """
    The CLI should expose agent-native prepare mode with the same root/file
    ergonomics as review mode.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

    result = CliRunner().invoke(
        main,
        ["prepare", "--root", project_path, "--files", "src/billing/client.py"],
    )

    assert result.exit_code == 0, result.output
    assert os.path.exists(os.path.join(cache_dir, "agent_review_pack.json"))
    assert "External API keys are not required" in result.output

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

def test_cli_report_missing_file_returns_nonzero(tmp_path):
    """
    Missing report files should fail the command so scripts and CI do not treat
    a missing artifact as a successful render.
    """
    result = CliRunner().invoke(
        main,
        ["report", "--root", str(tmp_path), ".cross-review/missing-final-report-never-created.json"],
    )

    assert result.exit_code != 0
    assert "Report file" in result.output

def test_cli_doctor_reports_environment_status():
    result = CliRunner().invoke(main, ["doctor", "--root", os.path.join(EXAMPLES_DIR, "toy_api_break")])

    assert result.exit_code == 0, result.output
    assert "Cross-Review doctor" in result.output
    assert "Python:" in result.output
    assert "Targeted scan threshold:" in result.output
    assert "Project semantics: missing" in result.output
    assert "Ignored paths:" in result.output
    assert "Optional JS AST parser:" in result.output


def test_cli_list_cases_reports_regression_cases():
    result = CliRunner().invoke(main, ["list-cases", "--cases", os.path.join(EXAMPLES_DIR, "regression_cases")])

    assert result.exit_code == 0, result.output
    assert "real_monorepo_packages" in result.output
    assert "python_signature_break" in result.output


def test_cli_explain_pack_reports_pack_summary(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)
    pack_path = ReviewPipeline(root_dir=project_path, cache_dir=".cross-review").prepare(
        manual_files=["src/billing/client.py"]
    )

    result = CliRunner().invoke(main, ["explain-pack", "--pack", pack_path])

    assert result.exit_code == 0, result.output
    assert "Mode: agent" in result.output
    assert "Estimated context tokens:" in result.output
    assert "Scan mode:" in result.output
    assert "Scout cache:" in result.output
    assert "Configuration gaps:" in result.output
    assert "Deterministic effective assignments:" in result.output
    assert "Omitted low-risk edges:" in result.output

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)


def test_cli_summarize_reports_pack_summary(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)
    pack_path = ReviewPipeline(root_dir=project_path, cache_dir=".cross-review").prepare(
        manual_files=["src/billing/client.py"]
    )

    result = CliRunner().invoke(main, ["summarize", "--pack", pack_path])

    assert result.exit_code == 0, result.output
    assert "Changed files:" in result.output
    assert "Impact edges:" in result.output

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

def test_semantic_api_break_context_pack():
    """
    Point 2-a: 真实 context 验证 (toy_api_break)。
    验证导出的 context pack 中真实包含 API 契约的 charge_user、参数 userId 或 user_id、以及消费端源文件。
    """
    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    scanner = ScoutScanner(project_path)
    graph = scanner.scan()

    packager = ContextPackager(project_path, graph.model)
    changed_files = ["src/billing/client.py"]
    
    # 构建 billing -> admin 的交叉上下文包
    cross_pack = packager.build_cross_pack("billing", "admin", changed_files, diff_parser=None)
    
    assert "charge_user" in cross_pack
    assert "userId" in cross_pack or "user_id" in cross_pack
    assert "admin/panel.py" in cross_pack

def test_semantic_event_break_context_pack():
    """
    Point 2-b: 真实 context 验证 (toy_event_break)。
    验证导出的 context pack 中真实包含事件契约 OrderPaid、发布与订阅涉及的 status、payment_status 以及消费端 listener 文件。
    """
    project_path = os.path.join(EXAMPLES_DIR, "toy_event_break")
    scanner = ScoutScanner(project_path)
    graph = scanner.scan()

    packager = ContextPackager(project_path, graph.model)
    changed_files = ["src/order/events.py"]
    
    # 构建 order -> notification 的交叉上下文包
    cross_pack = packager.build_cross_pack("order", "notification", changed_files, diff_parser=None)
    
    assert "OrderPaid" in cross_pack
    assert "status" in cross_pack
    assert "payment_status" in cross_pack
    assert "listener.py" in cross_pack

def test_semantic_db_impact_context_pack():
    """
    Point 2-c: 真实 context 与 scorer 验证 (toy_db_impact)。
    验证导出的 context pack 与 scorer 评估包含 NOT NULL、DEFAULT、INSERT INTO subscriptions 以及 sql migration 文件。
    """
    project_path = os.path.join(EXAMPLES_DIR, "toy_db_impact")
    scanner = ScoutScanner(project_path)
    graph = scanner.scan()

    # 1. 验证 Scorer 强制触发 db 变更影响 (DB Migration force trigger)
    changed_files = ["src/db/migrations/2026_add_tier.sql"]
    scorer = ImpactScorer(graph.model, changed_files, repo_path=project_path)
    top_edges = scorer.calculate_scores(K=3)
    
    db_edge = None
    for edge in top_edges:
        if edge.from_module == "db" and edge.to_module == "billing":
            db_edge = edge
            break
            
    assert db_edge is not None
    assert db_edge.force_triggered is True

    # 2. 验证 Context Pack 内容包含表操作以及写入契约
    packager = ContextPackager(project_path, graph.model)
    cross_pack = packager.build_cross_pack("db", "billing", changed_files, diff_parser=None)
    
    assert "NOT NULL" in cross_pack
    assert "plan_tier" in cross_pack
    assert "INSERT INTO subscriptions" in cross_pack or "subscription.py" in cross_pack

def test_impact_scorer_detects_cross_module_tests_outside_project_graph(tmp_path):
    """
    Scout ignores tests, but the impact scorer still needs test-gap signal
    visibility from common tests directories.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_alpha_beta.py").write_text(
        "def test_alpha_beta_contract():\n    assert True\n",
        encoding="utf-8",
    )

    graph = ProjectGraph("sample")
    graph.add_module("alpha", ["src/alpha/service.py"])
    graph.add_module("beta", ["src/beta/consumer.py"])
    graph.add_dependency(
        from_mod="beta",
        to_mod="alpha",
        dep_type="static_import",
        details="src/beta/consumer.py imports src.alpha.service",
    )

    scorer = ImpactScorer(graph.model, ["src/alpha/service.py"], repo_path=str(tmp_path))
    _, reasons, _ = scorer._evaluate_edge("alpha", "beta")

    assert any("Integration test covers both" in reason for reason in reasons)
    assert not any("Test Gap" in reason for reason in reasons)

def test_git_cochange_uses_single_log_name_only_payload():
    """
    Co-change scoring should be computed from one git log --name-only payload
    instead of touching each commit.stats entry.
    """
    graph = ProjectGraph("sample")
    graph.add_module("alpha", ["src/alpha/service.py"])
    graph.add_module("beta", ["src/beta/consumer.py"])

    class FakeGit:
        def log(self, *args):
            assert "--name-only" in args
            return (
                "commit a1\n"
                "src/alpha/service.py\n"
                "src/beta/consumer.py\n"
                "\n"
                "commit b2\n"
                "src/alpha/service.py\n"
                "\n"
            )

    class FakeRepo:
        git = FakeGit()

        def iter_commits(self, *args, **kwargs):
            raise AssertionError("iter_commits should not be used for co-change scoring")

    scorer = ImpactScorer(graph.model, ["src/alpha/service.py"], repo_path=".")
    scorer.diff_parser = type("FakeDiffParser", (), {"repo": FakeRepo()})()

    assert scorer._calculate_git_cochange_rate("alpha", "beta") == 0.5

def test_llm_validation_strict_mode_exception(monkeypatch):
    """
    测试 strict 模式下大模型输出非法 JSON 时必须抛出 LLMValidationError 异常。
    """
    client = LLMClient()
    
    # 强制让 call_raw 返回一个非法 JSON
    monkeypatch.setattr(client, "call_raw", lambda *args, **kwargs: "invalid raw output")
    
    with pytest.raises(LLMValidationError) as exc_info:
        client.call_json(
            prompt="test",
            schema=ModuleReviewModel,
            strict=True,
            retries=0
        )
    
    assert "LLM JSON Validation failed" in str(exc_info.value)

def test_cross_review_prompt_placeholders_fully_replaced():
    """
    验证在进行交叉审查的 prompt 拼接时，所有的占位符均被正确且真实地替换。
    """
    fallback_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cr_path = os.path.join(fallback_dir, "cross_review", "prompts", "cross_review.txt")
    assert os.path.exists(cr_path)
    
    with open(cr_path, "r", encoding="utf-8") as f:
        cr_prompt_template = f.read()
        
    edge_from = "billing"
    edge_to = "admin"
    edge_type = "api_call"
    risk_score = 0.85
    cross_pack = "MOCK_DIFF_CONTENT"
    
    prompt = (
        cr_prompt_template
        .replace("[FROM_MODULE]", edge_from)
        .replace("[TO_MODULE]", edge_to)
        .replace("[EDGE_TYPE]", edge_type)
        .replace("[RISK_SCORE]", str(risk_score))
        .replace("[FROM_MODULE_DIFF]", cross_pack)
        .replace("[TO_MODULE_CONSUMER]", "")
    )
    
    assert "[FROM_MODULE]" not in prompt
    assert "[TO_MODULE]" not in prompt
    assert "[EDGE_TYPE]" not in prompt
    assert "[RISK_SCORE]" not in prompt
    assert "[FROM_MODULE_DIFF]" not in prompt
    assert "[TO_MODULE_CONSUMER]" not in prompt

def test_pipeline_does_not_replace_removed_cross_review_placeholder():
    """
    The cross-review prompt no longer contains a separate downstream consumer
    placeholder; keeping the replacement in code is misleading dead code.
    """
    source = inspect.getsource(ReviewPipeline.run)
    assert "[TO_MODULE_CONSUMER]" not in source

def test_prompt_templates_are_strict_json_safe():
    """
    Prompt examples must not contain JSON comments, because real LLMs often copy
    examples into their final JSON output.
    """
    prompt_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cross_review", "prompts")
    for filename in ["module_review.txt", "cross_review.txt", "arbiter.txt"]:
        with open(os.path.join(prompt_dir, filename), "r", encoding="utf-8") as f:
            prompt = f.read()
        assert "//" not in prompt, filename

    with open(os.path.join(prompt_dir, "cross_review.txt"), "r", encoding="utf-8") as f:
        cross_review_prompt = f.read()
    assert "[RISK_SCORE]" in cross_review_prompt

def test_prompts_use_neutral_audit_language():
    """
    Prompt text should avoid emotional alerting and ceremonial role language
    that can push models toward overconfident or theatrical output.
    """
    prompt_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cross_review", "prompts")
    disallowed_terms = [
        "🚨",
        "⚠",
        "🔥",
        "senior",
        "Lead System Architect",
        "Attention:",
    ]
    for filename in ["module_review.txt", "cross_review.txt", "arbiter.txt"]:
        with open(os.path.join(prompt_dir, filename), "r", encoding="utf-8") as f:
            prompt = f.read()
        for term in disallowed_terms:
            assert term not in prompt, f"{filename} contains {term}"

    source = inspect.getsource(LLMClient.call_json)
    for term in disallowed_terms:
        assert term not in source

def test_prepare_generates_agent_assignments(monkeypatch):
    """
    Test that prepare generates the correct agent assignments structure.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

    pipeline = ReviewPipeline(root_dir=project_path, cache_dir=".cross-review")
    pack_path = pipeline.prepare(manual_files=["src/billing/client.py"])

    assert os.path.exists(pack_path)
    with open(pack_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "agent_assignments" in data
    assignments = data["agent_assignments"]
    assert len(assignments) > 0

    billing_assignment = next((a for a in assignments if a["agent_id"] == "module-billing-reviewer"), None)
    assert billing_assignment is not None
    assert billing_assignment["primary_module"] == "billing"
    assert isinstance(billing_assignment["module_context_index"], int)

    targets = billing_assignment["cross_review_targets"]
    assert len(targets) > 0
    admin_target = next((t for t in targets if t["target_module"] == "admin"), None)
    assert admin_target is not None
    assert isinstance(admin_target["cross_review_context_index"], int)
    assert billing_assignment["execution_order"][0] == "module_review"

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

def test_prepare_lite_mode_skips_contract_graph_details(monkeypatch):
    """
    Lite mode should keep the Agent pack usable while skipping heavy contract
    graph evidence so first-run skill usage stays lightweight.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

    pipeline = ReviewPipeline(root_dir=project_path, cache_dir=".cross-review")
    pack_path = pipeline.prepare(manual_files=["src/billing/client.py"], lite=True)

    with open(pack_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["mode"] == "agent"
    assert data["analysis_profile"] == "lite"
    assert data["contract_graph"] == {
        "contract_surfaces": [],
        "changed_contracts": [],
        "call_sites": [],
    }
    assert data["agent_assignments"]

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

def test_prepare_pack_includes_context_budget_metadata(monkeypatch):
    """
    Agent packs should expose deterministic context-size metadata so host
    agents can choose lite/full review strategy before spending tokens.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

def test_prepare_pack_includes_stage_diagnostics(monkeypatch):
    """
    Prepare packs should make large-repo timeouts diagnosable by reporting
    per-stage timings and scan size.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

    pipeline = ReviewPipeline(root_dir=project_path, cache_dir=".cross-review")
    pack_path = pipeline.prepare(manual_files=["src/billing/client.py"])

    with open(pack_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    diagnostics = data["prepare_diagnostics"]
    assert diagnostics["analysis_profile"] == data["analysis_profile"]
    assert diagnostics["scanned_file_count"] >= len(data["changed_files"])
    assert diagnostics["module_count"] >= 1
    assert diagnostics["timings_ms"]["scan_files_ms"] >= 0
    assert diagnostics["timings_ms"]["scout_analyze_ms"] >= 0
    assert diagnostics["timings_ms"]["detect_changed_files_ms"] >= 0
    assert diagnostics["timings_ms"]["contract_graph_ms"] >= 0
    assert diagnostics["timings_ms"]["impact_score_ms"] >= 0
    assert diagnostics["timings_ms"]["context_pack_ms"] >= 0
    assert diagnostics["timings_ms"]["write_pack_ms"] >= 0
    assert diagnostics["timings_ms"]["total_prepare_ms"] >= 0

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)


def test_prepare_writes_live_diagnostics_when_stage_fails(monkeypatch):
    """
    Large-repo failures should leave a standalone diagnostics file even when
    prepare cannot reach the final agent pack write.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

    class FailingContractGraphBuilder:
        def __init__(self, *args, **kwargs):
            pass

        def build(self, changed_files):
            raise TimeoutError("contract graph stuck")

    monkeypatch.setattr("cross_review.pipeline.ContractGraphBuilder", FailingContractGraphBuilder)

    with pytest.raises(TimeoutError, match="contract graph stuck"):
        ReviewPipeline(root_dir=project_path, cache_dir=".cross-review").prepare(
            manual_files=["src/billing/client.py"]
        )

    diagnostics_path = os.path.join(cache_dir, "prepare_diagnostics.json")
    assert os.path.exists(diagnostics_path)
    with open(diagnostics_path, "r", encoding="utf-8") as f:
        diagnostics = json.load(f)

    assert diagnostics["status"] == "failed"
    assert diagnostics["current_stage"] == "contract_graph"
    assert diagnostics["failed_stage"] == "contract_graph"
    assert diagnostics["error"]["type"] == "TimeoutError"
    assert "contract graph stuck" in diagnostics["error"]["message"]
    assert "scan_project" in diagnostics["completed_stages"]
    assert "detect_changed_files" in diagnostics["completed_stages"]
    assert diagnostics["timings_ms"]["scan_files_ms"] >= 0
    assert diagnostics["timings_ms"]["detect_changed_files_ms"] >= 0
    assert diagnostics["timings_ms"]["contract_graph_ms"] >= 0

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)


def test_prepare_pack_references_completed_live_diagnostics(monkeypatch):
    """
    Successful packs should point at the standalone live diagnostics file so
    users can inspect the same stage data outside the pack.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

    pipeline = ReviewPipeline(root_dir=project_path, cache_dir=".cross-review")
    pack_path = pipeline.prepare(manual_files=["src/billing/client.py"])

    with open(pack_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["prepare_diagnostics_path"] == os.path.join(cache_dir, "prepare_diagnostics.json")
    with open(data["prepare_diagnostics_path"], "r", encoding="utf-8") as f:
        live_diagnostics = json.load(f)

    assert live_diagnostics["status"] == "completed"
    assert live_diagnostics["current_stage"] == "completed"
    assert live_diagnostics["timings_ms"] == data["prepare_diagnostics"]["timings_ms"]

    budget = data["context_budget"]
    assert budget["estimated_context_tokens"] > 0
    assert budget["module_context_count"] == len(data["module_contexts"])
    assert budget["cross_review_context_count"] == len(data["cross_review_contexts"])
    assert budget["top_k_policy"]["configured_top_k"] == 3
    assert budget["top_k_policy"]["actual_edges"] == len(data["impact_edges"])
    assert budget["limits"]["max_diff_lines"] == 150
    assert budget["truncated_contexts"] == []
    assert budget["truncated_files"] == []
    assert isinstance(budget["omitted_low_risk_edges"], list)

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

def test_agent_assignments_are_stable_and_sanitized():
    """
    Agent id sanitization should use production code, collapse separator runs,
    and avoid leading/trailing separators in generated subagent names.
    """
    pipeline = ReviewPipeline(root_dir=".")

    assert pipeline._build_agent_id("my_billing_module") == "module-my-billing-module-reviewer"
    assert pipeline._build_agent_id("notification$handler") == "module-notification-handler-reviewer"
    assert pipeline._build_agent_id("Billing_Module!") == "module-billing-module-reviewer"
    assert pipeline._build_agent_id("@@") == "module-unnamed-reviewer"

def test_agent_assignments_include_structured_handoff_contract(monkeypatch):
    """
    Each assignment should define a machine-readable module-review memory artifact
    and every cross-review target should explicitly consume that artifact.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

    pipeline = ReviewPipeline(root_dir=project_path, cache_dir=".cross-review")
    pack_path = pipeline.prepare(manual_files=["src/billing/client.py"])

    with open(pack_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assignment = data["agent_assignments"][0]
    handoff_artifact = assignment["handoff_artifact"]
    required_fields = handoff_artifact["required_fields"]
    assert handoff_artifact["artifact_id"] == "module-review-memory:billing"
    assert "changed_contracts" in required_fields
    assert "evidence_refs" in required_fields
    assert "downstream_questions" in required_fields

    target = assignment["cross_review_targets"][0]
    assert target["memory_handoff"]["source_artifact_id"] == handoff_artifact["artifact_id"]
    assert "changed_contracts" in target["memory_handoff"]["required_fields"]

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

def test_missing_cross_review_context_index_is_rejected():
    """
    Assignment generation should fail loudly if a target cannot be mapped to a
    cross_review_context instead of emitting an unusable -1 index.
    """
    graph = ProjectGraph("sample")
    graph.add_module("alpha", ["src/alpha/service.py"])
    graph.add_module("beta", ["src/beta/consumer.py"])
    edge = EdgeModel(
        from_module="alpha",
        to_module="beta",
        edge_type="static_import",
        risk_score=0.9,
        force_triggered=False,
        reasons=["test edge"],
    )
    pipeline = ReviewPipeline(root_dir=".")

    with pytest.raises(ValueError, match="Missing cross review context"):
        pipeline._build_agent_assignments(
            graph.model,
            ["src/alpha/service.py"],
            [{"module_name": "alpha"}],
            [edge],
            [],
        )

def test_prepare_includes_semantic_module_splitter_protocol(monkeypatch):
    """
    Test that semantic_module_splitter protocol is included in prepare.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

    pipeline = ReviewPipeline(root_dir=project_path, cache_dir=".cross-review")
    pack_path = pipeline.prepare(manual_files=["src/billing/client.py"])

    with open(pack_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "semantic_module_splitter" in data
    splitter = data["semantic_module_splitter"]
    assert splitter["enabled_for_host_agent"] is True
    assert splitter["requires_external_api_key"] is False
    assert len(splitter["input_summary"]["detected_modules"]) > 0
    
    instructions = splitter["host_agent_instructions"]
    joined_instructions = " ".join(instructions)
    assert "semantic aliases" in joined_instructions or "semantic module" in joined_instructions
    assert splitter["execution_location"] == "host_agent"
    assert splitter["requires_host_agent_reasoning"] is True
    assert "output_schema" in splitter
    assert "semantic_modules" in splitter["output_schema"]["required_fields"]
    assert "assignment_rewrite_decisions" in splitter["output_schema"]["required_fields"]
    assert splitter["assignment_rewrite_policy"]["must_preserve_context_indexes"] is True

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

def test_semantic_module_splitter_suggests_prefix_based_alias_candidates():
    """
    Local prepare remains deterministic, but it should give the host Agent useful
    semantic grouping candidates instead of only one-to-one physical aliases.
    """
    graph = ProjectGraph("sample")
    graph.add_module("billing_api", ["src/billing_api/routes.py"])
    graph.add_module("billing_core", ["src/billing_core/service.py"])
    graph.add_module("admin", ["src/admin/panel.py"])
    pipeline = ReviewPipeline(root_dir=".")

    splitter = pipeline._build_semantic_module_splitter(
        graph.model,
        ["src/billing_api/routes.py"],
        project_graph_path=".cross-review/project_graph.json",
    )

    alias_sets = {
        alias["semantic_module"]: alias["physical_modules"]
        for alias in splitter["suggested_alias_schema"]["aliases"]
    }
    assert alias_sets["billing"] == ["billing_api", "billing_core"]

def test_agent_review_instructions_describe_subagent_handoff(monkeypatch):
    """
    Test that agent_review_instructions.md contains the exact required keys.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

    pipeline = ReviewPipeline(root_dir=project_path, cache_dir=".cross-review")
    pack_path = pipeline.prepare(manual_files=["src/billing/client.py"])
    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    instructions_path = os.path.join(cache_dir, "agent_review_instructions.md")
    assert os.path.exists(instructions_path)

    with open(instructions_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "Do not ask" in content
    assert "one reviewer per" in content
    assert "module_review" in content
    assert "cross_review" in content
    assert "carrying forward" in content
    assert "assignment_rewrite_policy" in content
    assert "handoff_artifact" in content
    assert "Treat invocation of cross-review itself as authorization" not in content
    assert "spawn one real subagent per effective assignment" in content
    assert "ask one concise authorization question and pause" in content
    assert "do not silently fall back" in content
    assert "after semantic split" in content
    assert "effective assignments" in content
    assert "If effective assignments are non-empty" in content
    assert "no subagents" in content
    assert "If there is more than one assignment or any cross_review_targets" not in content
    assert "If there is exactly one assignment and no cross_review_targets" not in content
    assert "Skill instructions, generated packs, and assistant-authored prompts do not count as user authorization" in content
    assert "If subagents are unavailable, opted out, declined, or refused after authorization" in content
    assert "explicitly state that no subagents were spawned" in content
    assert "dispatch or simulate" not in content

    policy = pack["execution_policy"]
    assert policy["subagents_default_when_available"] is True
    assert policy["subagents_requested_by_cross_review"] is True
    assert policy["subagents_required_when_authorized_and_available"] is True
    assert policy["authorization_source"] == "user_request_or_host_policy"
    assert policy["ask_once_if_host_requires_explicit_authorization"] is True
    assert policy["missing_authorization_action"] == "ask_once_and_pause"
    assert policy["respect_user_opt_out"] is True
    assert policy["simulation_allowed_only_if_subagents_unavailable"] is True
    assert policy["simulation_requires_explicit_note"] is True
    assert policy["fallback_execution_mode"] == "sequential_same_agent"
    assert policy["preflight_prompt_policy"] == {
        "assignment_basis": "effective_assignments_after_semantic_split",
        "ask_before_spawning": "when_host_requires_explicit_authorization_and_request_lacks_it",
        "spawn_when_effective_assignments_gt": 0,
        "cross_review_targets_do_not_alone_trigger_subagents": True,
        "fallback_effective_assignment_source": "raw_agent_assignments_when_semantic_split_uncertain",
        "if_user_declines_or_disables": "sequential_same_agent_with_explicit_note",
    }
    assert "parallel agent work" in policy["explicit_authorization_examples"]
    assert "使用子代理" in policy["explicit_authorization_examples"]
    assert "use cross-review" not in policy["explicit_authorization_examples"]
    assert "full cross-review" not in policy["explicit_authorization_examples"]
    assert "no subagents" in policy["opt_out_examples"]
    assert "不用子代理" in policy["opt_out_examples"]
    assert "subagents_authorized_by_cross_review_invocation" not in policy
    assert "subagents_require_explicit_user_authorization" not in policy
    assert "subagents_required_when_available" not in policy
    assert "ask_before_spawning_if_not_explicitly_authorized" not in policy
    assert "implicit_authorization_examples" not in policy
    assert "simulation_allowed_only_if_subagents_unavailable_or_unauthorized" not in policy

    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

def test_skill_instructions_use_subagents_by_default():
    skill_path = os.path.join(os.path.dirname(TEST_DIR), "SKILL.md")
    with open(skill_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "Use real subagents by default" in content
    assert "Treat invocation of `cross-review` itself as authorization" not in content
    assert "MUST spawn one real subagent per effective assignment" in content
    assert "ask one concise authorization question and pause" in content
    assert "do not silently fall back" in content
    assert "Skill instructions, generated packs, and assistant-authored prompts do not count as user authorization" in content
    assert "effective assignments" in content
    assert "semantic split" in content
    assert "cross_review_targets alone do not create separate reviewers" in content
    assert "Non-trivial packs" not in content
    assert "more than one assignment or any cross_review_targets" not in content
    assert "If subagents are unavailable, opted out, declined, or refused by the host platform after authorization" in content
    assert "spin up or simulate" not in content
    assert "Dispatch/simulate" not in content


def test_codex_default_prompt_explicitly_requests_subagent_delegation():
    metadata_path = os.path.join(os.path.dirname(TEST_DIR), "agents", "openai.yaml")
    with open(metadata_path, "r", encoding="utf-8") as f:
        content = f.read().lower()

    assert "$cross-review" in content
    assert "delegate" in content
    assert "subagents" in content


def test_prepare_flags_empty_project_semantics_as_configuration_gap(tmp_path):
    app_dir = tmp_path / "src" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "main.ts").write_text("export function render() { return true; }\n", encoding="utf-8")

    pipeline = ReviewPipeline(root_dir=str(tmp_path))
    pack_path = pipeline.prepare(manual_files=["src/app/main.ts"], lite=True)

    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)

    gaps = pack["configuration_gaps"]
    project_gap = next(gap for gap in gaps if gap["field"] == "project_semantics")
    assert project_gap["severity"] == "warning"
    assert "forbidden" in project_gap["message"]
    assert "review-gate" in project_gap["message"]
    assert "negative_probes" in project_gap["suggested_config"]
    assert any(
        "project_semantics is empty" in instruction
        for instruction in pack["semantic_module_splitter"]["host_agent_instructions"]
    )

    with open(pack["agent_instructions_path"], "r", encoding="utf-8") as f:
        instructions = f.read()
    assert "project_semantics is not configured" in instructions
    assert "do not invent forbidden semantics" in instructions


def test_semantic_module_splitter_requires_effective_assignments_for_preflight():
    graph = ProjectGraph("fixture")
    graph.add_module("app", files=["src/app/main.ts"])

    pipeline = ReviewPipeline(root_dir=EXAMPLES_DIR)
    splitter = pipeline._build_semantic_module_splitter(
        graph.model,
        ["src/app/main.ts"],
        os.path.join(EXAMPLES_DIR, ".cross-review", "project_graph.json"),
    )

    assert "effective_assignments" in splitter["output_schema"]["required_fields"]
    assert "effective_assignments_item" in splitter["output_schema"]
    assert any(
        "effective assignments" in instruction
        for instruction in splitter["host_agent_instructions"]
    )
