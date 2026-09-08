"""Combining charts: native Vega composition, widgets, and placement editing."""
from __future__ import annotations

import pytest

from nexcraftviz.compose import (
    WIDGET_OP_REGISTRY,
    ComposeError,
    Group,
    Stat,
    Widget,
    WidgetOpError,
    WidgetOpList,
    apply_widget_ops,
    arrange,
    auto_layout,
    concat,
    group,
    layer,
    small_multiples,
    tile,
    widget,
)
from nexcraftviz.compose.vega import DEFAULT_SUBVIEW_WIDTH
from nexcraftviz.examples import (
    completion_gauge,
    completion_rate_tile,
    hiring_funnel,
    sourcing_donut,
    talent_acquisition_widget,
    time_to_hire,
)
from nexcraftviz.render import available as render_available
from nexcraftviz.render import to_png
from nexcraftviz.spec.diff import apply_patch
from nexcraftviz.spec.model import Spec
from nexcraftviz.spec.validate import validate
from nexcraftviz.table import KpiCard, build_table

ROWS = [
    {"region": "West", "quarter": "2026-Q1", "revenue": 128, "orders": 41},
    {"region": "East", "quarter": "2026-Q1", "revenue": 96, "orders": 33},
    {"region": "West", "quarter": "2026-Q2", "revenue": 141, "orders": 45},
    {"region": "East", "quarter": "2026-Q2", "revenue": 88, "orders": 29},
]


@pytest.fixture
def bar() -> Spec:
    return Spec({
        "data": {"values": ROWS}, "width": "container", "mark": "bar",
        "encoding": {"x": {"field": "region", "type": "nominal"},
                     "y": {"field": "revenue", "type": "quantitative"}},
    })


@pytest.fixture
def line() -> Spec:
    return Spec({
        "data": {"values": ROWS}, "width": "container", "mark": "line",
        "encoding": {"x": {"field": "quarter", "type": "ordinal"},
                     "y": {"field": "orders", "type": "quantitative"}},
    })


# ---------------------------------------------------------------------------
# native Vega composition
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "direction,operator",
    [("vertical", "vconcat"), ("horizontal", "hconcat"), ("wrap", "concat")],
)
def test_concat_uses_the_right_operator(bar, line, direction, operator) -> None:
    composed = concat([bar, line], direction=direction)
    assert operator in composed.raw


def test_concat_result_compiles(bar, line) -> None:
    _, report = validate(concat([bar, line], direction="horizontal"), max_tier=3)
    assert report.ok, report.summary()


def test_concat_hoists_shared_data(bar, line) -> None:
    """N panels over one result set should not embed the rows N times."""
    composed = concat([bar, line])
    assert composed.raw["data"]["values"] == ROWS
    assert all("data" not in view for view in composed.raw["vconcat"])


def test_concat_keeps_data_per_view_when_it_differs(bar) -> None:
    other = Spec({
        "data": {"values": [{"a": 1}]}, "mark": "bar",
        "encoding": {"x": {"field": "a", "type": "quantitative"}},
    })
    composed = concat([bar, other])
    assert "data" not in composed.raw
    assert all("data" in view for view in composed.raw["vconcat"])


def test_concat_replaces_container_sizing(bar, line) -> None:
    """`width: container` is a top-level feature; inside a concat it silently
    falls back to a default, so the panels come out a size nobody chose."""
    composed = concat([bar, line])
    assert all(view["width"] == DEFAULT_SUBVIEW_WIDTH for view in composed.raw["vconcat"])


def test_concat_strips_root_only_properties(bar) -> None:
    styled = bar.clone()
    styled.raw["config"] = {"axis": {"grid": False}}
    styled.raw["background"] = "#fff"

    composed = concat([styled, styled.clone()])
    assert all("config" not in v and "background" not in v for v in composed.raw["vconcat"])


def test_concat_shared_scales_must_be_asked_for(bar, line) -> None:
    assert "resolve" not in concat([bar, line]).raw
    shared = concat([bar, line], resolve_scales="shared")
    assert shared.raw["resolve"]["scale"]["y"] == "shared"


def test_concat_of_one_spec_is_that_spec(bar) -> None:
    assert "vconcat" not in concat([bar]).raw


