"""The manager, and the chart surface a dashboard host consumes. All offline."""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nexcraftviz.app.api import create_app
from nexcraftviz.app.chart_api import (
    ChartSurfaceError,
    annotate_chart,
    capabilities,
    chart_type_of,
    create_chart,
    envelope,
    question_for,
)
from nexcraftviz.manager import ManagerDecision, ManagerStep, run_instruction, split_clauses
from nexcraftviz.manager.split import label_all
from nexcraftviz.session import Session
from nexcraftviz.skills.manage import ManageIn, ManageSkill
from nexcraftviz.spec.model import Spec

ROWS: list[dict[str, Any]] = [
    {"region": "North", "revenue": 152.0},
    {"region": "West", "revenue": 128.0},
    {"region": "East", "revenue": 96.0},
    {"region": "South", "revenue": 71.0},
]

CHART = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "data": {"values": ROWS},
    "mark": "bar",
    "encoding": {
        "x": {"field": "region", "type": "nominal"},
        "y": {"field": "revenue", "type": "quantitative"},
    },
}


@pytest.fixture
def stub_llm():
    """Answers each skill by the shape of the schema it is handed."""
    calls: list[str] = []

    async def run(system: str, user: str, schema: dict[str, Any]):
        props = set((schema or {}).get("properties") or {})
        if "ops" in props and "unresolved" not in props:
            calls.append("edit")
            payload: dict[str, Any] = {
                "ops": [{"op": "sort_by", "channel": "x", "by": "revenue",
                         "order": "descending"}],
                "reasoning": "ranked",
            }
        elif "summary" in props and "headline" in props:
            calls.append("narrate")
            payload = {"summary": "North leads on revenue.", "headline": "North leads",
                       "points": []}
        elif "steps" in props:
            calls.append("manage")
            payload = {"status": "ok", "steps": [], "declined": [], "reason_if_not_ok": ""}
        elif "encodings" in props:
            calls.append("plan")
            payload = {
                "status": "ok", "confidence": 0.9, "chart_type": "bar",
                "alternatives": [], "rationale": "Four regions on one measure.",
                "encodings": [
                    {"channel": "x", "field": "region", "why": "the categories"},
                    {"channel": "y", "field": "revenue", "aggregate": "sum",
                     "why": "the measure"},
                ],
                "transforms": [], "styling": {}, "metadata": {}, "follow_ups": [],
            }
        elif "spec_json" in props:
            calls.append("generate")
            payload = {"spec_json": json.dumps(CHART), "chart_type": "bar",
                       "reasoning": "as planned"}
        else:
            calls.append("other")
            payload = {}
        return payload, {"model": "stub", "tokens_in": 10, "tokens_out": 5}

    run.calls = calls  # type: ignore[attr-defined]
    return run


def _session(**kwargs) -> Session:
    return Session(rows=ROWS, document=Spec(json.loads(json.dumps(CHART))), **kwargs)


# ---------------------------------------------------------------------------
# splitting and labelling
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("make it dark and sort descending", ["theme", "edit"]),
        ("make it wider, then make it dark", ["place", "theme"]),
        ("sort descending", ["edit"]),
        ("what does this show?", ["narrate"]),
        ("break it down by month", ["decline"]),
        ("start over", ["recreate"]),
        # Opens with a create verb but names an edit operation. With a chart on
        # screen that is a change, not a fresh start.
        ("show the top 10", ["edit"]),
    ],
)
def test_the_rules_label_the_common_phrasings_for_free(message, expected):
    clauses = label_all(message, has_chart=True, has_widget=True)
    assert [c.action for c in clauses] == expected
    assert all(c.confident for c in clauses), "these should cost no model call"


def test_a_conjunction_inside_one_instruction_is_not_a_split():
    """"sort by region and revenue" names two columns, not two instructions."""
    assert split_clauses("sort by region and revenue") == ["sort by region and revenue"]


def test_a_vague_instruction_is_handed_to_the_model():
    inputs = ManageIn(instruction="make it prettier", spec=CHART, rows=ROWS)
    assert inputs.needs_model()


def test_a_fully_labelled_instruction_is_not():
    inputs = ManageIn(instruction="make it dark and sort descending", spec=CHART, rows=ROWS)
    assert not inputs.needs_model()


# ---------------------------------------------------------------------------
# the manager
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_compound_instruction_applies_both_halves(stub_llm):
    """`route()` keeps one skill and silently drops the other half. This is the
    whole reason the manager exists."""
    session = _session()
    run = await run_instruction("make it dark and sort descending",
                                session=session, llm=stub_llm)
    assert run.actions == ["theme", "edit"]
    assert session.theme == "nexcraftviz-dark"
    assert session.document.raw["encoding"]["x"]["sort"]["order"] == "descending"
    assert "manage" not in stub_llm.calls, "the rules read both clauses"


