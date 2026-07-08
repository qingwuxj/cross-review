def parse_text_files(builder, suffixes):
    return builder._parse_text_files(suffixes)


def extract_typescript_surfaces(builder, analyses):
    return builder._extract_typescript_surfaces(analyses)


def extract_typescript_call_sites(builder, analyses, surfaces):
    return builder._extract_typescript_call_sites(analyses, surfaces)


__all__ = [
    "parse_text_files",
    "extract_typescript_surfaces",
    "extract_typescript_call_sites",
]
