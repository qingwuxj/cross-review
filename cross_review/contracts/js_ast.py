import re
from functools import lru_cache


def parser_status() -> str:
    try:
        _build_parser("typescript")
    except Exception:
        return "not_installed"
    return "available"


def parser_available() -> bool:
    return parser_status() == "available"


def extract_imports(source: str, source_type: str = "typescript") -> dict[str, dict[str, str]]:
    try:
        parser = _build_parser(source_type)
        tree = parser.parse(source.encode("utf-8"))
    except Exception:
        return {}

    source_bytes = source.encode("utf-8")
    imports: dict[str, dict[str, str]] = {}
    for node in _walk_tree(tree.root_node):
        if node.type != "import_statement":
            continue
        statement = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
        imports.update(_imports_from_statement(statement))
    return imports


@lru_cache(maxsize=2)
def _build_parser(source_type: str):
    from tree_sitter import Language, Parser
    import tree_sitter_typescript

    language_name = "language_tsx" if source_type == "tsx" else "language_typescript"
    language_factory = getattr(tree_sitter_typescript, language_name)
    raw_language = language_factory()
    try:
        language = Language(raw_language)
    except TypeError:
        language = raw_language

    parser = Parser()
    if hasattr(parser, "set_language"):
        parser.set_language(language)
    else:
        parser.language = language
    return parser


def _walk_tree(node):
    yield node
    for child in getattr(node, "children", []):
        yield from _walk_tree(child)


def _imports_from_statement(statement: str) -> dict[str, dict[str, str]]:
    imports: dict[str, dict[str, str]] = {}
    normalized = " ".join(statement.strip().rstrip(";").split())
    if not normalized.startswith("import "):
        return imports

    from_match = re.search(r"\bfrom\s+([\"'])(?P<path>[^\"']+)\1$", normalized)
    if not from_match:
        return imports

    import_path = from_match.group("path")
    clause = normalized[len("import "): from_match.start()].strip()
    if clause.startswith("type "):
        clause = clause[len("type "):].strip()
    if not clause:
        return imports

    named_start = clause.find("{")
    if named_start > 0:
        default_name = clause[:named_start].strip().rstrip(",").strip()
        if _is_identifier(default_name):
            imports[default_name] = {
                "imported_name": "default",
                "import_path": import_path,
            }

    named_match = re.search(r"\{(?P<names>[^}]+)\}", clause)
    if named_match:
        for raw_name in named_match.group("names").split(","):
            imported_name, local_name = _parse_named_import(raw_name)
            if not imported_name or not local_name:
                continue
            imports[local_name] = {
                "imported_name": imported_name,
                "import_path": import_path,
            }
        return imports

    namespace_match = re.match(r"\*\s+as\s+([A-Za-z_$][\w$]*)$", clause)
    if namespace_match:
        imports[namespace_match.group(1)] = {
            "imported_name": "*",
            "import_path": import_path,
        }
        return imports

    if _is_identifier(clause):
        imports[clause] = {
            "imported_name": "default",
            "import_path": import_path,
        }
    return imports


def _parse_named_import(raw_name: str) -> tuple[str | None, str | None]:
    value = raw_name.strip()
    if not value:
        return None, None
    if value.startswith("type "):
        value = value[len("type "):].strip()
    parts = [part.strip() for part in re.split(r"\s+as\s+", value, maxsplit=1)]
    imported_name = parts[0]
    local_name = parts[1] if len(parts) > 1 else imported_name
    if not _is_identifier(imported_name) or not _is_identifier(local_name):
        return None, None
    return imported_name, local_name


def _is_identifier(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z_$][\w$]*$", value or ""))


__all__ = [
    "parser_available",
    "parser_status",
    "extract_imports",
]
