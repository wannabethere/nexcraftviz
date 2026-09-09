"""Deterministic chart construction — rows in, a correct spec out, no model."""
from __future__ import annotations

import pytest

from nexcraftviz.recommend import BUILDABLE, BuildError, build_best, build_chart
from nexcraftviz.render import available as render_available
from nexcraftviz.render import to_png
from nexcraftviz.spec.validate import validate

CATEGORICAL = [{"region": r, "revenue": v} for r, v in
               (("West", 128), ("East", 96), ("North", 152), ("South", 71))]
TIMESERIES = [{"month": f"2026-{m:02d}-01", "revenue": 100 + m * 7} for m in range(1, 10)]
MULTI_SERIES = [{"month": f"2026-{m:02d}-01", "region": r, "revenue": 100 + m * 5 + i * 30}
                for m in range(1, 7) for i, r in enumerate(("West", "East"))]
TWO_MEASURES = [{"height": i, "weight": i * 2 + 3} for i in range(25)]
ONE_MEASURE = [{"score": i % 17} for i in range(60)]
TWO_DIMS = [{"team": f"T{t}", "week": f"W{w}", "hours": t + w}
            for t in range(9) for w in range(6)]
MEASURES_PER_CATEGORY = [{"region": r, "revenue": v, "orders": o} for r, v, o in
                         (("West", 128, 41), ("East", 96, 33), ("North", 152, 48))]
SINGLE_VALUE = [{"total_revenue": 12480}]


def _fixture_for(chart_type: str):
    return {
        "bar": CATEGORICAL, "donut": CATEGORICAL, "pie": CATEGORICAL,
        "line": TIMESERIES, "area": TIMESERIES,
        "multi_line": MULTI_SERIES,
        "grouped_bar": TWO_DIMS, "stacked_bar": TWO_DIMS, "heatmap": TWO_DIMS,
        "scatter": TWO_MEASURES, "bubble": TWO_MEASURES,
        "histogram": ONE_MEASURE, "boxplot": ONE_MEASURE,
        "kpi": SINGLE_VALUE,
    }[chart_type]


# ---------------------------------------------------------------------------
# every buildable type produces a working chart
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("chart_type", BUILDABLE)
def test_every_buildable_type_validates(chart_type: str) -> None:
    rows = _fixture_for(chart_type)
    spec = build_chart(rows, chart_type=chart_type)
    _, report = validate(spec, data=rows, max_tier=3)
    assert report.ok, f"{chart_type}: {report.summary()}"


@pytest.mark.skipif(not render_available(), reason="needs the `render` extra")
@pytest.mark.parametrize("chart_type", [t for t in BUILDABLE if t != "kpi"])
def test_every_buildable_type_actually_draws(chart_type: str) -> None:
    """Validating is not drawing — only a render proves the chart exists."""
    png = to_png(build_chart(_fixture_for(chart_type), chart_type=chart_type))
    assert len(png) > 2000, f"{chart_type} rendered almost nothing"


@pytest.mark.parametrize(
    "rows,expected",
    [
        (CATEGORICAL, "bar"),
        (TIMESERIES, "line"),
        (MULTI_SERIES, "multi_line"),
        (TWO_MEASURES, "scatter"),
        (ONE_MEASURE, "histogram"),
        (TWO_DIMS, "heatmap"),
    ],
)
def test_build_best_picks_the_shape_the_rules_recommend(rows, expected) -> None:
    _, built = build_best(rows)
    assert built == expected


# ---------------------------------------------------------------------------
# the decisions that matter
# ---------------------------------------------------------------------------

def test_a_rate_is_averaged_not_summed() -> None:
    """Adding four regions' completion percentages gives a meaningless 340%."""
    rows = [{"unit": u, "completion_pct": p} for u, p in (("A", 92.0), ("B", 61.0))]
    spec = build_chart(rows, chart_type="bar")
    encoding = spec.primary_view.encoding
    measure_channel = (
        encoding["x"] if encoding["x"].get("field") == "completion_pct" else encoding["y"]
    )
    assert measure_channel["aggregate"] == "mean"


