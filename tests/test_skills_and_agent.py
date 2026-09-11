"""The skill contract, the session in both modes, and the agent integrations."""
from __future__ import annotations

import json

import pytest

from nexcraftviz.compose.widget import Widget
from nexcraftviz.examples import talent_acquisition_widget
from nexcraftviz.integrations.tools import BY_NAME, call, op_schemas, tool_schemas
from nexcraftviz.session import Session
from nexcraftviz.session.route import route
from nexcraftviz.skills import REGISTRY, SkillError, get, names
from nexcraftviz.spec.model import Spec

ROWS = [
    {"region": "West", "revenue": 128, "orders": 41},
    {"region": "East", "revenue": 96, "orders": 33},
    {"region": "North", "revenue": 152, "orders": 48},
    {"region": "South", "revenue": 71, "orders": 22},
]

CHART = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "data": {"values": ROWS},
    "width": "container",
    "height": 200,
    "mark": "bar",
    "encoding": {
        "x": {"field": "region", "type": "nominal"},
        "y": {"field": "revenue", "type": "quantitative"},
    },
}

SORT_OPS = {
    "ops": [
        {"op": "sort_by", "channel": "x", "by": "revenue", "order": "descending"},
        {"op": "limit_top_n", "n": 3, "by": "revenue"},
    ],
    "reasoning": "North leads on revenue.",
}


def stub_llm(payload: dict):
    """A model runner that returns a fixed payload, for testing the plumbing."""

    async def run(system: str, user: str, schema: dict):
        assert system and user and schema, "the runner must receive all three"
        return payload, {"tokens_in": 0, "tokens_out": 0}

    return run


# ---------------------------------------------------------------------------
# the contract
# ---------------------------------------------------------------------------

def test_every_skill_declares_itself() -> None:
    for name, skill in REGISTRY.items():
        assert skill.spec.name == name
        assert skill.spec.summary
        assert skill.Input and skill.Output


@pytest.mark.parametrize("name", names())
def test_schemas_build_for_every_skill(name: str) -> None:
    skill = get(name)
    assert "properties" in skill.input_schema()
    assert skill.output_schema() is not None


@pytest.mark.parametrize("name", [n for n in names() if REGISTRY[n].spec.uses_llm])
def test_prompt_rendering_is_pure_and_complete(name: str) -> None:
    """Phase 1 must need no model and no network."""
    skill = get(name)
    inputs = _inputs_for(name)
    prompt = skill.render_prompt(inputs)

    assert len(prompt.system) > 400, "a prompt this short is not doing its job"
    assert prompt.user
    assert prompt.output_schema
    assert prompt.prompt_version.startswith(name)


def test_prompts_are_files_not_string_literals() -> None:
    """So they can be versioned, diffed and overridden without a release."""
    from nexcraftviz.skills.base import PROMPT_DIR, prompt_manifest

    manifest = prompt_manifest()["prompts"]
    for name, entry in manifest.items():
        assert (PROMPT_DIR / entry["file"]).exists(), name


def test_a_prompt_can_be_overridden_by_environment(tmp_path, monkeypatch) -> None:
    from nexcraftviz.skills.base import load_prompt

    load_prompt.cache_clear()
    (tmp_path / "viz.edit.txt").write_text("OVERRIDDEN", encoding="utf-8")
    monkeypatch.setenv("NEXCRAFTVIZ_PROMPT_DIR", str(tmp_path))
    try:
        text, _ = load_prompt("viz.edit")
        assert text == "OVERRIDDEN"
    finally:
        load_prompt.cache_clear()


def test_parse_accepts_a_json_string() -> None:
    """Plenty of tool-calling paths hand back a string."""
    parsed = get("viz.edit").parse(json.dumps(SORT_OPS))
    assert len(parsed.ops) == 2


def test_parse_reports_a_useful_error() -> None:
    with pytest.raises(SkillError, match="ops"):
        get("viz.edit").parse({"ops": [{"op": "not_a_real_op"}]})


def test_apply_is_pure_and_needs_no_model() -> None:
    skill = get("viz.edit")
    inputs = skill.coerce_input({"instruction": "sort", "spec": CHART, "rows": ROWS})
    result = skill.apply(inputs, skill.parse(SORT_OPS))

    assert result.ok
    assert isinstance(result.value, Spec)
    assert result.meta["valid"] is True
    assert result.inverse


