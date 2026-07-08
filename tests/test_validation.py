import json
import os
import shutil

from click.testing import CliRunner

from cross_review.cli import main
from cross_review.pipeline import ReviewPipeline
from cross_review.validation.pack_validator import validate_pack
from cross_review.validation.report_validator import validate_report


TEST_DIR = os.path.dirname(os.path.abspath(__file__))
EXAMPLES_DIR = os.path.join(os.path.dirname(TEST_DIR), "examples")


def _prepare_pack_path():
    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)
    return ReviewPipeline(root_dir=project_path, cache_dir=".cross-review").prepare(
        manual_files=["src/billing/client.py"]
    )


def _load_prepare_pack():
    pack_path = _prepare_pack_path()
    with open(pack_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(tmp_path, filename, payload):
    path = tmp_path / filename
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(path)


def _valid_report_with_file(file_path):
    return {
        "overall_risk": "high",
        "summary": "Evidence-backed validation sample.",
        "is_mock": False,
        "findings": {
            "blocking": [],
            "high": [
                {
                    "severity": "high",
                    "confidence": 0.9,
                    "file": file_path,
                    "line": 1,
                    "evidence": "Downstream usage still relies on the changed contract.",
                    "suggested_fix": "Update the downstream callsite or keep a compatibility wrapper.",
                }
            ],
            "medium": [],
            "low": [],
            "needs_human_review": [],
        },
    }


def _first_edge_evidence_ids(pack_path):
    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)
    edge = pack["impact_edges"][0]
    return edge["changed_contract_ids"][0], edge["callsite_ids"][0]


def _first_callsite(pack_path):
    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)
    callsite_id = pack["impact_edges"][0]["callsite_ids"][0]
    return next(
        callsite
        for callsite in pack["contract_graph"]["call_sites"]
        if callsite["callsite_id"] == callsite_id
    )


def test_validate_pack_accepts_prepare_output():
    pack_path = _prepare_pack_path()

    result = validate_pack(pack_path)

    assert result.valid is True
    assert result.errors == []


def test_validate_pack_accepts_lite_prepare_output():
    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)
    pack_path = ReviewPipeline(root_dir=project_path, cache_dir=".cross-review").prepare(
        manual_files=["src/billing/client.py"],
        lite=True,
    )

    result = validate_pack(pack_path)

    assert result.valid is True
    assert result.errors == []


def test_validate_pack_rejects_missing_cross_review_context_index(tmp_path):
    pack = _load_prepare_pack()
    pack["agent_assignments"][0]["cross_review_targets"][0]["cross_review_context_index"] = 99
    path = _write_json(tmp_path, "bad-pack.json", pack)

    result = validate_pack(path)

    assert result.valid is False
    assert any("cross_review_context_index" in error for error in result.errors)


def test_validate_pack_rejects_missing_handoff_artifact(tmp_path):
    pack = _load_prepare_pack()
    del pack["agent_assignments"][0]["handoff_artifact"]
    path = _write_json(tmp_path, "bad-pack.json", pack)

    result = validate_pack(path)

    assert result.valid is False
    assert any("handoff_artifact" in error for error in result.errors)


def test_validate_pack_rejects_unknown_contract_graph_references(tmp_path):
    pack = _load_prepare_pack()
    pack["impact_edges"][0]["changed_contract_ids"] = ["python:function:src/missing.py:missing"]
    pack["impact_edges"][0]["callsite_ids"] = ["python:call:src/missing.py:missing:1"]
    path = _write_json(tmp_path, "bad-pack.json", pack)

    result = validate_pack(path)

    assert result.valid is False
    assert any("changed_contract_ids" in error for error in result.errors)
    assert any("callsite_ids" in error for error in result.errors)


def test_validate_pack_rejects_execution_policy_without_default_subagent_policy(tmp_path):
    pack = _load_prepare_pack()
    pack["execution_policy"] = {
        "subagents_default_when_available": True,
        "simulation_requires_explicit_note": True,
        "fallback_execution_mode": "sequential_same_agent",
    }
    path = _write_json(tmp_path, "bad-pack.json", pack)

    result = validate_pack(path)

    assert result.valid is False
    assert any("subagents_requested_by_cross_review" in error for error in result.errors)
    assert any("subagents_required_when_authorized_and_available" in error for error in result.errors)
    assert any("ask_once_if_host_requires_explicit_authorization" in error for error in result.errors)
    assert any("authorization_source" in error for error in result.errors)
    assert any("missing_authorization_action" in error for error in result.errors)


def test_validate_pack_rejects_execution_policy_without_preflight_prompt_policy(tmp_path):
    pack = _load_prepare_pack()
    del pack["execution_policy"]["preflight_prompt_policy"]
    path = _write_json(tmp_path, "bad-pack.json", pack)

    result = validate_pack(path)

    assert result.valid is False
    assert any("preflight_prompt_policy" in error for error in result.errors)


def test_validate_report_rejects_high_finding_without_evidence(tmp_path):
    pack_path = _prepare_pack_path()
    report = _valid_report_with_file("src/admin/panel.py")
    report["findings"]["high"][0]["evidence"] = ""
    report_path = _write_json(tmp_path, "bad-report.json", report)

    result = validate_report(pack_path, report_path)

    assert result.valid is False
    assert any("evidence" in error for error in result.errors)