@pytest.mark.asyncio
async def test_narration_runs_last_so_it_describes_the_final_chart(stub_llm):
    session = _session()
    run = await run_instruction("what does this show and sort it descending",
                                session=session, llm=stub_llm)
    assert run.actions == ["edit", "narrate"]
    assert run.narration is not None
    assert session.document.raw["encoding"]["x"].get("sort")


@pytest.mark.asyncio
async def test_an_out_of_scope_request_is_declined_with_a_reason(stub_llm):
    session = _session()
    run = await run_instruction("break it down by month", session=session, llm=stub_llm)
    assert run.actions == ["decline"]
    assert "needs a different query" in run.decision.declined[0]
    # Nothing was drawn: a chart answering a question nobody asked is worse
    # than a refusal.
    assert session.document.raw == CHART
    assert stub_llm.calls == []


@pytest.mark.asyncio
async def test_a_failed_step_does_not_stop_the_ones_after_it():
    async def flaky(system, user, schema):
        props = set((schema or {}).get("properties") or {})
        if "ops" in props:
            raise RuntimeError("the edit model fell over")
        return {"summary": "still described", "headline": "", "points": []}, {}

    session = _session()
    run = await run_instruction("sort descending and tell me what it shows",
                                session=session, llm=flaky)
    assert not run.ok
    assert run.narration is not None, "the narration still ran"
    assert any("edit" in failure for failure in run.failures())


@pytest.mark.asyncio
async def test_layout_with_no_widget_becomes_a_chart_edit(stub_llm):
    """The instruction is real; dropping it silently would be the bug."""
    session = _session()
    run = await run_instruction("make it wider", session=session, llm=stub_llm)
    assert run.actions == ["edit"]


@pytest.mark.asyncio
async def test_with_no_chart_every_change_is_a_build(stub_llm):
    session = Session(rows=ROWS)
    run = await run_instruction("sort descending", session=session, llm=stub_llm)
    assert run.actions == ["recreate"]
    assert session.has_chart


def test_the_repair_rewrites_a_step_that_could_not_run():
    skill = ManageSkill()
    inputs = ManageIn(instruction="move it left", spec=CHART, rows=ROWS)  # no widget
    decision = ManagerDecision(steps=[ManagerStep(action="place", instruction="move it left")])
    result = skill.apply(inputs, decision)
    assert [s.action for s in result.value.steps] == ["edit"]


# ---------------------------------------------------------------------------
# the chart surface
# ---------------------------------------------------------------------------

def test_capabilities_names_every_type_the_package_can_produce():
    caps = capabilities()
    assert caps["renderer"] == "vega-lite"
    # The endpoint this replaces allowed seven and 500'd on the rest.
    assert len(caps["chart_types"]) >= 20
    for chart_type in ("kpi", "heatmap", "scatter", "donut", "gauge", "table_with_cells"):
        assert chart_type in caps["chart_types"]


def test_a_full_vega_spec_is_refused_rather_than_returned_blank():
    """A Vega-Lite host takes Vega-Lite only. A spec it cannot draw renders nothing
    and reports nothing, which is the worst of both."""
    vega = Spec({"$schema": "https://vega.github.io/schema/vega/v5.json", "marks": []})
    with pytest.raises(ChartSurfaceError, match="cannot draw"):
        envelope(vega, rows=ROWS)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (CHART, "bar"),
        ({"kpi_metadata": {"chart_type": "kpi_hconcat"}}, "kpi_hconcat"),
        ({"kpi_metadata": {}}, "kpi"),
        ({"columns": [], "$schema": "x"}, "chart"),
    ],
)
def test_chart_type_is_read_from_the_spec(raw, expected):
    """The spec is what gets rendered, so it is the only thing that cannot be
    out of date."""
    assert chart_type_of(Spec(raw)) == expected


def test_the_envelope_carries_what_a_vega_lite_consumer_reads():
    result = envelope(Spec(dict(CHART)), rows=ROWS, reasoning="because")
    for key in ("chart_type", "chart_schema", "reasoning"):
        assert key in result
    assert result["row_count"] == len(ROWS)
    assert result["renderer"] == "vega-lite"


@pytest.mark.asyncio
async def test_create_runs_the_full_pipeline(stub_llm):
    """A chart nobody asked for is exactly the one that should be planned and
    gated — nobody is watching for a wrong answer."""
    result = await create_chart(question="Which region leads?", rows=ROWS, llm=stub_llm)
    assert result["chart_type"] == "bar"
    assert result["chart_schema"]["mark"] == "bar"
    assert result["plan"] is not None
    assert result["verdict"] is not None
    assert "plan" in stub_llm.calls and "generate" in stub_llm.calls


@pytest.mark.asyncio
async def test_create_refuses_without_a_question(stub_llm):
    with pytest.raises(ChartSurfaceError, match="question is required"):
        await create_chart(question="  ", rows=ROWS, llm=stub_llm)


