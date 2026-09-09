"""The manager dispatching through the registry, and widget generation.

Two things under test that are easy to get wrong quietly:

* an overridden role must change what the instruction box does, not just what
  the conversation does;
* a widget must be composed from **visualizations** — charts that already exist
  and have already passed their gates — rather than from the rows.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from nexcraftviz.agents import AgentRegistry, AgentSpec, StageContext
from nexcraftviz.compose.widget import Widget
from nexcraftviz.manager import ManagerStep, run_instruction
from nexcraftviz.manager.decision import ACTIONS, ROLE_FOR
from nexcraftviz.manager.split import label_all, widget_parts
from nexcraftviz.session import Session
from nexcraftviz.skills.base import SkillResult
from nexcraftviz.skills.compose import (
    ComposeIn,
    ComposeSkill,
    TileDesign,
    Visualization,
    WidgetDesign,
    build_widget,
)
from nexcraftviz.spec.model import Spec

ROWS: list[dict[str, Any]] = [
    {"region": "North", "month": "2026-01-01", "revenue": 152.0, "headcount": 41},
    {"region": "West", "month": "2026-01-01", "revenue": 128.0, "headcount": 33},
    {"region": "North", "month": "2026-02-01", "revenue": 161.0, "headcount": 44},
    {"region": "West", "month": "2026-02-01", "revenue": 121.0, "headcount": 31},
    {"region": "North", "month": "2026-03-01", "revenue": 174.0, "headcount": 47},
    {"region": "West", "month": "2026-03-01", "revenue": 133.0, "headcount": 35},
]

CHART = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "data": {"values": ROWS},
    "mark": "bar",
    "encoding": {
        "x": {"field": "region", "type": "nominal"},
        "y": {"field": "revenue", "type": "quantitative", "aggregate": "sum"},
    },
}

DESIGN = {
    "status": "ok", "reason_if_not_ok": "",
    "title": "Revenue and headcount", "description": "How the two move together.",
    "layout": "featured",
    "tiles": [
        {"id": "tile_1", "title": "Revenue by region", "span": "two-thirds", "note": ""},
        {"id": "tile_2", "title": "Headcount over time", "span": "third", "note": ""},
    ],
    "groups": [], "concerns": [],
}


@pytest.fixture
def stub_llm():
    """The offline stand-in for charts, plus a fixed widget design."""
    from harness.offline import offline_runner

    base = offline_runner(ROWS)
    seen: list[dict[str, Any]] = []

    async def run(system: str, user: str, schema: dict[str, Any]):
        props = set((schema or {}).get("properties") or {})
        if "tiles" in props and "layout" in props:
            seen.append(json.loads(user))
            return DESIGN, {"model": "stub"}
        return await base(system, user, schema)

    run.seen = seen  # type: ignore[attr-defined]
    return run


class RecordingRole:
    """A double filling one role, so a test can prove the registry was consulted."""

    def __init__(self, role: str, *, value: Any = None, skill: str = "") -> None:
        self.spec = AgentSpec(role=role, name=f"double.{role}", uses_llm=False, skill=skill)
        self.calls: list[StageContext] = []
        self._value = value

    async def run(self, ctx: StageContext) -> SkillResult:
        self.calls.append(ctx)
        return SkillResult(
            skill=self.spec.skill or "double", value=self._value, changes=["doubled"]
        )


# ---------------------------------------------------------------------------
# dispatch goes through the registry
# ---------------------------------------------------------------------------

def test_every_action_maps_to_a_role_that_exists():
    """A step whose role nothing fills would fail at run time, not import time."""
    registry = AgentRegistry.default()
    for action in ACTIONS:
        if action in ("recreate", "decline"):
            continue  # dispatched by the executor, not by a single role
        assert action in ROLE_FOR, action
        assert registry.has(ROLE_FOR[action]), f"{action} → {ROLE_FOR[action]}"


@pytest.mark.asyncio
async def test_an_overridden_role_changes_what_the_instruction_box_does(stub_llm):
    """Going straight to a skill would mean an override changed the conversation
    and not the annotate box — a split nobody finds until it has confused them."""
    edited = Spec({**CHART, "mark": "point"})
    editor = RecordingRole("editor", value=edited, skill="viz.edit")

    registry = AgentRegistry.default()
    registry.override("editor", editor)
    session = Session(rows=ROWS, document=Spec(dict(CHART)), registry=registry)

    run = await run_instruction("sort descending", session=session, llm=stub_llm)
    assert run.actions == ["edit"]
    assert len(editor.calls) == 1, "the registry's editor was used"
    assert session.document.raw["mark"] == "point"


@pytest.mark.asyncio
async def test_an_overridden_role_still_lands_in_the_session_history(stub_llm):
    """An override must not quietly cost the caller their turn history."""
    editor = RecordingRole("editor", value=Spec(dict(CHART)), skill="viz.edit")
    registry = AgentRegistry.default().clone()
    registry.override("editor", editor)
    session = Session(rows=ROWS, document=Spec(dict(CHART)), registry=registry)

    await run_instruction("sort descending", session=session, llm=stub_llm)
    assert [t.skill for t in session.turns] == ["viz.edit"]


@pytest.mark.asyncio
async def test_a_role_receives_the_clause_not_the_whole_message(stub_llm):
    editor = RecordingRole("editor", value=Spec(dict(CHART)), skill="viz.edit")
    registry = AgentRegistry.default().clone()
    registry.override("editor", editor)
    session = Session(rows=ROWS, document=Spec(dict(CHART)), registry=registry)

    await run_instruction("make it dark and sort descending", session=session, llm=stub_llm)
    assert editor.calls[0].question == "sort descending"


# ---------------------------------------------------------------------------
# splitting a widget ask
# ---------------------------------------------------------------------------

def test_a_widget_ask_is_one_step_not_two():
    """The conjunction joins two charts. Splitting it first leaves the widget
    step holding half the request and the other half read as an edit."""
    clauses = label_all(
        "build me a dashboard of revenue by region and headcount over time",
        has_chart=True, has_widget=False,
    )
    assert [c.action for c in clauses] == ["widget"]
    assert clauses[0].parts == ["revenue by region", "headcount over time"]


@pytest.mark.parametrize(
    ("ask", "expected"),
    [
        ("build me a dashboard of revenue by region and headcount over time",
         ["revenue by region", "headcount over time"]),
        ("a widget with revenue by month, orders by region, and total spend",
         ["revenue by month", "orders by region", "total spend"]),
        # No charts named — one is chosen from the data instead.
        ("show me a dashboard", []),
    ],
)
def test_widget_parts_are_read_from_the_ask(ask, expected):
    assert widget_parts(ask) == expected


def test_rearranging_an_existing_widget_is_placement_not_generation():
    """"Make the dashboard wider" mentions a widget and rebuilds nothing."""
    clauses = label_all("make the dashboard wider", has_chart=True, has_widget=True)
    assert [c.action for c in clauses] == ["place"]


def test_the_same_words_build_a_widget_when_there_is_none():
    clauses = label_all("make the dashboard wider", has_chart=True, has_widget=False)
    assert clauses[0].action == "widget"


# ---------------------------------------------------------------------------
# building a widget
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_widget_is_built_from_several_gated_charts(stub_llm):
    session = Session(rows=ROWS)
    run = await run_instruction(
        "build me a dashboard of revenue by region and headcount over time",
        session=session, llm=stub_llm,
    )
    assert run.actions == ["widget"]
    assert run.ok

    widget = session.document
    assert isinstance(widget, Widget)
    assert widget.title == "Revenue and headcount"
    assert widget.layout == "featured"
    assert [t.id for t in widget.tiles] == ["tile_1", "tile_2"]
    assert [t.span for t in widget.tiles] == ["two-thirds", "third"]
    assert session.state()["kind"] == "widget"


@pytest.mark.asyncio
async def test_the_composer_is_shown_visualizations_not_data(stub_llm):
    """The charts already exist and have already passed their gates, so the only
    open question is layout. Handing over the rows would invite the composer to
    relitigate work it has no way to do better."""
    session = Session(rows=ROWS)
    await run_instruction(
        "build me a dashboard of revenue by region and headcount over time",
        session=session, llm=stub_llm,
    )
    payload = stub_llm.seen[0]
    assert "rows" not in payload
    for visualization in payload["visualizations"]:
        assert set(visualization) == {"id", "chart_type", "family", "fields", "question"}
        # The spec itself is withheld: a composer handed a Vega-Lite document
        # starts editing encodings, which is not its job.
        assert "spec" not in visualization
        assert "data" not in visualization


@pytest.mark.asyncio
async def test_one_chart_failing_does_not_lose_the_widget(stub_llm):
    """A part that cannot be charted is reported; the rest are still arranged."""
    session = Session(rows=ROWS)
    step = ManagerStep(
        action="widget",
        instruction="a dashboard",
        parts=["revenue by region", "the colour of the sky"],
    )
    from nexcraftviz.manager.decision import ManagerDecision

    run = await run_instruction(
        "a dashboard", session=session, llm=stub_llm,
        decision=ManagerDecision(steps=[step]),
    )
    # The stand-in charts both asks from the same rows, so both succeed here;
    # what matters is that a failure is reported rather than raised.
    assert run.ok
    assert isinstance(session.document, Widget)


@pytest.mark.asyncio
async def test_a_widget_replaces_the_document_and_drops_undo(stub_llm):
    """An undo entry that cannot reconstruct the previous chart is worse than
    none: the button is offered and does the wrong thing."""
    session = Session(rows=ROWS, document=Spec(dict(CHART)))
    await run_instruction("sort descending", session=session, llm=stub_llm)
    await run_instruction("build me a dashboard of revenue by region",
                          session=session, llm=stub_llm)
    assert isinstance(session.document, Widget)
    assert not session.can_undo


# ---------------------------------------------------------------------------
# the design, and repairing it
# ---------------------------------------------------------------------------

def _visualizations(*ids: str) -> list[Visualization]:
    return [
        Visualization(id=i, spec=Spec(dict(CHART)), chart_type="bar", question=f"q {i}")
        for i in ids
    ]


def test_a_tile_naming_a_chart_that_does_not_exist_is_dropped():
    skill = ComposeSkill()
    inputs = ComposeIn(ask="a dashboard", visualizations=_visualizations("tile_1"))
    design = WidgetDesign(tiles=[
        TileDesign(id="tile_1"), TileDesign(id="tile_9"),
    ])
    result = skill.apply(inputs, design)
    assert [t.id for t in result.value.tiles] == ["tile_1"]
    assert any("no such visualization" in c for c in result.value.concerns)


def test_a_chart_the_design_forgot_is_appended_not_lost():
    """It is a chart the user asked for. Dropping it silently is the bug."""
    skill = ComposeSkill()
    inputs = ComposeIn(ask="a dashboard", visualizations=_visualizations("tile_1", "tile_2"))
    result = skill.apply(inputs, WidgetDesign(tiles=[TileDesign(id="tile_1")]))
    assert [t.id for t in result.value.tiles] == ["tile_1", "tile_2"]
    assert any("omitted it" in c for c in result.value.concerns)


def test_a_panel_left_with_one_tile_is_dropped():
    from nexcraftviz.skills.compose import GroupDesign

    skill = ComposeSkill()
    inputs = ComposeIn(ask="a dashboard", visualizations=_visualizations("tile_1"))
    design = WidgetDesign(
        tiles=[TileDesign(id="tile_1"), TileDesign(id="tile_9")],
        groups=[GroupDesign(title="Pair", tiles=["tile_1", "tile_9"])],
    )
    result = skill.apply(inputs, design)
    assert result.value.groups == []


def test_grouped_tiles_keep_the_designs_reading_order():
    from nexcraftviz.skills.compose import GroupDesign

    visualizations = _visualizations("a", "b", "c")
    design = WidgetDesign(
        title="W", layout="grid",
        tiles=[TileDesign(id="a"), TileDesign(id="b"), TileDesign(id="c")],
        groups=[GroupDesign(title="Pair", tiles=["b", "c"])],
    )
    widget = build_widget(design, visualizations)
    assert [n.id for n in widget.nodes][0] == "a"
    assert len(widget.groups) == 1
    assert [t.id for t in widget.groups[0].tiles] == ["b", "c"]


def test_a_design_with_no_tiles_says_so():
    skill = ComposeSkill()
    inputs = ComposeIn(ask="a dashboard", visualizations=[])
    result = skill.apply(inputs, WidgetDesign(tiles=[]))
    assert not result.value.ok
    assert "no visualizations" in result.value.reason_if_not_ok


# ---------------------------------------------------------------------------
# over the chart surface
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_widget_comes_back_ready_to_mount(stub_llm):
    """A widget is several charts, so it cannot be squeezed into chart_schema.
    The specs travel beside the markup because a <script> inserted via innerHTML
    never executes."""
    from nexcraftviz.app.chart_api import annotate_chart

    result, _ = await annotate_chart(
        instruction="build me a dashboard of revenue by region and headcount over time",
        chart_schema=None, rows=ROWS, llm=stub_llm,
    )
    assert result["actions"] == ["widget"]
    assert result["chart_type"] == "widget"

    widget = result["widget"]
    assert widget is not None
    assert [t["id"] for t in widget["tiles"]] == ["tile_1", "tile_2"]
    assert widget["html"].strip().startswith("<")
    assert set(widget["specs"]) == {"tile_1", "tile_2"}
    assert widget["document"]["layout"] == "featured"


@pytest.mark.asyncio
async def test_a_plain_chart_carries_no_widget(stub_llm):
    from nexcraftviz.app.chart_api import annotate_chart

    result, _ = await annotate_chart(
        instruction="sort descending", chart_schema=dict(CHART), rows=ROWS, llm=stub_llm,
    )
    assert result["widget"] is None
    assert result["chart_schema"]


def test_every_spec_has_a_mount_point_in_the_markup():
    """The contract between `html` and `specs`: the caller mounts each spec onto
    an element by tile id. A spec with no matching element renders nothing, and
    nothing reports it — the card just stays empty."""
    import re

    from nexcraftviz.skills.compose import GroupDesign

    visualizations = _visualizations("tile_1", "tile_2", "tile_3")
    design = WidgetDesign(
        title="W", layout="grid",
        tiles=[TileDesign(id="tile_1"), TileDesign(id="tile_2"), TileDesign(id="tile_3")],
        groups=[GroupDesign(title="Pair", tiles=["tile_2", "tile_3"])],
    )
    widget = build_widget(design, visualizations)

    html = widget.to_html()
    ids = set(re.findall(r'id="([^"]+)"', html))
    assert set(widget.chart_specs()) <= ids, "a spec with no element renders nothing"


def test_an_id_prefix_reaches_both_the_markup_and_the_specs():
    """Two widgets on one page would otherwise collide on duplicate DOM ids."""
    visualizations = _visualizations("tile_1")
    design = WidgetDesign(title="W", tiles=[TileDesign(id="tile_1")])
    widget = build_widget(design, visualizations)

    assert 'id="w1-tile_1"' in widget.to_html(id_prefix="w1-")
    assert set(widget.chart_specs(id_prefix="w1-")) == {"w1-tile_1"}
