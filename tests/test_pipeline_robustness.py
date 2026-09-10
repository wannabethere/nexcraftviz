"""The pipeline returns a failed run — never a 500 — when the model misbehaves.

Found live, not imagined: viz.plan came back with a field placed where the
schema has none, `SkillError` escaped the pipeline, and /v1/chart/create would
have answered with a stack trace. These pin the boundary down.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nexcraftviz.app.api import create_app
from nexcraftviz.pipeline import ChartRequest, run_pipeline

ROWS: list[dict[str, Any]] = [
    {"region": "North", "revenue": 152.0},
    {"region": "West", "revenue": 128.0},
    {"region": "East", "revenue": 96.0},
]

PLAN = {
    "status": "ok", "confidence": 0.9, "chart_type": "bar", "alternatives": [],
    "rationale": "", "transforms": [], "styling": {}, "metadata": {}, "follow_ups": [],
    "encodings": [
        {"channel": "x", "field": "region", "aggregate": "", "sort": "", "why": ""},
        {"channel": "y", "field": "revenue", "aggregate": "sum", "sort": "", "why": ""},
    ],
}

SPEC = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "data": {"values": ROWS},
    "mark": "bar",
    "encoding": {
        "x": {"field": "region", "type": "nominal"},
        "y": {"field": "revenue", "type": "quantitative", "aggregate": "sum"},
    },
}


def _routing_stub(*, critic_raises: bool = False):
    """Answers each skill by its schema; optionally a critic that times out."""

    async def run(system: str, user: str, schema: dict[str, Any]):
        props = set((schema or {}).get("properties") or {})
        if "encodings" in props:
            return PLAN, {"model": "stub"}
        if "spec_json" in props:
            return {"spec_json": json.dumps(SPEC), "chart_type": "bar",
                    "reasoning": ""}, {"model": "stub"}
        if "answers_question" in props:
            if critic_raises:
                raise TimeoutError("critic model timed out")
            return {"answers_question": True, "score": 0.9, "complaint": "",
                    "suggestion": ""}, {"model": "stub"}
        return {}, {"model": "stub"}

    return run


async def _malformed(system: str, user: str, schema: dict[str, Any]):
    # The shape the live model actually produced.
    return {"status": "ok", "chart_type": "bar", "narrative_angle": "misplaced",
            "encodings": [{"channel": "x", "field": "region"}]}, {"model": "stub"}


@pytest.mark.asyncio
async def test_an_unreadable_plan_is_a_failed_run_not_an_exception():
    run = await run_pipeline(ChartRequest(question="revenue by region", rows=ROWS),
                             llm=_malformed)
    assert run.status == "failed"
    assert "narrative_angle" in run.reason
    assert run.spec is None


@pytest.mark.asyncio
async def test_a_provider_outage_is_a_failed_run_with_the_reason():
    async def down(system: str, user: str, schema: dict[str, Any]):
        raise ConnectionError("provider unreachable")

    run = await run_pipeline(ChartRequest(question="revenue by region", rows=ROWS), llm=down)
    assert run.status == "failed"
    assert "the model call failed" in run.reason
    assert "provider unreachable" in run.reason


@pytest.mark.asyncio
async def test_a_bug_in_our_own_code_still_fails_loudly(monkeypatch):
    """Only the runner is wrapped. Turning every exception into a failed run
    would hide genuine bugs behind something that reads like a flaky provider."""
    import nexcraftviz.pipeline as module

    async def broken(*args: Any, **kwargs: Any):
        raise AttributeError("a genuine bug")

    monkeypatch.setattr(module, "_plan", broken)
    with pytest.raises(AttributeError, match="a genuine bug"):
        await run_pipeline(ChartRequest(question="revenue by region", rows=ROWS),
                           llm=_routing_stub())


@pytest.mark.asyncio
async def test_a_critic_that_cannot_answer_keeps_the_chart():
    """The chart already passed every deterministic gate; a missing opinion is
    not a failed chart."""
    run = await run_pipeline(ChartRequest(question="revenue by region", rows=ROWS),
                             llm=_routing_stub(critic_raises=True), evaluate="full")
    assert run.spec is not None
    assert run.status == "ok"
    critic = next(g for g in run.stages.evaluate.gates if g.gate == "critic")
    assert critic.passed
    assert "critic unavailable" in critic.detail and "timed out" in critic.detail


@pytest.mark.asyncio
async def test_a_working_critic_is_unaffected():
    run = await run_pipeline(ChartRequest(question="revenue by region", rows=ROWS),
                             llm=_routing_stub(), evaluate="full")
    assert run.status == "ok"
    assert run.stages.evaluate.critique is not None
    assert not any(g.gate == "critic" for g in run.stages.evaluate.gates)


def test_the_http_surface_answers_422_not_500():
    """What a caller actually sees: a reason it can read."""
    client = TestClient(create_app(llm=_malformed))
    response = client.post("/v1/chart/create",
                           json={"question": "revenue by region", "rows": ROWS})
    assert response.status_code == 422
    assert "narrative_angle" in response.json()["detail"]
