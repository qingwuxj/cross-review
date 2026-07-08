def extract_graphql_surfaces(builder, analyses):
    return builder._extract_graphql_surfaces(analyses)


def extract_graphql_call_sites(builder, analyses, surfaces):
    return builder._extract_graphql_call_sites(analyses, surfaces)


__all__ = [
    "extract_graphql_surfaces",
    "extract_graphql_call_sites",
]
