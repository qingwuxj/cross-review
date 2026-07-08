import json
import os
from typing import Any

from cross_review.schemas.models import FinalReportModel
from cross_review.validation.pack_validator import ValidationResult, validate_pack


def validate_report(pack_path: str, report_path: str) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    pack_result = validate_pack(pack_path)
    errors.extend(pack_result.errors)
    warnings.extend(pack_result.warnings)

    pack = _load_json(pack_path, errors, "pack")
    report_payload = _load_json(report_path, errors, "report")
    if pack is None or report_payload is None:
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    try:
        report = FinalReportModel.model_validate(report_payload)
    except Exception as exc:
        errors.append(f"report schema validation failed: {exc}")
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    project_root = pack.get("project_root")
    if not isinstance(project_root, str):
        errors.append("pack.project_root must be a string.")
        project_root = None
    contract_graph = pack.get("contract_graph") if isinstance(pack.get("contract_graph"), dict) else {}
    known_changed_contract_ids = {
        item.get("contract_id")
        for item in contract_graph.get("changed_contracts", [])
        if isinstance(item, dict) and item.get("contract_id")
    }
    changed_contracts_by_id = {
        item.get("contract_id"): item
        for item in contract_graph.get("changed_contracts", [])
        if isinstance(item, dict) and item.get("contract_id")
    }
    known_callsite_ids = {
        item.get("callsite_id")
        for item in contract_graph.get("call_sites", [])
        if isinstance(item, dict) and item.get("callsite_id")
    }
    callsites_by_id = {
        item.get("callsite_id"): item
        for item in contract_graph.get("call_sites", [])
        if isinstance(item, dict) and item.get("callsite_id")
    }

    for bucket, findings in report.findings.items():
        for idx, finding in enumerate(findings):
            prefix = f"findings.{bucket}[{idx}]"
            if not finding.evidence.strip():
                errors.append(f"{prefix}.evidence is required.")
            if not finding.suggested_fix.strip():
                errors.append(f"{prefix}.suggested_fix is required.")
            elif _is_generic_fix(finding.suggested_fix):
                errors.append(f"{prefix}.suggested_fix is too generic.")
            if finding.line <= 0:
                errors.append(f"{prefix}.line must be a positive integer.")
            if project_root:
                resolved_file = _resolve_under_root(project_root, finding.file)
                if resolved_file is None:
                    errors.append(f"{prefix}.file is outside project_root: {finding.file}")
                elif not os.path.isfile(resolved_file):
                    errors.append(f"{prefix}.file does not exist: {finding.file}")
            if bucket in {"blocking", "high"}:
                has_dynamic_exception = bool(
                    finding.dynamic_boundary_exception and finding.dynamic_boundary_exception.strip()
                )
                if has_dynamic_exception and not finding.callsite_id:
                    warnings.append(
                        f"{prefix} uses dynamic_boundary_exception instead of callsite_id."
                    )
                    continue
                if not finding.changed_contract_id:
                    errors.append(f"{prefix}.changed_contract_id is required for high/blocking findings.")
                elif known_changed_contract_ids and finding.changed_contract_id not in known_changed_contract_ids:
                    errors.append(
                        f"{prefix}.changed_contract_id does not exist in pack contract_graph: "
                        f"{finding.changed_contract_id}"
                    )
                if not finding.callsite_id:
                    errors.append(f"{prefix}.callsite_id is required for high/blocking findings.")
                elif known_callsite_ids and finding.callsite_id not in known_callsite_ids:
                    errors.append(
                        f"{prefix}.callsite_id does not exist in pack contract_graph: {finding.callsite_id}"
                    )
                if finding.changed_contract_id and finding.callsite_id:
                    if not _edge_contains_evidence(pack, finding.changed_contract_id, finding.callsite_id):
                        errors.append(
                            f"{prefix} changed_contract_id and callsite_id do not reference the same impact edge."
                        )
                    callsite = callsites_by_id.get(finding.callsite_id)
                    if callsite and not _finding_matches_callsite(finding.file, finding.line, callsite):
                        errors.append(f"{prefix}.file/line does not match callsite evidence.")
                    changed_contract = changed_contracts_by_id.get(finding.changed_contract_id)
                    if callsite and changed_contract and not _evidence_is_grounded(
                        finding.evidence,
                        changed_contract,
                        callsite,
                    ):
                        errors.append(f"{prefix}.evidence is not grounded in pack evidence.")

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


def _load_json(path: str, errors: list[str], label: str) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        errors.append(f"Could not load {label} JSON from {path}: {exc}")
        return None
    if not isinstance(payload, dict):
        errors.append(f"{label} JSON root must be an object: {path}")
        return None
    return payload


def _raw_finding(report_payload: dict[str, Any], bucket: str, idx: int) -> dict[str, Any]:
    try:
        finding = report_payload["findings"][bucket][idx]
    except Exception:
        return {}
    return finding if isinstance(finding, dict) else {}


def _edge_contains_evidence(pack: dict[str, Any], changed_contract_id: str, callsite_id: str) -> bool:
    for edge in pack.get("impact_edges", []):
        if not isinstance(edge, dict):
            continue
        if (
            changed_contract_id in edge.get("changed_contract_ids", [])
            and callsite_id in edge.get("callsite_ids", [])
        ):
            return True
    return False


def _finding_matches_callsite(file_path: str, line: int, callsite: dict[str, Any]) -> bool:
    return (
        file_path == callsite.get("file")
        and isinstance(callsite.get("line"), int)
        and abs(line - callsite["line"]) <= 3
    )


def _evidence_is_grounded(
    evidence: str,
    changed_contract: dict[str, Any],
    callsite: dict[str, Any],
) -> bool:
    evidence_norm = _normalize_text(evidence)
    candidates = [
        changed_contract.get("contract_id"),
        changed_contract.get("risk_reason"),
        changed_contract.get("previous_signature"),
        changed_contract.get("current_signature"),
        changed_contract.get("diff_summary"),
        callsite.get("callsite_id"),
        callsite.get("usage"),
        callsite.get("evidence"),
    ]
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        candidate_norm = _normalize_text(candidate)
        if candidate_norm and (candidate_norm in evidence_norm or evidence_norm in candidate_norm):
            return True
    return False


def _is_generic_fix(suggested_fix: str) -> bool:
    normalized = _normalize_text(suggested_fix).strip(". ")
    generic_values = {
        "fix it",
        "fix this",
        "update code",
        "handle it",
        "make it work",
        "resolve the issue",
    }
    if normalized in generic_values:
        return True
    words = [word for word in normalized.split(" ") if word]
    return len(words) < 4


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def _resolve_under_root(root: str, file_path: str) -> str | None:
    root_abs = os.path.abspath(root)
    path_abs = os.path.abspath(file_path if os.path.isabs(file_path) else os.path.join(root_abs, file_path))
    try:
        if os.path.commonpath([root_abs, path_abs]) != root_abs:
            return None
    except ValueError:
        return None
    return path_abs
