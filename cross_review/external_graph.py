from __future__ import annotations

import json
import os
from typing import Any

from cross_review.graph import ProjectGraph
from cross_review.schemas.models import DependencyModel, ProjectGraphModel, ProjectModule


def load_external_project_graph(root_dir: str, graph_path: str) -> ProjectGraph:
    resolved_path = graph_path if os.path.isabs(graph_path) else os.path.join(root_dir, graph_path)
    with open(resolved_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if not isinstance(payload, dict):
        raise ValueError("External project graph must be a JSON object.")

    model = _native_model(payload) or _simplified_model(payload)
    graph = ProjectGraph(model.project_name)
    graph.model = model
    return graph


def _native_model(payload: dict[str, Any]) -> ProjectGraphModel | None:
    modules = payload.get("modules")
    if not isinstance(modules, dict):
        return None
    try:
        return ProjectGraphModel.model_validate(payload)
    except Exception as exc:
        raise ValueError(f"External native project graph is invalid: {exc}") from exc


def _simplified_model(payload: dict[str, Any]) -> ProjectGraphModel:
    raw_modules = payload.get("modules")
    if not isinstance(raw_modules, list):
        raise ValueError("External project graph must contain modules as an object or list.")

    modules: dict[str, ProjectModule] = {}
    for raw_module in raw_modules:
        if not isinstance(raw_module, dict):
            continue
        name = _clean_string(raw_module.get("name") or raw_module.get("id") or raw_module.get("module"))
        if not name:
            continue
        modules[name] = ProjectModule(
            files=_string_list(raw_module.get("files")),
            criticality=_clean_string(raw_module.get("criticality")) or "medium",
            exports=_string_list(raw_module.get("exports")),
            routes=_string_list(raw_module.get("routes")),
            events=_string_list(raw_module.get("events")),
            db_tables=_string_list(raw_module.get("db_tables") or raw_module.get("tables")),
        )

    dependencies = []
    for raw_dep in payload.get("dependencies") or payload.get("edges") or []:
        if not isinstance(raw_dep, dict):
            continue
        from_module = _clean_string(raw_dep.get("from_module") or raw_dep.get("from") or raw_dep.get("source"))
        to_module = _clean_string(raw_dep.get("to_module") or raw_dep.get("to") or raw_dep.get("target"))
        if not from_module or not to_module or from_module not in modules or to_module not in modules:
            continue
        dependencies.append(
            DependencyModel(
                from_module=from_module,
                to_module=to_module,
                type=_clean_string(raw_dep.get("type")) or "static_import",
                details=_clean_string(raw_dep.get("details")) or "external project graph edge",
                consumer_files=_string_list(raw_dep.get("consumer_files")),
                provider_files=_string_list(raw_dep.get("provider_files")),
                symbol_edges=_symbol_edge_list(raw_dep.get("symbol_edges")),
            )
        )

    project_name = _clean_string(payload.get("project_name") or payload.get("name")) or "external-project-graph"
    return ProjectGraphModel(project_name=project_name, modules=modules, dependencies=dependencies)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({item.replace("\\", "/").strip("/") for item in value if isinstance(item, str) and item.strip()})


def _symbol_edge_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    edges = []
    for item in value:
        if not isinstance(item, dict):
            continue
        symbol = _clean_string(item.get("symbol"))
        provider_file = _clean_string(item.get("provider_file"))
        consumer_file = _clean_string(item.get("consumer_file"))
        if not symbol or not provider_file or not consumer_file:
            continue
        edge = dict(item)
        edge["symbol"] = symbol
        edge["provider_file"] = provider_file.replace("\\", "/").strip("/")
        edge["consumer_file"] = consumer_file.replace("\\", "/").strip("/")
        edges.append(edge)
    return edges


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    return clean or None


__all__ = ["load_external_project_graph"]