def test_edit_reports_a_missing_column_instead_of_guessing() -> None:
    skill = get("viz.edit")
    inputs = skill.coerce_input({"instruction": "break down by cost centre", "spec": CHART,
                                 "rows": ROWS})
    result = skill.apply(inputs, skill.parse({"ops": [], "needs_data": "cost_centre"}))

    assert "cost_centre" in result.warnings[0]
    assert result.value.raw == CHART


def test_edit_validates_the_result_rather_than_trusting_it() -> None:
    skill = get("viz.edit")
    inputs = skill.coerce_input({"instruction": "x", "spec": CHART, "rows": ROWS})
    result = skill.apply(inputs, skill.parse(
        {"ops": [{"op": "set_color_field", "field": "not_a_column", "type": "nominal"}]}
    ))
    assert result.meta["valid"] is False
    assert any("not_a_column" in w for w in result.warnings)


@pytest.mark.parametrize("name", [n for n in names() if not REGISTRY[n].spec.uses_llm])
async def test_deterministic_skills_run_with_no_model(name: str) -> None:
    result = await get(name).run(_inputs_for(name))
    assert result.value is not None


async def test_a_model_backed_skill_says_what_to_do_without_one() -> None:
    with pytest.raises(SkillError, match="render_prompt"):
        await get("viz.edit").run({"instruction": "sort", "spec": CHART})


async def test_run_chains_all_three_phases() -> None:
    result = await get("viz.edit").run(
        {"instruction": "sort descending, top 3", "spec": CHART, "rows": ROWS},
        llm=stub_llm(SORT_OPS),
    )
    assert result.ok and result.changes


def test_theme_rejects_an_unknown_preset() -> None:
    skill = get("viz.theme")
    result = skill.apply(skill.coerce_input({"spec": CHART, "theme": "chartreuse"}), skill.Output())
    assert result.failed and "unknown theme" in result.failed[0][1]


def test_narration_is_checked_for_chart_mechanics() -> None:
    """The prompt forbids it; a regression here is invisible until a customer reads it."""
    skill = get("viz.narrate")
    inputs = skill.coerce_input({"spec": CHART, "rows": ROWS})
    result = skill.apply(inputs, skill.parse(
        {"summary": "The x-axis shows the regions and the legend shows revenue."}
    ))
    assert any("mechanics" in w for w in result.warnings)


def _inputs_for(name: str) -> dict:
    if name == "viz.generate":
        return {"question": "revenue by region", "rows": ROWS}
    if name == "viz.recommend":
        return {"rows": ROWS, "question": "revenue by region"}
    if name == "viz.place":
        return {"instruction": "make it wider", "widget": talent_acquisition_widget()}
    if name == "viz.narrate":
        return {"spec": CHART, "rows": ROWS, "question": "revenue by region"}
    if name == "viz.theme":
        return {"spec": CHART, "theme": "nexcraftviz-dark"}
    if name == "viz.plan":
        return {"question": "revenue by region", "rows": ROWS}
    if name == "viz.critique":
        return {"question": "revenue by region", "spec": CHART, "rows": ROWS}
    if name == "viz.compose":
        return {
            "ask": "a dashboard of revenue and orders",
            "visualizations": [
                {"id": "tile_1", "spec": CHART, "chart_type": "bar",
                 "question": "revenue by region"},
            ],
        }
    if name == "viz.manage":
        return {"instruction": "make it dark and sort descending", "spec": CHART,
                "rows": ROWS}
    if name == "viz.edit":
        return {"instruction": "sort descending", "spec": CHART, "rows": ROWS}
    # No silent fallback: a new skill should fail here rather than be handed an
    # edit-shaped input that happens to validate and prove nothing.
    if name == "viz.suggest_questions":
        return {"question": "How are we doing on compliance training?", "count": 4}
    raise AssertionError(f"no test input defined for {name!r}")


# ---------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "message,expected",
    [
        ("show me revenue by region", "viz.generate"),
        ("sort it descending", "viz.edit"),
        ("colour by region", "viz.edit"),
        ("what does this show?", "viz.narrate"),
        ("explain the chart", "viz.narrate"),
        ("switch to dark mode", "viz.theme"),
        ("use the powerbi theme", "viz.theme"),
    ],
)
def test_routing_a_chart_session(message: str, expected: str) -> None:
    assert route(message, has_chart=True, has_widget=False).skill == expected


