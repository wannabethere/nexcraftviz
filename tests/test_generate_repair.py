"""Broken spec JSON: repaired when unambiguous, regenerated once when not.

The generator writes its Vega-Lite document as a JSON string inside a JSON
answer. Strict mode guarantees the answer, not the document inside it, and a
live model dropped one brace in a nested KPI spec — the whole chart failed.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from nexcraftviz.pipeline import ChartRequest, run_pipeline
from nexcraftviz.skills import REGISTRY
from tests.test_jsonfix import LIVE_KPI

KPI_ROWS = [{"open_findings": 47}]
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


def _spec(**mark: Any) -> str:
    return json.dumps({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": ROWS},
        "mark": {"type": "bar", **mark},
        "encoding": {
            "x": {"field": "region", "type": "nominal"},
            "y": {"field": "revenue", "type": "quantitative", "aggregate": "sum"},
        },
    })


UNREPAIRABLE = '{"mark": "bar", "encoding": {"x": {"field": "never closed'


def _answer(spec_json: str) -> dict[str, Any]:
    return {"spec_json": spec_json, "chart_type": "bar", "reasoning": ""}


def _stub(*answers: str):
    """Plans normally; answers each generation from `answers`, in order."""
    calls: list[str] = []

    async def run(system: str, user: str, schema: dict[str, Any]):
        props = set((schema or {}).get("properties") or {})
        if "encodings" in props:
            return PLAN, {"model": "stub"}
        if "spec_json" in props:
            calls.append(user)
            return _answer(answers[min(len(calls), len(answers)) - 1]), {"model": "stub"}
        return {"answers_question": True, "score": 1.0, "complaint": "",
                "suggestion": ""}, {"model": "stub"}

    run.calls = calls  # type: ignore[attr-defined]
    return run


# ---------------------------------------------------------------------------
# inside the skill
# ---------------------------------------------------------------------------

def test_the_live_broken_kpi_is_repaired_and_the_repair_is_recorded():
    skill = REGISTRY["viz.generate"]
    output = skill.parse(_answer(LIVE_KPI))
    result = skill.apply(skill.coerce_input({"question": "total open findings",
                                             "rows": KPI_ROWS}), output)
    assert result.value is not None and not result.failed, result.failed
    assert result.meta["json_repaired"]
    assert any("repaired the model's JSON" in change for change in result.changes)
    assert result.meta["repaired"][0] == result.meta["json_repaired"][0]


def test_unrepairable_json_fails_with_the_parsers_own_words():
    skill = REGISTRY["viz.generate"]
    output = skill.parse(_answer(UNREPAIRABLE))
    result = skill.apply(skill.coerce_input({"question": "q", "rows": ROWS}), output)
    assert result.value is None
    assert "not valid JSON" in result.failed[0][1]
    assert "Unterminated string" in result.failed[0][1]


# ---------------------------------------------------------------------------
# in the pipeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_unusable_generation_gets_the_one_regeneration():
    stub = _stub(UNREPAIRABLE, _spec())
    run = await run_pipeline(ChartRequest(question="revenue by region", rows=ROWS), llm=stub)
    assert run.status == "ok", run.reason
    assert len(stub.calls) == 2
    assert run.regenerations == 1
    # The second attempt was told what broke.
    assert "previous_attempt_rejected_because" in stub.calls[1]
    assert "not valid JSON" in stub.calls[1]


@pytest.mark.asyncio
async def test_a_generation_that_stays_broken_fails_after_one_retry_not_a_loop():
    stub = _stub(UNREPAIRABLE, UNREPAIRABLE)
    run = await run_pipeline(ChartRequest(question="revenue by region", rows=ROWS), llm=stub)
    assert run.status == "failed"
    assert len(stub.calls) == 2
    assert "not valid JSON" in run.reason


@pytest.mark.asyncio
async def test_a_deliberate_decline_is_not_retried():
    """No chart suits this data is a decision. Asking again repeats it."""
    stub = _stub("")
    run = await run_pipeline(ChartRequest(question="revenue by region", rows=ROWS), llm=stub)
    assert run.spec is None
    assert len(stub.calls) == 1
    assert run.regenerations == 0


@pytest.mark.asyncio
async def test_the_budget_is_shared_with_evaluation():
    """A retry spent on broken JSON is THE retry. A gate failure on the second
    attempt is delivered with its verdict, not regenerated a third time."""
    stub = _stub(UNREPAIRABLE, _spec(color="#FF0000"))
    run = await run_pipeline(ChartRequest(question="revenue by region", rows=ROWS), llm=stub)
    assert len(stub.calls) == 2
    assert run.regenerations == 1
    assert run.stages.evaluate is not None and not run.stages.evaluate.passed
