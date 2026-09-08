"""Rules-based recommendation, and the generated playground pages."""
from __future__ import annotations

from pathlib import Path

import pytest

from nexcraftviz.data.profile import profile_rows
from nexcraftviz.gallery import build_gallery, build_use_case, use_case_rows, write
from nexcraftviz.recommend.rules import recommend


def _top(rows, question: str = "") -> str:
    return recommend(profile_rows(rows), question=question).best.chart_type


# ---------------------------------------------------------------------------
# shape → chart
# ---------------------------------------------------------------------------

def test_time_plus_measure_is_a_trend() -> None:
    rows = [{"month": f"2026-{m:02d}-01", "revenue": m * 10} for m in range(1, 13)]
    assert _top(rows) == "line"


def test_time_plus_dimension_is_several_series() -> None:
    rows = [
        {"month": f"2026-{m:02d}-01", "region": region, "revenue": m * 10}
        for m in range(1, 7)
        for region in ("West", "East")
    ]
    assert _top(rows) == "multi_line"


def test_dimension_plus_measure_is_a_bar() -> None:
    rows = [{"region": r, "revenue": 10} for r in ("West", "East", "North", "South")]
    assert _top(rows) == "bar"


def test_a_single_row_of_numbers_is_a_kpi() -> None:
    assert _top([{"total": 12480}]) == "kpi"


def test_one_measure_over_many_rows_is_a_distribution() -> None:
    assert _top([{"score": i} for i in range(40)]) == "histogram"


def test_two_measures_and_no_dimension_is_a_scatter() -> None:
    rows = [{"height": i, "weight": i * 2} for i in range(30)]
    assert _top(rows) == "scatter"


def test_two_wide_dimensions_prefer_a_matrix() -> None:
    rows = [
        {"team": f"T{t}", "week": f"W{w}", "hours": t + w}
        for t in range(10)
        for w in range(6)
    ]
    assert _top(rows) == "heatmap"


def test_a_per_row_series_column_belongs_in_a_rich_table() -> None:
    rows = [{"team": f"T{i}", "trend": [1, 2, 3, 4]} for i in range(6)]
    assert _top(rows) == "table_with_cells"


# ---------------------------------------------------------------------------
# the time-axis distinction
# ---------------------------------------------------------------------------

def test_a_per_row_date_attribute_is_not_a_time_axis() -> None:
    """`next_audit` describes each row; it is not something to plot against.

    Treating any temporal column as an axis recommended a line chart for a
    result set that contains no series at all.
    """
    rows = [
        {"business_unit": unit, "completion_pct": pct, "next_audit": due}
        for unit, pct, due in (
            ("Retail", 52.0, "2026-03-14"),
            ("Clinical", 92.3, "2026-03-06"),
            ("Logistics", 38.0, "2026-03-23"),
            ("Technology", 94.0, "2026-03-07"),
        )
    ]
    profile = profile_rows(rows)
    assert profile.times, "the column is still temporal"
    assert profile.time_axis is None, "but it is an attribute, not an axis"
    assert _top(rows) == "bar"


def test_repeated_timestamps_are_an_axis() -> None:
    rows = [
        {"month": f"2026-{m:02d}-01", "region": r, "revenue": 1}
        for m in (1, 2, 3)
        for r in ("West", "East")
    ]
    assert profile_rows(rows).time_axis.name == "month"


def test_one_row_per_timestamp_is_still_an_axis_when_nothing_else_identifies_it() -> None:
    rows = [{"month": f"2026-{m:02d}-01", "revenue": m} for m in range(1, 8)]
    assert profile_rows(rows).time_axis.name == "month"


# ---------------------------------------------------------------------------
# question hints
# ---------------------------------------------------------------------------

