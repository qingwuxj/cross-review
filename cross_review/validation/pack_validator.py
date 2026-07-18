import json
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_pack(pack_path: str) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    pack = _load_json(pack_path, errors)
    if pack is None:
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    required_keys = [
        "mode",
        "requires_external_api_key",
        "execution_policy",
        "project_root",
        "changed_files",
        "project_graph_path",
        "impact_edges",
        "module_contexts",
        "cross_review_contexts",
        "agent_assignments",
        "semantic_module_splitter",
    ]
    _require_keys(pack, required_keys, "pack", errors)

    project_root = pack.get("project_root")
    if not isinstance(project_root, str) or not os.path.isdir(project_root):
        errors.append(f"project_root does not exist or is not a directory: {project_root}")

    project_graph_path = pack.get("project_graph_path")
    if isinstance(project_graph_path, str):
        resolved_graph_path = _resolve_path(os.path.dirname(os.path.abspath(pack_path)), project_graph_path)
        if not os.path.isfile(resolved_graph_path):
            errors.append(f"project_graph_path does not exist: {project_graph_path}")
    else:
        errors.append("project_graph_path must be a string.")

    module_contexts = _list_or_empty(pack.get("module_contexts"), "module_contexts", errors)
    cross_review_contexts = _list_or_empty(pack.get("cross_review_contexts"), "cross_review_contexts", errors)
    agent_assignments = _list_or_empty(pack.get("agent_assignments"), "agent_assignments", errors)
    _validate_contract_graph_references(pack, errors)
    _validate_execution_policy(pack.get("execution_policy"), errors)
    _validate_integrations(pack.get("integrations"), errors)

    for assignment_idx, assignment in enumerate(agent_assignments):
        if not isinstance(assignment, dict):
            errors.append(f"agent_assignments[{assignment_idx}] must be an object.")
            continue
        _validate_assignment(
            assignment,
            assignment_idx,
            module_contexts,
            cross_review_contexts,
            project_root if isinstance(project_root, str) else None,
            errors,
            warnings,
        )

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


def _validate_integrations(integrations: Any, errors: list[str]):
    if integrations is None:
        return
    if not isinstance(integrations, dict):
        errors.append("integrations must be an object.")
        return
    codegraph = integrations.get("codegraph")
    if codegraph is None:
        return
    if not isinstance(codegraph, dict):
        errors.append("integrations.codegraph must be an object.")
        return
    for key in ["enabled", "available", "index_present"]:
        if not isinstance(codegraph.get(key), bool):
            errors.append(f"integrations.codegraph.{key} must be a boolean.")
    if not isinstance(codegraph.get("status"), str):
        errors.append("integrations.codegraph.status must be a string.")


def _validate_execution_policy(policy: Any, errors: list[str]):
    if not isinstance(policy, dict):
        errors.append("execution_policy must be an object.")
        return
    expected_true_keys = [
        "subagents_default_when_available",
        "subagents_requested_by_cross_review",
        "subagents_authorized_by_cross_review_skill_use",
        "subagents_required_when_available",
        "subagents_required_when_authorized_and_available",
        "respect_user_opt_out",
        "simulation_allowed_only_if_subagents_unavailable",
        "simulation_requires_explicit_note",
    ]
    for key in expected_true_keys:
        if policy.get(key) is not True:
            errors.append(f"execution_policy.{key} must be true.")
    expected_policy_values = {
        "authorization_source": "cross_review_skill_use_or_user_request",
        "ask_once_if_host_requires_explicit_authorization": False,
        "missing_authorization_action": "not_applicable_directly_authorized",
        "fallback_execution_mode": "sequential_same_agent",
    }
    for key, expected in expected_policy_values.items():
        if policy.get(key) != expected:
            errors.append(f"execution_policy.{key} must be {expected!r}.")
    preflight_policy = policy.get("preflight_prompt_policy")
    if not isinstance(preflight_policy, dict):
        errors.append("execution_policy.preflight_prompt_policy must be an object.")
    else:
        expected_values = {
            "assignment_basis": "effective_assignments_after_semantic_split",
            "ask_before_spawning": "never_when_cross_review_skill_is_used",
            "spawn_when_effective_assignments_gt": 0,
            "cross_review_targets_do_not_alone_trigger_subagents": True,
            "fallback_effective_assignment_source": "raw_agent_assignments_when_semantic_split_uncertain",
            "if_user_declines_or_disables": "sequential_same_agent_with_explicit_note",
        }
        for key, expected in expected_values.items():
            if preflight_policy.get(key) != expected:
                errors.append(f"execution_policy.preflight_prompt_policy.{key} must be {expected!r}.")


