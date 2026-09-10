"""An ambiguous plan with a chart in it is drawn, with the caveat attached.

viz.plan's rule 6 asks the planner, for a question with two readings, to plan
the conservative one and name the other. The pipeline stopped on any status but
`ok`, so live, widget_entity_rows delivered no chart — and, the reason being
empty, no word of why.
"""
from __future__ import annotations

from typing import Any

import pytest

from nexcraftviz.pipeline import ChartRequest, run_pipeline
from tests.test_generate_repair import PLAN, ROWS, _answer, _spec


def _stub(plan: dict[str, Any]):
    calls: list[str] = []

    async def run(system: str, user: str, schema: dict[str, Any]):
        props = set((schema or {}).get("properties") or {})
        if "encodings" in props:
            return plan, {"model": "stub"}
        if "spec_json" in props:
            calls.append(user)
            return _answer(_spec()), {"model": "stub"}
        return {"answers_question": True, "score": 1.0, "complaint": "",
                "suggestion": ""}, {"model": "stub"}

    run.calls = calls  # type: ignore[attr-defined]
    return run


@pytest.mark.asyncio
async def test_an_ambiguous_plan_is_drawn_and_says_so():
    reason = "'longest open' could mean the single oldest control or the average per owner"
    stub = _stub({**PLAN, "status": "ambiguous", "reason_if_not_ok": reason})
    run = await run_pipeline(ChartRequest(question="who is slowest?", rows=ROWS), llm=stub)
    assert run.spec is not None
    assert run.status == "ambiguous" and run.reason == reason
    assert len(stub.calls) == 1
    # The plan reached the generator: the decision was not re-made from scratch.
    assert '"plan"' in stub.calls[0]


@pytest.mark.asyncio
async def test_an_ambiguous_plan_with_nothing_to_draw_still_stops():
    stub = _stub({**PLAN, "status": "ambiguous", "chart_type": "", "encodings": [],
                  "reason_if_not_ok": "which measure?"})
    run = await run_pipeline(ChartRequest(question="show it", rows=ROWS), llm=stub)
    assert run.spec is None and run.status == "ambiguous"
    assert stub.calls == []
