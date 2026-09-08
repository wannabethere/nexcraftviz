"""Operation algebra.

The round-trip test is the important one: every op, applied and then undone via
its inverse patch, must restore the exact original spec. That is what lets the
agent offer undo without each op hand-writing an inverse — and it catches ops
that mutate more than they claim to.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from nexcraftviz.spec.diff import apply_patch
from nexcraftviz.spec.model import Spec
from nexcraftviz.spec.ops import (
    OP_REGISTRY,
    AddAnnotation,
    AddReferenceLine,
    AddSeries,
    Aggregate,
    ApplyConfig,
    BinField,
    DropSeries,
    FacetBy,
    GroupBy,
    LimitTopN,
    OpError,
    OpList,
    ResolveScale,
    SetAxis,
    SetColorField,
    SetMark,
    SetPalette,
    SetScale,
    SetSize,
    SetTitle,
    SetTooltip,
    SortBy,
    StackMode,
    apply_ops,
    parse_ops,
)

# ---------------------------------------------------------------------------
# round trip
# ---------------------------------------------------------------------------

def _every_op() -> list:
    """One representative instance of each op, valid against `bar_spec`."""
    return [
        SetMark(mark="line"),
        SetSize(width=600, height=300),
        SetTitle(text="Revenue by region", subtitle="FY26"),
        SetPalette(scheme="tealblues"),
        SetColorField(field="region", type="nominal"),
        SortBy(channel="y", by="revenue", order="ascending"),
        LimitTopN(n=3, by="revenue"),
        StackMode(mode="normalize", channel="x"),
        GroupBy(field="region"),
        FacetBy(field="region", mode="column"),
        BinField(channel="x", maxbins=10),
        Aggregate(channel="x", aggregate="mean", field="revenue"),
        AddReferenceLine(value=100.0, axis="y", label="target"),
        AddSeries(field="orders", mark="line", independent_scale=True),
        SetAxis(channel="x", title="Revenue", format=".1f", grid=True),
        SetScale(channel="x", type="log", zero=False),
        SetTooltip(fields=["region", "revenue"]),
        AddAnnotation(text="peak", x="North", y=175.0),
        ResolveScale(channel="y", resolution="independent"),
        ApplyConfig(config={"axis": {"grid": False}}),
    ]


@pytest.mark.parametrize("operation", _every_op(), ids=lambda o: o.op)
def test_op_round_trips_via_inverse_patch(bar_spec: Spec, operation) -> None:
    before = bar_spec.hash
    result = apply_ops(bar_spec, [operation])

    assert not result.failed, result.failed
    restored = apply_patch(result.spec, result.inverse)
    assert restored.hash == before


def test_drop_series_round_trips(layered_spec: Spec) -> None:
    before = layered_spec.hash
    result = apply_ops(layered_spec, [DropSeries(index=1)])

    assert not result.failed
    assert apply_patch(result.spec, result.inverse).hash == before


def test_every_registered_op_is_covered_by_the_round_trip() -> None:
    """A new op must come with a round-trip case, or this fails."""
    covered = {op.op for op in _every_op()} | {"drop_series"}
    assert covered == set(OP_REGISTRY), f"uncovered ops: {set(OP_REGISTRY) - covered}"


def test_apply_ops_does_not_mutate_the_input(bar_spec: Spec) -> None:
    before = bar_spec.hash
    apply_ops(bar_spec, [SetTitle(text="changed")])
    assert bar_spec.hash == before


# ---------------------------------------------------------------------------
# individual behaviours
# ---------------------------------------------------------------------------

def test_set_palette_targets_the_colour_scale_when_a_field_is_encoded(line_spec: Spec) -> None:
    result = apply_ops(line_spec, [SetPalette(scheme="viridis")])
    encoding = result.spec.primary_view.encoding
    assert encoding["color"]["scale"]["scheme"] == "viridis"
    # The mark must not also be painted — that would override the scale.
    assert "color" not in (result.spec.primary_view.node.get("mark") or {})


def test_set_palette_paints_the_mark_when_nothing_is_colour_encoded(bar_spec: Spec) -> None:
    result = apply_ops(bar_spec, [SetPalette(color="#ff8800")])
    assert result.spec.primary_view.node["mark"]["color"] == "#ff8800"


def test_set_palette_range_replaces_a_previous_scheme(line_spec: Spec) -> None:
    once = apply_ops(line_spec, [SetPalette(scheme="viridis")]).spec
    twice = apply_ops(once, [SetPalette(range=["#111111", "#222222"])]).spec
    scale = twice.primary_view.encoding["color"]["scale"]
    assert scale["range"] == ["#111111", "#222222"]
    assert "scheme" not in scale


def test_set_palette_with_no_argument_fails_without_touching_the_spec(bar_spec: Spec) -> None:
    result = apply_ops(bar_spec, [SetPalette()])
    assert result.failed and result.failed[0][0] == "set_palette"
    assert not result.changed


def test_limit_top_n_installs_window_and_filter(bar_spec: Spec) -> None:
    result = apply_ops(bar_spec, [LimitTopN(n=3, by="revenue")])
    transforms = result.spec.primary_view.node["transform"]
    assert transforms[0]["window"][0]["op"] == "row_number"
    assert "<= 3" in transforms[1]["filter"]


def test_limit_top_n_replaces_rather_than_stacks(bar_spec: Spec) -> None:
    once = apply_ops(bar_spec, [LimitTopN(n=10, by="revenue")]).spec
    twice = apply_ops(once, [LimitTopN(n=5, by="revenue")]).spec
    transforms = twice.primary_view.node["transform"]
    assert len(transforms) == 2, "a second limit must replace the first"
    assert "<= 5" in transforms[1]["filter"]


def test_add_reference_line_converts_a_unit_spec_into_layers(bar_spec: Spec) -> None:
    result = apply_ops(bar_spec, [AddReferenceLine(value=120.0, label="target")])
    raw = result.spec.raw
    assert "mark" not in raw, "the unit mark must move into the layer"
    assert len(raw["layer"]) == 3  # original + rule + label
    # Shared properties stay at the root or the layers will not resolve.
    assert raw["data"]["values"]
    assert raw["width"] == 420


def test_add_series_with_independent_scale_sets_resolve(bar_spec: Spec) -> None:
    result = apply_ops(bar_spec, [AddSeries(field="orders", independent_scale=True)])
    assert result.spec.raw["resolve"]["scale"]["y"] == "independent"


def test_drop_series_collapses_back_to_a_unit_spec(layered_spec: Spec) -> None:
    result = apply_ops(layered_spec, [DropSeries(index=1)])
    assert "layer" not in result.spec.raw
    assert result.spec.raw["mark"] == "bar"


def test_group_by_clears_stacking(bar_spec: Spec) -> None:
    result = apply_ops(bar_spec, [GroupBy(field="region")])
    encoding = result.spec.primary_view.encoding
    assert encoding["xOffset"]["field"] == "region"
    assert encoding["x"]["stack"] is None


def test_facet_by_switches_axis_rather_than_stacking_both(bar_spec: Spec) -> None:
    once = apply_ops(bar_spec, [FacetBy(field="region", mode="column")]).spec
    twice = apply_ops(once, [FacetBy(field="region", mode="row")]).spec
    encoding = twice.primary_view.encoding
    assert "row" in encoding and "column" not in encoding


def test_set_mark_keeps_a_bare_string_when_there_are_no_properties(line_spec: Spec) -> None:
    result = apply_ops(line_spec, [SetMark(mark="area")])
    assert result.spec.primary_view.node["mark"] == "area"


def test_set_mark_preserves_existing_mark_properties(bar_spec: Spec) -> None:
    result = apply_ops(bar_spec, [SetMark(mark="area")])
    mark = result.spec.primary_view.node["mark"]
    assert mark["type"] == "area"
    assert mark["cornerRadius"] == 3


def test_set_title_with_empty_text_removes_it(bar_spec: Spec) -> None:
    titled = apply_ops(bar_spec, [SetTitle(text="hello")]).spec
    cleared = apply_ops(titled, [SetTitle(text="")]).spec
    assert "title" not in cleared.raw


def test_apply_config_deep_merges(bar_spec: Spec) -> None:
    once = apply_ops(bar_spec, [ApplyConfig(config={"axis": {"grid": True, "domain": False}})]).spec
    twice = apply_ops(once, [ApplyConfig(config={"axis": {"grid": False}})]).spec
    assert twice.raw["config"]["axis"] == {"grid": False, "domain": False}


def test_stack_mode_skips_marks_that_do_not_stack(line_spec: Spec) -> None:
    result = apply_ops(line_spec, [StackMode(mode="normalize")])
    assert not result.changed


# ---------------------------------------------------------------------------
# error handling and parsing
# ---------------------------------------------------------------------------

def test_a_failing_op_does_not_discard_the_others(bar_spec: Spec) -> None:
    result = apply_ops(
        bar_spec,
        [
            SetTitle(text="kept"),
            DropSeries(index=9),  # no layers — this one fails
            SetSize(width=800),
        ],
    )
    assert result.applied == ["set_title", "set_size"]
    assert [name for name, _ in result.failed] == ["drop_series"]
    assert result.spec.raw["title"] == "kept"
    assert result.spec.raw["width"] == 800


def test_strict_mode_raises_instead_of_skipping(bar_spec: Spec) -> None:
    with pytest.raises(OpError):
        apply_ops(bar_spec, [DropSeries(index=9)], strict=True)


def test_parse_ops_accepts_llm_shaped_dicts() -> None:
    parsed = parse_ops(
        {"ops": [{"op": "set_palette", "scheme": "tealblues"}, {"op": "set_size", "width": 500}]}
    )
    assert [op.op for op in parsed] == ["set_palette", "set_size"]


def test_parse_ops_rejects_an_unknown_op() -> None:
    with pytest.raises(OpError, match="unknown op"):
        parse_ops([{"op": "make_it_pretty"}])


def test_out_of_range_view_index_is_reported(bar_spec: Spec) -> None:
    result = apply_ops(bar_spec, [SetPalette(color="#fff", view=7)])
    assert result.failed and "out of range" in result.failed[0][1]


def test_root_level_ops_reject_a_view_argument() -> None:
    """`set_title` is document-level; accepting `view` would be a lie."""
    with pytest.raises(ValidationError):
        SetTitle(text="x", view=0)


def test_op_union_serializes_to_one_json_schema() -> None:
    """This schema is what a structured-output call targets, so it must build."""
    schema = OpList.model_json_schema()
    assert "ops" in schema["properties"]
    assert len(schema["$defs"]) >= len(OP_REGISTRY)


def test_describe_renders_a_human_change_summary(bar_spec: Spec) -> None:
    result = apply_ops(bar_spec, [SetTitle(text="Revenue")])
    assert any("title" in line for line in result.describe())
