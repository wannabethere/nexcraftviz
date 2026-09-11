"""The dashboard skill: a skill file checked before it runs, and runs that pause
for a person and for the host. All offline — a stub model and a stub host."""
from __future__ import annotations

import copy
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nexcraftviz.app.api import create_app
from nexcraftviz.compose.widget import Widget, tile, widget
from nexcraftviz.spec.model import Spec
from nexcraftviz.table import build_table
from nexcraftviz.workflows import (
    Executor,
    SkillFileError,
    WorkflowError,
    builtin_skills,
    parse_skill,
)
from nexcraftviz.workflows.actions import grid_layout
from tests.test_generate_repair import PLAN, ROWS, _answer, _spec

BAR: dict[str, Any] = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "title": "Revenue by region",
    "data": {"values": ROWS},
    "mark": "bar",
    "encoding": {"x": {"field": "region", "type": "nominal"},
                 "y": {"field": "revenue", "type": "quantitative"}},
}
KPI: dict[str, Any] = {"kpi_metadata": {"chart_type": "metric_kpi", "chart_subtype": "counter",
                                        "label": "Total revenue", "value": 376}}


def _widget_doc(title: str = "Revenue", *payloads: dict[str, Any]) -> dict[str, Any]:
    tiles = [tile(Spec(copy.deepcopy(p)), id=f"t{i}", title=f"tile {i}")
             for i, p in enumerate(payloads or (BAR,))]
    return widget(*tiles, title=title).to_dict()


def _stub():
    async def run(system: str, user: str, schema: dict[str, Any]):
        props = set((schema or {}).get("properties") or {})
        if "questions" in props:
            return {"questions": [
                {"text": "Total revenue", "visual": "kpi", "why": "the headline"},
                {"text": "Revenue by region", "visual": "bar", "why": "where it comes from"},
                {"text": "revenue by  region", "visual": "bar", "why": "the same again"},
            ]}, {"model": "stub"}
        if "encodings" in props:
            return PLAN, {"model": "stub"}
        if "spec_json" in props:
            return _answer(_spec()), {"model": "stub"}
        if {"headline", "summary"} <= props:
            return {"summary": "North brings in the most.", "headline": "North leads",
                    "points": []}, {"model": "stub"}
        return {"answers_question": True, "score": 1.0, "complaint": "",
                "suggestion": ""}, {"model": "stub"}
    return run


# ---------------------------------------------------------------------------
# the skill file
# ---------------------------------------------------------------------------

def test_the_shipped_skill_loads_with_its_three_intents():
    skill = builtin_skills()["dashboard"]
    assert [i.id for i in skill.intents] == ["from_question", "from_widgets", "enrich_widget"]


def _skill(steps: str, asks: str = '{question: "What?"}') -> str:
    return (
        "skill: t\n"
        "intents:\n"
        f"  - {{id: go, label: Go, workflow: main, asks: {asks}}}\n"
        "workflows:\n"
        "  main:\n"
        "    steps:\n"
        f"{steps}\n"
    )


@pytest.mark.parametrize(("steps", "problem"), [
    ("      - {id: p, uses: host.publish, with: {x: 1}}", "must follow an approve pause"),
    ("      - {id: a, uses: chart.teleport}", "unknown action 'chart.teleport'"),
    ("      - {id: a, uses: widget.table, with: {rows: $steps.b}}\n"
     "      - {id: b, uses: widget.table}", "$steps.b is not an earlier step"),
    ("      - {id: a, uses: widget.table, with: {rows: $intent.rows}}",
     "$intent.rows is not an ask of intent 'go'"),
    ("      - {id: a, uses: widget.table, with: {rows: $item}}", "$item is only meaningful"),
    ("      - {id: a, uses: widget.table, pause: ask}", "exactly one of `uses` or `pause`"),
    ("      - {id: a, uses: widget.table, when: always}", "`when` must be a $ reference"),
])
def test_the_loader_refuses_a_skill_that_is_wrong(steps, problem):
    with pytest.raises(SkillFileError) as caught:
        parse_skill(_skill(steps))
    assert problem in str(caught.value)


def test_a_called_workflow_may_not_pause():
    text = (
        "skill: t\n"
        "intents: [{id: go, label: Go, workflow: main}]\n"
        "workflows:\n"
        "  helper:\n"
        "    steps: [{id: wait, pause: ask, prompt: \"Why?\"}]\n"
        "  main:\n"
        "    steps: [{id: call, uses: workflow.helper}]\n"
    )
    with pytest.raises(SkillFileError, match="runs straight through"):
        parse_skill(text)


def test_a_correct_skill_parses():
    step = "      - {id: t, uses: widget.table, with: {title: $intent.question}}"
    skill = parse_skill(_skill(step))
    assert skill.outline()["intents"][0]["steps"] == [{"id": "t", "kind": "action",
                                                       "uses": "widget.table"}]


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_from_question_runs_through_every_pause_to_publish():
    executor = Executor(builtin_skills(), llm=_stub())
    run = await executor.start("dashboard", "from_question",
                               answers={"question": "How is revenue doing?"})
    assert (run.status, run.pending["kind"]) == ("paused", "select"), run.trace
    options = run.pending["options"]
    assert [o["text"] for o in options] == ["Total revenue", "Revenue by region"]  # deduped

    run = await executor.resume(run, answer=[o["id"] for o in options])
    assert (run.status, run.pending["action"]) == ("needs_host", "host.query")
    requests = run.pending["requests"]
    assert [r["question"] for r in requests] == ["Total revenue", "Revenue by region"]

    run = await executor.resume(run, host_result={r["id"]: {"rows": ROWS} for r in requests})
    assert (run.status, run.pending["kind"]) == ("paused", "approve"), run.trace
    dashboard = run.pending["show"]
    assert dashboard["kind"] == "nexcraftviz.dashboard" and len(dashboard["layout"]) == 2
    assert all(cell["x"] + cell["w"] <= 12 for cell in dashboard["layout"])
    widgets = run.artifacts["widgets"]
    assert len(widgets) == 2
    assert all(w["narration"]["headline"] == "North leads" for w in widgets)

    run = await executor.resume(run, answer=True)
    assert (run.status, run.pending["action"]) == ("needs_host", "host.publish")
    assert run.pending["requests"][0]["dashboard"]["kind"] == "nexcraftviz.dashboard"

    run = await executor.resume(run, host_result={"dashboard_id": "d-1"})
    assert run.status == "done" and run.artifacts["published"] == {"dashboard_id": "d-1"}


