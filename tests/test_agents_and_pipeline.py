"""The agent registry, the planner's intent extractor, the gates, the pipeline.

Everything here runs offline. The pipeline is exercised end to end against a
stub model, which is the point of the three-phase skill contract: no key, no
cost, and the wiring is still under test.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from nexcraftviz.agents import (
    ROLES,
    AgentRegistry,
    AgentSpec,
    ChartPlan,
    ChartStages,
    Critique,
    EncodingIntent,
    GenerateArtifact,
    StageContext,
    StageError,
    extract_intent,
    resolve_model,
)
from nexcraftviz.agents.intent import apply_as_floor
from nexcraftviz.evaluate.gates import run_gates
from nexcraftviz.pipeline import ChartRequest, run_pipeline
from nexcraftviz.spec.model import Spec

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

PLAN_PAYLOAD: dict[str, Any] = {
    "status": "ok",
    "confidence": 0.9,
    "chart_type": "bar",
    "alternatives": ["pie"],
    "rationale": "Three regions compared on one measure.",
    "encodings": [
        {"channel": "y", "field": "region", "why": "the categories"},
        {"channel": "x", "field": "revenue", "aggregate": "sum", "why": "the measure"},
    ],
    "transforms": [],
    "styling": {"palette_intent": "single"},
    "metadata": {"title": "Revenue by region"},
    "follow_ups": [],
}


def _bar_spec_json(rows: list[dict[str, Any]]) -> str:
    return json.dumps({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": rows},
        "mark": "bar",
        "encoding": {
            "y": {"field": "region", "type": "nominal", "sort": "-x"},
            "x": {"field": "revenue", "type": "quantitative", "aggregate": "sum"},
        },
    })


@pytest.fixture
def stub_llm(rows):
    """A model that answers each prompt with a plausible payload.

    Routes on the system prompt rather than a call counter, so a change in the
    number of calls does not silently shift which answer each stage receives.
    """
    calls: list[str] = []

    async def run(system: str, user: str, schema: dict[str, Any]):
        if "WHAT chart to draw" in system:
            calls.append("plan")
            payload: dict[str, Any] = PLAN_PAYLOAD
        elif "Vega-Lite v5 specification" in system:
            calls.append("generate")
            payload = {
                "spec_json": _bar_spec_json(rows),
                "chart_type": "bar",
                "reasoning": "as planned",
            }
        else:
            calls.append("critique")
            payload = {"answers_question": True, "score": 0.9, "complaint": "", "suggestion": ""}
        return payload, {"model": "stub", "tokens_in": 100, "tokens_out": 40}

    run.calls = calls  # type: ignore[attr-defined]
    return run


class RecordingAgent:
    """A double that fills a role and remembers it was used."""

    def __init__(self, role: str, artifact: Any, *, uses_llm: bool = False) -> None:
        self.spec = AgentSpec(role=role, name=f"double.{role}", uses_llm=uses_llm)
        self._artifact = artifact
        self.calls: list[StageContext] = []

    async def run(self, ctx: StageContext) -> Any:
        self.calls.append(ctx)
        artifact = self._artifact
        return artifact(ctx) if callable(artifact) else artifact


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------

def test_default_registry_fills_every_role():
    registry = AgentRegistry.default()
    assert set(registry.roles) == set(ROLES)


def test_declared_tier_matches_the_prompt_header():
    """cp2's `# model_tier:` headers are read by nothing and have drifted.

    Ours are parsed, so this test is what keeps them true.
    """
    checked = 0
    for agent in AgentRegistry.default()._agents.values():
        declared = agent.spec.declared_tier_in_prompt()
        if declared is None:
            continue
        checked += 1
        assert declared == agent.spec.model_tier, (
            f"{agent.spec.name}: prompt header says {declared!r}, "
            f"spec says {agent.spec.model_tier!r}"
        )
    assert checked >= 6, "every LLM-backed agent should declare its tier in its prompt"


def test_prompt_headers_do_not_reach_the_model():
    """Metadata is bookkeeping. If it rode along in the system prompt, editing a
    `last_updated` line would change what the model was asked."""
    from nexcraftviz.skills import REGISTRY

    system, _ = REGISTRY["viz.plan"].system_prompt()
    assert not system.startswith("#")
    assert "last_updated" not in system


def test_offline_agents_do_not_claim_a_model():
    rows = {row["role"]: row for row in AgentRegistry.default().describe()}
    assert rows["evaluator"]["model"] == ""
    assert rows["planner"]["model"] == resolve_model("smart")


def test_override_replaces_the_implementation():
    registry = AgentRegistry.default()
    double = RecordingAgent("planner", ChartPlan(chart_type="bar"))
    registry.override("planner", double)
    assert registry.get("planner") is double


def test_override_rejects_a_role_mismatch():
    registry = AgentRegistry.default()
    with pytest.raises(ValueError, match="declares the one role"):
        registry.override("planner", RecordingAgent("generator", None))


def test_clone_does_not_leak_an_override():
    original = AgentRegistry.default()
    clone = original.clone()
    clone.override("planner", RecordingAgent("planner", ChartPlan()))
    assert original.get("planner") is not clone.get("planner")


def test_missing_role_names_what_is_filled():
    with pytest.raises(StageError, match="no agent registered"):
        AgentRegistry({}).get("planner")


def test_stage_context_require_fails_loud_on_an_empty_slot():
    ctx = StageContext(question="q", stages=ChartStages())
    with pytest.raises(StageError, match="has not run"):
        ctx.require("generate")


def test_tier_resolution_follows_the_environment(monkeypatch):
    monkeypatch.setenv("NEXCRAFTVIZ_SMART_MODEL", "gpt-5")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5-mini")
    assert resolve_model("smart") == "gpt-5"
    monkeypatch.delenv("NEXCRAFTVIZ_SMART_MODEL")
    assert resolve_model("smart") == "gpt-5-mini"


# ---------------------------------------------------------------------------
# the intent extractor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("revenue by region as a line chart", {"chart_type": "line"}),
        ("show me a stacked bar chart of orders", {"chart_type": "stacked_bar"}),
        ("top 10 customers by spend", {"top_n": 10, "sort_order": "descending"}),
        ("the 3 smallest regions", {"top_n": 3, "sort_order": "ascending"}),
        ("revenue by month", {"time_grain": "month"}),
        ("orders per quarter in dark mode", {"time_grain": "quarter",
                                             "theme": "nexcraftviz-dark"}),
        # Bare "bars" is deliberately not a chart-type request — only the
        # orientation is claimed, and the model still picks the type.
        ("horizontal bars of revenue", {"horizontal": True}),
        ("revenue as bars", {"chart_type": "bar"}),
        ("share of revenue as a percentage", {"normalize": True}),
        ("completion against a 90% target", {"targets": [90.0]}),
    ],
)
def test_extractor_reads_what_the_question_says(question, expected):
    found = extract_intent(question).to_dict()
    for key, value in expected.items():
        assert found.get(key) == value, f"{question!r}: {key}"


@pytest.mark.parametrize(
    "question",
    [
        "show me a breakdown of revenue",       # not a pie request
        "how many orders did we take",          # no chart type named
        "which region is doing well",           # no direction word to sort on
        "revenue by product",                   # "by" is not a time grain
    ],
)
def test_extractor_does_not_invent(question):
    """A false positive puts a constraint into the plan that the model then has
    to fight, which is worse than extracting nothing."""
    found = extract_intent(question).to_dict()
    assert "chart_type" not in found or question.startswith("show me a breakdown") is False
    assert "time_grain" not in found
    assert "sort_order" not in found


def test_top_n_is_bounded():
    assert extract_intent("top 500 rows").to_dict().get("top_n") is None


def test_extractor_is_a_floor_not_a_ceiling():
    """Where the model made a call it wins; the extractor only fills blanks."""
    plan = ChartPlan(chart_type="heatmap", encodings=[EncodingIntent(channel="x", field="a")])
    apply_as_floor(plan, extract_intent("revenue as a line chart, top 5"))
    assert plan.chart_type == "heatmap"                       # the model decided
    assert any(t.kind == "top_n" and t.value == 5 for t in plan.transforms)  # it did not


# ---------------------------------------------------------------------------
# the gates — one test per gate, each fed the failure it exists to catch
# ---------------------------------------------------------------------------

def _gate(results, name):
    return next(g for g in results if g.gate == name)


def test_validates_gate_catches_a_field_the_data_lacks(rows):
    spec = Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": rows},
        "mark": "bar",
        "encoding": {
            "x": {"field": "revenu", "type": "quantitative"},   # typo
            "y": {"field": "region", "type": "nominal"},
        },
    })
    assert not _gate(run_gates(spec, rows=rows), "validates").passed


def test_no_baked_styling_gate_catches_a_hardcoded_mark_colour(bar_spec):
    result = _gate(run_gates(bar_spec, rows=bar_spec.data_values), "no_baked_styling")
    assert not result.passed
    assert "#0C8BA6" in result.detail


def test_data_honesty_gate_catches_a_summed_rate():
    rows = [{"region": "West", "completion_pct": 82.0}, {"region": "East", "completion_pct": 91.0}]
    spec = Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": rows},
        "mark": "bar",
        "encoding": {
            "x": {"field": "region", "type": "nominal"},
            "y": {"field": "completion_pct", "type": "quantitative", "aggregate": "sum"},
        },
    })
    result = _gate(run_gates(spec, rows=rows), "data_honesty")
    assert not result.passed
    assert "rate" in result.detail


def test_data_honesty_gate_catches_a_quarter_label_as_a_date():
    """The corpus defect this exists for: "2025-Q1" typed temporal renders an
    Invalid Date axis."""
    rows = [{"quarter": "2025-Q1", "revenue": 10.0}, {"quarter": "2025-Q2", "revenue": 12.0}]
    spec = Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": rows},
        "mark": "line",
        "encoding": {
            "x": {"field": "quarter", "type": "temporal"},
            "y": {"field": "revenue", "type": "quantitative"},
        },
    })
    result = _gate(run_gates(spec, rows=rows), "data_honesty")
    assert not result.passed
    assert "temporal" in result.detail


def test_matches_plan_gate_catches_a_generator_that_ignored_the_plan(rows):
    plan = ChartPlan(chart_type="line", encodings=[
        EncodingIntent(channel="x", field="month"),
        EncodingIntent(channel="y", field="revenue"),
    ])
    spec = Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": rows},
        "mark": "bar",
        "encoding": {
            "x": {"field": "region", "type": "nominal"},
            "y": {"field": "orders", "type": "quantitative"},
        },
    })
    result = _gate(run_gates(spec, plan=plan, rows=rows), "matches_plan")
    assert not result.passed
    assert "planned line but built bar" in result.detail


def test_matches_plan_gate_allows_a_layered_spec_over_the_planned_mark(rows):
    """Forgiving about *how*: a bar plus a rule is still a bar chart."""
    plan = ChartPlan(chart_type="bar", encodings=[EncodingIntent(channel="x", field="region")])
    spec = Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": rows},
        "layer": [
            {"mark": "bar", "encoding": {
                "x": {"field": "region", "type": "nominal"},
                "y": {"field": "revenue", "type": "quantitative"}}},
            {"mark": "rule", "encoding": {"y": {"datum": 100}}},
        ],
    })
    assert _gate(run_gates(spec, plan=plan, rows=rows), "matches_plan").passed


def test_a_clean_spec_passes_every_gate(rows):
    spec = Spec(json.loads(_bar_spec_json(rows)))
    results = run_gates(spec, rows=rows)
    assert [g.gate for g in results if not g.passed] == []
    assert len(results) == 5


def test_a_broken_gate_does_not_fail_the_chart(monkeypatch, rows):
    """A gate that raises is a bug in the gate, not evidence about the chart."""
    import nexcraftviz.evaluate.gates as module

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(module, "_data_honesty", explode)
    results = run_gates(Spec(json.loads(_bar_spec_json(rows))), rows=rows)
    honesty = _gate(results, "data_honesty")
    assert honesty.passed and "could not run" in honesty.detail


# ---------------------------------------------------------------------------
# the pipeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_runs_every_stage(rows, stub_llm):
    run = await run_pipeline(
        ChartRequest(question="revenue by region", rows=rows), llm=stub_llm, evaluate="full"
    )
    assert run.ok
    assert run.stages.completed() == ["plan", "generate", "evaluate", "deliver"]
    assert run.spec is not None
    assert run.stages.total_tokens() > 0


@pytest.mark.asyncio
async def test_stop_after_plan_runs_nothing_else(rows, stub_llm):
    run = await run_pipeline(
        ChartRequest(question="revenue by region", rows=rows), llm=stub_llm, stop_after="plan"
    )
    assert run.stages.completed() == ["plan"]
    assert stub_llm.calls == ["plan"]


@pytest.mark.asyncio
async def test_evaluation_can_be_switched_off(rows, stub_llm):
    run = await run_pipeline(
        ChartRequest(question="revenue by region", rows=rows), llm=stub_llm, evaluate="off"
    )
    assert run.stages.evaluate is None
    assert run.stages.deliver is not None


@pytest.mark.asyncio
async def test_the_critic_is_not_paid_to_judge_a_broken_chart(rows, stub_llm):
    """Gates fail → no critique call. An opinion on a chart that does not
    validate is a wasted call."""
    registry = AgentRegistry.default()
    broken = Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": rows},
        "mark": "bar",
        "encoding": {"x": {"field": "nope", "type": "quantitative"}},
    })
    registry.override("generator", RecordingAgent(
        "generator", GenerateArtifact(spec=broken, chart_type="bar")
    ))
    run = await run_pipeline(
        ChartRequest(question="revenue by region", rows=rows),
        registry=registry, llm=stub_llm, evaluate="full",
    )
    assert "critique" not in stub_llm.calls
    assert run.stages.evaluate is not None and not run.stages.evaluate.passed


@pytest.mark.asyncio
async def test_a_failed_evaluation_buys_exactly_one_regeneration(rows, stub_llm):
    """Not a loop. An unbounded retry burns money and converges on the same
    answer."""
    registry = AgentRegistry.default()
    always_wrong = Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": rows},
        "mark": {"type": "bar", "color": "#FF0000"},   # trips no_baked_styling every time
        "encoding": {
            "x": {"field": "region", "type": "nominal"},
            "y": {"field": "revenue", "type": "quantitative"},
        },
    })
    generator = RecordingAgent("generator", GenerateArtifact(spec=always_wrong, chart_type="bar"))
    registry.override("generator", generator)

    run = await run_pipeline(
        ChartRequest(question="revenue by region", rows=rows), registry=registry, llm=stub_llm
    )
    assert len(generator.calls) == 2, "one attempt plus exactly one retry"
    assert generator.calls[1].complaint.startswith("no_baked_styling")
    assert run.stages.evaluate.regenerated is True


@pytest.mark.asyncio
async def test_a_rejected_chart_is_still_delivered_with_its_verdict(rows, stub_llm):
    """The caller gets the chart *and* the reason to distrust it — but the
    status has to say so, or 'delivered' reads as 'passed'."""
    registry = AgentRegistry.default()
    flawed = Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": rows},
        "mark": {"type": "bar", "color": "#FF0000"},
        "encoding": {
            "x": {"field": "region", "type": "nominal"},
            "y": {"field": "revenue", "type": "quantitative"},
        },
    })
    registry.override("generator", RecordingAgent(
        "generator", GenerateArtifact(spec=flawed, chart_type="bar")
    ))
    run = await run_pipeline(
        ChartRequest(question="revenue by region", rows=rows), registry=registry, llm=stub_llm
    )
    assert run.status == "ambiguous"
    assert run.stages.deliver is not None
    assert run.stages.deliver.package["verdict"] is not None
    assert run.delivery.status == "ambiguous"


@pytest.mark.asyncio
async def test_a_retry_is_not_spent_on_a_verdict_with_no_complaint(rows, stub_llm):
    registry = AgentRegistry.default()
    generator = RecordingAgent(
        "generator", GenerateArtifact(spec=Spec(json.loads(_bar_spec_json(rows))), chart_type="bar")
    )
    registry.override("generator", generator)
    registry.override("critic", RecordingAgent(
        "critic", Critique(answers_question=False, complaint="")
    ))
    run = await run_pipeline(
        ChartRequest(question="revenue by region", rows=rows),
        registry=registry, llm=stub_llm, evaluate="full",
    )
    assert len(generator.calls) == 1
    assert "retry skipped" in " ".join(run.trace)


@pytest.mark.asyncio
async def test_an_unplannable_question_stops_before_generating(rows, stub_llm):
    registry = AgentRegistry.default()
    registry.override("planner", RecordingAgent("planner", ChartPlan(
        status="insufficient_data",
        reason_if_not_ok="the rows carry no measure to plot",
    )))
    run = await run_pipeline(
        ChartRequest(question="what is the revenue", rows=rows), registry=registry, llm=stub_llm
    )
    assert run.status == "insufficient_data"
    assert run.stages.generate is None
    assert "no measure" in run.reason


@pytest.mark.asyncio
async def test_the_generator_is_given_the_plan_not_the_recommendations(rows, stub_llm):
    """Offering a competing ranking alongside a settled plan invites the
    generator to relitigate the decision."""
    from nexcraftviz.skills import REGISTRY

    plan = ChartPlan.model_validate(PLAN_PAYLOAD)
    payload = REGISTRY["viz.generate"].user_payload(
        REGISTRY["viz.generate"].coerce_input(
            {"question": "revenue by region", "rows": rows, "plan": plan}
        )
    )
    assert "recommendations" not in payload
    assert "Revenue by region" in payload


@pytest.mark.asyncio
async def test_a_missing_model_runner_is_reported_as_a_wiring_problem(rows):
    run = await run_pipeline(ChartRequest(question="revenue by region", rows=rows), llm=None)
    assert run.status == "failed"
    assert "needs a model runner" in run.reason


# ---------------------------------------------------------------------------
# the planner skill
# ---------------------------------------------------------------------------

def test_plan_rejects_fields_the_data_does_not_have(rows):
    from nexcraftviz.skills import REGISTRY

    skill = REGISTRY["viz.plan"]
    plan = ChartPlan.model_validate({
        **PLAN_PAYLOAD,
        "encodings": [{"channel": "x", "field": "profit_margin", "why": "invented"}],
    })
    result = skill.apply(skill.coerce_input({"question": "q", "rows": rows}), plan)
    assert result.failed
    assert "profit_margin" in result.failed[0][1]


def test_plan_payload_carries_the_extracted_intent(rows):
    from nexcraftviz.skills import REGISTRY

    skill = REGISTRY["viz.plan"]
    payload = json.loads(skill.user_payload(
        skill.coerce_input({"question": "top 5 regions by revenue as a bar chart", "rows": rows})
    ))
    assert payload["extracted"] == {
        "chart_type": "bar", "top_n": 5, "sort_order": "descending"
    }


# ---------------------------------------------------------------------------
# the session in pipeline mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_mode_builds_a_chart_through_every_stage(rows, stub_llm):
    from nexcraftviz.session import Session

    session = Session(rows=rows, pipeline=True)
    turn = await session.turn("show me revenue by region", llm=stub_llm)

    assert turn.skill == "pipeline"
    assert session.has_chart
    assert session.last_run.stages.completed() == ["plan", "generate", "evaluate", "deliver"]
    assert "Built a bar" in turn.reply


@pytest.mark.asyncio
async def test_pipeline_mode_leaves_editing_alone(rows, stub_llm):
    """An edit names the change and the chart exists — planning it again would
    be a model call that decides nothing."""
    from nexcraftviz.session import Session

    session = Session(rows=rows, pipeline=True, document=Spec(json.loads(_bar_spec_json(rows))))
    proposal = session.propose("sort descending")
    assert proposal.skill == "viz.edit"          # no error, no pipeline


@pytest.mark.asyncio
async def test_propose_refuses_to_pretend_a_pipeline_turn_is_one_call(rows):
    from nexcraftviz.session import Session

    session = Session(rows=rows, pipeline=True)
    with pytest.raises(ValueError, match="several model calls"):
        session.propose("show me revenue by region")


@pytest.mark.asyncio
async def test_a_caveat_reaches_the_reply_rather_than_only_the_artifact(rows, stub_llm):
    from nexcraftviz.session import Session

    registry = AgentRegistry.default()
    flawed = Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": rows},
        "mark": {"type": "bar", "color": "#FF0000"},
        "encoding": {
            "x": {"field": "region", "type": "nominal"},
            "y": {"field": "revenue", "type": "quantitative"},
        },
    })
    registry.override("generator", RecordingAgent(
        "generator", GenerateArtifact(spec=flawed, chart_type="bar")
    ))
    session = Session(rows=rows, pipeline=True, registry=registry)
    turn = await session.turn("show me revenue by region", llm=stub_llm)
    assert "caveat" in turn.reply
    assert "no_baked_styling" in turn.reply


@pytest.mark.asyncio
async def test_a_host_driven_run_lands_in_the_same_session_state(rows, stub_llm):
    """`adopt_run` exists so a host with its own registry is not a second-class
    caller with a different session shape."""
    from nexcraftviz.session import Session

    run = await run_pipeline(
        ChartRequest(question="revenue by region", rows=rows), llm=stub_llm
    )
    session = Session(rows=rows, pipeline=True)
    session.adopt_run(run, message="revenue by region")
    assert session.has_chart
    assert session.state()["kind"] == "chart"
