"""What happened — per stage, not per chart.

"The chart was wrong" is not actionable. "The planner picked bar, the generator
built a line, and `matches_plan` caught it" tells you which prompt to open. So
the report is organised by stage, with the gate outcomes alongside, and a
comparison mode because the question is almost always "is this better than
before?" rather than "how good is this?".
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.run import ScenarioResult
from nexcraftviz.agents.artifacts import Telemetry

_STAGES = ("plan", "generate", "evaluate", "deliver")


def summarise(results: list[ScenarioResult]) -> dict[str, Any]:
    """The numbers behind the text report, so a caller can chart them too."""
    passed = [r for r in results if r.passed]
    runs = [r.run for r in results if r.run is not None]

    stages: dict[str, dict[str, Any]] = {}
    for name in _STAGES:
        telemetries: list[Telemetry] = []
        for run in runs:
            recorded = run.stages.telemetry().get(name)
            if recorded is not None:
                telemetries.append(recorded)
        if not telemetries:
            continue
        stages[name] = {
            "runs": len(telemetries),
            "wall_ms_total": sum(t.wall_ms for t in telemetries),
            "wall_ms_median": _median([t.wall_ms for t in telemetries]),
            "tokens": sum(t.tokens for t in telemetries),
            "retries": sum(t.retry_count for t in telemetries),
            "agent": telemetries[0].agent,
            "model": telemetries[0].model,
        }

    gates: dict[str, dict[str, int]] = {}
    for run in runs:
        if run.stages.evaluate is None:
            continue
        for gate in run.stages.evaluate.gates:
            counts = gates.setdefault(gate.gate, {"pass": 0, "fail": 0})
            counts["pass" if gate.passed else "fail"] += 1

    # What the number is evidence of. A summary that reports "6/6 passed"
    # without saying an offline run graded only the wiring is a summary that
    # will eventually be quoted as if it graded the prompts.
    graded = sorted({r.graded for r in results}) or ["full"]

    return {
        "scenarios": len(results),
        "graded": "+".join(graded),
        "passed": len(passed),
        "failed": len(results) - len(passed),
        "regenerations": sum(
            1 for run in runs
            if run.stages.evaluate is not None and run.stages.evaluate.regenerated
        ),
        "tokens": sum(run.stages.total_tokens() for run in runs),
        "wall_ms": sum(run.wall_ms for run in runs),
        "stages": stages,
        "gates": gates,
    }


def report_run(results: list[ScenarioResult], *, verbose: bool = False) -> str:
    summary = summarise(results)
    scope = (
        "" if summary["graded"] == "full"
        else f" [{summary['graded']}: the prompts were not graded]"
    )
    lines = [
        f"{summary['passed']}/{summary['scenarios']} scenarios passed"
        f"  ({summary['tokens']} tokens, {summary['wall_ms']} ms"
        + (f", {summary['regenerations']} regenerated" if summary["regenerations"] else "")
        + ")" + scope,
        "",
    ]

    if summary["stages"]:
        lines.append("per stage")
        header = f"  {'stage':<10}{'runs':>6}{'median ms':>12}{'tokens':>9}  agent"
        lines.append(header)
        for name, stage in summary["stages"].items():
            lines.append(
                f"  {name:<10}{stage['runs']:>6}{stage['wall_ms_median']:>12}"
                f"{stage['tokens']:>9}  {stage['agent']}"
            )
        lines.append("")

    if summary["gates"]:
        lines.append("gates")
        for gate, counts in sorted(summary["gates"].items()):
            total = counts["pass"] + counts["fail"]
            lines.append(f"  {gate:<20}{counts['pass']:>4}/{total} passed")
        lines.append("")

    failures = [r for r in results if not r.passed]
    if failures:
        lines.append("failures")
        for result in failures:
            reason = result.error or "; ".join(result.problems)
            lines.append(f"  {result.scenario}: {reason}")
        lines.append("")

    if verbose:
        for result in results:
            if result.run is None:
                continue
            lines.append(f"{result.scenario}")
            lines.extend(f"    {step}" for step in result.run.trace)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def compare(before: Path, after: Path) -> str:
    """Two saved runs, side by side. The question is usually "is it better?"."""
    a = json.loads(Path(before).read_text(encoding="utf-8"))
    b = json.loads(Path(after).read_text(encoding="utf-8"))

    a_by_name = {r["scenario"]: r for r in a["results"]}
    b_by_name = {r["scenario"]: r for r in b["results"]}

    fixed, broken, unchanged = [], [], 0
    for name in sorted(set(a_by_name) | set(b_by_name)):
        was = a_by_name.get(name, {}).get("passed")
        now = b_by_name.get(name, {}).get("passed")
        if was == now:
            unchanged += 1
        elif now:
            fixed.append(name)
        else:
            broken.append(name)

    lines = [
        f"{Path(before).name} → {Path(after).name}",
        f"  fixed:     {len(fixed)}",
        f"  broken:    {len(broken)}",
        f"  unchanged: {unchanged}",
    ]
    for name in fixed:
        lines.append(f"  + {name}")
    for name in broken:
        lines.append(f"  - {name}")
    return "\n".join(lines) + "\n"


def _median(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2
