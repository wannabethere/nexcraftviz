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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexcraftviz.evals.cases import Case, cases_for
from nexcraftviz.skills import get
from nexcraftviz.skills.base import LLMRunner
from nexcraftviz.spec.model import Spec

#: The binding that actually enforces the schema. Anything else is a fallback.
STRICT_BINDING = "json_schema_strict"


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
    #: The structured-output binding the provider actually used for this call.
    binding: str = ""
    #: What the answer produced, for a report to show: a spec's JSON or a
    #: widget's document. None for decisions that produce no artifact.
    artifact: dict[str, Any] | None = None
    #: The model's whole answer — narration prose, a generator's reasoning, a
    #: plan's rationale — so a report can show what was said, not only what
    #: it produced.
    answer: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Everything a report shows — the question, the answer, the artifact."""
        meta = self.meta or {}
        return {
            "id": self.case.id,
            "skill": self.case.skill,
            "checks": self.case.checks,
            "instruction": self.case.instruction,
            "inputs": self.case.inputs,
            "ok": self.ok,
            "decided_right": self.decided_right,
            "result_works": self.result_works,
            "error": self.error,
            "notes": self.notes,
            "missing": self.missing,
            "forbidden": self.forbidden,
            "emitted": self.emitted,
            "binding": self.binding,
            "artifact": self.artifact,
            "answer": self.answer,
            "tokens": {k: meta[k] for k in ("tokens_in", "tokens_out") if k in meta},
        }

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

    def bindings(self) -> dict[str, list[str]]:
        """Which structured-output binding each skill's calls actually got.

        The provider falls back from strict to a looser binding silently, when
        OpenAI refuses a schema. A skill running non-strict still mostly works —
        viz.plan passed four of five cases that way — which is exactly why it
        has to be visible: the failures it causes read as a flaky model rather
        than a schema defect.
        """
        seen: dict[str, set[str]] = {}
        for result in self.results:
            if result.binding:
                seen.setdefault(result.case.skill, set()).add(result.binding)
        return {skill: sorted(used) for skill, used in seen.items()}

    def non_strict(self) -> list[str]:
        return sorted(
            skill for skill, used in self.bindings().items() if used != [STRICT_BINDING]
        )

    def render(self) -> str:
        lines = [f"model: {self.model or '(unknown)'}", ""]
        bindings = self.bindings()
        for skill, (hit, total) in sorted(self.by_skill().items()):
            used = bindings.get(skill, [])
            warning = ""
            if used and used != [STRICT_BINDING]:
                warning = f"   ⚠ ran {', '.join(used)} — NOT strict, its schema was refused"
            lines.append(f"{skill}  {hit}/{total}{warning}")
            lines.extend(r.line() for r in self.results if r.case.skill == skill)
            lines.append("")
        lines.append(f"total {self.passed}/{self.total}")
        if self.non_strict():
            lines.append(f"NOT STRICT: {', '.join(self.non_strict())} — fix the schema; "
                         f"see tests/test_strict_schemas.py")
        return "\n".join(lines)


def save_report(report: Report, directory: Path) -> Path:
    """Write a timestamped `eval-*.json` — what `harness report --html` reads.

    Printing alone was not enough: a live pass costs five minutes and real
    tokens, and a result that only reached the terminal had to be run again to
    be shown to anyone.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"eval-{time.strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(
        json.dumps({
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "model": report.model,
            "results": [r.to_dict() for r in report.results],
        }, indent=2, default=str),
        encoding="utf-8",
    )
    return path


async def run_case(case: Case, llm: LLMRunner) -> CaseResult:
    """Run one case against a live model."""
    result = CaseResult(case=case)
    skill = get(case.skill)

    # Keep the model's own answer. Several skills repair it in `apply` — filling
    # blanks from the rules, appending a chart the model forgot — and grading
    # the repaired result would pass a model that said nothing at all.
    captured: dict[str, Any] = {}

    async def recording(system: str, user: str, schema: dict[str, Any]) -> Any:
        payload, meta = await llm(system, user, schema)
        captured["payload"] = payload
        return payload, meta

    try:
        outcome = await skill.run(case.inputs, llm=recording)
    except Exception as exc:  # noqa: BLE001 — a provider failure is a case failure
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    result.meta = outcome.meta
    result.binding = str((outcome.meta or {}).get("structured_output") or "")
    if outcome.output is not None and hasattr(outcome.output, "model_dump"):
        result.answer = outcome.output.model_dump(mode="json", exclude={"telemetry"})
    value = outcome.value
    if isinstance(value, Spec):
        result.artifact = value.raw
    elif hasattr(value, "tiles") and hasattr(value, "to_dict"):
        result.artifact = value.to_dict()
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

    if case.skill in GRADED_RAW:
        raw = skill.parse(captured.get("payload") or {})
        result.emitted = _describe(raw)
        problems = check_decision(case, raw)
        result.notes.extend(problems)
        result.decided_right = result.decided_right and not problems

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
        if outcome.meta.get("json_repaired"):
            # Production repairs and gates it, so the case can pass — but the
            # prompt produced broken JSON, and that belongs in the report.
            notes.append(
                f"the model's JSON needed repair: {'; '.join(outcome.meta['json_repaired'])}"
            )
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