def test_a_count_is_summed() -> None:
    spec = build_chart(CATEGORICAL, chart_type="bar")
    encoding = spec.primary_view.encoding
    measure_channel = encoding["y"] if encoding["y"].get("field") == "revenue" else encoding["x"]
    assert measure_channel["aggregate"] == "sum"


def test_many_categories_go_horizontal() -> None:
    """Vertical bar labels collide past a handful of categories."""
    rows = [{"account": f"Customer number {i}", "value": i} for i in range(12)]
    encoding = build_chart(rows, chart_type="bar").primary_view.encoding
    assert encoding["y"]["field"] == "account", "the category belongs on y when there are many"


def test_few_categories_stay_vertical() -> None:
    encoding = build_chart(CATEGORICAL, chart_type="bar").primary_view.encoding
    assert encoding["x"]["field"] == "region"


def test_bars_are_sorted_by_the_measure() -> None:
    encoding = build_chart(CATEGORICAL, chart_type="bar").primary_view.encoding
    assert encoding["x"]["sort"] == "-y"


def test_quarter_labels_are_never_temporal() -> None:
    """The defect already in the shipped corpus, which this cannot reproduce."""
    rows = [{"quarter": f"2026-Q{q}", "nps": 60 + q} for q in range(1, 5)]
    spec, _ = build_best(rows)
    for _, _, definition in spec.encodings():
        assert definition.get("type") != "temporal", definition


def test_a_per_row_date_is_not_used_as_an_axis() -> None:
    rows = [{"unit": u, "completion_pct": p, "next_audit": d} for u, p, d in
            (("A", 92.0, "2026-03-01"), ("B", 61.0, "2026-03-14"), ("C", 38.0, "2026-04-02"))]
    spec, built = build_best(rows)
    assert built == "bar"
    assert "next_audit" not in {f for _, _, f in spec.field_refs()}


def test_several_measures_are_folded_into_a_series() -> None:
    """One dimension and two measures fits neither a plain bar nor a
    two-dimension grouped bar; the measures have to become the series."""
    spec, built = build_best(MEASURES_PER_CATEGORY, question="revenue and orders by region")
    assert built == "grouped_bar"
    assert spec.raw["transform"][0]["fold"] == ["revenue", "orders"]
    assert spec.primary_view.encoding["color"]["field"] == "measure"


def test_the_reported_type_is_the_type_actually_built() -> None:
    """An earlier version reported grouped_bar and silently built a plain bar."""
    for rows in (CATEGORICAL, TIMESERIES, MULTI_SERIES, TWO_DIMS, MEASURES_PER_CATEGORY):
        spec, built = build_best(rows)
        if built == "grouped_bar":
            assert "xOffset" in spec.primary_view.encoding
        if built == "heatmap":
            assert spec.primary_view.mark_type == "rect"
        if built in ("line", "multi_line"):
            assert "line" in str(spec.primary_view.node["mark"])


def test_no_styling_is_baked_in() -> None:
    """A hard-coded colour silently overrides whatever theme is applied later."""
    for chart_type in (t for t in BUILDABLE if t != "kpi"):
        spec = build_chart(_fixture_for(chart_type), chart_type=chart_type)
        mark = spec.primary_view.node.get("mark")
        if isinstance(mark, dict):
            assert "color" not in mark and "fill" not in mark, chart_type
        assert "config" not in spec.raw


def test_rows_are_embedded_so_the_chart_has_data() -> None:
    spec = build_chart(CATEGORICAL, chart_type="bar")
    assert spec.data_values == CATEGORICAL


def test_a_title_is_carried_through() -> None:
    spec = build_chart(CATEGORICAL, chart_type="bar", title="Revenue")
    assert spec.raw["title"] == "Revenue"


def test_the_question_narrows_which_measure_is_charted() -> None:
    """"revenue by region" over data that also has `orders` should chart
    revenue. Without this the rules see two measures and reasonably build a
    grouped bar answering a question nobody asked."""
    spec, built = build_best(MEASURES_PER_CATEGORY, question="revenue by region")
    fields = {f for _, _, f in spec.field_refs()}

    assert built == "bar"
    assert fields == {"region", "revenue"}


def test_naming_both_measures_charts_both() -> None:
    spec, built = build_best(MEASURES_PER_CATEGORY, question="revenue and orders by region")
    assert built == "grouped_bar"