def test_concat_rejects_an_empty_list() -> None:
    with pytest.raises(ComposeError, match="at least one"):
        concat([])


def test_concat_refuses_a_non_vega_payload(bar) -> None:
    """This is the whole reason widgets exist — say so, rather than emitting a
    spec that renders a blank panel."""
    kpi = KpiCard(label="Total", value=10).to_spec()
    with pytest.raises(ComposeError, match="compose.widget"):
        concat([bar, kpi])

    table = build_table(ROWS).to_spec()
    with pytest.raises(ComposeError, match="compose.widget"):
        concat([bar, table])


def test_layer_makes_a_dual_axis(bar, line) -> None:
    composed = layer([bar, line], axis="y")
    assert composed.raw["resolve"]["scale"]["y"] == "independent"
    _, report = validate(composed, max_tier=3)
    assert report.ok, report.summary()


def test_layer_hoists_size_to_the_layer(bar, line) -> None:
    """A width on a layer member is ignored, which reads as a silent bug."""
    composed = layer([bar, line])
    assert "width" in composed.raw
    assert all("width" not in view for view in composed.raw["layer"])


def test_layer_requires_shared_data(bar) -> None:
    other = Spec({
        "data": {"values": [{"a": 1}]}, "mark": "line",
        "encoding": {"x": {"field": "a", "type": "quantitative"}},
    })
    with pytest.raises(ComposeError, match="share their data"):
        layer([bar, other])


def test_layer_needs_two_specs(bar) -> None:
    with pytest.raises(ComposeError, match="at least two"):
        layer([bar])


def test_small_multiples_uses_the_column_channel(bar) -> None:
    """The `facet` operator form validates and compiles, then fails to appear
    in the frontend renderer — so encoding-level faceting it is."""
    faceted = small_multiples(bar, "quarter", columns=2)
    assert faceted.raw["encoding"]["column"]["field"] == "quarter"
    assert "facet" not in faceted.raw
    assert faceted.raw["width"] != "container"


def test_small_multiples_refuses_a_composed_spec(bar, line) -> None:
    with pytest.raises(ComposeError, match="single view"):
        small_multiples(concat([bar, line]), "quarter")


# ---------------------------------------------------------------------------
# widgets
# ---------------------------------------------------------------------------

def test_a_widget_can_mix_families_that_a_concat_cannot(bar) -> None:
    composed = widget(
        tile(KpiCard(label="Revenue", value=453), title="Revenue"),
        tile(bar, title="By region"),
        tile(build_table(ROWS), title="Detail"),
        title="Review",
    )
    assert composed.is_mixed_family
    assert set(composed.family_counts()) == {"kpi", "vega-lite", "table"}


def test_tiles_flattens_groups(bar) -> None:
    composed = widget(
        group(tile(bar, id="a"), tile(bar, id="b"), id="g", title="G"),
        tile(bar, id="c"),
    )
    assert [t.id for t in composed.tiles] == ["a", "b", "c"]
    assert [n.id for n in composed.nodes] == ["g", "c"]


def test_find_and_parent_reach_into_groups(bar) -> None:
    composed = widget(group(tile(bar, id="a"), id="g", title="G"))
    assert composed.find("a").id == "a"
    assert composed.parent_of("a").id == "g"
    assert composed.find("missing") is None


@pytest.mark.parametrize(
    "payload,expected",
    [
        (None, "unknown"),
        ("kpi", "kpi"),
        ("stats", "stats"),
    ],
)
def test_payload_less_tiles_still_have_a_family(payload, expected) -> None:
    """A stat box beside a hero chart is a real tile, not an unknown one."""
    if payload == "kpi":
        entry = tile(headline=KpiCard(label="x", value=1), title="t")
    elif payload == "stats":
        entry = tile(stats=[Stat(label="a", value=1)], title="t")
    else:
        entry = tile(title="t")
    assert entry.family == expected


def test_auto_layout_puts_kpis_in_a_row(bar) -> None:
    nodes = [
        tile(KpiCard(label="a", value=1), id="k1"),
        tile(KpiCard(label="b", value=2), id="k2"),
        tile(bar, id="c1"),
        tile(bar, id="c2"),
    ]
    assert auto_layout(nodes) == "kpi_row_plus_grid"