#: Skills whose `apply` repairs the model's answer, so they are graded on the
#: raw payload instead. `viz.generate` is graded raw too, inside
#: `_check_outcome`, for the same reason: tier-2 repair rewrites a bad
#: `temporal` back to `nominal`.
GRADED_RAW = frozenset({"viz.plan", "viz.manage", "viz.compose", "viz.critique"})

#: The `expect` keys the scorer reads, per skill. A test asserts every case uses
#: only these — a mistyped key would otherwise check nothing and pass forever.
EXPECT_KEYS: dict[str, frozenset[str]] = {
    "viz.plan": frozenset({"chart_type", "status", "no_sum_of"}),
    "viz.manage": frozenset({"actions", "status", "widget_parts"}),
    "viz.compose": frozenset({"span", "first", "last", "titled"}),
    "viz.critique": frozenset({"answers_question"}),
}

#: Titles that name the container rather than what is in it.
_GENERIC_TITLES = frozenset({"", "dashboard", "widget", "chart", "charts"})


def check_decision(case: Case, raw: Any) -> list[str]:
    """What the model decided, against what the case expects. Empty is a pass."""
    expect = case.expect
    problems: list[str] = []

    if case.skill == "viz.plan":
        allowed_status = tuple(expect.get("status", ("ok",)))
        if raw.status not in allowed_status:
            reason = f" — {raw.reason_if_not_ok}" if raw.reason_if_not_ok else ""
            problems.append(
                f"status {raw.status!r}, expected one of {list(allowed_status)}{reason}"
            )
        if raw.status == "ok":
            columns = {key for row in case.inputs.get("rows") or [] for key in row}
            invented = sorted(raw.fields_used() - columns)
            if invented:
                problems.append(f"planned fields the data does not have: {', '.join(invented)}")
            allowed = expect.get("chart_type")
            if allowed and raw.chart_type not in allowed:
                problems.append(f"chart type {raw.chart_type!r}, expected one of {list(allowed)}")
            summed = expect.get("no_sum_of")
            if summed and any(e.field == summed and e.aggregate == "sum" for e in raw.encodings):
                problems.append(f"sums {summed!r}, which is a rate — it should be averaged")

    elif case.skill == "viz.manage":
        actions = [step.action for step in raw.steps]
        if "status" in expect and raw.status != expect["status"]:
            problems.append(f"status {raw.status!r}, expected {expect['status']!r}")
        if "actions" in expect and actions != list(expect["actions"]):
            problems.append(f"actions {actions}, expected {list(expect['actions'])}")
        if "widget_parts" in expect:
            widgets = [step for step in raw.steps if step.action == "widget"]
            if len(widgets) != 1:
                problems.append(
                    f"{len(widgets)} widget step(s), expected exactly one — got {actions}"
                )
            elif len(widgets[0].parts) != expect["widget_parts"]:
                problems.append(
                    f"widget parts {widgets[0].parts}, expected {expect['widget_parts']}"
                )

    elif case.skill == "viz.compose":
        wanted = [v["id"] for v in case.inputs.get("visualizations") or []]
        got = [tile.id for tile in raw.tiles]
        duplicated = sorted({i for i in got if got.count(i) > 1})
        invented = sorted(set(got) - set(wanted))
        dropped = [i for i in wanted if i not in got]
        if duplicated:
            problems.append(f"tile(s) listed twice: {', '.join(duplicated)}")
        if invented:
            problems.append(f"invented tile(s): {', '.join(invented)}")
        if dropped:
            problems.append(f"dropped chart(s): {', '.join(dropped)}")
        spans = {tile.id: tile.span for tile in raw.tiles}
        for tile_id, allowed in (expect.get("span") or {}).items():
            if spans.get(tile_id) not in allowed:
                problems.append(
                    f"{tile_id} spans {spans.get(tile_id)!r}, expected one of {list(allowed)}"
                )
        if "first" in expect and (not got or got[0] != expect["first"]):
            problems.append(f"reading order starts {got[:1]}, expected {expect['first']!r}")
        if "last" in expect and (not got or got[-1] != expect["last"]):
            problems.append(f"reading order ends {got[-1:]}, expected {expect['last']!r}")
        if expect.get("titled") and raw.title.strip().lower() in _GENERIC_TITLES:
            problems.append(f"title {raw.title!r} names the container, not the content")

    elif case.skill == "viz.critique":
        wanted_verdict = expect.get("answers_question")
        if wanted_verdict is not None and raw.answers_question != wanted_verdict:
            said = f" — said: {raw.complaint}" if raw.complaint else ""
            problems.append(
                f"answers_question={raw.answers_question}, expected {wanted_verdict}{said}"
            )
        elif wanted_verdict is False and not raw.complaint.strip():
            problems.append("rejected without a complaint — nothing a regeneration can act on")

    return problems


def _describe(raw: Any) -> list[dict[str, Any]]:
    """The model's answer, for the report."""
    steps = getattr(raw, "steps", None)
    if steps is not None:
        return [step.model_dump(mode="json") for step in steps]
    return [raw.model_dump(mode="json", exclude={"telemetry"})]


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
    parser.add_argument("--save", default="", help="Also write eval-*.json into this directory.")
    args = parser.parse_args(argv)

    from nexcraftviz.integrations.providers import ProviderError, openai_runner

    try:
        llm = openai_runner(model=args.model or None)
    except ProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    report = asyncio.run(run(llm, skill=args.skill, model=args.model))
    if args.save:
        print(f"saved to {save_report(report, Path(args.save))}", file=sys.stderr)
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