def _validate_contract_graph_references(pack: dict[str, Any], errors: list[str]):
    contract_graph = pack.get("contract_graph")
    if contract_graph is None:
        return
    if not isinstance(contract_graph, dict):
        errors.append("contract_graph must be an object.")
        return

    changed_contracts = _list_or_empty(
        contract_graph.get("changed_contracts"),
        "contract_graph.changed_contracts",
        errors,
    )
    call_sites = _list_or_empty(
        contract_graph.get("call_sites"),
        "contract_graph.call_sites",
        errors,
    )
    changed_contract_ids = {
        item.get("contract_id")
        for item in changed_contracts
        if isinstance(item, dict) and item.get("contract_id")
    }
    callsite_ids = {
        item.get("callsite_id")
        for item in call_sites
        if isinstance(item, dict) and item.get("callsite_id")
    }

    for edge_idx, edge in enumerate(_list_or_empty(pack.get("impact_edges"), "impact_edges", errors)):
        if not isinstance(edge, dict):
            errors.append(f"impact_edges[{edge_idx}] must be an object.")
            continue
        for contract_id in edge.get("changed_contract_ids", []):
            if contract_id not in changed_contract_ids:
                errors.append(
                    f"impact_edges[{edge_idx}].changed_contract_ids references unknown contract: "
                    f"{contract_id}"
                )
        for callsite_id in edge.get("callsite_ids", []):
            if callsite_id not in callsite_ids:
                errors.append(
                    f"impact_edges[{edge_idx}].callsite_ids references unknown callsite: "
                    f"{callsite_id}"
                )


def _validate_assignment(
    assignment: dict[str, Any],
    assignment_idx: int,
    module_contexts: list[Any],
    cross_review_contexts: list[Any],
    project_root: str | None,
    errors: list[str],
    warnings: list[str],
):
    required_assignment_keys = [
        "agent_id",
        "primary_module",
        "primary_files",
        "module_context_index",
        "cross_review_targets",
        "execution_order",
        "handoff_artifact",
    ]
    prefix = f"agent_assignments[{assignment_idx}]"
    _require_keys(assignment, required_assignment_keys, prefix, errors)

    module_context_index = assignment.get("module_context_index")
    if not _valid_index(module_context_index, module_contexts):
        errors.append(f"{prefix}.module_context_index is out of range: {module_context_index}")

    primary_files = _list_or_empty(assignment.get("primary_files"), f"{prefix}.primary_files", errors)
    if project_root:
        for file_path in primary_files:
            if not isinstance(file_path, str):
                errors.append(f"{prefix}.primary_files contains a non-string path: {file_path}")
                continue
            resolved_file = _resolve_under_root(project_root, file_path)
            if resolved_file is None or not os.path.isfile(resolved_file):
                errors.append(f"{prefix}.primary_files file does not exist: {file_path}")

    handoff_artifact = assignment.get("handoff_artifact")
    artifact_id = None
    if isinstance(handoff_artifact, dict):
        artifact_id = handoff_artifact.get("artifact_id")
        required_fields = handoff_artifact.get("required_fields")
        if not artifact_id:
            errors.append(f"{prefix}.handoff_artifact.artifact_id is required.")
        if not isinstance(required_fields, list) or not required_fields:
            errors.append(f"{prefix}.handoff_artifact.required_fields must be a non-empty list.")
    else:
        errors.append(f"{prefix}.handoff_artifact must be an object.")

    targets = _list_or_empty(assignment.get("cross_review_targets"), f"{prefix}.cross_review_targets", errors)
    for target_idx, target in enumerate(targets):
        if not isinstance(target, dict):
            errors.append(f"{prefix}.cross_review_targets[{target_idx}] must be an object.")
            continue
        target_prefix = f"{prefix}.cross_review_targets[{target_idx}]"
        cross_review_context_index = target.get("cross_review_context_index")
        if not _valid_index(cross_review_context_index, cross_review_contexts):
            errors.append(
                f"{target_prefix}.cross_review_context_index is out of range: "
                f"{cross_review_context_index}"
            )

        memory_handoff = target.get("memory_handoff")
        if not isinstance(memory_handoff, dict):
            errors.append(f"{target_prefix}.memory_handoff must be an object.")
            continue
        source_artifact_id = memory_handoff.get("source_artifact_id")
        if artifact_id and source_artifact_id != artifact_id:
            errors.append(
                f"{target_prefix}.memory_handoff.source_artifact_id does not match "
                f"assignment handoff artifact: {source_artifact_id}"
            )
        if not isinstance(memory_handoff.get("required_fields"), list) or not memory_handoff["required_fields"]:
            errors.append(f"{target_prefix}.memory_handoff.required_fields must be a non-empty list.")

    if not targets:
        warnings.append(f"{prefix} has no cross_review_targets.")


def _load_json(path: str, errors: list[str]) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        errors.append(f"Could not load JSON from {path}: {exc}")
        return None
    if not isinstance(payload, dict):
        errors.append(f"JSON root must be an object: {path}")
        return None
    return payload


def _require_keys(payload: dict[str, Any], keys: list[str], prefix: str, errors: list[str]):
    for key in keys:
        if key not in payload:
            errors.append(f"{prefix}.{key} is required.")


def _list_or_empty(value: Any, label: str, errors: list[str]) -> list[Any]:
    if isinstance(value, list):
        return value
    errors.append(f"{label} must be a list.")
    return []


def _valid_index(index: Any, collection: list[Any]) -> bool:
    return isinstance(index, int) and 0 <= index < len(collection)


def _resolve_path(base_dir: str, path: str) -> str:
    if os.path.isabs(path):
        return os.path.abspath(path)
    return os.path.abspath(os.path.join(base_dir, path))


def _resolve_under_root(root: str, file_path: str) -> str | None:
    root_abs = os.path.abspath(root)
    path_abs = os.path.abspath(file_path if os.path.isabs(file_path) else os.path.join(root_abs, file_path))
    try:
        if os.path.commonpath([root_abs, path_abs]) != root_abs:
            return None
    except ValueError:
        return None
    return path_abs
