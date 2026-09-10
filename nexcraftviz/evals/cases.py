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


#: Four teams' completion rates. Summed they make a meaningless 314%.
TEAM_RATE_ROWS: list[dict[str, Any]] = [
    {"team": "Platform", "completion_pct": 82.0},
    {"team": "Data", "completion_pct": 91.0},
    {"team": "Growth", "completion_pct": 64.0},
    {"team": "Support", "completion_pct": 77.0},
]

TOTAL_ROWS: list[dict[str, Any]] = [{"total_revenue": 561.0}]

SORTED_BAR_SPEC = {
    **BAR_SPEC,
    "encoding": {
        "x": {"field": "region", "type": "nominal", "sort": "-y"},
        "y": {"field": "revenue", "type": "quantitative"},
    },
}

ORDERS_BAR_SPEC = {
    **BAR_SPEC,
    "encoding": {
        "x": {"field": "region", "type": "nominal"},
        "y": {"field": "orders", "type": "quantitative"},
    },
}

#: One big number where a comparison was asked for.
TOTAL_TEXT_SPEC = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "data": {"values": REGION_ROWS},
    "mark": {"type": "text", "fontSize": 40},
    "encoding": {"text": {"field": "revenue", "type": "quantitative", "aggregate": "sum"}},
}

KPI_SPEC = {"kpi_metadata": {"chart_type": "kpi", "title": "Total revenue", "value": 561}}

