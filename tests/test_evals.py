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


def test_every_model_backed_skill_has_eval_cases() -> None:
    """Derived from the registry, not listed. The hard-coded version kept
    passing after four new model-backed skills landed with no cases at all — a
    coverage guard that stopped guarding on exactly the day it was needed."""
    from nexcraftviz.skills import REGISTRY

    model_backed = {name for name, skill in REGISTRY.items() if skill.spec.uses_llm}
    covered = {c.skill for c in ALL_CASES}
    assert model_backed <= covered, f"no eval cases for: {sorted(model_backed - covered)}"


def test_expectation_keys_are_ones_the_scorer_reads() -> None:
    """A mistyped `expect` key checks nothing and passes forever."""
    from nexcraftviz.evals.runner import EXPECT_KEYS

    for case in ALL_CASES:
        unknown = set(case.expect) - EXPECT_KEYS.get(case.skill, frozenset())
        assert not unknown, f"{case.id}: {sorted(unknown)}"


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



# ---------------------------------------------------------------------------
# the scorer discriminates — plans, routing, layouts, verdicts
# ---------------------------------------------------------------------------

def _case(case_id: str):
    return next(c for c in cases_for() if c.id == case_id)


def _plan(chart_type: str, encodings: list[tuple[str, str, str]], *, status: str = "ok"):
    return {
        "status": status,
        "reason_if_not_ok": "" if status == "ok" else "the data has no such column",
        "confidence": 0.8, "chart_type": chart_type, "alternatives": [], "rationale": "",
        "encodings": [
            {"channel": c, "field": f, "aggregate": a, "sort": "", "why": ""}
            for c, f, a in encodings
        ],
        "transforms": [], "styling": {}, "metadata": {}, "follow_ups": [],
    }


def _decision(*steps: dict, status: str = "ok"):
    return {"status": status, "reason_if_not_ok": "", "declined": [], "steps": list(steps)}


def _step(action: str, instruction: str, parts: list[str] | None = None):
    return {"action": action, "instruction": instruction, "why": "", "parts": parts or []}


def _design(tiles: list[tuple[str, str]], *, title: str = "Revenue overview"):
    return {
        "status": "ok", "reason_if_not_ok": "", "title": title, "description": "",
        "layout": "grid",
        "tiles": [{"id": i, "title": i, "span": s, "note": ""} for i, s in tiles],
        "groups": [], "concerns": [],
    }


def _verdict(answers: bool, complaint: str = ""):
    return {"answers_question": answers, "score": 0.5, "complaint": complaint, "suggestion": ""}


async def test_a_plan_with_the_right_chart_passes() -> None:
    result = await run_case(
        _case("plan-ranking"),
        _stub(_plan("bar", [("x", "region", ""), ("y", "revenue", "sum")])),
    )
    assert result.ok, result.line()


async def test_a_plan_with_the_wrong_chart_fails() -> None:
    result = await run_case(
        _case("plan-ranking"),
        _stub(_plan("pie", [("theta", "revenue", "sum"), ("color", "region", "")])),
    )
    assert not result.ok
    assert "chart type 'pie'" in result.line()


async def test_a_summed_rate_fails_the_rate_case() -> None:
    result = await run_case(
        _case("plan-rate-not-summed"),
        _stub(_plan("bar", [("x", "team", ""), ("y", "completion_pct", "sum")])),
    )
    assert not result.ok
    assert "rate" in result.line()


async def test_planning_a_substitute_fails_the_refusal_case() -> None:
    """Revenue by region is a real chart of real columns. It is still the wrong
    answer to "by cost centre", which is the point of the case."""
    result = await run_case(
        _case("plan-missing-column"),
        _stub(_plan("bar", [("x", "region", ""), ("y", "revenue", "sum")])),
    )
    assert not result.ok


async def test_declining_passes_the_refusal_case() -> None:
    result = await run_case(
        _case("plan-missing-column"), _stub(_plan("", [], status="insufficient_data"))
    )
    assert result.ok, result.line()


async def test_both_halves_in_order_pass() -> None:
    result = await run_case(
        _case("manage-compound"),
        _stub(_decision(_step("theme", "make it dark"), _step("edit", "sort descending"))),
    )
    assert result.ok, result.line()


async def test_dropping_half_the_instruction_fails() -> None:
    result = await run_case(
        _case("manage-compound"), _stub(_decision(_step("theme", "make it dark")))
    )
    assert not result.ok


async def test_an_empty_decision_fails_even_though_repair_would_fill_it() -> None:
    """The rules would restore both steps in `apply`. Graded on the repaired
    decision, a model that said nothing at all would pass."""
    result = await run_case(_case("manage-compound"), _stub(_decision()))
    assert not result.ok
    assert "actions []" in result.line()


async def test_narrating_first_fails() -> None:
    result = await run_case(
        _case("manage-narrate-last"),
        _stub(_decision(_step("narrate", "what does this show"),
                        _step("edit", "sort it descending"))),
    )
    assert not result.ok


async def test_a_widget_without_parts_fails_even_though_repair_would_split_it() -> None:
    result = await run_case(
        _case("manage-widget"),
        _stub(_decision(_step("widget", "build me a dashboard of revenue and orders"))),
    )
    assert not result.ok
    assert "widget parts" in result.line()


