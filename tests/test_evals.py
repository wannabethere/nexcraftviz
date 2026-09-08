"""The eval harness itself — offline.

These do not call a model. They check that the fixtures are coherent and that
the scorer actually discriminates, so a green live run means something. A
scorer that passes everything is worse than no scorer at all.
"""
from __future__ import annotations

import os

import pytest

from nexcraftviz.evals import ALL_CASES, cases_for, run, run_case
from nexcraftviz.skills import names

LIVE = os.getenv("NEXCRAFTVIZ_LIVE_EVAL", "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# fixtures are coherent
# ---------------------------------------------------------------------------

def test_every_case_targets_a_real_skill() -> None:
    for case in ALL_CASES:
        assert case.skill in names(), case.id


def test_case_ids_are_unique() -> None:
    ids = [c.id for c in ALL_CASES]
    assert len(ids) == len(set(ids))


def test_every_case_says_what_it_checks() -> None:
    """An eval case with no stated purpose cannot be triaged when it fails."""
    for case in ALL_CASES:
        assert case.checks, case.id


def test_expected_operations_are_real_operations() -> None:
    """A typo in a fixture would make a case unpassable, and look like a
    prompt regression."""
    from nexcraftviz.compose.ops import WIDGET_OP_REGISTRY
    from nexcraftviz.spec.ops import OP_REGISTRY

    for case in ALL_CASES:
        registry = WIDGET_OP_REGISTRY if case.skill == "viz.place" else OP_REGISTRY
        for expectation in [*case.must, *case.must_not]:
            assert expectation.op in registry, f"{case.id}: {expectation.op}"


def test_every_case_can_be_prepared() -> None:
    for case in cases_for():
        assert case.inputs
        if case.skill == "viz.place":
            assert case.inputs["widget"] is not None


def test_the_skills_under_test_are_the_model_backed_ones() -> None:
    covered = {c.skill for c in ALL_CASES}
    assert covered == {"viz.edit", "viz.generate", "viz.place", "viz.narrate"}


# ---------------------------------------------------------------------------
# the scorer discriminates
# ---------------------------------------------------------------------------

def _stub(payload: dict):
    async def run_stub(system: str, user: str, schema: dict):
        return payload, {"model": "stub"}
    return run_stub


async def test_a_correct_answer_passes() -> None:
    case = next(c for c in cases_for("viz.edit") if c.id == "sort-descending")
    result = await run_case(case, _stub({
        "ops": [{"op": "sort_by", "channel": "x", "by": "revenue", "order": "descending"}],
        "reasoning": "North leads.",
    }))
    assert result.ok and result.decided_right


async def test_a_missing_operation_fails() -> None:
    case = next(c for c in cases_for("viz.edit") if c.id == "sort-descending")
    result = await run_case(case, _stub({"ops": [], "reasoning": "did nothing"}))
    assert not result.ok
    assert result.missing


async def test_the_wrong_direction_fails() -> None:
    """Scoring on arguments, not just op names."""
    case = next(c for c in cases_for("viz.edit") if c.id == "sort-descending")
    result = await run_case(case, _stub({
        "ops": [{"op": "sort_by", "channel": "x", "by": "revenue", "order": "ascending"}],
    }))
    assert not result.ok


async def test_substituting_a_column_fails_the_refusal_case() -> None:
    """The case that matters most: inventing a breakdown nobody asked for."""
    case = next(c for c in cases_for("viz.edit") if c.id == "missing-column")
    result = await run_case(case, _stub({
        "ops": [{"op": "set_color_field", "field": "status", "type": "nominal"}],
    }))
    assert not result.ok
    assert result.forbidden


async def test_refusing_passes_the_refusal_case() -> None:
    case = next(c for c in cases_for("viz.edit") if c.id == "missing-column")
    result = await run_case(case, _stub({"ops": [], "needs_data": "cost_centre"}))
    assert result.ok


async def test_quarter_labels_encoded_as_temporal_fail() -> None:
    """The defect already sitting in the shipped corpus."""
    import json

    case = next(c for c in cases_for("viz.generate") if c.id == "generate-quarter-labels")
    bad = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "mark": "line",
        "encoding": {
            "x": {"field": "quarter", "type": "temporal"},
            "y": {"field": "nps", "type": "quantitative"},
        },
    }
    result = await run_case(case, _stub({"spec_json": json.dumps(bad), "chart_type": "line"}))
    assert not result.ok
    assert "Invalid Date" in result.notes[0]


async def test_ordinal_quarter_labels_pass() -> None:
    import json

    case = next(c for c in cases_for("viz.generate") if c.id == "generate-quarter-labels")
    good = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "mark": "line",
        "encoding": {
            "x": {"field": "quarter", "type": "ordinal"},
            "y": {"field": "nps", "type": "quantitative"},
        },
    }
    result = await run_case(case, _stub({"spec_json": json.dumps(good), "chart_type": "line"}))
    assert result.ok, result.notes


async def test_narration_describing_mechanics_fails() -> None:
    case = next(c for c in cases_for("viz.narrate") if c.id == "narrate-bar")
    result = await run_case(case, _stub({
        "summary": "The x-axis shows regions and the legend shows revenue.",
        "headline": "A chart",
    }))
    assert not result.ok


async def test_a_row_that_overflows_the_grid_fails() -> None:
    case = next(c for c in cases_for("viz.place") if c.id == "place-widen")
    result = await run_case(case, _stub({
        "ops": [{"op": "set_span", "tile": "tile-funnel", "span": "full"},
                {"op": "set_span", "tile": "tile-conversion", "span": "half"}],
    }))
    assert not result.ok
    assert "wraps" in result.notes[0]


async def test_a_provider_error_is_a_case_failure_not_a_crash() -> None:
    async def broken(system, user, schema):
        raise RuntimeError("rate limited")

    case = cases_for("viz.edit")[0]
    result = await run_case(case, broken)
    assert not result.ok and "rate limited" in result.error


async def test_the_report_summarises_per_skill() -> None:
    report = await run(_stub({"ops": [], "reasoning": ""}), skill="viz.edit")
    assert report.total == len(cases_for("viz.edit"))
    assert "viz.edit" in report.by_skill()
    assert "total" in report.render()


# ---------------------------------------------------------------------------
# live — opt in only
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not LIVE or not os.getenv("OPENAI_API_KEY"),
    reason="set NEXCRAFTVIZ_LIVE_EVAL=1 and OPENAI_API_KEY to run against a real model",
)
async def test_live_prompts() -> None:
    from nexcraftviz.integrations.providers import openai_runner

    report = await run(openai_runner())
    print("\n" + report.render())
    assert report.passed == report.total, report.render()
