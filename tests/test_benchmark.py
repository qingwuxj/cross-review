import os
import json

from click.testing import CliRunner

from cross_review.benchmark.runner import BenchmarkRunner
from cross_review.cli import main


TEST_DIR = os.path.dirname(os.path.abspath(__file__))
REGRESSION_CASES_DIR = os.path.join(os.path.dirname(TEST_DIR), "examples", "regression_cases")


def test_benchmark_runner_reports_expected_contract_graph_hits():
    summary = BenchmarkRunner(REGRESSION_CASES_DIR).run()

    assert summary.total_cases == 26
    assert summary.passed_cases == 26
    assert summary.failed_cases == 0
    results_by_name = {result.case_name: result for result in summary.case_results}
    assert set(results_by_name) == {
        "real_express_service",
        "real_fastify_react",
        "real_fastapi_sqlalchemy",
        "real_graphql_frontend",
        "real_monorepo_packages",
        "real_proto_wrapper",
        "python_alias_import_signature_break",
        "python_class_constructor_break",
        "python_event_payload_rename",
        "python_keyword_only_signature_break",
        "python_module_import_alias_break",
        "python_orm_insert_missing_column",
        "python_requests_route_break",
        "python_route_param_break",
        "python_signature_break",
        "python_sql_not_null_migration",
        "graphql_field_break",
        "ts_arrow_export_break",
        "ts_axios_template_route_break",
        "ts_class_constructor_break",
        "ts_default_export_break",
        "ts_export_rename",
        "ts_express_route_break",
        "ts_import_alias_call_break",
        "ts_path_alias_import_break",
        "proto_rpc_method_break",
    }
    assert results_by_name["python_signature_break"].passed is True


def test_benchmark_cli_reports_passes():
    result = CliRunner().invoke(main, ["benchmark", "--cases", REGRESSION_CASES_DIR])

    assert result.exit_code == 0
    assert "26/26 cases passed" in result.output
    assert "expected_edge_hit_rate" in result.output


def test_benchmark_summary_includes_quality_metrics():
    summary = BenchmarkRunner(REGRESSION_CASES_DIR).run()

    metrics = summary.metrics
    assert metrics["expected_edge_hit_rate"] == 1.0
    assert metrics["changed_contract_hit_rate"] == 1.0
    assert metrics["callsite_hit_rate"] == 1.0
    assert metrics["unexpected_edges_count"] == 0
    assert metrics["estimated_context_tokens"] > 0
    assert metrics["runtime_ms"] >= 0


def test_benchmark_runner_checks_changed_contract_change_types(tmp_path):
    case_dir = tmp_path / "wrong_change_type_case"
    billing_dir = case_dir / "src" / "billing"
    admin_dir = case_dir / "src" / "admin"
    billing_dir.mkdir(parents=True)
    admin_dir.mkdir(parents=True)
    (billing_dir / "client.py").write_text(
        "def charge_user(user_id: str) -> bool:\n"
        "    return True\n",
        encoding="utf-8",
    )
    (admin_dir / "panel.py").write_text(
        "from src.billing.client import charge_user\n\n"
        "def trigger_billing_override(user_id):\n"
        "    return charge_user(user_id)\n",
        encoding="utf-8",
    )
    (case_dir / "expected.json").write_text(
        json.dumps({
            "name": "wrong_change_type_case",
            "changed_files": ["src/billing/client.py"],
            "expected_edges": [
                {
                    "from_module": "billing",
                    "to_module": "admin",
                    "changed_contract_ids": [
                        "python:function:src/billing/client.py:charge_user"
                    ],
                    "changed_contract_change_types": {
                        "python:function:src/billing/client.py:charge_user": "signature_changed"
                    },
                    "callsite_id_prefixes": [
                        "python:call:src/admin/panel.py:trigger_billing_override:"
                    ],
                }
            ],
        }),
        encoding="utf-8",
    )

    summary = BenchmarkRunner(str(tmp_path)).run()

    assert summary.failed_cases == 1
    assert "expected change_type" in summary.case_results[0].failures[0]