def test_auto_layout_stacks_when_a_group_carries_the_structure(bar) -> None:
    assert auto_layout([group(tile(bar, id="a"), id="g", title="G")]) == "single_column"


def test_arrange_assigns_spans_but_respects_explicit_ones(bar) -> None:
    nodes = [
        tile(KpiCard(label="a", value=1), id="k"),
        tile(bar, id="c"),
        tile(bar, id="wide", span="full"),
    ]
    spans = {n.id: n.span for n in arrange(nodes, "kpi_row_plus_grid")}
    assert spans["k"] == "quarter"
    assert spans["c"] == "half"
    assert spans["wide"] == "full", "an explicit span outranks the layout"


def test_arrange_does_not_mutate_the_input(bar) -> None:
    nodes = [tile(bar, id="c")]
    arrange(nodes, "two_column_grid")
    assert nodes[0].span == "auto"


def test_a_compound_tile_is_not_sized_like_a_bare_kpi() -> None:
    """A headline over a chart over a stat strip needs room; a number does not."""
    plain = tile(KpiCard(label="a", value=1), id="k")
    compound = completion_rate_tile().model_copy(update={"span": "auto"})

    assert arrange([plain], "two_column_grid")[0].span == "quarter"
    assert arrange([compound], "two_column_grid")[0].span != "quarter"


def test_widget_round_trips_through_a_dict(bar) -> None:
    original = widget(
        group(tile(bar, id="a", title="A", span="half"), id="g", title="G"),
        tile(KpiCard(label="k", value=3), id="k", title="K"),
        title="W",
        description="D",
    )
    restored = Widget.from_dict(original.to_dict())

    assert restored.to_dict() == original.to_dict()
    assert isinstance(restored.nodes[0], Group)


def test_widget_document_is_not_pretending_to_be_a_spec(bar) -> None:
    payload = widget(tile(bar, id="a")).to_dict()
    assert payload["kind"] == "nexcraftviz.widget"
    assert "$schema" not in payload


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def test_widget_html_carries_spans_and_groups() -> None:
    html = talent_acquisition_widget().to_html()
    assert "nxv-group" in html
    assert "nxv-span--two-thirds" in html and "nxv-span--third" in html
    assert "nxv-grid--12" in html


def test_widget_html_never_stacks_two_grid_definitions() -> None:
    """`.nxv-grid` and `.nxv-grid--12` both set grid-template-columns at equal
    specificity, so an element carrying both silently loses its spans."""
    html = talent_acquisition_widget().to_html()
    assert 'class="nxv-grid nxv-grid--12' not in html


def test_id_prefix_keeps_two_renderings_of_one_widget_apart() -> None:
    """Without it, a before/after page emits duplicate DOM ids and only the
    first copy ever gets a chart."""
    composed = talent_acquisition_widget()
    first = composed.to_html(id_prefix="a-")
    second = composed.to_html(id_prefix="b-")

    assert 'id="a-tile-funnel"' in first
    assert 'id="b-tile-funnel"' in second
    assert 'id="a-tile-funnel"' not in second


def test_compound_tile_renders_headline_chart_and_stats() -> None:
    from nexcraftviz.compose.widget import render_tile

    markup = render_tile(completion_rate_tile())
    assert "nxv-kpi__value" in markup      # headline
    assert "nxv-chart" in markup           # gauge
    assert "nxv-stats" in markup           # supporting strip
    assert "nxv-widget__tile--compound" in markup


def test_stats_render_their_tones() -> None:
    from nexcraftviz.compose.widget import render_stats

    html = render_stats([
        Stat(label="Completed", value=7542, tone="pass"),
        Stat(label="Not started", value=1452, tone="muted"),
    ])
    assert "is-pass" in html and "is-muted" in html
    assert "7,542" in html


# ---------------------------------------------------------------------------
# placement operations
# ---------------------------------------------------------------------------

@pytest.fixture
def board(bar) -> Widget:
    return widget(
        tile(bar, id="t1", title="One"),
        tile(bar, id="t2", title="Two"),
        tile(bar, id="t3", title="Three"),
        title="Board",
        layout="two_column_grid",
    )