async def test_a_widget_with_its_parts_passes() -> None:
    result = await run_case(
        _case("manage-widget"),
        _stub(_decision(_step("widget", "build me a dashboard",
                              ["revenue by region", "orders by region"]))),
    )
    assert result.ok, result.line()


async def test_a_complete_design_in_reading_order_passes() -> None:
    result = await run_case(
        _case("compose-reading-order"),
        _stub(_design([("tile_1", "third"), ("tile_2", "two-thirds"), ("tile_3", "full")])),
    )
    assert result.ok, result.line()


async def test_a_dropped_chart_fails_even_though_repair_would_append_it() -> None:
    result = await run_case(
        _case("compose-every-chart-once"),
        _stub(_design([("tile_1", "half"), ("tile_2", "half")])),
    )
    assert not result.ok
    assert "dropped chart(s): tile_3" in result.line()


async def test_a_squeezed_table_fails() -> None:
    result = await run_case(
        _case("compose-reading-order"),
        _stub(_design([("tile_1", "third"), ("tile_2", "third"), ("tile_3", "third")])),
    )
    assert not result.ok
    assert "tile_3 spans 'third'" in result.line()


async def test_a_generic_title_fails() -> None:
    result = await run_case(
        _case("compose-every-chart-once"),
        _stub(_design([("tile_1", "third"), ("tile_2", "third"), ("tile_3", "third")],
                      title="Dashboard")),
    )
    assert not result.ok


async def test_a_critic_that_accepts_the_wrong_chart_fails() -> None:
    result = await run_case(_case("critique-wrong-question"), _stub(_verdict(True)))
    assert not result.ok


async def test_a_rejection_without_a_complaint_fails() -> None:
    result = await run_case(_case("critique-wrong-question"), _stub(_verdict(False)))
    assert not result.ok
    assert "without a complaint" in result.line()


async def test_a_specific_rejection_passes() -> None:
    result = await run_case(
        _case("critique-wrong-question"),
        _stub(_verdict(False, "the chart shows revenue by region; the question is about time")),
    )
    assert result.ok, result.line()


async def test_a_critic_that_rejects_the_right_chart_fails() -> None:
    result = await run_case(
        _case("critique-right-chart"), _stub(_verdict(False, "could be prettier"))
    )
    assert not result.ok



# ---------------------------------------------------------------------------
# a silent fallback is reported, not hidden
# ---------------------------------------------------------------------------

def _bound(payload: dict, binding: str):
    async def run_stub(system: str, user: str, schema: dict):
        return payload, {"model": "stub", "structured_output": binding}
    return run_stub


async def test_a_non_strict_binding_is_reported_not_hidden() -> None:
    """How this was found: viz.plan ran non-strict for an entire live run and
    nothing said so. The one failure it caused looked like a flaky model."""
    from nexcraftviz.evals.runner import Report

    strict = await run_case(
        _case("plan-ranking"),
        _bound(_plan("bar", [("x", "region", ""), ("y", "revenue", "sum")]),
               "json_schema_strict"),
    )
    loose = await run_case(
        _case("plan-single-value"),
        _bound(_plan("kpi", [("text", "total_revenue", "")]), "json_schema"),
    )
    report = Report(results=[strict, loose], model="stub")
    assert report.bindings()["viz.plan"] == ["json_schema", "json_schema_strict"]
    assert report.non_strict() == ["viz.plan"]
    assert "NOT strict" in report.render()


async def test_an_all_strict_run_carries_no_warning() -> None:
    from nexcraftviz.evals.runner import Report

    result = await run_case(
        _case("plan-ranking"),
        _bound(_plan("bar", [("x", "region", ""), ("y", "revenue", "sum")]),
               "json_schema_strict"),
    )
    rendered = Report(results=[result]).render()
    assert "NOT strict" not in rendered and "NOT STRICT" not in rendered


def test_a_saved_eval_carries_what_the_html_report_reads(tmp_path):
    """A live pass printed 33/33 and wrote nothing, so the report could not
    show it without paying for the run again."""
    import json as _json

    from harness.html_report import build_report
    from nexcraftviz.evals.cases import Case
    from nexcraftviz.evals.runner import CaseResult, Report, save_report

    case = Case(id="plan-demo", skill="viz.plan", instruction="which region leads?",
                inputs={"question": "which region leads?"}, checks="a ranking is a bar")
    result = CaseResult(case=case, ok=True, decided_right=True,
                        binding="json_schema_strict", answer={"status": "ok"},
                        meta={"tokens_in": 10, "tokens_out": 2, "model": "m"})
    path = save_report(Report(results=[result], model="gpt-test"), tmp_path)

    saved = _json.loads(path.read_text())
    assert path.name.startswith("eval-") and saved["model"] == "gpt-test"
    row = saved["results"][0]
    assert row["tokens"] == {"tokens_in": 10, "tokens_out": 2}
    assert {"id", "skill", "checks", "instruction", "ok", "binding", "answer",
            "artifact", "notes", "missing"} <= set(row)
    html = build_report(eval_path=path, run_path=None)
    assert "plan-demo" in html and "a ranking is a bar" in html
