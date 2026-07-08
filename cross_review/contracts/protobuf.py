def extract_proto_surfaces(builder, analyses):
    return builder._extract_proto_surfaces(analyses)


def extract_proto_call_sites(builder, analyses, surfaces):
    return builder._extract_proto_call_sites(analyses, surfaces)


__all__ = [
    "extract_proto_surfaces",
    "extract_proto_call_sites",
]