def test_validate_report_rejects_nonexistent_finding_file(tmp_path):
    pack_path = _prepare_pack_path()
    report = _valid_report_with_file("src/missing.py")
    report_path = _write_json(tmp_path, "bad-report.json", report)

    result = validate_report(pack_path, report_path)

    assert result.valid is False
    assert any("does not exist" in error for error in result.errors)


def test_validate_report_rejects_high_finding_without_contract_evidence(tmp_path):
    pack_path = _prepare_pack_path()
    report = _valid_report_with_file("src/admin/panel.py")
    report_path = _write_json(tmp_path, "bad-report.json", report)

    result = validate_report(pack_path, report_path)

    assert result.valid is False
    assert any("changed_contract_id" in error for error in result.errors)
    assert any("callsite_id" in error for error in result.errors)


def test_validate_report_accepts_high_finding_with_valid_contract_evidence(tmp_path):
    pack_path = _prepare_pack_path()
    changed_contract_id, callsite_id = _first_edge_evidence_ids(pack_path)
    callsite = _first_callsite(pack_path)
    report = _valid_report_with_file("src/admin/panel.py")
    report["findings"]["high"][0]["line"] = callsite["line"]
    report["findings"]["high"][0]["evidence"] = callsite["usage"]
    report["findings"]["high"][0]["changed_contract_id"] = changed_contract_id
    report["findings"]["high"][0]["callsite_id"] = callsite_id
    report_path = _write_json(tmp_path, "good-report.json", report)

    result = validate_report(pack_path, report_path)

    assert result.valid is True
    assert result.errors == []


def test_validate_report_rejects_evidence_not_present_in_pack(tmp_path):
    pack_path = _prepare_pack_path()
    changed_contract_id, callsite_id = _first_edge_evidence_ids(pack_path)
    callsite = _first_callsite(pack_path)
    report = _valid_report_with_file("src/admin/panel.py")
    report["findings"]["high"][0]["line"] = callsite["line"]
    report["findings"]["high"][0]["evidence"] = "This claim is not grounded in the prepared pack."
    report["findings"]["high"][0]["changed_contract_id"] = changed_contract_id
    report["findings"]["high"][0]["callsite_id"] = callsite_id
    report_path = _write_json(tmp_path, "bad-report.json", report)

    result = validate_report(pack_path, report_path)

    assert result.valid is False
    assert any("evidence is not grounded" in error for error in result.errors)


def test_validate_report_rejects_callsite_file_or_line_mismatch(tmp_path):
    pack_path = _prepare_pack_path()
    changed_contract_id, callsite_id = _first_edge_evidence_ids(pack_path)
    callsite = _first_callsite(pack_path)
    report = _valid_report_with_file("src/billing/client.py")
    report["findings"]["high"][0]["line"] = callsite["line"] + 20
    report["findings"]["high"][0]["evidence"] = callsite["usage"]
    report["findings"]["high"][0]["changed_contract_id"] = changed_contract_id
    report["findings"]["high"][0]["callsite_id"] = callsite_id
    report_path = _write_json(tmp_path, "bad-report.json", report)

    result = validate_report(pack_path, report_path)

    assert result.valid is False
    assert any("does not match callsite" in error for error in result.errors)


def test_validate_report_rejects_generic_suggested_fix(tmp_path):
    pack_path = _prepare_pack_path()
    changed_contract_id, callsite_id = _first_edge_evidence_ids(pack_path)
    callsite = _first_callsite(pack_path)
    report = _valid_report_with_file("src/admin/panel.py")
    report["findings"]["high"][0]["line"] = callsite["line"]
    report["findings"]["high"][0]["evidence"] = callsite["usage"]
    report["findings"]["high"][0]["suggested_fix"] = "Fix it."
    report["findings"]["high"][0]["changed_contract_id"] = changed_contract_id
    report["findings"]["high"][0]["callsite_id"] = callsite_id
    report_path = _write_json(tmp_path, "bad-report.json", report)

    result = validate_report(pack_path, report_path)

    assert result.valid is False
    assert any("suggested_fix is too generic" in error for error in result.errors)


def test_validate_pack_cli_returns_zero_for_valid_pack():
    pack_path = _prepare_pack_path()

    result = CliRunner().invoke(main, ["validate-pack", "--pack", pack_path])

    assert result.exit_code == 0
    assert "Pack is valid" in result.output


def test_prepare_cli_lite_generates_valid_lite_pack():
    project_path = os.path.join(EXAMPLES_DIR, "toy_api_break")
    cache_dir = os.path.join(project_path, ".cross-review")
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

    result = CliRunner().invoke(
        main,
        ["prepare", "--root", project_path, "--files", "src/billing/client.py", "--lite"],
    )

    assert result.exit_code == 0
    pack_path = os.path.join(cache_dir, "agent_review_pack.json")
    with open(pack_path, "r", encoding="utf-8") as f:
        pack = json.load(f)
    assert pack["analysis_profile"] == "lite"


def test_validate_report_cli_returns_nonzero_for_invalid_report(tmp_path):
    pack_path = _prepare_pack_path()
    report = _valid_report_with_file("src/missing.py")
    report_path = _write_json(tmp_path, "bad-report.json", report)

    result = CliRunner().invoke(
        main,
        ["validate-report", "--pack", pack_path, "--report", report_path],
    )

    assert result.exit_code != 0
    assert "does not exist" in result.output
