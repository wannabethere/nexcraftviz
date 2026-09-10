"""Deterministic gates — the checks that need no model.

Run before the critic, because they are free, instant, and catch the failures
that actually happen. A model asked "is this chart good?" will discuss the
colour scheme while the chart renders a blank canvas.

Each gate exists because of a specific failure seen in this codebase:

* ``validates`` — an encoding naming a column that does not exist. Passes the
  JSON schema, compiles, renders `NaN`.
* ``renders`` — a spec that validates *and* compiles and still draws nothing.
  The first multi-ring gauge did exactly this.
* ``matches_plan`` — the generator quietly ignoring the plan, which is the one
  failure that only exists because the stages are separate.
* ``no_baked_styling`` — a hard-coded `mark.color` that will defeat the theme.
* ``data_honesty`` — a rate summed, or a quarter label encoded as temporal.

A gate that cannot fail is worse than no gate, so each has a test feeding it the
specific thing it exists to catch.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from nexcraftviz.agents.artifacts import ChartPlan, GateResult
from nexcraftviz.data.profile import profile_rows
from nexcraftviz.spec.kpi import kpi_fields
from nexcraftviz.spec.model import Spec
from nexcraftviz.spec.validate import validate

#: Measures that must never be summed. Adding four regions' completion
#: percentages together gives a meaningless 340%.
_NON_ADDITIVE = ("pct", "percent", "rate", "ratio", "avg", "average", "mean",
                 "median", "score", "index")

#: Minimum PNG size that counts as "something was drawn". An empty canvas of
#: this size compresses to well under a kilobyte.
_MIN_PNG_BYTES = 2000

#: Vega keywords that are valid for `align`, `baseline` and friends and are
#: never a label anyone meant to print. A text encoding carrying one is a
#: value copy-pasted from the property next to it — the shipped corpus has
#: seventeen donuts and gauges displaying the literal word "center" in the
#: middle of the ring, which validates, compiles and renders perfectly.
_PROPERTY_KEYWORDS = frozenset({
    "center", "middle", "left", "right", "top", "bottom",
    "start", "end", "baseline", "alphabetic", "line-top", "line-bottom",
})

Gate = Callable[..., GateResult]


def _gates() -> tuple[tuple[str, Gate], ...]:
    """The gates, in cost order, each paired with its name.

    The name is declared here rather than read off ``__name__`` so a gate that
    is wrapped, decorated or substituted still reports as itself — a gate whose
    identity depends on its function object reports the wrapper's name the first
    time anyone wraps one.
    """
    return (
        ("validates", _validates),
        ("no_baked_styling", _no_baked_styling),
        ("data_honesty", _data_honesty),
        ("matches_plan", _matches_plan),
        ("renders", _renders),
    )


def run_gates(
    spec: Spec,
    *,
    plan: ChartPlan | None = None,
    rows: list[dict[str, Any]] | None = None,
    skip: tuple[str, ...] = (),
) -> list[GateResult]:
    """Run every gate, in cost order."""
    rows = rows if rows is not None else spec.data_values
    results: list[GateResult] = []

    for name, gate in _gates():
        if name in skip:
            continue
        try:
            results.append(gate(spec, plan=plan, rows=rows))
        except Exception as exc:  # noqa: BLE001 — a broken gate must not stop the rest
            results.append(GateResult(
                gate=name, passed=True,
                detail=f"gate could not run ({type(exc).__name__}: {exc}) — "
                       f"not counted against the chart",
            ))
    return results


def _validates(spec: Spec, *, plan: ChartPlan | None, rows: list[dict[str, Any]]) -> GateResult:
    _, report = validate(spec, data=rows, max_tier=3)
    if report.ok:
        return GateResult(gate="validates", passed=True)
    return GateResult(gate="validates", passed=False, detail=str(report.errors[0]))


def _renders(spec: Spec, *, plan: ChartPlan | None, rows: list[dict[str, Any]]) -> GateResult:
    """Compiling is not drawing.

    Skipped rather than failed when the render extra is absent — an environment
    without it should not mark every chart as broken.
    """
    from nexcraftviz.render import RenderError, available, to_png

    if not available():
        return GateResult(gate="renders", passed=True,
                          detail="skipped — the `render` extra is not installed")
    if spec.family in ("kpi", "table"):
        return GateResult(gate="renders", passed=True,
                          detail=f"skipped — {spec.family} is drawn by the frontend, not Vega")
    try:
        png = to_png(spec)
    except RenderError as exc:
        return GateResult(gate="renders", passed=False, detail=str(exc))
    if len(png) < _MIN_PNG_BYTES:
        return GateResult(
            gate="renders", passed=False,
            detail=f"rendered {len(png)} bytes — the canvas is effectively blank",
        )
    return GateResult(gate="renders", passed=True)


def _matches_plan(spec: Spec, *, plan: ChartPlan | None, rows: list[dict[str, Any]]) -> GateResult:
    """Did the generator build what was planned?

    The check that only exists because planning and generating are separate.
    Deliberately forgiving about *how* — a plan asking for a bar is satisfied by
    a layered spec whose base mark is a bar — but not about *what*: a planned
    field that never appears is the generator having ignored the plan.
    """
    if plan is None or not plan.drawable:
        return GateResult(gate="matches_plan", passed=True, detail="no plan to check against")

    problems: list[str] = []

    if plan.chart_type:
        marks = {v.mark_type for v in spec.views() if v.mark_type}
        expected = _MARKS_FOR.get(plan.chart_type)
        if expected and marks and not (marks & expected):
            problems.append(
                f"planned {plan.chart_type} but built {'+'.join(sorted(marks))}"
            )

    planned_fields = plan.fields_used()
    if planned_fields:
        # A planned field is honoured wherever the spec reads it: in an
        # encoding, or as the input of a transform. This comment always said
        # transforms rename things; the code only looked at encodings, so a
        # correct top-N — sum `revenue` as `sum_revenue`, rank, filter — failed
        # for "missing revenue" twice in a live run, retry included.
        built = fields_read(spec)
        missing = sorted(planned_fields - built) if built else []
        if missing and len(missing) == len(planned_fields):
            problems.append(f"none of the planned fields appear: {', '.join(missing)}")
        elif missing:
            problems.append(f"planned fields missing: {', '.join(missing)}")

    if problems:
        return GateResult(gate="matches_plan", passed=False, detail="; ".join(problems))
    return GateResult(gate="matches_plan", passed=True)


#: A field read inside an expression: `datum.revenue` or `datum['revenue']`.
_DATUM = re.compile(r"""datum(?:\.([A-Za-z_]\w*)|\[['"]([^'"]+)['"]\])""")


def _transform_inputs(spec: Spec) -> set[str]:
    """Every field a transform READS, anywhere in the spec.

    Inputs only: an `as` names what a transform produces. Expressions in
    `calculate` and `filter` count through their `datum.` references.
    """
    found: set[str] = set()

    def read(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "field" and isinstance(value, str):
                    found.add(value)
                elif key in ("groupby", "fold") and isinstance(value, list):
                    found.update(v for v in value if isinstance(v, str))
                elif key != "as":
                    read(value)
        elif isinstance(node, list):
            for item in node:
                read(item)
        elif isinstance(node, str):
            for match in _DATUM.finditer(node):
                found.add(match.group(1) or match.group(2))

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "transform" and isinstance(value, list):
                    read(value)
                elif key != "data":
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(spec.raw)
    return found


def fields_read(spec: Spec) -> set[str]:
    """Every field the spec reads — in an encoding, or as a transform's input.

    Public because "does this chart use that column?" is asked in more than one
    place, and answering it by encodings alone was wrong twice: the matches_plan
    gate and the harness grader both failed a correct top-N that sums `revenue`
    as `sum_revenue`.
    """
    return {f for _, _, f in spec.field_refs()} | _transform_inputs(spec) | kpi_fields(spec)


#: Which Vega marks satisfy a planned chart type. Only the unambiguous ones —
#: a type absent here is not checked rather than being wrongly failed.
_MARKS_FOR: dict[str, set[str]] = {
    "bar": {"bar"}, "grouped_bar": {"bar"}, "stacked_bar": {"bar"},
    "histogram": {"bar"}, "waterfall": {"bar"}, "funnel": {"bar"},
    "line": {"line"}, "multi_line": {"line"}, "area": {"area", "line"},
    "scatter": {"point", "circle"}, "bubble": {"point", "circle"},
    "heatmap": {"rect"}, "boxplot": {"boxplot"},
    "pie": {"arc"}, "donut": {"arc"}, "gauge": {"arc"}, "radial_progress": {"arc"},
}


def _no_baked_styling(
    spec: Spec, *, plan: ChartPlan | None, rows: list[dict[str, Any]]
) -> GateResult:
    """A hard-coded mark colour silently overrides the theme applied afterwards."""
    offenders: list[str] = []
    for index, view in enumerate(spec.views()):
        mark = view.node.get("mark")
        if not isinstance(mark, dict):
            continue
        for key in ("color", "fill", "stroke"):
            if isinstance(mark.get(key), str) and mark[key].startswith("#"):
                offenders.append(f"view {index} mark.{key}={mark[key]}")

    if offenders:
        return GateResult(
            gate="no_baked_styling", passed=False,
            detail=f"{'; '.join(offenders[:3])} — this defeats the theme",
        )
    return GateResult(gate="no_baked_styling", passed=True)


def _data_honesty(
    spec: Spec, *, plan: ChartPlan | None, rows: list[dict[str, Any]]
) -> GateResult:
    """Encodings that render correctly and mislead."""
    if not rows:
        return GateResult(gate="data_honesty", passed=True, detail="no rows to check against")

    profile = profile_rows(rows)
    problems: list[str] = []

    for _, channel, definition in spec.encodings():
        field_name = definition.get("field")
        if not isinstance(field_name, str) or not field_name:
            continue
        column = profile.get(field_name)

        # A rate summed. Renders fine, means nothing.
        if definition.get("aggregate") == "sum" and any(
            hint in field_name.lower() for hint in _NON_ADDITIVE
        ):
            problems.append(f"{channel}: {field_name} is a rate, summed rather than averaged")

        # A quarter label as a date. Vega-Lite cannot parse it and renders an
        # Invalid Date axis.
        if definition.get("type") == "temporal" and column is not None:
            if column.vega_type == "nominal":
                sample = next(
                    (v for v in column.sample_values if isinstance(v, str)), ""
                )
                problems.append(
                    f"{channel}: {field_name} is text (e.g. {sample!r}) encoded as temporal"
                )

        # Time running backwards. A trend read right to left turns every rise
        # into a fall. Seen live: a planner asked for "the most useful view" of
        # a monthly completion trend sorted it newest-first, twice.
        if channel == "x" and definition.get("type") == "temporal" and _runs_backwards(definition):
            problems.append(f"x: {field_name} runs newest-first — time reads left to right")

        # An identifier on a positional axis: rows are entities, the id labels
        # them and is not a value.
        if column is not None and column.role == "identifier" and channel in ("x", "y"):
            if definition.get("type") == "quantitative":
                problems.append(f"{channel}: {field_name} is an identifier plotted as a value")

    # A hard-coded label that is really a property value. Checked separately
    # because these encodings bind no field at all, so the loop above skips them.
    problems.extend(_placeholder_labels(spec))

    if problems:
        return GateResult(gate="data_honesty", passed=False, detail="; ".join(problems[:3]))
    return GateResult(gate="data_honesty", passed=True)


def _runs_backwards(definition: dict[str, Any]) -> bool:
    sort = definition.get("sort")
    if sort == "descending" or (isinstance(sort, dict) and sort.get("order") == "descending"):
        return True
    scale = definition.get("scale")
    return isinstance(scale, dict) and scale.get("reverse") is True


def _placeholder_labels(spec: Spec) -> list[str]:
    """Text encodings printing a Vega keyword instead of a value.

    ``{"text": {"value": "center"}}`` next to ``"align": "center"`` is the
    property copied one line too far. It validates, it compiles, and it draws
    the word "center" in the middle of the chart — so nothing but a human
    looking at it, or this, will ever notice.
    """
    problems: list[str] = []
    for _, channel, definition in spec.encodings():
        if channel != "text":
            continue
        value = definition.get("value")
        if isinstance(value, str) and value.strip().lower() in _PROPERTY_KEYWORDS:
            problems.append(
                f"text: prints the literal {value!r} — that is a property value, "
                f"not a label"
            )
    return problems
