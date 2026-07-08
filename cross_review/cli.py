import click
import json
import os
import platform
import shutil
import sys
from importlib.metadata import version
from cross_review.config import load_config
from cross_review.scout import ScoutScanner
from cross_review.pipeline import ReviewPipeline
from cross_review.schemas.models import FinalReportModel
from cross_review.benchmark.runner import BenchmarkRunner
from cross_review.integrations.codegraph_export import CodeGraphGraphExporter
from cross_review.validation.pack_validator import validate_pack
from cross_review.validation.report_validator import validate_report

def safe_echo(message="", err=False):
    encoding = (sys.stderr if err else sys.stdout).encoding or "utf-8"
    text = str(message).encode(encoding, errors="replace").decode(encoding)
    click.echo(text, err=err)

@click.group()
def main():
    """Cross-Review: 依赖图驱动的多 Agent 交叉审查 CLI 工具"""
    pass

@main.command()
def init():
    """在当前项目初始化配置文件和 prompt 模版"""
    os.makedirs(".cross-review", exist_ok=True)
    os.makedirs("cross_review/prompts", exist_ok=True)

    prompts_dir = "cross_review/prompts"
    bundled_prompts_dir = os.path.join(os.path.dirname(__file__), "prompts")

    for filename in ["module_review.txt", "cross_review.txt", "arbiter.txt"]:
        target_path = os.path.join(prompts_dir, filename)
        if not os.path.exists(target_path):
            shutil.copyfile(os.path.join(bundled_prompts_dir, filename), target_path)

    safe_echo("Successfully initialized .cross-review cache and default prompt templates.")

@main.command("init-config")
@click.option('--root', default='.', type=click.Path(file_okay=False, dir_okay=True), help='要写入配置文件的项目根目录')
@click.option('--large-repo', is_flag=True, help='写入适合大仓库首跑的保守配置')
@click.option('--force', is_flag=True, help='覆盖已有 cross-review.toml')
def init_config_command(root, large_repo, force):
    """生成 cross-review.toml 配置文件"""
    root_abs = os.path.abspath(root)
    os.makedirs(root_abs, exist_ok=True)
    config_path = os.path.join(root_abs, "cross-review.toml")
    if os.path.exists(config_path) and not force:
        raise click.ClickException(f"Config already exists: {config_path}. Use --force to overwrite it.")

    template = _large_repo_config_template() if large_repo else _default_config_template()
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(template)
    safe_echo(f"Wrote config to: {config_path}")

@main.command()
@click.argument('path', default='.')
def scout(path):
    """扫描项目物理与契约依赖，生成 project_graph.json"""
    click.echo(f"Scouting project structure at: {path}...")
    try:
        scanner = ScoutScanner(path)
        graph = scanner.scan()
        cache_dir = os.path.join(os.path.abspath(path), ".cross-review")
        os.makedirs(cache_dir, exist_ok=True)
        graph_path = os.path.join(cache_dir, "project_graph.json")
        graph.save_to_file(graph_path)
        safe_echo(f"Successfully generated dependency graph at {graph_path}")
    except Exception as e:
        raise click.ClickException(f"Error during project scout: {e}") from e

@main.command()
@click.option('--root', default='.', type=click.Path(file_okay=False, dir_okay=True), help='要审查的项目根目录')
@click.option('--base', default='main', help='Git 对比基线分支')
@click.option('--head', default='HEAD', help='当前分支')
@click.option('--files', multiple=True, help='手动指定需要审查的文件')
@click.option('--worktree', is_flag=True, help='包含工作区所有暂存/未暂存/未跟踪文件的改动')
@click.option('--staged', is_flag=True, help='只包含暂存区的改动')
@click.option('--lite', is_flag=True, help='使用轻量 Agent pack，跳过 contract graph 细节')
def prepare(root, base, head, files, worktree, staged, lite):
    """为宿主 Agent 准备审查上下文，不调用外部大模型 API"""
    safe_echo("Preparing Cross-Review agent pack...")
    try:
        pipeline = ReviewPipeline(root_dir=root)

        diff_mode = None
        if worktree:
            diff_mode = "worktree"
        elif staged:
            diff_mode = "staged"

        pack_path = pipeline.prepare(
            base_branch=base,
            head_branch=head,
            manual_files=list(files) if files else None,
            diff_mode=diff_mode,
            lite=lite,
        )
        safe_echo(f"\nAgent review pack written to: {pack_path}")
        safe_echo("External API keys are not required for prepare mode.")
    except Exception as e:
        raise click.ClickException(f"Error preparing agent review pack: {e}") from e

