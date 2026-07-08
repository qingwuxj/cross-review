import json
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any

from cross_review.pipeline import ReviewPipeline


@dataclass
class BenchmarkCaseResult:
    case_name: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    metrics: dict[str, float | int] = field(default_factory=dict)


@dataclass
class BenchmarkSummary:
    total_cases: int
    passed_cases: int
    failed_cases: int
    case_results: list[BenchmarkCaseResult]
    metrics: dict[str, float | int] = field(default_factory=dict)


class BenchmarkRunner:
    def __init__(self, cases_dir: str):
        self.cases_dir = os.path.abspath(cases_dir)

    def run(self) -> BenchmarkSummary:
        started = time.perf_counter()
        case_results = [self._run_case(case_dir) for case_dir in self._case_dirs()]
        passed_cases = sum(1 for result in case_results if result.passed)
        metrics = self._aggregate_metrics(case_results, started)
        return BenchmarkSummary(
            total_cases=len(case_results),
            passed_cases=passed_cases,
            failed_cases=len(case_results) - passed_cases,
            case_results=case_results,
            metrics=metrics,
        )

    def _case_dirs(self) -> list[str]:
        if not os.path.isdir(self.cases_dir):
            return []
        dirs = []
        for name in sorted(os.listdir(self.cases_dir)):
            case_dir = os.path.join(self.cases_dir, name)
            if os.path.isdir(case_dir) and os.path.isfile(os.path.join(case_dir, "expected.json")):
                dirs.append(case_dir)
        return dirs

    def _run_case(self, case_dir: str) -> BenchmarkCaseResult:
        expected = self._load_expected(case_dir)
        case_name = expected.get("name") or os.path.basename(case_dir)
        failures: list[str] = []
        cache_dir = os.path.join(case_dir, ".cross-review")
        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir)

        try:
            pack_path = ReviewPipeline(root_dir=case_dir, cache_dir=".cross-review").prepare(
                manual_files=expected.get("changed_files", [])
            )
            with open(pack_path, "r", encoding="utf-8") as f:
                pack = json.load(f)
        except Exception as exc:
            return BenchmarkCaseResult(
                case_name=case_name,
                passed=False,
                failures=[f"prepare failed: {exc}"],
                metrics={},
            )

        case_metrics = self._initial_case_metrics(pack, expected)
        for expected_edge in expected.get("expected_edges", []):
            self._check_expected_edge(pack, expected_edge, failures, case_metrics)

        expected_pairs = {
            (edge.get("from_module"), edge.get("to_module"))
            for edge in expected.get("expected_edges", [])
        }
        actual_pairs = {
            (edge.get("from_module"), edge.get("to_module"))
            for edge in pack.get("impact_edges", [])
        }
        case_metrics["unexpected_edges_count"] = len(actual_pairs - expected_pairs)

        return BenchmarkCaseResult(
            case_name=case_name,
            passed=not failures,
            failures=failures,
            metrics=case_metrics,
        )

    def _initial_case_metrics(self, pack: dict[str, Any], expected: dict[str, Any]) -> dict[str, float | int]:
        expected_contracts = sum(
            len(edge.get("changed_contract_ids", []))
            for edge in expected.get("expected_edges", [])
        )
        expected_callsite_prefixes = sum(
            len(edge.get("callsite_id_prefixes", []))
            for edge in expected.get("expected_edges", [])
        )
        return {
            "expected_edges_total": len(expected.get("expected_edges", [])),
            "expected_edges_hit": 0,
            "expected_changed_contracts_total": expected_contracts,
            "expected_changed_contracts_hit": 0,
            "expected_callsites_total": expected_callsite_prefixes,
            "expected_callsites_hit": 0,
            "unexpected_edges_count": 0,
            "estimated_context_tokens": pack.get("context_budget", {}).get("estimated_context_tokens", 0),
        }

    def _check_expected_edge(
        self,
        pack: dict[str, Any],
        expected_edge: dict[str, Any],
        failures: list[str],
        metrics: dict[str, float | int],
    ):
        from_module = expected_edge["from_module"]
        to_module = expected_edge["to_module"]
        edge = next(
            (
                edge
                for edge in pack.get("impact_edges", [])
                if edge.get("from_module") == from_module and edge.get("to_module") == to_module
            ),
            None,
        )
        if edge is None:
            failures.append(f"missing expected edge {from_module} -> {to_module}")
            return
        metrics["expected_edges_hit"] += 1

        actual_contract_ids = set(edge.get("changed_contract_ids", []))
        for contract_id in expected_edge.get("changed_contract_ids", []):
            if contract_id not in actual_contract_ids:
                failures.append(f"missing changed contract id on {from_module} -> {to_module}: {contract_id}")
            else:
                metrics["expected_changed_contracts_hit"] += 1

        changed_contracts = {
            changed.get("contract_id"): changed
            for changed in pack.get("contract_graph", {}).get("changed_contracts", [])
        }
        for contract_id, expected_change_type in expected_edge.get("changed_contract_change_types", {}).items():
            actual_change_type = changed_contracts.get(contract_id, {}).get("change_type")
            if actual_change_type != expected_change_type:
                failures.append(
                    "expected change_type on "
                    f"{from_module} -> {to_module} for {contract_id}: "
                    f"{expected_change_type}, got {actual_change_type}"
                )

        actual_callsite_ids = edge.get("callsite_ids", [])
        for prefix in expected_edge.get("callsite_id_prefixes", []):
            if not any(callsite_id.startswith(prefix) for callsite_id in actual_callsite_ids):
                failures.append(f"missing callsite id prefix on {from_module} -> {to_module}: {prefix}")
            else:
                metrics["expected_callsites_hit"] += 1

    def _aggregate_metrics(
        self,
        case_results: list[BenchmarkCaseResult],
        started: float,
    ) -> dict[str, float | int]:
        totals = {
            "expected_edges_total": 0,
            "expected_edges_hit": 0,
            "expected_changed_contracts_total": 0,
            "expected_changed_contracts_hit": 0,
            "expected_callsites_total": 0,
            "expected_callsites_hit": 0,
            "unexpected_edges_count": 0,
            "estimated_context_tokens": 0,
        }
        for result in case_results:
            for key in totals:
                totals[key] += int(result.metrics.get(key, 0))

        return {
            "expected_edge_hit_rate": self._rate(
                totals["expected_edges_hit"],
                totals["expected_edges_total"],
            ),
            "changed_contract_hit_rate": self._rate(
                totals["expected_changed_contracts_hit"],
                totals["expected_changed_contracts_total"],
            ),
            "callsite_hit_rate": self._rate(
                totals["expected_callsites_hit"],
                totals["expected_callsites_total"],
            ),
            "unexpected_edges_count": totals["unexpected_edges_count"],
            "estimated_context_tokens": totals["estimated_context_tokens"],
            "runtime_ms": int((time.perf_counter() - started) * 1000),
        }

    def _rate(self, numerator: int, denominator: int) -> float:
        if denominator == 0:
            return 1.0
        return round(numerator / denominator, 4)

    def _load_expected(self, case_dir: str) -> dict[str, Any]:
        with open(os.path.join(case_dir, "expected.json"), "r", encoding="utf-8") as f:
            return json.load(f)