def test_set_span(board) -> None:
    result = apply_widget_ops(board, [{"op": "set_span", "tile": "t1", "span": "full"}])
    assert result.widget.find("t1").span == "full"


def test_move_tile_before_and_after(board) -> None:
    moved = apply_widget_ops(board, [{"op": "move_tile", "tile": "t3", "before": "t1"}]).widget
    assert [n.id for n in moved.nodes] == ["t3", "t1", "t2"]

    moved = apply_widget_ops(board, [{"op": "move_tile", "tile": "t1", "after": "t3"}]).widget
    assert [n.id for n in moved.nodes] == ["t2", "t3", "t1"]


def test_group_tiles_takes_the_position_of_the_first(board) -> None:
    """Grouping should not also reorder the page."""
    result = apply_widget_ops(board, [
        {"op": "group_tiles", "tiles": ["t2", "t3"], "title": "Pair", "id": "g"}
    ])
    assert [n.id for n in result.widget.nodes] == ["t1", "g"]
    assert result.widget.find("g").tile_ids() == ["t2", "t3"]


def test_ungroup_restores_the_tiles_in_place(board) -> None:
    grouped = apply_widget_ops(board, [
        {"op": "group_tiles", "tiles": ["t1", "t2"], "title": "Pair", "id": "g"}
    ]).widget
    result = apply_widget_ops(grouped, [{"op": "ungroup_tiles", "group": "g"}])
    assert [n.id for n in result.widget.nodes] == ["t1", "t2", "t3"]


def test_move_a_tile_into_and_out_of_a_group(board) -> None:
    grouped = apply_widget_ops(board, [
        {"op": "group_tiles", "tiles": ["t1"], "title": "P", "id": "g"}
    ]).widget

    moved_in = apply_widget_ops(grouped, [
        {"op": "move_tile", "tile": "t2", "into": "g"}
    ]).widget
    assert moved_in.find("g").tile_ids() == ["t1", "t2"]
    assert moved_in.parent_of("t2").id == "g"

    moved_out = apply_widget_ops(moved_in, [
        {"op": "move_tile", "tile": "t2", "to_root": True}
    ]).widget
    assert moved_out.parent_of("t2") is None


def test_set_layout_rejects_an_unknown_name(board) -> None:
    result = apply_widget_ops(board, [{"op": "set_layout", "layout": "spiral"}])
    assert result.failed and "unknown layout" in result.failed[0][1]


def test_remove_tile(board) -> None:
    result = apply_widget_ops(board, [{"op": "remove_tile", "tile": "t2"}])
    assert [n.id for n in result.widget.nodes] == ["t1", "t3"]


@pytest.mark.parametrize(
    "ops",
    [
        [{"op": "set_span", "tile": "t1", "span": "full"}],
        [{"op": "move_tile", "tile": "t3", "before": "t1"}],
        [{"op": "group_tiles", "tiles": ["t1", "t2"], "title": "Pair", "id": "g"}],
        [{"op": "set_layout", "layout": "single_column"}],
        [{"op": "set_tile_title", "tile": "t1", "title": "Renamed"}],
        [{"op": "remove_tile", "tile": "t2"}],
        [{"op": "set_widget_title", "title": "New", "description": "d"}],
    ],
    ids=lambda ops: ops[0]["op"],
)
def test_every_placement_op_round_trips_via_its_inverse(board, ops) -> None:
    before = board.to_dict()
    result = apply_widget_ops(board, ops)

    assert not result.failed, result.failed
    assert result.changed
    assert apply_patch(result.widget.to_dict(), result.inverse).raw == before


def test_ungroup_round_trips(board) -> None:
    grouped = apply_widget_ops(board, [
        {"op": "group_tiles", "tiles": ["t1", "t2"], "title": "P", "id": "g"}
    ]).widget
    before = grouped.to_dict()
    result = apply_widget_ops(grouped, [{"op": "ungroup_tiles", "group": "g"}])
    assert apply_patch(result.widget.to_dict(), result.inverse).raw == before


