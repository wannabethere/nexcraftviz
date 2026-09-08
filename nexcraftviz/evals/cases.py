"""Eval fixtures — what a model is *supposed* to do.

The point of this suite is not to prove the plumbing works; the offline tests
already do that against a stub. It measures the one thing a stub cannot: whether
the prompts are good enough that a real model emits the right operations.

Scoring is on the **operation sequence**, not the resulting JSON. Two specs can
differ in whitespace, key order or an irrelevant default and be identical; two
op lists that differ are genuinely different decisions. It also means a case
stays valid when the applier's output changes.

Each case carries a ``must`` (operations that have to be there, with the
arguments that matter) and an optional ``must_not``. Exact-sequence matching
would fail a model for adding a reasonable extra step, which is not the
behaviour worth enforcing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nexcraftviz.spec.model import Spec

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

REGION_ROWS: list[dict[str, Any]] = [
    {"region": "West", "revenue": 128.0, "orders": 41, "status": "On track"},
    {"region": "East", "revenue": 96.0, "orders": 33, "status": "At risk"},
    {"region": "North", "revenue": 152.0, "orders": 48, "status": "On track"},
    {"region": "South", "revenue": 71.0, "orders": 22, "status": "Blocked"},
    {"region": "Central", "revenue": 114.0, "orders": 37, "status": "On track"},
]

QUARTER_ROWS: list[dict[str, Any]] = [
    {"quarter": "2025-Q1", "nps": 65}, {"quarter": "2025-Q2", "nps": 68},
    {"quarter": "2025-Q3", "nps": 71}, {"quarter": "2025-Q4", "nps": 74},
    {"quarter": "2026-Q1", "nps": 76}, {"quarter": "2026-Q2", "nps": 78},
]

MONTHLY_ROWS: list[dict[str, Any]] = [
    {"month": f"2026-{m:02d}-01", "region": r, "revenue": 100 + m * 3 + i * 20}
    for m in range(1, 7)
    for i, r in enumerate(("West", "East"))
]

BAR_SPEC = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "data": {"values": REGION_ROWS},
    "width": "container", "height": 220,
    "mark": "bar",
    "encoding": {
        "x": {"field": "region", "type": "nominal"},
        "y": {"field": "revenue", "type": "quantitative"},
    },
}


@dataclass
class Expectation:
    """One operation that must (or must not) appear."""

    op: str
    #: Argument values that matter. Others are the model's business.
    args: dict[str, Any] = field(default_factory=dict)

    def matches(self, emitted: dict[str, Any]) -> bool:
        if emitted.get("op") != self.op:
            return False
        return all(emitted.get(key) == value for key, value in self.args.items())

    def __str__(self) -> str:
        if not self.args:
            return self.op
        rendered = ", ".join(f"{k}={v!r}" for k, v in self.args.items())
        return f"{self.op}({rendered})"


@dataclass
class Case:
    id: str
    skill: str
    instruction: str
    inputs: dict[str, Any]
    must: list[Expectation] = field(default_factory=list)
    must_not: list[Expectation] = field(default_factory=list)
    #: What this case is actually testing, for the report.
    checks: str = ""


def _edit(case_id: str, instruction: str, must, *, must_not=None, spec=None,
          rows=None, checks="") -> Case:
    return Case(
        id=case_id,
        skill="viz.edit",
        instruction=instruction,
        inputs={
            "instruction": instruction,
            "spec": spec or BAR_SPEC,
            "rows": rows if rows is not None else REGION_ROWS,
        },
        must=must,
        must_not=must_not or [],
        checks=checks,
    )


EDIT_CASES: list[Case] = [
    _edit(
        "sort-descending",
        "sort it so the biggest region is first",
        [Expectation("sort_by", {"order": "descending"})],
        checks="the most basic instruction there is",
    ),
    _edit(
        "sort-ascending",
        "show the worst performers first",
        [Expectation("sort_by", {"order": "ascending"})],
        checks="direction is inferred from 'worst', not from the word 'ascending'",
    ),
    _edit(
        "top-n",
        "just show me the top 3",
        [Expectation("limit_top_n", {"n": 3})],
        checks="picks limit_top_n rather than writing a filter",
    ),
    _edit(
        "reference-line",
        "add a target line at 120",
        [Expectation("add_reference_line", {"value": 120.0})],
        checks="target lines are a dedicated operation",
    ),
    _edit(
        "colour-by",
        "colour the bars by status",
        [Expectation("set_color_field", {"field": "status"})],
        checks="uses a real column name",
    ),
    _edit(
        "change-mark",
        "make it a line chart instead",
        [Expectation("set_mark", {"mark": "line"})],
        checks="mark changes go through set_mark",
    ),
    _edit(
        "title",
        "give it the title 'Revenue by region'",
        [Expectation("set_title", {"text": "Revenue by region"})],
        checks="text is carried through verbatim",
    ),
    _edit(
        "missing-column",
        "break it down by cost centre",
        [],
        must_not=[Expectation("set_color_field"), Expectation("facet_by"),
                  Expectation("group_by")],
        checks="THE important one: refuses rather than substituting a column "
               "that happens to exist. Substituting answers a question nobody asked.",
    ),
    _edit(
        "compound",
        "sort descending and keep only the top 3",
        [Expectation("sort_by", {"order": "descending"}), Expectation("limit_top_n", {"n": 3})],
        checks="two instructions in one sentence",
    ),
    _edit(
        "no-hallucinated-palette",
        "use a teal colour scheme",
        [Expectation("set_palette")],
        checks="recolouring is set_palette, not a hand-written mark colour",
    ),
]

GENERATE_CASES: list[Case] = [
    Case(
        id="generate-bar",
        skill="viz.generate",
        instruction="revenue by region",
        inputs={"question": "What is revenue by region?", "rows": REGION_ROWS},
        checks="a categorical dimension and one measure should be a bar",
    ),
    Case(
        id="generate-quarter-labels",
        skill="viz.generate",
        instruction="NPS trend by quarter",
        inputs={"question": "How has NPS moved by quarter?", "rows": QUARTER_ROWS},
        checks="THE trap: '2025-Q1' is NOT temporal. Encoding it as temporal "
               "renders an Invalid Date axis — the exact defect already in the corpus.",
    ),
    Case(
        id="generate-multi-series",
        skill="viz.generate",
        instruction="revenue over time by region",
        inputs={"question": "How is revenue trending by region?", "rows": MONTHLY_ROWS},
        checks="a real time axis plus a dimension is several series",
    ),
]

PLACE_CASES: list[Case] = [
    Case(
        id="place-widen",
        skill="viz.place",
        instruction="make the funnel wider and shrink the stats next to it",
        inputs={"instruction": "make the funnel wider and shrink the stats next to it",
                "widget": None},  # filled at run time
        must=[Expectation("set_span", {"tile": "tile-funnel"}),
              Expectation("set_span", {"tile": "tile-conversion"})],
        checks="widening one tile must narrow its neighbour, or the row wraps",
    ),
    Case(
        id="place-group",
        skill="viz.place",
        instruction="put sourcing and time to hire together in their own panel",
        inputs={"instruction": "put sourcing and time to hire together in their own panel",
                "widget": None},
        must=[Expectation("group_tiles")],
        checks="grouping is a dedicated operation",
    ),
    Case(
        id="place-move",
        skill="viz.place",
        instruction="move time to hire above sourcing",
        inputs={"instruction": "move time to hire above sourcing", "widget": None},
        must=[Expectation("move_tile", {"tile": "tile-time-to-hire"})],
        checks="positions are relative to another tile, never an index",
    ),
]

NARRATE_CASES: list[Case] = [
    Case(
        id="narrate-bar",
        skill="viz.narrate",
        instruction="what does this show?",
        inputs={"spec": BAR_SPEC, "rows": REGION_ROWS,
                "question": "What does this show?"},
        checks="must lead with the answer and avoid chart mechanics",
    ),
]

ALL_CASES: list[Case] = [*EDIT_CASES, *GENERATE_CASES, *PLACE_CASES, *NARRATE_CASES]


def cases_for(skill: str = "") -> list[Case]:
    prepared = [_prepare(case) for case in ALL_CASES]
    return [c for c in prepared if not skill or c.skill == skill]


def _prepare(case: Case) -> Case:
    """Fill in fixtures that have to be built at run time."""
    if case.skill == "viz.place" and case.inputs.get("widget") is None:
        from nexcraftviz.examples import talent_acquisition_widget

        case.inputs["widget"] = talent_acquisition_widget()
    return case


def base_spec() -> Spec:
    return Spec(BAR_SPEC)
