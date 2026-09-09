"""Run the pipeline over a scenario set.

A scenario is a question, some rows, and what the run ought to produce. The
expectations are deliberately *loose about the chart and strict about the
mistakes*: asserting an exact spec would fail on every harmless rewording, while
asserting that a rate is never summed catches a real regression. So a scenario
says the chart type it expects (or a set of acceptable ones), which fields must
appear, and which gates must pass — not what the JSON should look like.

Runs offline by default. With ``llm=None`` the harness still exercises routing,
the gates, the artifacts and the report; only the model's judgement is absent,
and the report says so rather than implying the numbers mean more than they do.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from nexcraftviz.agents import AgentRegistry
from nexcraftviz.pipeline import ChartRequest, ChartRun, run_pipeline

SCENARIO_DIR = Path(__file__).parent / "scenarios"
RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class Scenario:
    """One case: a question, its rows, and what a good answer looks like."""

    name: str
    question: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    #: Any one of these satisfies the case. A list because "revenue over time"
    #: is legitimately a line *or* an area chart, and failing the second is
    #: measuring conformity rather than quality.
    expect_chart_type: list[str] = field(default_factory=list)
    expect_fields: list[str] = field(default_factory=list)
    #: Gates that must pass. Empty means all of them.
    require_gates: list[str] = field(default_factory=list)
    #: A gate we expect to *fail* — for scenarios that exist to prove a gate
    #: bites. Without these the gate suite can rot into always-passing.
    expect_gate_failures: list[str] = field(default_factory=list)
    note: str = ""

    @classmethod
    def from_dict(cls, name: str, raw: dict[str, Any]) -> Scenario:
        return cls(
            name=name,
            question=raw["question"],
            rows=list(raw.get("rows") or []),
            expect_chart_type=_as_list(raw.get("expect_chart_type")),
            expect_fields=_as_list(raw.get("expect_fields")),
            require_gates=_as_list(raw.get("require_gates")),
            expect_gate_failures=_as_list(raw.get("expect_gate_failures")),
            note=raw.get("note", ""),
        )


@dataclass
class ScenarioResult:
    scenario: str
    passed: bool
    run: ChartRun | None = None
    problems: list[str] = field(default_factory=list)
    error: str = ""
    #: What this result is evidence of. An offline run grades the wiring; only
    #: a live run grades the chart.
    graded: str = "full"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "passed": self.passed,
            "graded": self.graded,
            "problems": self.problems,
            "error": self.error,
            "run": self.run.to_dict() if self.run else None,
            "chart_type": (
                self.run.stages.generate.chart_type
                if self.run and self.run.stages.generate else ""
            ),
            "gates": [
                {"gate": g.gate, "passed": g.passed, "detail": g.detail}
                for g in (self.run.stages.evaluate.gates
                          if self.run and self.run.stages.evaluate else [])
            ],
        }


def load_scenarios(directory: Path | str | None = None) -> list[Scenario]:
    """Every scenario on disk, in filename order so a run is reproducible."""
    root = Path(directory or SCENARIO_DIR)
    scenarios: list[Scenario] = []
    for path in sorted(root.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for name, raw in (document.get("scenarios") or {}).items():
            scenarios.append(Scenario.from_dict(name, raw))
    return scenarios


async def run_scenarios(
    scenarios: list[Scenario],
    *,
    llm: Any = None,
    registry: AgentRegistry | None = None,
    evaluate: str = "gates",
) -> list[ScenarioResult]:
    """Run each scenario and grade it. One failure does not stop the rest.

    With no ``llm`` the run uses the offline stand-in, which measures the wiring
    and nothing about any prompt — so the chart-type and field expectations are
    not graded. Crediting the stand-in for agreeing with itself would report a
    green suite for something that was never tested.
    """
    registry = registry or AgentRegistry.default()
    offline = llm is None
    results: list[ScenarioResult] = []
    for scenario in scenarios:
        runner = llm
        if offline:
            from harness.offline import offline_runner

            # A fresh stand-in per scenario, holding that scenario's rows.
            runner = offline_runner(scenario.rows)
        try:
            run = await run_pipeline(
                ChartRequest(question=scenario.question, rows=scenario.rows),
                registry=registry, llm=runner, evaluate=evaluate,  # type: ignore[arg-type]
            )
        except Exception as exc:  # noqa: BLE001 — a crashed case is a result, not the end
            results.append(ScenarioResult(
                scenario.name, False, error=f"{type(exc).__name__}: {exc}"
            ))
            continue
        results.append(_grade(scenario, run, offline=offline))
    return results


def _grade(scenario: Scenario, run: ChartRun, *, offline: bool = False) -> ScenarioResult:
    problems: list[str] = []

    if run.spec is None:
        return ScenarioResult(scenario.name, False, run=run,
                              problems=[run.reason or "no chart was produced"])

    if not offline:
        built = run.stages.generate.chart_type if run.stages.generate else ""
        if scenario.expect_chart_type and built not in scenario.expect_chart_type:
            problems.append(
                f"chart type {built!r}, expected one of {scenario.expect_chart_type}"
            )

        present = {field_name for _, _, field_name in run.spec.field_refs()}
        missing = [f for f in scenario.expect_fields if f not in present]
        if missing:
            problems.append(f"fields absent from the spec: {', '.join(missing)}")

    evaluation = run.stages.evaluate
    if evaluation is not None:
        failed = {g.gate for g in evaluation.failures}
        required = set(scenario.require_gates) or {g.gate for g in evaluation.gates}
        # A scenario that exists to prove a gate bites opts out of that gate's
        # requirement rather than being marked broken for doing its job.
        required -= set(scenario.expect_gate_failures)
        for gate in sorted(required & failed):
            detail = next(g.detail for g in evaluation.failures if g.gate == gate)
            problems.append(f"gate {gate} failed: {detail}")
        for gate in scenario.expect_gate_failures:
            if gate not in failed:
                problems.append(f"gate {gate} was expected to fail here and did not")

    return ScenarioResult(scenario.name, not problems, run=run, problems=problems,
                          graded="wiring only" if offline else "full")


def save_results(results: list[ScenarioResult], *, directory: Path | None = None) -> Path:
    """Write a timestamped JSON file and return its path."""
    root = directory or RESULTS_DIR
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"run-{time.strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(
        json.dumps({
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "results": [r.to_dict() for r in results],
        }, indent=2, default=str),
        encoding="utf-8",
    )
    return path


def latest_result(directory: Path | None = None) -> Path | None:
    root = directory or RESULTS_DIR
    runs = sorted(root.glob("run-*.json"))
    return runs[-1] if runs else None


def run_sync(
    scenarios: list[Scenario], *, llm: Any = None, evaluate: str = "gates"
) -> list[ScenarioResult]:
    """For the CLI, which is not async."""
    return asyncio.run(run_scenarios(scenarios, llm=llm, evaluate=evaluate))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]
