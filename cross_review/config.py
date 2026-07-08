from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

DEFAULT_ANALYZERS = ["python", "sql", "typescript", "graphql", "protobuf"]
DEFAULT_IGNORED_PATHS = [
    "dist/**",
    "build/**",
    "coverage/**",
    "generated/**",
    ".next/**",
    "out/**",
    "node_modules/**",
    "tests/**",
    "test/**",
    "__tests__/**",
]
DEFAULT_LOW_VALUE_MODULES = ["scripts", "exports", "dist", "build", "generated", "coverage"]


@dataclass(frozen=True)
class ReviewConfig:
    top_k: int = 3
    lite: bool = False
    expand_critical_top_k: bool = True
    auto_lite_file_threshold: int = 1000
    targeted_scan_file_threshold: int = 2000
    enabled_analyzers: list[str] = field(default_factory=lambda: list(DEFAULT_ANALYZERS))
    low_value_modules: list[str] = field(default_factory=lambda: list(DEFAULT_LOW_VALUE_MODULES))


@dataclass(frozen=True)
class ContextConfig:
    max_diff_lines: int = 150
    max_consumer_files: int = 3
    target_context_tokens: int = 12000
    token_estimate_chars_per_token: int = 4


@dataclass(frozen=True)
class ProjectSemanticsConfig:
    review_gates: list[str] = field(default_factory=list)
    forbidden_semantics: list[str] = field(default_factory=list)
    negative_probes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "review_gates": list(self.review_gates),
            "forbidden_semantics": list(self.forbidden_semantics),
            "negative_probes": list(self.negative_probes),
        }


@dataclass(frozen=True)
class ProjectGraphConfig:
    external_graph_path: str | None = None


@dataclass(frozen=True)
class CodeGraphIntegrationConfig:
    enabled: str = "auto"
    command: str = "codegraph"
    timeout_seconds: int = 20
    max_explore_chars: int = 12000
    affected_depth: int = 5


@dataclass(frozen=True)
class IntegrationsConfig:
    codegraph: CodeGraphIntegrationConfig = field(default_factory=CodeGraphIntegrationConfig)


@dataclass(frozen=True)
class CrossReviewConfig:
    review: ReviewConfig = field(default_factory=ReviewConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    project_graph: ProjectGraphConfig = field(default_factory=ProjectGraphConfig)
    integrations: IntegrationsConfig = field(default_factory=IntegrationsConfig)
    module_aliases: dict[str, list[str]] = field(default_factory=dict)
    ignored_paths: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORED_PATHS))
    known_dynamic_boundaries: list[str] = field(default_factory=list)
    path_aliases: dict[str, str] = field(default_factory=dict)
    project_semantics: ProjectSemanticsConfig = field(default_factory=ProjectSemanticsConfig)
    source_path: str | None = None


