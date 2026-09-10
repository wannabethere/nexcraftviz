"""The harness: check, setup, run, report — all offline."""
from __future__ import annotations

import json

import pytest

from harness.check import check_environment
from harness.report import compare, report_run, summarise
from harness.run import Scenario, load_scenarios, run_scenarios, save_results
from harness.setup import setup_environment

# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

def test_check_reports_every_prerequisite():
    report = check_environment()
    names = {c.name for c in report.checks}
    assert names == {
        "package", "prompts", "themes", "corpus", "render", "fonts", "api_key",
        "agents", "retrieval", "auth",
    }


def test_check_names_a_missing_key_and_how_to_fix_it(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("nexcraftviz.env.load", lambda *a, **k: [])
    report = check_environment()
    key = next(c for c in report.checks if c.name == "api_key")
    assert key.state == "missing"
    assert "OPENAI_API_KEY" in key.fix
    # A missing key is configuration, not breakage: offline runs still work.
    assert report.can_run_offline
    assert not report.can_run_live


def test_check_never_raises_on_a_broken_component(monkeypatch):
    """A harness that crashes while telling you what is broken is the worst
    possible version of itself."""
    import harness.check as module

    monkeypatch.setattr(
        module, "_check_themes",
        lambda: module.Check("themes", "missing", "simulated failure"),
    )
    report = check_environment()
    assert not report.can_run_offline
    assert "themes" in report.text()


def test_the_deliberate_powerbi_deviation_is_a_warning_not_a_failure():
    """Matching PowerBI is the point, so its palette stays below AA forever."""
    themes = next(c for c in check_environment().checks if c.name == "themes")
    assert themes.state in ("ok", "degraded")
    if themes.state == "degraded":
        assert "powerbi" in themes.detail


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

def test_setup_writes_an_env_template(tmp_path):
    report = setup_environment(tmp_path)
    written = (tmp_path / ".env").read_text()
    assert "OPENAI_API_KEY=" in written
    assert "NEXCRAFTVIZ_SMART_MODEL" in written
    assert str(tmp_path / ".env") in report.created[0]


def test_setup_never_overwrites_an_existing_env(tmp_path):
    """A .env holds a real key. Losing one to a setup command is a bad afternoon."""
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-real")
    report = setup_environment(tmp_path)
    assert (tmp_path / ".env").read_text() == "OPENAI_API_KEY=sk-real"
    assert any("already exists" in s for s in report.skipped)


def test_setup_reports_what_it_will_not_do_itself(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("nexcraftviz.env.load", lambda *a, **k: [])
    report = setup_environment(tmp_path)
    assert any("will not write one for you" in item for item in report.todo)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def test_the_shipped_scenarios_load():
    scenarios = load_scenarios()
    assert len(scenarios) >= 6
    assert all(s.question for s in scenarios)


@pytest.mark.asyncio
async def test_an_offline_run_exercises_every_stage():
    results = await run_scenarios(load_scenarios())
    assert all(r.passed for r in results), [r.problems for r in results if not r.passed]
    for result in results:
        assert result.run is not None
        assert result.run.stages.completed() == ["plan", "generate", "evaluate", "deliver"]


@pytest.mark.asyncio
async def test_an_offline_run_does_not_claim_to_have_graded_the_prompts():
    """The stand-in agreeing with itself is not evidence about a prompt."""
    results = await run_scenarios(load_scenarios()[:2])
    assert all(r.graded == "wiring only" for r in results)
    assert "the prompts were not graded" in report_run(results)


@pytest.mark.asyncio
async def test_a_scenario_that_cannot_produce_a_chart_fails_rather_than_crashing():
    results = await run_scenarios([Scenario(name="empty", question="chart this", rows=[])])
    assert not results[0].passed
    assert results[0].problems or results[0].error


@pytest.mark.asyncio
async def test_one_broken_scenario_does_not_stop_the_rest():
    scenarios = [
        Scenario(name="empty", question="chart this", rows=[]),
        *load_scenarios()[:1],
    ]
    results = await run_scenarios(scenarios)
    assert len(results) == 2
    assert results[1].passed


@pytest.mark.asyncio
async def test_an_expectation_that_is_not_met_is_reported(monkeypatch):
    """A live-mode grading path: the offline skip must not hide a real miss."""
    scenario = Scenario(
        name="wrong_type",
        question="Which region brought in the most revenue?",
        rows=[{"region": "North", "revenue": 152.0}, {"region": "West", "revenue": 128.0}],
        expect_chart_type=["heatmap"],
    )
    from harness.offline import offline_runner

    results = await run_scenarios([scenario], llm=offline_runner(scenario.rows))
    assert not results[0].passed
    assert "expected one of ['heatmap']" in results[0].problems[0]


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_report_is_organised_by_stage():
    """"The chart was wrong" is not actionable; "the planner picked bar" is."""
    results = await run_scenarios(load_scenarios()[:2])
    text = report_run(results)
    assert "per stage" in text
    for stage in ("plan", "generate", "evaluate", "deliver"):
        assert stage in text
    assert "gates" in text


@pytest.mark.asyncio
async def test_the_summary_counts_gate_outcomes():
    summary = summarise(await run_scenarios(load_scenarios()[:2]))
    assert summary["gates"]["validates"]["pass"] == 2
    assert set(summary["stages"]) == {"plan", "generate", "evaluate", "deliver"}


@pytest.mark.asyncio
async def test_results_round_trip_to_disk_and_compare(tmp_path):
    results = await run_scenarios(load_scenarios()[:2])
    first = save_results(results, directory=tmp_path)

    broken = json.loads(first.read_text())
    broken["results"][0]["passed"] = False
    second = tmp_path / "run-99999999T999999.json"
    second.write_text(json.dumps(broken))

    text = compare(first, second)
    assert "broken:    1" in text
    assert f"- {results[0].scenario}" in text


def test_setup_does_not_tell_you_to_fix_a_deliberate_deviation(tmp_path):
    """The PowerBI palette is below WCAG AA on purpose — parity is the point.
    Listing it under "still to do" would send people to fix what is correct."""
    report = setup_environment(tmp_path)
    assert not any("themes" in item for item in report.todo)
    text = report.text()
    if any("themes" in note for note in report.notes):
        assert "Worth knowing" in text


def test_setup_creates_a_root_it_was_pointed_at(tmp_path):
    target = tmp_path / "fresh" / "project"
    setup_environment(target)
    assert (target / ".env").exists()


def test_an_expected_field_read_by_a_transform_counts():
    """Found live: ranked_categories and top_n_is_honoured drew correct charts —
    summing `revenue` as `sum_revenue` — and the grader failed both for
    "fields absent from the spec: revenue"."""
    from harness.run import Scenario, _grade
    from nexcraftviz.agents.artifacts import ChartStages, GenerateArtifact
    from nexcraftviz.pipeline import ChartRun
    from nexcraftviz.spec.model import Spec

    rows = [{"region": "North", "revenue": 152.0}, {"region": "West", "revenue": 128.0}]
    spec = Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": rows},
        "transform": [{"aggregate": [{"op": "sum", "field": "revenue", "as": "sum_revenue"}],
                       "groupby": ["region"]}],
        "mark": "bar",
        "encoding": {"y": {"field": "region", "type": "nominal"},
                     "x": {"field": "sum_revenue", "type": "quantitative"}},
    })
    run = ChartRun(stages=ChartStages(generate=GenerateArtifact(spec=spec, chart_type="bar")))
    scenario = Scenario(name="t", question="q", rows=rows, expect_fields=["revenue", "region"])
    result = _grade(scenario, run, offline=False)
    assert result.passed, result.problems


def test_an_expected_field_read_nowhere_still_fails():
    from harness.run import Scenario, _grade
    from nexcraftviz.agents.artifacts import ChartStages, GenerateArtifact
    from nexcraftviz.pipeline import ChartRun
    from nexcraftviz.spec.model import Spec

    rows = [{"region": "North", "revenue": 152.0, "orders": 4}]
    spec = Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": rows}, "mark": "bar",
        "encoding": {"y": {"field": "region", "type": "nominal"},
                     "x": {"field": "revenue", "type": "quantitative"}},
    })
    run = ChartRun(stages=ChartStages(generate=GenerateArtifact(spec=spec, chart_type="bar")))
    scenario = Scenario(name="t", question="q", rows=rows, expect_fields=["orders"])
    result = _grade(scenario, run, offline=False)
    assert not result.passed and "orders" in result.problems[0]
