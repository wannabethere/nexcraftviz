"""Boards: records as cards in lanes, built from the rows with no model."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from nexcraftviz.board import BoardSpec, build_board
from nexcraftviz.board.build import layout_hints_from_plan
from nexcraftviz.spec.model import Spec

PROJECTS = [
    {"project": "Extranet", "description": "Unit administration rollout across the regional offices.",
     "status": "Active", "region": "Lesotho", "segment": "Gov", "assigned": 2, "project_id": "p1"},
    {"project": "Liberty Group", "description": "Migrate from the legacy learning platform to the new one.",
     "status": "In-Progress - Implementation", "region": "AMER", "segment": "SMB", "assigned": 3, "project_id": "p2"},
    {"project": "Paradigm", "description": "Member onboarding redesign, delivered and signed off.",
     "status": "Completed", "region": "Guinea", "segment": "SMB", "assigned": 2, "project_id": "p3"},
    {"project": "Atlas", "description": "Estimating the integration effort with the architects.",
     "status": "Pre-Sales Estimation", "region": "EMEA", "segment": "Enterprise", "assigned": 1, "project_id": "p4"},
    {"project": "Cedar", "description": "Statement of work with procurement for review now.",
     "status": "SOW in progress", "region": "APAC", "segment": "SMB", "assigned": 1, "project_id": "p5"},
    {"project": "Ember", "description": "Paused at the client's request until budget is confirmed.",
     "status": "On Hold", "region": "EMEA", "segment": "Gov", "assigned": 0, "project_id": "p6"},
    {"project": "Beacon", "description": "Portal relaunch for the member community this quarter.",
     "status": "Active", "region": "AMER", "segment": "Enterprise", "assigned": 4, "project_id": "p7"},
]


def test_the_layout_is_read_from_the_rows():
    board = build_board(PROJECTS)
    layout = board.board
    assert layout.group_by == "status"
    assert layout.title_field == "project"
    assert layout.description_field == "description"
    assert set(layout.tag_fields) == {"region", "segment"}
    assert (layout.badge_field, layout.badge_label) == ("assigned", "assigned")


def test_lanes_follow_the_pipeline_not_the_alphabet():
    keys = [lane.key for lane in build_board(PROJECTS).lanes]
    assert keys == ["Pre-Sales Estimation", "SOW in progress", "In-Progress - Implementation",
                    "Active", "On Hold", "Completed"]


def test_cards_carry_what_the_screenshot_shows():
    active = next(lane for lane in build_board(PROJECTS).lanes if lane.key == "Active")
    assert active.count == 2 and active.tone == "pass"
    card = active.cards[0]
    assert card.title == "Extranet"
    assert card.tags == ["Lesotho", "Gov"] or card.tags == ["Gov", "Lesotho"]
    assert card.badge == "2 assigned"
    assert card.row["project_id"] == "p1"  # a host can link the card to its record


def test_a_status_column_is_a_lane_even_when_every_value_differs():
    rows = [{"name": f"P{i}", "status": s} for i, s in enumerate(["New", "Active", "Done"])]
    assert build_board(rows).board.group_by == "status"


def test_no_column_with_a_handful_of_values_means_no_lanes():
    rows = [{"name": f"P{i}", "notes": f"note {i}"} for i in range(30)]
    assert build_board(rows).lanes == []


def test_a_lane_keeps_its_count_past_the_cards_it_shows():
    rows = [{"name": f"P{i}", "status": "Active" if i % 2 else "Done"} for i in range(40)]
    lane = build_board(rows, max_cards=5).lanes[0]
    assert lane.count == 20 and len(lane.cards) == 5


def test_the_planners_parts_win_over_the_guess():
    plan = SimpleNamespace(encodings=[
        SimpleNamespace(channel="group", field="segment"),
        SimpleNamespace(channel="title", field="project"),
        SimpleNamespace(channel="tag", field="region"),
        SimpleNamespace(channel="badge", field="assigned"),
        SimpleNamespace(channel="group", field="not_a_column"),
    ])
    hints = layout_hints_from_plan(plan, set(PROJECTS[0]))
    board = build_board(PROJECTS, **hints)
    assert board.board.group_by == "segment"
    assert board.board.tag_fields == ["region"]


def test_a_board_is_its_own_family_never_a_table():
    spec = build_board(PROJECTS).to_spec()
    assert spec.family == "board"
    assert BoardSpec.from_spec(spec).lanes
    assert Spec({"columns": [{"field": "a", "header": "A"}]}).family == "table"


def test_a_board_validates_and_passes_the_gates():
    from nexcraftviz.evaluate.gates import run_gates
    from nexcraftviz.spec.validate import validate

    spec = build_board(PROJECTS).to_spec()
    _, report = validate(spec, max_tier=3)
    assert report.ok, report.summary()
    assert all(g.passed for g in run_gates(spec, plan=None, rows=PROJECTS)), \
        [g for g in run_gates(spec, plan=None, rows=PROJECTS) if not g.passed]


def test_the_generator_builds_a_board_without_a_model():
    from nexcraftviz.agents.artifacts import ChartPlan, ChartStages
    from nexcraftviz.agents.builtin import GeneratorAgent
    from nexcraftviz.agents.registry import StageContext

    async def no_model(*_a, **_k):
        raise AssertionError("a board must not call the model")

    plan = ChartPlan(status="ok", chart_type="board", confidence=0.9)
    stages = ChartStages()
    stages.plan = plan
    ctx = StageContext(question="show the pipeline as a board", rows=PROJECTS, language="English",
                       theme="", stages=stages, llm=no_model, options={})
    artifact = asyncio.run(GeneratorAgent().run(ctx))
    assert artifact.ok and artifact.chart_type == "board"
    assert artifact.spec.family == "board"


def test_the_chart_surface_returns_a_board():
    from nexcraftviz.app.chart_api import chart_type_of, envelope

    spec = build_board(PROJECTS, title="Projects by status").to_spec()
    out = envelope(spec, rows=PROJECTS)
    assert chart_type_of(spec) == "board"
    assert out["chart_schema"]["lanes"]


def test_naming_a_board_is_explicit():
    from nexcraftviz.agents.intent import extract_intent

    for asked in ("show the projects as a board", "kanban of open deals", "the pipeline board"):
        assert extract_intent(asked).chart_type == "board", asked


def test_the_reference_renderer_draws_lanes_and_cards():
    from nexcraftviz.render.html import render_board

    html = render_board(build_board(PROJECTS), max_cards=1)
    assert html.count('class="nxv-board__lane') == 6
    assert "2 assigned" in html and "+1 more" in html


def test_every_board_pair_draws_in_the_gallery():
    from nexcraftviz.corpus.loader import seed
    from nexcraftviz.gallery import _board_of
    from nexcraftviz.render.html import render_board

    pairs = [p for p in seed().pairs if p.family == "board"]
    assert len(pairs) >= 2
    for pair in pairs:
        board = _board_of(pair)
        assert len(board.lanes) >= 3, pair.name
        assert "nxv-board__card" in render_board(board)


def test_a_money_badge_reads_as_a_figure():
    rows = [{"deal": "A", "stage": "Won", "amount": 48000}, {"deal": "B", "stage": "Lost", "amount": 1500}]
    card = build_board(rows).lanes[0].cards[0]
    assert card.badge == "48,000"


def test_a_field_shows_once_on_a_card():
    board = build_board(PROJECTS, subtitle_field="segment", tag_fields=["region", "segment", "status"])
    assert board.board.tag_fields == ["region"]