@pytest.mark.asyncio
async def test_a_no_at_the_approval_publishes_nothing():
    executor = Executor(builtin_skills())
    run = await executor.start("dashboard", "from_widgets",
                               answers={"title": "Revenue", "widgets": [_widget_doc()]})
    assert run.pending["kind"] == "approve"
    run = await executor.resume(run, answer=False)
    assert run.status == "cancelled" and run.pending is None
    assert run.artifacts["published"] is None


@pytest.mark.asyncio
async def test_arranging_widgets_needs_no_model_and_sizes_them_by_content():
    executor = Executor(builtin_skills())
    run = await executor.start("dashboard", "from_widgets", answers={
        "widgets": [_widget_doc("Revenue"), _widget_doc("Headline", KPI)]})
    run = await executor.resume(run, answer="yes")
    assert run.status == "needs_host"
    layout = run.pending["requests"][0]["dashboard"]["layout"]
    assert [(cell["w"], cell["h"]) for cell in layout] == [(6, 4), (3, 2)]


@pytest.mark.asyncio
async def test_enrich_widget_adds_a_table_from_the_widgets_own_rows():
    executor = Executor(builtin_skills())
    run = await executor.start("dashboard", "enrich_widget",
                               answers={"widget": _widget_doc(), "add_table": True})
    assert run.status == "done", run.trace
    [enriched] = run.artifacts["widgets"]
    assert [c["field"] for c in enriched["table"]["columns"]] == ["region", "revenue"]
    assert "nxv-table" in Widget.from_dict(enriched).to_html()


@pytest.mark.asyncio
async def test_a_chart_with_no_model_fails_the_run_with_the_reason():
    executor = Executor(builtin_skills())
    run = await executor.start("dashboard", "from_question",
                               answers={"question": "Revenue by region"})
    run = await executor.resume(run, answer=["q1"])
    run = await executor.resume(run, host_result={run.pending["requests"][0]["id"]: {"rows": ROWS}})
    assert run.status == "failed" and "needs a model" in run.error


@pytest.mark.asyncio
async def test_bad_answers_are_refused_before_anything_runs():
    executor = Executor(builtin_skills())
    with pytest.raises(WorkflowError, match="needs: question"):
        await executor.start("dashboard", "from_question", answers={})
    with pytest.raises(WorkflowError, match="no intent"):
        await executor.start("dashboard", "nope")
    run = await executor.start("dashboard", "from_widgets", answers={"widgets": [_widget_doc()]})
    with pytest.raises(WorkflowError, match="yes or no"):
        await executor.resume(run, answer=None)


# ---------------------------------------------------------------------------
# widgets, layout, and the API
# ---------------------------------------------------------------------------

def test_a_widget_carries_its_narration_and_table():
    built = widget(tile(Spec(copy.deepcopy(BAR)), id="t1"), title="Revenue")
    built.narration = {"headline": "North leads", "summary": "North brings in the most.",
                       "points": [{"text": "North 152"}]}
    built.table = build_table(ROWS)
    doc = built.to_dict()
    assert doc["version"] == 2 and doc["narration"]["headline"] == "North leads"
    html = Widget.from_dict(doc).to_html()
    assert "North leads" in html and "North 152" in html and "nxv-table" in html


def test_the_grid_packs_left_to_right_and_wraps_at_twelve():
    docs = [{"id": f"w{i}", "kind": "nexcraftviz.widget",
             "nodes": [{"kind": "tile", "family": "vega-lite"}]} for i in range(3)]
    assert [(c["x"], c["y"]) for c in grid_layout(docs)] == [(0, 0), (6, 0), (0, 4)]


def test_the_workflow_endpoints_run_a_skill_end_to_end(monkeypatch):
    monkeypatch.delenv("NEXCRAFTVIZ_API_TOKEN", raising=False)
    client = TestClient(create_app())

    [skill] = client.get("/v1/workflows").json()["skills"]
    assert skill["skill"] == "dashboard"
    assert [i["id"] for i in skill["intents"]] == ["from_question", "from_widgets", "enrich_widget"]

    run = client.post("/v1/workflows/dashboard/runs", json={
        "intent": "from_widgets", "answers": {"widgets": [_widget_doc()]}}).json()
    assert run["status"] == "paused" and "answers" not in run
    resume = f"/v1/workflows/runs/{run['run_id']}/resume"
    assert client.post(resume, json={"answer": True}).json()["status"] == "needs_host"
    done = client.post(resume, json={"host_result": {"dashboard_id": "d-9"}}).json()
    assert done["status"] == "done" and done["artifacts"]["published"] == {"dashboard_id": "d-9"}
    assert client.get(f"/v1/workflows/runs/{run['run_id']}").json()["status"] == "done"

    assert client.post(resume, json={"answer": True}).status_code == 409
    assert client.post("/v1/workflows/dashboard/runs", json={"intent": "nope"}).status_code == 422
    assert client.post("/v1/workflows/nope/runs", json={"intent": "x"}).status_code == 404
