"""Score a model against the eval cases.

Two things are measured, and they are different questions:

* **Did the model decide correctly?** — the expected operations appear, and the
  forbidden ones do not.
* **Does the result actually work?** — the operations apply, the spec validates,
  and (where a renderer is available) it draws something. A model can emit a
  perfectly reasonable-looking operation with an argument that produces an empty
  chart, and only applying it finds out.

Both are reported per case, because a prompt change that improves one and
regresses the other is worth seeing rather than averaging away.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from nexcraftviz.evals.cases import Case, cases_for
from nexcraftviz.skills import get
from nexcraftviz.skills.base import LLMRunner
from nexcraftviz.spec.model import Spec


@dataclass
class CaseResult:
    case: Case
    ok: bool = False
    decided_right: bool = False
    result_works: bool = True
    emitted: list[dict[str, Any]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        detail = ""
        if self.error:
            detail = f" — {self.error}"
        elif self.missing:
            detail = f" — missing {', '.join(self.missing)}"
        elif self.forbidden:
            detail = f" — emitted forbidden {', '.join(self.forbidden)}"
        elif self.notes:
            detail = f" — {self.notes[0]}"
        return f"  {mark}  {self.case.id:<26}{detail}"


@dataclass
class Report:
    results: list[CaseResult] = field(default_factory=list)
    model: str = ""

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def total(self) -> int:
        return len(self.results)

    def by_skill(self) -> dict[str, tuple[int, int]]:
        out: dict[str, tuple[int, int]] = {}
        for result in self.results:
            hit, total = out.get(result.case.skill, (0, 0))
            out[result.case.skill] = (hit + int(result.ok), total + 1)
        return out

    def render(self) -> str:
        lines = [f"model: {self.model or '(unknown)'}", ""]
        for skill, (hit, total) in sorted(self.by_skill().items()):
            lines.append(f"{skill}  {hit}/{total}")
            lines.extend(r.line() for r in self.results if r.case.skill == skill)
            lines.append("")
        lines.append(f"total {self.passed}/{self.total}")
        return "\n".join(lines)


async def run_case(case: Case, llm: LLMRunner) -> CaseResult:
    """Run one case against a live model."""
    result = CaseResult(case=case)
    skill = get(case.skill)

    try:
        outcome = await skill.run(case.inputs, llm=llm)
    except Exception as exc:  # noqa: BLE001 — a provider failure is a case failure
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    result.meta = outcome.meta
    output = outcome.output
    # Plain model_dump: `exclude_defaults` drops the `op` discriminator itself
    # (it has a default) and any argument the model happened to agree with,
    # so every expectation silently stopped matching.
    result.emitted = [op.model_dump() for op in getattr(output, "ops", [])]

    result.missing = [
        str(expected)
        for expected in case.must
        if not any(expected.matches(op) for op in result.emitted)
    ]
    result.forbidden = [
        str(banned)
        for banned in case.must_not
        if any(banned.matches(op) for op in result.emitted)
    ]
    result.decided_right = not result.missing and not result.forbidden

    result.result_works, notes = _check_outcome(case, outcome)
    result.notes.extend(notes)
    result.ok = result.decided_right and result.result_works
    return result


def _check_outcome(case: Case, outcome: Any) -> tuple[bool, list[str]]:
    """Did the decision actually produce something that works?"""
    notes: list[str] = []

    if outcome.failed:
        return False, [f"operation failed: {outcome.failed[0][1]}"]

    if case.skill == "viz.generate":
        spec = outcome.value
        if not isinstance(spec, Spec) or not spec:
            return False, ["no spec produced"]
        if outcome.meta.get("valid") is False:
            return False, [outcome.warnings[0] if outcome.warnings else "invalid spec"]

        # Grade what the MODEL said, not what repair rescued. `apply` runs
        # tier-2 repair, which silently rewrites a bad `temporal` back to
        # `nominal` — excellent in production, and it would mask exactly the
        # prompt failure this case exists to detect.
        raw = Spec.from_json_lenient(getattr(outcome.output, "spec_json", ""))
        rows = case.inputs.get("rows") or []
        for _, _, definition in raw.encodings():
            field_name = definition.get("field")
            if not field_name or definition.get("type") != "temporal":
                continue
            values = [row.get(field_name) for row in rows[:4]]
            if any(isinstance(v, str) and "Q" in v for v in values):
                return False, [
                    f"encoded {field_name!r} as temporal, but it holds quarter "
                    f"labels — this renders an Invalid Date axis (repair caught it, "
                    f"but the prompt should have)"
                ]

    if case.skill == "viz.narrate":
        summary = getattr(outcome.output, "summary", "")
        if not summary:
            return False, ["empty narration"]
        if outcome.warnings:
            return False, [outcome.warnings[0]]
        if not any(char.isdigit() for char in summary):
            notes.append("narration cites no figures")

    if case.skill == "viz.place":
        from nexcraftviz.compose.widget import SPAN_COLUMNS, Group

        widget = outcome.value
        for node in getattr(widget, "nodes", []):
            if isinstance(node, Group):
                total = sum(SPAN_COLUMNS.get(t.span, 3) for t in node.tiles)
                if total > 12:
                    return False, [f"group {node.id!r} spans {total} of 12 — the row wraps"]

    return True, notes


async def run(llm: LLMRunner, *, skill: str = "", model: str = "") -> Report:
    """Run every case (or one skill's) and report."""
    report = Report(model=model)
    for case in cases_for(skill):
        report.results.append(await run_case(case, llm))
    if not report.model:
        for result in report.results:
            if result.meta.get("model"):
                report.model = str(result.meta["model"])
                break
    return report


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - entry point
    """``python -m nexcraftviz.evals.runner`` — needs a configured provider."""
    import argparse
    import asyncio
    import sys

    parser = argparse.ArgumentParser(description="Run the live prompt evals.")
    parser.add_argument("--skill", default="", help="Only this skill.")
    parser.add_argument("--model", default="", help="Override OPENAI_MODEL.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)

    from nexcraftviz.integrations.providers import ProviderError, openai_runner

    try:
        llm = openai_runner(model=args.model or None)
    except ProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    report = asyncio.run(run(llm, skill=args.skill, model=args.model))
    if args.json:
        print(json.dumps({
            "model": report.model,
            "passed": report.passed,
            "total": report.total,
            "cases": [
                {"id": r.case.id, "skill": r.case.skill, "ok": r.ok,
                 "missing": r.missing, "forbidden": r.forbidden,
                 "notes": r.notes, "error": r.error}
                for r in report.results
            ],
        }, indent=2))
    else:
        print(report.render())
    return 0 if report.passed == report.total else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
