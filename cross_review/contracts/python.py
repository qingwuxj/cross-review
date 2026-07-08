def parse_python_files(builder):
    return builder._parse_python_files()


def extract_python_surfaces(builder, analyses):
    return builder._extract_surfaces(analyses)


def extract_python_call_sites(builder, analyses, surfaces):
    return builder._extract_call_sites(analyses, surfaces)


__all__ = [
    "parse_python_files",
    "extract_python_surfaces",
    "extract_python_call_sites",
]