@pytest.mark.parametrize(
    "message,expected",
    [
        ("make the funnel wider", "viz.place"),
        ("move the donut above the table", "viz.place"),
        ("group those two into a panel", "viz.place"),
        ("use a single column layout", "viz.place"),
    ],
)
def test_routing_a_widget_session(message: str, expected: str) -> None:
    assert route(message, has_widget=True, has_chart=False).skill == expected


def test_an_empty_session_always_creates() -> None:
    """An edit instruction with nothing to edit is a request to create."""
    assert route("sort descending", has_chart=False, has_widget=False).skill == "viz.generate"


def test_layout_words_with_no_widget_fall_back_to_editing() -> None:
    decision = route("make it wider", has_chart=True, has_widget=False)
    assert decision.skill == "viz.edit"
    assert not decision.confident


def test_a_data_change_beats_a_layout_word() -> None:
    """"sort the panel" is about the data, whatever "panel" suggests."""
    decision = route("sort the panel by revenue", has_chart=True, has_widget=True)
    assert decision.skill == "viz.edit"
    assert not decision.confident


# ---------------------------------------------------------------------------
# the session, in both modes
# ---------------------------------------------------------------------------

def test_skill_mode_needs_no_model_to_propose() -> None:
    session = Session(rows=ROWS, document=Spec(CHART))
    proposal = session.propose("sort descending and keep the top 3")

    assert proposal.skill == "viz.edit"
    assert proposal.needs_model
    assert proposal.prompt.system and proposal.prompt.output_schema


def test_skill_mode_commits_what_the_host_model_returned() -> None:
    session = Session(rows=ROWS, document=Spec(CHART))
    proposal = session.propose("sort descending and keep the top 3")
    turn = session.commit(proposal, SORT_OPS)

    assert turn.ok
    assert turn.reply == "North leads on revenue."
    assert session.can_undo
    assert session.document.primary_view.encoding["x"]["sort"]


async def test_hosted_mode_runs_the_model_itself() -> None:
    session = Session(rows=ROWS, document=Spec(CHART))
    turn = await session.turn("sort descending", llm=stub_llm(SORT_OPS))
    assert turn.ok and turn.changes


async def test_both_modes_reach_the_same_state() -> None:
    """`commit` is the whole second half of `turn`, so neither is second-class."""
    hosted = Session(rows=ROWS, document=Spec(CHART))
    await hosted.turn("sort descending", llm=stub_llm(SORT_OPS))

    driven = Session(rows=ROWS, document=Spec(CHART))
    driven.commit(driven.propose("sort descending"), SORT_OPS)

    assert hosted.document.hash == driven.document.hash


async def test_a_deterministic_turn_needs_no_model_in_hosted_mode() -> None:
    session = Session(rows=ROWS, document=Spec(CHART))
    turn = await session.turn("switch to dark mode")

    assert turn.skill == "viz.theme"
    assert session.theme == "nexcraftviz-dark"
    assert "config" in session.document.raw


async def test_a_model_backed_turn_without_a_model_says_so() -> None:
    session = Session(rows=ROWS, document=Spec(CHART))
    with pytest.raises(ValueError, match="propose"):
        await session.turn("sort descending")


def test_undo_and_redo_step_through_document_changes() -> None:
    session = Session(rows=ROWS, document=Spec(CHART))
    before = session.document.hash
    session.commit(session.propose("sort descending"), SORT_OPS)
    after = session.document.hash

    assert session.undo()
    assert session.document.hash == before
    assert session.redo()
    assert session.document.hash == after
    assert not session.redo()


def test_undo_on_a_fresh_session_is_a_no_op() -> None:
    assert Session().undo() is False


def test_a_new_edit_clears_the_redo_stack() -> None:
    session = Session(rows=ROWS, document=Spec(CHART))
    session.commit(session.propose("sort"), SORT_OPS)
    session.undo()
    assert session.can_redo

    session.commit(session.propose("sort"), SORT_OPS)
    assert not session.can_redo


def test_a_widget_session_ships_html_and_specs() -> None:
    """Injected <script> tags never run, so a client cannot scrape the specs."""
    state = Session(document=talent_acquisition_widget()).state()

    assert state["kind"] == "widget"
    assert state["html"] and "nxv-group" in state["html"]
    assert set(state["specs"]) == {"tile-funnel", "tile-sourcing", "tile-time-to-hire"}


def test_narrating_a_widget_picks_a_chart_and_says_which() -> None:
    """A widget is several charts and the prompt describes one."""
    session = Session(document=talent_acquisition_widget())
    proposal = session.propose("what does this show?")

    assert proposal.skill == "viz.narrate"
    assert "tile" in proposal.inputs["question"].lower()