TABLE_SPEC = {
    "columns": [
        {"field": "region", "header": "Region", "render": "text"},
        {"field": "revenue", "header": "Revenue", "render": "number"},
    ],
    "data": {"values": REGION_ROWS},
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
    #: For skills whose output is not an operation list — a plan, a routing
    #: decision, a layout, a verdict. Keys are read per skill by
    #: `runner.check_decision`, and a test rejects any key it does not read.
    expect: dict[str, Any] = field(default_factory=dict)


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

PLAN_CASES: list[Case] = [
    Case(
        id="plan-ranking",
        skill="viz.plan",
        instruction="which region brought in the most revenue?",
        inputs={"question": "Which region brought in the most revenue?", "rows": REGION_ROWS},
        expect={"chart_type": ("bar",)},
        checks="a ranking question over one dimension and one measure is a bar",
    ),
    Case(
        id="plan-single-value",
        skill="viz.plan",
        instruction="what is total revenue?",
        inputs={"question": "What is total revenue?", "rows": TOTAL_ROWS},
        expect={"chart_type": ("kpi",)},
        checks="one number is a KPI, not a chart with a single bar",
    ),
    Case(
        id="plan-rate-not-summed",
        skill="viz.plan",
        instruction="show completion rate by team",
        inputs={"question": "Show completion rate by team", "rows": TEAM_RATE_ROWS},
        expect={"no_sum_of": "completion_pct"},
        checks="a rate is averaged, never summed — four teams added together is 314%",
    ),
    Case(
        id="plan-quarter-labels",
        skill="viz.plan",
        instruction="how has NPS moved by quarter?",
        inputs={"question": "How has NPS moved by quarter?", "rows": QUARTER_ROWS},
        expect={"chart_type": ("line", "area", "bar")},
        checks="quarter labels are ordered text; the plan should still reach a trend shape",
    ),
    Case(
        id="plan-missing-column",
        skill="viz.plan",
        instruction="break revenue down by cost centre",
        inputs={"question": "Break revenue down by cost centre", "rows": REGION_ROWS},
        expect={"status": ("insufficient_data", "ambiguous")},
        checks="THE important one: there is no cost centre. Planning revenue by region "
               "instead answers a question nobody asked.",
    ),
]


def _manage(case_id: str, instruction: str, expect: dict[str, Any], checks: str) -> Case:
    return Case(
        id=case_id,
        skill="viz.manage",
        instruction=instruction,
        inputs={"instruction": instruction, "spec": BAR_SPEC, "rows": REGION_ROWS},
        expect=expect,
        checks=checks,
    )


MANAGE_CASES: list[Case] = [
    _manage("manage-compound", "make it dark and sort descending",
            {"actions": ["theme", "edit"]},
            "keeps both halves, in order — the router alone drops one"),
    _manage("manage-narrate-last", "what does this show and sort it descending",
            {"actions": ["edit", "narrate"]},
            "narration goes last, so it describes the chart the user ends up looking at"),
    _manage("manage-unlabelled-edit", "can you make the biggest region stand out",
            {"actions": ["edit"]},
            "the rules cannot read it; the model must prefer an edit over a rebuild"),
    _manage("manage-decline", "compare this with last year",
            {"actions": ["decline"]},
            "there is no prior period in the rows; declining beats charting the wrong thing"),
    _manage("manage-widget", "build me a dashboard of revenue by region and orders by region",
            {"widget_parts": 2},
            "one widget step with two parts — the 'and' joins charts, not instructions"),
    _manage("manage-ambiguous", "make it better",
            {"status": "ambiguous"},
            "genuinely unclear; guessing an action is worse than saying so"),
]


def _viz(identifier: str, spec: dict[str, Any], chart_type: str, question: str) -> dict[str, Any]:
    return {"id": identifier, "spec": spec, "chart_type": chart_type, "question": question}


COMPOSE_CASES: list[Case] = [
    Case(
        id="compose-reading-order",
        skill="viz.compose",
        instruction="a revenue overview",
        inputs={
            "ask": "a revenue overview",
            "visualizations": [
                _viz("tile_1", KPI_SPEC, "kpi", "What is total revenue?"),
                _viz("tile_2", SORTED_BAR_SPEC, "bar", "Which region brought in the most?"),
                _viz("tile_3", TABLE_SPEC, "table_with_cells", "Revenue by region, row by row"),
            ],
        },
        expect={
            "first": "tile_1",
            "last": "tile_3",
            "span": {"tile_1": ("quarter", "third", "half"), "tile_3": ("full",)},
            "titled": True,
        },
        checks="the number first, the table last and full-width, and a title that "
               "names the content",
    ),
    Case(
        id="compose-every-chart-once",
        skill="viz.compose",
        instruction="compare revenue and orders across regions",
        inputs={
            "ask": "compare revenue and orders across regions",
            "visualizations": [
                _viz("tile_1", BAR_SPEC, "bar", "Revenue by region"),
                _viz("tile_2", ORDERS_BAR_SPEC, "bar", "Orders by region"),
                _viz("tile_3", SORTED_BAR_SPEC, "bar", "Regions ranked by revenue"),
            ],
        },
        expect={"titled": True},
        checks="every chart gets exactly one tile — none dropped as redundant, none invented",
    ),
]

CRITIQUE_CASES: list[Case] = [
    Case(
        id="critique-wrong-question",
        skill="viz.critique",
        instruction="How has revenue changed over time?",
        inputs={"question": "How has revenue changed over time?", "spec": BAR_SPEC,
                "rows": REGION_ROWS},
        expect={"answers_question": False},
        checks="revenue by region cannot answer a question about time, and the "
               "complaint has to say so",
    ),
    Case(
        id="critique-total-for-comparison",
        skill="viz.critique",
        instruction="How does revenue compare across regions?",
        inputs={"question": "How does revenue compare across regions?",
                "spec": TOTAL_TEXT_SPEC, "rows": REGION_ROWS},
        expect={"answers_question": False},
        checks="one total hides exactly the comparison it was asked for",
    ),
    Case(
        id="critique-right-chart",
        skill="viz.critique",
        instruction="Which region brought in the most revenue?",
        inputs={"question": "Which region brought in the most revenue?",
                "spec": SORTED_BAR_SPEC, "rows": REGION_ROWS},
        expect={"answers_question": True},
        checks="a sorted bar answers a ranking question — a critic that rejects "
               "everything is a cost with no benefit",
    ),
]

ALL_CASES: list[Case] = [
    *EDIT_CASES, *GENERATE_CASES, *PLACE_CASES, *NARRATE_CASES,
    *PLAN_CASES, *MANAGE_CASES, *COMPOSE_CASES, *CRITIQUE_CASES,
]


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