@main.command()
@click.option('--root', default='.', type=click.Path(file_okay=False, dir_okay=True), help='要审查的项目根目录')
@click.option('--base', default='main', help='Git 对比基线分支')
@click.option('--head', default='HEAD', help='当前分支')
@click.option('--files', multiple=True, help='手动指定需要审查的文件')
@click.option('--worktree', is_flag=True, help='包含工作区所有暂存/未暂存/未跟踪文件的改动')
@click.option('--staged', is_flag=True, help='只包含暂存区的改动')
def review(root, base, head, files, worktree, staged):
    """运行主审与交叉复审 pipeline"""
    safe_echo("Starting Multi-Agent Cross-Review pipeline...")
    try:
        pipeline = ReviewPipeline(root_dir=root)
        
        diff_mode = None
        if worktree:
            diff_mode = "worktree"
        elif staged:
            diff_mode = "staged"
            
        report_json = pipeline.run(
            base_branch=base, 
            head_branch=head, 
            manual_files=list(files) if files else None,
            diff_mode=diff_mode
        )
        safe_echo(f"\nPipeline review completed. Structured report written to: {report_json}")
    except Exception as e:
        raise click.ClickException(f"Error executing pipeline: {e}") from e

@main.command()
@click.option('--root', default='.', type=click.Path(file_okay=False, dir_okay=True), help='报告所属项目根目录')
@click.argument('report_path')
def report(root, report_path):
    """渲染 final_report.json 为高颜值 Markdown 报告"""
    safe_echo(f"Rendering report from {report_path}...")
    try:
        if not os.path.isabs(report_path) and not os.path.exists(report_path):
            report_path = os.path.join(root, report_path)
        if not os.path.exists(report_path):
            raise click.ClickException(f"Report file '{report_path}' not found.")

        with open(report_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        final_report = FinalReportModel.model_validate(data)
        pipeline = ReviewPipeline(root_dir=root)
        # 从缓存读取关联边（如有）用于渲染表格
        edges = []
        edges_path = os.path.join(root, ".cross-review", "impact_edges.json")
        if os.path.exists(edges_path):
            try:
                from cross_review.schemas.models import EdgeModel
                with open(edges_path, "r", encoding="utf-8") as ef:
                    edge_data = json.load(ef)
                edges = [EdgeModel.model_validate(ed) for ed in edge_data]
            except Exception:
                pass
        
        markdown_content = pipeline.render_markdown(final_report, edges)
        safe_echo("\n=================== FINAL CROSS-REVIEW REPORT ===================")
        safe_echo(markdown_content)
        safe_echo("=================================================================\n")
    except Exception as e:
        raise click.ClickException(f"Error rendering report: {e}") from e

@main.command()
def clean():
    """清理本地中间状态缓存"""
    if os.path.exists(".cross-review"):
        shutil.rmtree(".cross-review")
        click.echo("Cleaned .cross-review cache.")
    else:
        click.echo("No cache to clean.")


@main.command("codegraph-export")
@click.option('--root', default='.', type=click.Path(file_okay=False, dir_okay=True), help='CodeGraph 项目根目录')
@click.option('--out', "out_path", default='.codegraph/cross-review.json', type=click.Path(dir_okay=False), help='输出 external graph JSON 路径')
@click.option('--command', default='codegraph', help='CodeGraph 命令，可设为 "npx -y @colbymchenry/codegraph"')
@click.option('--timeout-seconds', default=60, type=int, help='单个 CodeGraph 命令超时时间')
@click.option('--max-files', default=500, type=int, help='最多从 CodeGraph node 输出解析多少个源码文件')
@click.option('--symbol-limit-per-file', default=2, type=int, help='每个 provider 文件最多查询多少个 symbol callers；0 表示禁用 symbol 级边')
@click.option('--caller-limit', default=20, type=int, help='每个 symbol 最多读取多少个 CodeGraph callers')
@click.option('--query-limit', default=10, type=int, help='每个 symbol 精确匹配时最多读取多少个 CodeGraph query 结果；0 表示直接按名称查询 callers')
def codegraph_export_command(root, out_path, command, timeout_seconds, max_files, symbol_limit_per_file, caller_limit, query_limit):
    """导出 CodeGraph 索引为 cross-review external graph JSON"""
    root_abs = os.path.abspath(root)
    output_path = out_path if os.path.isabs(out_path) else os.path.join(root_abs, out_path)
    try:
        exporter = CodeGraphGraphExporter(
            root_abs,
            command=command,
            timeout_seconds=timeout_seconds,
            max_files=max_files,
            symbol_limit_per_file=symbol_limit_per_file,
            caller_limit=caller_limit,
            query_limit=query_limit,
        )
        graph = exporter.build()
        output_parent = os.path.dirname(output_path)
        if output_parent:
            os.makedirs(output_parent, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(graph, f, indent=2, ensure_ascii=False)
        safe_echo(f"Wrote CodeGraph external graph to: {output_path}")
        safe_echo(f"Modules: {len(graph.get('modules', []))}")
        safe_echo(f"Dependencies: {len(graph.get('dependencies', []))}")
        safe_echo(f"Symbol edges: {len(graph.get('metadata', {}).get('codegraph', {}).get('symbol_edges', []))}")
    except Exception as e:
        raise click.ClickException(f"Error exporting CodeGraph external graph: {e}") from e


@main.command("doctor")
@click.option('--root', default='.', type=click.Path(file_okay=False, dir_okay=True), help='要检查的项目根目录')
def doctor_command(root):
    """检查本地环境、配置和 Git 可用性"""
    root_abs = os.path.abspath(root)
    safe_echo("Cross-Review doctor")
    safe_echo(f"Root: {root_abs}")
    safe_echo(f"Python: {platform.python_version()}")
    try:
        import pydantic
        import git

        safe_echo(f"click: {version('click')}")
        safe_echo(f"pydantic: {pydantic.__version__}")
        safe_echo(f"gitpython: {git.__version__}")
    except Exception as exc:
        raise click.ClickException(f"Missing runtime dependency: {exc}") from exc

    config = load_config(root_abs)
    safe_echo(f"Config: {config.source_path or 'not found'}")
    safe_echo(f"Enabled analyzers: {', '.join(config.review.enabled_analyzers)}")
    safe_echo(f"Auto-lite threshold: {config.review.auto_lite_file_threshold}")
    safe_echo(f"Targeted scan threshold: {config.review.targeted_scan_file_threshold}")
    safe_echo(f"External project graph: {config.project_graph.external_graph_path or 'not configured'}")
    safe_echo(f"CodeGraph integration: {config.integrations.codegraph.enabled}")
    safe_echo(f"Ignored paths: {', '.join(config.ignored_paths) or 'none'}")
    if (
        config.project_semantics.review_gates
        or config.project_semantics.forbidden_semantics
        or config.project_semantics.negative_probes
    ):
        safe_echo("Project semantics: configured")
    else:
        safe_echo("Project semantics: missing")
        safe_echo("Warning: project_semantics is empty; forbidden/review-gate obligations will be reported as a configuration gap.")
    safe_echo(f"Optional JS AST parser: {_optional_js_ast_status()}")
    try:
        from cross_review.diff import GitDiffParser

        GitDiffParser(root_abs)
        safe_echo("Git: available")
    except Exception:
        safe_echo("Git: unavailable")


@main.command("list-cases")
@click.option("--cases", "cases_dir", default="examples/regression_cases", type=click.Path(file_okay=False, dir_okay=True), help="回归样例目录")
def list_cases_command(cases_dir):
    """列出 benchmark regression cases"""
    cases_abs = os.path.abspath(cases_dir)
    if not os.path.isdir(cases_abs):
        raise click.ClickException(f"Cases directory does not exist: {cases_dir}")
    count = 0
    for name in sorted(os.listdir(cases_abs)):
        expected_path = os.path.join(cases_abs, name, "expected.json")
        if not os.path.isfile(expected_path):
            continue
        with open(expected_path, "r", encoding="utf-8") as f:
            expected = json.load(f)
        safe_echo(expected.get("name") or name)
        count += 1
    safe_echo(f"Total cases: {count}")


@main.command("explain-pack")
@click.option("--pack", "pack_path", required=True, type=click.Path(dir_okay=False), help="agent_review_pack.json 路径")
def explain_pack_command(pack_path):
    """解释 Agent review pack 的关键结构和预算"""
    pack = _load_pack_or_raise(pack_path)
    safe_echo(f"Mode: {pack.get('mode')}")
    safe_echo(f"Analysis profile: {pack.get('analysis_profile')}")
    safe_echo(f"Changed files: {len(pack.get('changed_files', []))}")
    safe_echo(f"Impact edges: {len(pack.get('impact_edges', []))}")
    safe_echo(f"Agent assignments: {len(pack.get('agent_assignments', []))}")
    budget = pack.get("context_budget", {}) if isinstance(pack.get("context_budget"), dict) else {}
    safe_echo(f"Estimated context tokens: {budget.get('estimated_context_tokens', 'unknown')}")
    safe_echo(f"Omitted low-risk edges: {len(budget.get('omitted_low_risk_edges', []))}")
    config = pack.get("analysis_config", {}) if isinstance(pack.get("analysis_config"), dict) else {}
    safe_echo(f"Enabled analyzers: {', '.join(config.get('enabled_analyzers', [])) or 'default'}")
    safe_echo(f"External project graph: {config.get('external_project_graph_path') or 'not configured'}")
    integrations = pack.get("integrations", {}) if isinstance(pack.get("integrations"), dict) else {}
    codegraph = integrations.get("codegraph", {}) if isinstance(integrations.get("codegraph"), dict) else {}
    safe_echo(f"CodeGraph: {codegraph.get('status', 'unknown')} ({codegraph.get('reason') or 'no reason'})")
    diagnostics = pack.get("prepare_diagnostics", {}) if isinstance(pack.get("prepare_diagnostics"), dict) else {}
    safe_echo(f"Scan mode: {diagnostics.get('scan_mode', 'unknown')}")
    safe_echo(f"Scout cache: {diagnostics.get('scout_cache_status', 'unknown')}")
    safe_echo(
        "Scanned/source files: "
        f"{diagnostics.get('scanned_file_count', 'unknown')}/"
        f"{diagnostics.get('source_file_count', 'unknown')}"
    )
    gaps = pack.get("configuration_gaps", [])
    safe_echo(f"Configuration gaps: {len(gaps) if isinstance(gaps, list) else 'unknown'}")
    splitter = pack.get("semantic_module_splitter", {}) if isinstance(pack.get("semantic_module_splitter"), dict) else {}
    effective = splitter.get("deterministic_effective_assignments", [])
    safe_echo(f"Deterministic effective assignments: {len(effective) if isinstance(effective, list) else 'unknown'}")


@main.command("summarize")
@click.option("--pack", "pack_path", required=True, type=click.Path(dir_okay=False), help="agent_review_pack.json 路径")
def summarize_command(pack_path):
    """输出 pack 的极简摘要"""
    pack = _load_pack_or_raise(pack_path)
    safe_echo(f"Changed files: {', '.join(pack.get('changed_files', [])) or 'none'}")
    safe_echo(f"Impact edges: {len(pack.get('impact_edges', []))}")
    for edge in pack.get("impact_edges", [])[:5]:
        safe_echo(
            f"- {edge.get('from_module')} -> {edge.get('to_module')} "
            f"({edge.get('edge_type')}, score={edge.get('risk_score')})"
        )


def _load_pack_or_raise(pack_path: str) -> dict:
    if not os.path.exists(pack_path):
        raise click.ClickException(f"Pack file does not exist: {pack_path}")
    try:
        with open(pack_path, "r", encoding="utf-8") as f:
            pack = json.load(f)
    except Exception as exc:
        raise click.ClickException(f"Could not read pack: {exc}") from exc
    if not isinstance(pack, dict):
        raise click.ClickException("Pack JSON root must be an object.")
    return pack


def _optional_js_ast_status() -> str:
    from cross_review.contracts import js_ast

    if js_ast.parser_status() != "available":
        return "not installed (install extra: cross-review-skill[js-ast])"
    return "available"


def _default_config_template() -> str:
    return """# cross-review.toml

[review]
top_k = 3
lite = false
auto_lite_file_threshold = 1000
targeted_scan_file_threshold = 2000
enabled_analyzers = ["python", "sql", "typescript", "graphql", "protobuf"]
expand_critical_top_k = true
low_value_modules = ["scripts", "exports", "dist", "build", "generated", "coverage"]

[context]
max_context_lines = 150
max_diff_lines = 150
max_consumer_files = 3
target_context_tokens = 12000
token_estimate_chars_per_token = 4

ignored_paths = ["generated/**", "dist/**", "build/**", "vendor/**", "coverage/**", "node_modules/**", "tests/**", "test/**", "__tests__/**"]
known_dynamic_boundaries = []

[project_graph]
# Optional external graph JSON. Supports native cross-review project_graph.json
# or simplified {"modules": [...], "dependencies": [...]} exports from codegraph tools.
# external_graph_path = ".codegraph/cross-review.json"

[integrations.codegraph]
# auto: use CodeGraph when the CLI is installed and .codegraph/ exists.
# true: require CodeGraph context and record an error if it is unavailable.
# false: disable CodeGraph integration.
enabled = "auto"
command = "codegraph"
timeout_seconds = 20
max_explore_chars = 12000
affected_depth = 5

[module_aliases]

[path_aliases]

[project_semantics]
# Fill these with repository-specific invariants. Leave empty only when no
# forbidden/review-gate semantics are known; empty values are reported as a
# configuration gap in generated packs.
# review_gates = ["review-gate"]
# forbidden_semantics = ["Forbidden rows must not render as allowed fallback states."]
# negative_probes = ["Create a forbidden review-gate fixture and verify it remains blocked."]
review_gates = []
forbidden_semantics = []
negative_probes = []
"""


def _large_repo_config_template() -> str:
    return """# cross-review.toml
# Conservative first-run defaults for repositories with large source graphs.

[review]
top_k = 1
lite = false
auto_lite_file_threshold = 500
targeted_scan_file_threshold = 500
enabled_analyzers = ["python", "sql"]
expand_critical_top_k = false
low_value_modules = ["scripts", "exports", "dist", "build", "generated", "coverage"]

[context]
max_context_lines = 80
max_diff_lines = 80
max_consumer_files = 1
target_context_tokens = 8000
token_estimate_chars_per_token = 4

ignored_paths = ["generated/**", "dist/**", "build/**", "vendor/**", "coverage/**", "node_modules/**", "tests/**", "test/**", "__tests__/**"]
known_dynamic_boundaries = []

[project_graph]
# Optional external graph JSON. Useful when a large repository already has a
# codegraph index and prepare should skip the built-in scout stage.
# external_graph_path = ".codegraph/cross-review.json"

[integrations.codegraph]
enabled = "auto"
command = "codegraph"
timeout_seconds = 20
max_explore_chars = 8000
affected_depth = 3

[module_aliases]

[path_aliases]

[project_semantics]
# Fill these with repository-specific invariants. Leave empty only when no
# forbidden/review-gate semantics are known; empty values are reported as a
# configuration gap in generated packs.
# review_gates = ["review-gate"]
# forbidden_semantics = ["Forbidden rows must not render as allowed fallback states."]
# negative_probes = ["Create a forbidden review-gate fixture and verify it remains blocked."]
review_gates = []
forbidden_semantics = []
negative_probes = []
"""

@main.command("validate-pack")
@click.option("--pack", "pack_path", required=True, type=click.Path(dir_okay=False), help="agent_review_pack.json 路径")
def validate_pack_command(pack_path):
    """校验 Agent review pack 的结构、索引和 handoff 协议"""
    result = validate_pack(pack_path)
    for warning in result.warnings:
        safe_echo(f"Warning: {warning}", err=True)
    if not result.valid:
        raise click.ClickException("\n".join(result.errors))
    safe_echo("Pack is valid.")

@main.command("validate-report")
@click.option("--pack", "pack_path", required=True, type=click.Path(dir_okay=False), help="agent_review_pack.json 路径")
@click.option("--report", "report_path", required=True, type=click.Path(dir_okay=False), help="final_report.json 路径")
def validate_report_command(pack_path, report_path):
    """校验最终审查报告是否有结构化证据和有效文件引用"""
    result = validate_report(pack_path, report_path)
    for warning in result.warnings:
        safe_echo(f"Warning: {warning}", err=True)
    if not result.valid:
        raise click.ClickException("\n".join(result.errors))
    safe_echo("Report is valid.")

@main.command("benchmark")
@click.option("--cases", "cases_dir", default="examples/regression_cases", type=click.Path(file_okay=False, dir_okay=True), help="回归样例目录")
def benchmark_command(cases_dir):
    """运行固定回归样例，检查 impact edge / contract / call-site 命中率"""
    summary = BenchmarkRunner(cases_dir).run()
    safe_echo(f"{summary.passed_cases}/{summary.total_cases} cases passed.")
    for name, value in summary.metrics.items():
        safe_echo(f"{name}: {value}")
    for result in summary.case_results:
        status = "PASS" if result.passed else "FAIL"
        safe_echo(f"[{status}] {result.case_name}")
        for failure in result.failures:
            safe_echo(f"  - {failure}")
    if summary.failed_cases:
        raise click.ClickException(f"{summary.failed_cases} benchmark case(s) failed.")

if __name__ == "__main__":
    main()