def test_every_registered_op_is_covered() -> None:
    covered = {
        "set_span", "move_tile", "group_tiles", "ungroup_tiles",
        "set_layout", "set_tile_title", "remove_tile", "set_widget_title",
    }
    assert covered == set(WIDGET_OP_REGISTRY)


def test_apply_does_not_mutate_the_input(board) -> None:
    before = board.to_dict()
    apply_widget_ops(board, [{"op": "remove_tile", "tile": "t1"}])
    assert board.to_dict() == before


def test_a_bad_tile_id_does_not_discard_the_other_edits(board) -> None:
    result = apply_widget_ops(board, [
        {"op": "set_span", "tile": "t1", "span": "full"},
        {"op": "set_span", "tile": "nope", "span": "full"},
        {"op": "set_layout", "layout": "single_column"},
    ])
    assert result.applied == ["set_span", "set_layout"]
    assert [name for name, _ in result.failed] == ["set_span"]
    assert result.widget.find("t1").span == "full"


def test_a_malformed_argument_is_reported_like_any_other_failure(board) -> None:
    """Parsing per operation, not up front — one bad value should not discard
    the valid edits queued behind it."""
    result = apply_widget_ops(board, [
        {"op": "set_span", "tile": "t1", "span": "three-fifths"},
        {"op": "set_layout", "layout": "single_column"},
    ])
    assert result.applied == ["set_layout"]
    assert result.failed[0][0] == "set_span"
    assert "span" in result.failed[0][1]


def test_an_unknown_op_is_reported_not_raised(board) -> None:
    result = apply_widget_ops(board, [{"op": "make_it_pretty"}])
    assert result.failed and "unknown widget op" in result.failed[0][1]


def test_strict_mode_raises(board) -> None:
    with pytest.raises(WidgetOpError):
        apply_widget_ops(board, [{"op": "remove_tile", "tile": "nope"}], strict=True)


def test_placement_ops_serialise_to_one_schema() -> None:
    schema = WidgetOpList.model_json_schema()
    assert "ops" in schema["properties"]
    assert len(schema["$defs"]) >= len(WIDGET_OP_REGISTRY)


# ---------------------------------------------------------------------------
# the worked examples
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "spec_fn",
    [completion_gauge, hiring_funnel, sourcing_donut, time_to_hire],
    ids=lambda fn: fn.__name__,
)
def test_example_specs_are_valid(spec_fn) -> None:
    _, report = validate(spec_fn(), max_tier=3)
    assert report.ok, report.summary()


@pytest.mark.skipif(not render_available(), reason="needs the `render` extra")
@pytest.mark.parametrize(
    "spec_fn",
    [completion_gauge, hiring_funnel, sourcing_donut, time_to_hire],
    ids=lambda fn: fn.__name__,
)
def test_example_specs_actually_draw_something(spec_fn) -> None:
    """A spec can validate at tier 3 and render an empty canvas.

    The first version of the gauge did exactly that — `theta`/`theta2` as mark
    properties with the value as an encoding. Compiling is not drawing, and
    only a render catches the difference.
    """
    png = to_png(spec_fn(), scale=1)
    assert len(png) > 2000, "rendered but produced almost no pixels"


def test_the_gauge_uses_the_pattern_that_actually_draws() -> None:
    """Guards the two details that silently produce a blank chart."""
    raw = completion_gauge().raw
    assert raw["autosize"] == {"type": "none"}

    arcs = [
        view for view in raw["layer"]
        if isinstance(view.get("mark"), dict) and view["mark"].get("type") == "arc"
    ]
    assert arcs
    for arc in arcs:
        assert "startAngle" in arc["mark"], "start angle belongs on the mark"
        assert arc["encoding"]["theta"]["scale"] is None, "theta must be read as radians"


def test_the_talent_widget_matches_the_reference_layout() -> None:
    composed = talent_acquisition_widget()
    assert [n.id for n in composed.nodes] == [
        "group-pipeline", "tile-sourcing", "tile-time-to-hire"
    ]
    pipeline = composed.find("group-pipeline")
    assert isinstance(pipeline, Group)
    assert [t.span for t in pipeline.tiles] == ["two-thirds", "third"]
    assert composed.find("tile-sourcing").span == "half"
