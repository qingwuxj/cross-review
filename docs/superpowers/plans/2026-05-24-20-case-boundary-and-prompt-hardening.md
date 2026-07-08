# 20-Case Boundary and Prompt Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise benchmark coverage from 10 to 20 cases while keeping default skill prompts concise and neutral.

**Architecture:** Add fixtures first, then implement only the deterministic evidence extraction required by failing fixtures. Keep default install dependency-free; GraphQL and protobuf support are shallow contract/call-site extractors, not full compilers.

**Tech Stack:** Python 3.11, pytest, Click, Pydantic, stdlib `ast`, deterministic regex/structural parsing.

---

### Task 1: Add 10 Regression Fixtures

**Files:**
- Create: `examples/regression_cases/python_keyword_only_signature_break/**`
- Create: `examples/regression_cases/python_module_import_alias_break/**`
- Create: `examples/regression_cases/python_requests_route_break/**`
- Create: `examples/regression_cases/python_orm_insert_missing_column/**`
- Create: `examples/regression_cases/ts_path_alias_import_break/**`
- Create: `examples/regression_cases/ts_default_export_break/**`
- Create: `examples/regression_cases/ts_class_constructor_break/**`
- Create: `examples/regression_cases/ts_axios_template_route_break/**`
- Create: `examples/regression_cases/graphql_field_break/**`
- Create: `examples/regression_cases/proto_rpc_method_break/**`
- Modify: `tests/test_benchmark.py`

- [x] Add fixtures and expected files.
- [x] Update benchmark assertions to expect 20 cases.
- [x] Run `python -m pytest tests/test_benchmark.py -q` and confirm the new unsupported boundaries fail.

### Task 2: Contract Graph Extractor Updates

**Files:**
- Modify: `cross_review/scout.py`
- Modify: `cross_review/contracts/contract_graph.py`
- Modify: `cross_review/pipeline.py`
- Test: `tests/test_contract_graph.py`

- [x] Add `.graphql`, `.gql`, and `.proto` to deterministic scanning.
- [x] Resolve Python `import src.module.file as alias` call-sites.
- [x] Detect SQLAlchemy-style `insert("table")` writer call-sites.
- [x] Detect TypeScript default exports/imports.
- [x] Detect GraphQL field surfaces and downstream query field reads.
- [x] Detect protobuf RPC method surfaces and downstream client method calls.

### Task 3: Prompt Neutrality Review

**Files:**
- Modify: `cross_review/prompts/module_review.txt`
- Modify: `cross_review/prompts/cross_review.txt`
- Modify: `cross_review/prompts/arbiter.txt`
- Modify: `cross_review/llm.py`
- Test: `tests/test_pipeline.py`

- [x] Remove emotional/ceremonial role wording from prompts.
- [x] Remove alert emoji and emotional retry language from LLM retry prompt.
- [x] Add a prompt neutrality test for prompt templates and retry text.

### Task 4: Documentation and Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/release-checklist.md`

- [x] Update benchmark count, capability list, and limitations.
- [x] Run the full verification chain.