def load_config(root_dir: str, config_path: str | None = None) -> CrossReviewConfig:
    root_abs = os.path.abspath(root_dir)
    resolved_path = _resolve_config_path(root_abs, config_path)
    if resolved_path is None:
        return CrossReviewConfig()

    raw = _load_toml(resolved_path)
    review_raw = _dict(raw.get("review"))
    context_raw = _dict(raw.get("context"))
    project_graph_raw = _dict(raw.get("project_graph"))
    integrations_raw = _dict(raw.get("integrations"))
    codegraph_raw = _dict(integrations_raw.get("codegraph"))
    semantics_raw = _dict(raw.get("project_semantics"))

    has_configured_top_k = "top_k" in review_raw
    return CrossReviewConfig(
        review=ReviewConfig(
            top_k=_positive_int(review_raw.get("top_k"), default=3),
            lite=_bool(review_raw.get("lite"), default=False),
            expand_critical_top_k=_bool(
                review_raw.get("expand_critical_top_k"),
                default=not has_configured_top_k,
            ),
            auto_lite_file_threshold=_nonnegative_int(
                review_raw.get("auto_lite_file_threshold"),
                default=1000,
            ),
            targeted_scan_file_threshold=_nonnegative_int(
                review_raw.get("targeted_scan_file_threshold"),
                default=2000,
            ),
            enabled_analyzers=_string_list(
                review_raw.get("enabled_analyzers"),
                default=DEFAULT_ANALYZERS,
                allowed=set(DEFAULT_ANALYZERS),
            ),
            low_value_modules=_string_list(
                review_raw.get("low_value_modules"),
                default=DEFAULT_LOW_VALUE_MODULES,
            ),
        ),
        context=ContextConfig(
            max_diff_lines=_positive_int(
                context_raw.get("max_diff_lines", context_raw.get("max_context_lines")),
                default=150,
            ),
            max_consumer_files=_positive_int(context_raw.get("max_consumer_files"), default=3),
            target_context_tokens=_positive_int(context_raw.get("target_context_tokens"), default=12000),
            token_estimate_chars_per_token=_positive_int(
                context_raw.get("token_estimate_chars_per_token"),
                default=4,
            ),
        ),
        project_graph=ProjectGraphConfig(
            external_graph_path=_optional_string(project_graph_raw.get("external_graph_path")),
        ),
        integrations=IntegrationsConfig(
            codegraph=CodeGraphIntegrationConfig(
                enabled=_enabled_mode(codegraph_raw.get("enabled"), default="auto"),
                command=_optional_string(codegraph_raw.get("command")) or "codegraph",
                timeout_seconds=_positive_int(codegraph_raw.get("timeout_seconds"), default=20),
                max_explore_chars=_positive_int(codegraph_raw.get("max_explore_chars"), default=12000),
                affected_depth=_positive_int(codegraph_raw.get("affected_depth"), default=5),
            ),
        ),
        module_aliases=_string_list_map(raw.get("module_aliases")),
        ignored_paths=_string_list(
            raw.get("ignored_paths", context_raw.get("ignored_paths")),
            default=DEFAULT_IGNORED_PATHS,
        ),
        known_dynamic_boundaries=_string_list(
            raw.get("known_dynamic_boundaries", context_raw.get("known_dynamic_boundaries")),
            default=[],
        ),
        path_aliases=_string_map(raw.get("path_aliases")),
        project_semantics=ProjectSemanticsConfig(
            review_gates=_string_list(semantics_raw.get("review_gates"), default=[]),
            forbidden_semantics=_string_list(semantics_raw.get("forbidden_semantics"), default=[]),
            negative_probes=_string_list(semantics_raw.get("negative_probes"), default=[]),
        ),
        source_path=resolved_path,
    )


def _resolve_config_path(root_dir: str, config_path: str | None) -> str | None:
    candidates = []
    if config_path:
        candidates.append(config_path if os.path.isabs(config_path) else os.path.join(root_dir, config_path))
    else:
        candidates.extend(
            [
                os.path.join(root_dir, "cross-review.toml"),
                os.path.join(root_dir, ".cross-review.toml"),
                os.path.join(root_dir, ".cross-review", "config.toml"),
            ]
        )
    return next((path for path in candidates if os.path.exists(path)), None)


def _load_toml(path: str) -> dict[str, Any]:
    try:
        import tomllib

        with open(path, "rb") as f:
            return tomllib.load(f)
    except ModuleNotFoundError:
        return _load_simple_toml(path)


def _load_simple_toml(path: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    section: dict[str, Any] = data
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                section_name = line[1:-1].strip()
                section = data
                for part in section_name.split("."):
                    section = section.setdefault(part.strip(), {})
                continue
            if "=" not in line:
                continue
            key, raw_value = [part.strip() for part in line.split("=", 1)]
            section[key] = _parse_simple_value(raw_value)
    return data


def _parse_simple_value(raw_value: str) -> Any:
    if raw_value in {"true", "false"}:
        return raw_value == "true"
    if re.fullmatch(r"-?\d+", raw_value):
        return int(raw_value)
    if raw_value.startswith("[") and raw_value.endswith("]"):
        inner = raw_value[1:-1].strip()
        if not inner:
            return []
        return [_parse_simple_value(item.strip()) for item in inner.split(",")]
    if (
        len(raw_value) >= 2
        and raw_value[0] == raw_value[-1]
        and raw_value[0] in {"'", '"'}
    ):
        return raw_value[1:-1]
    return raw_value


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _positive_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value > 0:
        return value
    return default


def _nonnegative_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value >= 0:
        return value
    return default


def _bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _enabled_mode(value: Any, default: str = "auto") -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"auto", "true", "false"}:
            return normalized
    return default


def _string_list_map(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[str]] = {}
    for key, items in value.items():
        if isinstance(key, str) and isinstance(items, list):
            strings = [item for item in items if isinstance(item, str)]
            if strings:
                result[key] = strings
    return result


def _string_list(value: Any, default: list[str], allowed: set[str] | None = None) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    result = []
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if not normalized:
            continue
        if allowed is not None and normalized not in allowed:
            continue
        result.append(normalized)
    return result or list(default)


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, str) and key and item
    }


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    return clean or None