def test_a_question_nudges_the_ranking() -> None:
    rows = [{"stage": s, "count": 10} for s in ("Applied", "Screened", "Offer")]
    plain = recommend(profile_rows(rows))
    asked = recommend(
        profile_rows(rows), question="what is the share of each stage?"
    )

    plain_scores = {r.chart_type: r.score for r in plain}
    asked_scores = {r.chart_type: r.score for r in asked}
    assert asked_scores["donut"] > plain_scores["donut"]


def test_a_question_cannot_conjure_a_shape_that_is_not_there() -> None:
    """Asking for a trend over data with no time column must not produce a line."""
    rows = [{"region": r, "revenue": 10} for r in ("West", "East", "North")]
    result = recommend(profile_rows(rows), question="show the trend over time")
    types = {r.chart_type for r in result}
    assert "line" not in types and "multi_line" not in types


def test_recommendations_are_deduped_and_ordered() -> None:
    rows = [{"month": f"2026-{m:02d}-01", "a": m, "b": m} for m in range(1, 9)]
    result = recommend(profile_rows(rows))
    types = [r.chart_type for r in result]

    assert len(types) == len(set(types))
    assert [r.score for r in result] == sorted((r.score for r in result), reverse=True)


def test_there_is_always_a_recommendation() -> None:
    assert recommend(profile_rows([{"a": "x"}])).best is not None


def test_every_recommendation_explains_itself() -> None:
    rows = [{"region": r, "revenue": 10} for r in ("West", "East", "North")]
    assert all(r.reason for r in recommend(profile_rows(rows)))


# ---------------------------------------------------------------------------
# generated pages
# ---------------------------------------------------------------------------

def test_use_case_rows_exercise_every_column_role() -> None:
    profile = profile_rows(use_case_rows())
    roles = {c.role for c in profile.columns}
    assert {"dimension", "measure", "series", "time"} <= roles


def test_use_case_page_is_complete() -> None:
    html = build_use_case()
    # the table comes first, before any chart mount
    assert html.index("nxv-table") < html.index("__nxvSpecs")
    markers = ("nxv-step", "nxv-cell--", "Operations the model emits", "What actually changed")
    for marker in markers:
        assert marker in html, f"missing {marker}"


def test_use_case_edits_all_apply() -> None:
    """A silently-skipped op would leave the page showing an unchanged chart."""
    from nexcraftviz.gallery import USE_CASE_EDITS, _use_case_chart
    from nexcraftviz.spec.ops import apply_ops

    spec = _use_case_chart(use_case_rows())
    for instruction, ops in USE_CASE_EDITS:
        result = apply_ops(spec, ops)
        assert not result.failed, f"{instruction}: {result.failed}"
        assert result.changed, f"{instruction} changed nothing"
        spec = result.spec


def test_gallery_covers_the_whole_corpus() -> None:
    html = build_gallery()
    assert html.count("nxv-gallery__item") == 200
    # all three families are represented
    assert "nxv-kpi--" in html and "nxv-cell--" in html and "__nxvSpecs" in html


def test_gallery_labels_synthesised_rows() -> None:
    """The table pairs ship no data; the page must not imply otherwise."""
    assert "Rows synthesised" in build_gallery()


def test_widgets_page_shows_both_forms_and_the_edits() -> None:
    from nexcraftviz.gallery import build_widgets

    html = build_widgets()
    assert "nxv-group" in html                     # a grouped widget
    assert "nxv-widget__tile--compound" in html     # a compound tile
    assert "Operations" in html                     # placement edited by ops
    assert "hconcat" in html                        # and the single-spec form
    # Each rendering of the same widget must have its own mount ids.
    assert 'id="w2-tile-funnel"' in html and 'id="w3-tile-funnel"' in html


@pytest.mark.parametrize(
    "name",
    ["index.html", "usecase.html", "widgets.html", "gallery.html",
     "playground.css", "playground.js"],
)
def test_write_emits_every_asset(tmp_path: Path, name: str) -> None:
    write(tmp_path, limit=5)
    assert (tmp_path / name).stat().st_size > 0