def test_a_caller_can_override_the_router() -> None:
    session = Session(rows=ROWS, document=Spec(CHART))
    assert session.propose("anything", skill="viz.narrate").skill == "viz.narrate"


# ---------------------------------------------------------------------------
# agent integrations
# ---------------------------------------------------------------------------

def test_there_is_no_tool_that_calls_a_model() -> None:
    """In agent mode the agent IS the model; a tool that calls one is a round
    trip and a second, worse prompt."""
    assert "viz_generate" not in BY_NAME
    assert "viz_edit" not in BY_NAME


@pytest.mark.parametrize("style", ["openai", "anthropic", "mcp"])
def test_tool_schemas_build_for_every_style(style: str) -> None:
    schemas = tool_schemas(style)  # type: ignore[arg-type]
    assert len(schemas) == len(BY_NAME)
    key = {"openai": "function", "anthropic": "input_schema", "mcp": "inputSchema"}[style]
    assert all(key in s for s in schemas)


def test_guidance_hands_over_the_real_prompt_and_vocabulary() -> None:
    payload = call("viz_guidance", {"skill": "viz.edit"})
    assert len(payload["guidance"]) > 400
    assert "sort_by" in payload["operations"]
    assert payload["apply_with"] == "viz_apply_ops"


def test_guidance_names_the_layout_applier_for_placement() -> None:
    assert call("viz_guidance", {"skill": "viz.place"})["apply_with"] == "viz_apply_layout"


def test_apply_ops_tool_validates_and_repairs() -> None:
    broken = json.loads(json.dumps(CHART))
    broken["encoding"]["y"]["field"] = "Revenue"

    result = call("viz_apply_ops", {"spec": broken, "ops": [], "rows": ROWS})
    assert result["valid"]
    assert result["repaired"]


def test_apply_layout_tool_returns_markup() -> None:
    result = call("viz_apply_layout", {
        "widget": talent_acquisition_widget().to_dict(),
        "ops": [{"op": "set_span", "tile": "tile-sourcing", "span": "full"}],
    })
    assert result["applied"] == ["set_span"]
    assert "nxv-span--full" in result["html"]
    assert Widget.from_dict(result["widget"]).find("tile-sourcing").span == "full"


def test_tools_report_errors_rather_than_raising() -> None:
    """A tool-calling loop handles a bad result far better than an exception."""
    assert "error" in call("nope", {})
    assert "error" in call("viz_theme", {"spec": CHART, "theme": "chartreuse"})
    assert "error" in call("viz_profile", {"wrong": "argument"})


def test_a_tool_that_throws_is_still_a_result() -> None:
    assert "error" in call("viz_apply_layout", {"widget": {"nodes": [{}]}, "ops": []})


def test_compose_tool_refuses_a_non_vega_payload() -> None:
    from nexcraftviz.table import KpiCard

    result = call("viz_compose", {"specs": [CHART, KpiCard(label="x", value=1).to_spec().raw]})
    assert "error" in result and "widget" in result["error"]


def test_table_tool_needs_no_model() -> None:
    result = call("viz_table", {"rows": ROWS})
    assert result["html"].startswith("<table")
    assert [c["render"] for c in result["columns"]]


def test_operation_schemas_are_offered_for_structured_output() -> None:
    schemas = op_schemas()
    assert "ops" in schemas["chart_ops"]["properties"]
    assert "ops" in schemas["layout_ops"]["properties"]


def test_mcp_server_exposes_the_same_toolkit() -> None:
    """A thin adapter — one implementation, three front doors."""
    from nexcraftviz.integrations import mcp_server

    assert "viz_apply_ops" in mcp_server.INSTRUCTIONS
    assert len(mcp_server.INSTRUCTIONS) > 500
    # Importable without the extra, so the CLI can report it properly.
    assert isinstance(mcp_server.available(), bool)


def test_claude_code_skill_package_is_present_and_honest() -> None:
    from pathlib import Path

    root = Path(__file__).parent.parent
    skill = (root / "skills" / "nexcraftviz" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---")
    assert "description:" in skill
    for tool in ("viz_profile", "viz_recommend", "viz_guidance", "viz_apply_ops"):
        assert tool in skill

    reference = (root / "skills" / "nexcraftviz" / "references" / "operations.md").read_text()
    assert "sort_by" in reference and "set_span" in reference