def test_no_question_charts_everything() -> None:
    _, built = build_best(MEASURES_PER_CATEGORY)
    assert built == "grouped_bar"


def test_narrowing_keeps_the_dimension_it_is_grouped_by() -> None:
    """Naming one measure must not also discard the dimension."""
    spec, _ = build_best(MEASURES_PER_CATEGORY, question="revenue")
    assert "region" in {f for _, _, f in spec.field_refs()}


def test_matching_is_on_word_boundaries() -> None:
    """`order` must not match "reorder"."""
    from nexcraftviz.data.profile import profile_rows
    from nexcraftviz.recommend.build import columns_mentioned

    profile = profile_rows(MEASURES_PER_CATEGORY)
    assert columns_mentioned("reorder the chart", profile) == []
    assert "orders" in columns_mentioned("show orders", profile)


def test_humanised_names_are_matched() -> None:
    from nexcraftviz.data.profile import profile_rows
    from nexcraftviz.recommend.build import columns_mentioned

    rows = [{"completion_pct": 92.0, "unit": "A"}]
    assert "completion_pct" in columns_mentioned("completion pct by unit", profile_rows(rows))


# ---------------------------------------------------------------------------
# honest failure
# ---------------------------------------------------------------------------

def test_no_rows_is_an_error_not_an_empty_chart() -> None:
    with pytest.raises(BuildError, match="no rows"):
        build_chart([])


def test_an_unbuildable_type_says_what_is_available() -> None:
    with pytest.raises(BuildError, match="available"):
        build_chart(CATEGORICAL, chart_type="sankey")


def test_a_scatter_without_two_measures_is_refused() -> None:
    with pytest.raises(BuildError, match="two measures"):
        build_chart(CATEGORICAL, chart_type="scatter")


def test_data_with_no_measure_is_refused() -> None:
    with pytest.raises(BuildError):
        build_best([{"a": "x"}, {"a": "y"}])


def test_a_single_row_becomes_a_kpi_not_a_one_bar_chart() -> None:
    """"What is my total?" is the most basic question there is; it should not
    fail just because a single number is not a Vega chart."""
    spec, built = build_best(SINGLE_VALUE)
    assert built == "kpi"
    assert spec.family == "kpi"
    assert spec.raw["kpi_metadata"]["value"] == 12480


def test_a_percentage_kpi_carries_its_unit() -> None:
    spec = build_chart([{"completion_pct": 87.4}], chart_type="kpi")
    assert spec.raw["kpi_metadata"]["unit"] == "%"


def test_build_best_falls_past_types_it_cannot_construct() -> None:
    """The recommender knows types with no builder — funnel, table_with_cells."""
    spec, built = build_best(TWO_DIMS)
    assert built in BUILDABLE and spec


# ---------------------------------------------------------------------------
# wired into the rest
# ---------------------------------------------------------------------------

async def test_a_session_with_no_model_still_produces_a_chart() -> None:
    from nexcraftviz.agent import Session

    session = Session(rows=CATEGORICAL)
    turn = await session.turn("show me revenue by region")

    assert turn.ok
    assert session.has_chart
    assert any("no model" in w for w in turn.warnings), "must say where the chart came from"


async def test_a_session_with_no_model_still_reports_unbuildable_data() -> None:
    from nexcraftviz.agent import Session

    session = Session(rows=[{"a": "x"}])
    turn = await session.turn("chart this")
    assert not turn.ok


async def test_editing_still_needs_a_model() -> None:
    """Only creation has a deterministic path; editing is a judgement."""
    from nexcraftviz.agent import Session

    session = Session(rows=CATEGORICAL)
    await session.turn("show revenue by region")
    with pytest.raises(ValueError, match="propose"):
        await session.turn("sort it descending")


def test_the_agent_tool_builds_without_a_model() -> None:
    from nexcraftviz.integrations.tools import call

    result = call("viz_build_chart", {"rows": CATEGORICAL, "question": "revenue by region"})
    assert result["valid"] and result["chart_type"] == "bar"


def test_the_agent_tool_reports_an_error_rather_than_raising() -> None:
    from nexcraftviz.integrations.tools import call

    assert "error" in call("viz_build_chart", {"rows": []})