@pytest.mark.asyncio
async def test_annotate_returns_the_changed_chart_and_what_it_did(stub_llm):
    result, _ = await annotate_chart(
        instruction="make it dark and sort descending",
        chart_schema=dict(CHART), rows=ROWS, llm=stub_llm,
    )
    assert result["actions"] == ["theme", "edit"]
    assert result["chart_schema"]["encoding"]["x"]["sort"]["order"] == "descending"
    assert result["declined"] == []


@pytest.mark.asyncio
async def test_annotate_reports_a_decline_without_touching_the_chart(stub_llm):
    result, _ = await annotate_chart(
        instruction="break it down by month",
        chart_schema=dict(CHART), rows=ROWS, llm=stub_llm,
    )
    assert result["actions"] == ["decline"]
    assert result["declined"]
    assert result["chart_schema"]["encoding"] == CHART["encoding"]


@pytest.mark.asyncio
async def test_a_narration_returns_prose_rather_than_a_redrawn_chart(stub_llm):
    result, _ = await annotate_chart(
        instruction="what does this show?", chart_schema=dict(CHART), rows=ROWS,
        llm=stub_llm,
    )
    assert result["narration"]["summary"]
    assert result["chart_schema"]["encoding"] == CHART["encoding"]


# ---------------------------------------------------------------------------
# the composed question
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("rows", "fragment"),
    [
        (ROWS, "revenue compare across region"),
        ([{"month": f"2026-0{i}-01", "revenue": float(i)} for i in range(1, 6)],
         "changed over month"),
        ([{"total": 5.0}], "What is the total?"),
        ([], "Summarise this result set"),
    ],
)
def test_the_question_composer_reads_the_data(rows, fragment):
    assert fragment in question_for(rows)


def test_a_hint_is_prefixed_not_replaced():
    assert question_for(ROWS, hint="Q3 pipeline").startswith("Q3 pipeline:")


# ---------------------------------------------------------------------------
# over HTTP
# ---------------------------------------------------------------------------

def test_capabilities_over_http():
    client = TestClient(create_app())
    body = client.get("/v1/chart/capabilities").json()
    assert body["renderer"] == "vega-lite"
    assert "heatmap" in body["chart_types"]


def test_the_routes_say_plainly_when_there_is_no_model():
    client = TestClient(create_app())
    response = client.post("/v1/chart/create", json={"question": "q", "rows": ROWS})
    assert response.status_code == 503
    assert "no model configured" in response.json()["detail"]


def test_annotate_over_http(stub_llm):
    client = TestClient(create_app(llm=stub_llm))
    response = client.post("/v1/chart/annotate", json={
        "instruction": "sort descending", "chart_schema": CHART, "rows": ROWS,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["actions"] == ["edit"]
    assert body["chart_schema"]["encoding"]["x"]["sort"]


def test_an_unknown_session_is_a_404(stub_llm):
    client = TestClient(create_app(llm=stub_llm))
    response = client.post("/v1/chart/annotate", json={
        "instruction": "sort descending", "chart_schema": CHART,
        "rows": ROWS, "session_id": "sess_nope",
    })
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# the seven-type trap
# ---------------------------------------------------------------------------

KPI_ROWS = [{"open_findings": 47}]
HEATMAP_ROWS = [
    {"unit": "Retail", "severity": "High", "findings": 12},
    {"unit": "Retail", "severity": "Low", "findings": 22},
    {"unit": "Digital", "severity": "High", "findings": 31},
    {"unit": "Digital", "severity": "Low", "findings": 16},
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows", "question", "expected"),
    [
        (KPI_ROWS, "How many findings are open?", "kpi"),
        (HEATMAP_ROWS, "How do findings break down by unit and severity?",
         ("heatmap", "grouped_bar", "stacked_bar")),
    ],
)
async def test_types_outside_the_old_seven_round_trip(rows, question, expected):
    """The endpoint this replaces declared chart_type as a Literal of seven, and
    FastAPI validates responses — so a KPI or a heatmap was a 500, even though
    the UI already has KPI rendering. These must come back intact."""
    from harness.offline import offline_runner

    result = await create_chart(question=question, rows=rows, llm=offline_runner(rows))
    expected = (expected,) if isinstance(expected, str) else expected
    assert result["chart_type"] in expected
    assert result["chart_schema"]
    assert result["chart_type"] in capabilities()["chart_types"]


@pytest.mark.asyncio
async def test_a_kpi_keeps_the_metadata_the_ui_branches_on():
    """A host renders a KPI by reading chart_type for "kpi" and the kpi_metadata
    block. Losing either turns a KPI into a blank card."""
    from harness.offline import offline_runner

    result = await create_chart(
        question="How many findings are open?", rows=KPI_ROWS,
        llm=offline_runner(KPI_ROWS),
    )
    assert "kpi" in result["chart_type"]
    assert isinstance(result["chart_schema"].get("kpi_metadata"), dict)
