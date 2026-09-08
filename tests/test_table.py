"""Rich tables: the contract, the builder's renderer choices, and HTML output."""
from __future__ import annotations

import json

import pytest

from nexcraftviz.corpus.loader import seed
from nexcraftviz.render.html import render_kpi, render_table
from nexcraftviz.spec.validate import validate
from nexcraftviz.table import (
    RENDERERS,
    Column,
    KpiCard,
    TableSpec,
    build_table,
    sample_rows,
    with_sample_rows,
)

CORPUS = seed()


@pytest.fixture
def entity_rows() -> list[dict]:
    """One row per entity, with a column for every renderer worth exercising."""
    return [
        {"owner": "Ada Lovelace", "role": "Engineer", "completion_pct": 92.0,
         "readiness_score": 88, "status": "On track", "tier": "P1",
         "vs_target": 4.2, "due": "2026-03-01", "trend": [3, 5, 4, 7, 9, 11],
         "employee_id": 1001, "assignments": 1240},
        {"owner": "Grace Hopper", "role": "Manager", "completion_pct": 61.0,
         "readiness_score": 54, "status": "At risk", "tier": "P2",
         "vs_target": -1.8, "due": "2026-03-14", "trend": [8, 7, 7, 5, 6, 4],
         "employee_id": 1002, "assignments": 980},
        {"owner": "Alan Turing", "role": "Analyst", "completion_pct": 34.0,
         "readiness_score": 31, "status": "Blocked", "tier": "P1",
         "vs_target": 0, "due": "2026-04-02", "trend": [2, 2, 3, 2, 1, 1],
         "employee_id": 1003, "assignments": 2310},
    ]


# ---------------------------------------------------------------------------
# renderer selection
# ---------------------------------------------------------------------------

def test_builder_picks_a_renderer_per_column(entity_rows) -> None:
    chosen = {c.field: c.render for c in build_table(entity_rows).columns}
    assert chosen == {
        "owner": "avatar_name",
        "role": "text",
        "completion_pct": "progress_bar",
        "readiness_score": "heatmap_cell",
        "status": "pill",
        "tier": "badge",
        "vs_target": "trend_arrow",
        "due": "date",
        "trend": "sparkline",
        "employee_id": "number",
        "assignments": "number",
    }


def test_status_values_get_a_tone_map_without_configuration(entity_rows) -> None:
    status = next(c for c in build_table(entity_rows).columns if c.field == "status")
    assert status.color_map == {"On track": "pass", "At risk": "fix", "Blocked": "fail"}


def test_an_avatar_column_picks_up_a_subtitle(entity_rows) -> None:
    owner = next(c for c in build_table(entity_rows).columns if c.field == "owner")
    assert owner.subtitle_field == "role"


def test_an_identifier_number_is_muted_and_unformatted(entity_rows) -> None:
    ident = next(c for c in build_table(entity_rows).columns if c.field == "employee_id")
    assert ident.muted is True and ident.format == ""


def test_a_large_count_gets_thousands_separators(entity_rows) -> None:
    counts = next(c for c in build_table(entity_rows).columns if c.field == "assignments")
    assert counts.format == ","


def test_job_titles_do_not_become_badges(entity_rows) -> None:
    """An earlier rule ("short and unspaced") turned `Engineer` into a badge."""
    role = next(c for c in build_table(entity_rows).columns if c.field == "role")
    assert role.render == "text"


def test_a_percent_named_column_outside_0_100_is_not_a_progress_bar() -> None:
    rows = [{"completion_rate": 4200.0}, {"completion_rate": 5100.0}]
    column = build_table(rows).columns[0]
    assert column.render == "number"


def test_a_delta_column_without_negatives_stays_a_number() -> None:
    rows = [{"growth": 4.0}, {"growth": 7.5}, {"growth": 2.0}]
    assert build_table(rows).columns[0].render == "number"


def test_a_high_cardinality_status_column_is_not_a_pill() -> None:
    """A pill on 400 distinct free-text values produces 400 chips."""
    rows = [{"status": f"note {i}"} for i in range(40)]
    assert build_table(rows).columns[0].render != "pill"


def test_max_columns_is_respected() -> None:
    rows = [{f"c{i}": i for i in range(30)}]
    assert len(build_table(rows, max_columns=5).columns) == 5


def test_empty_rows_produce_an_empty_table() -> None:
    table = build_table([])
    assert table.columns == [] and table.rows == []


def test_headers_are_humanised(entity_rows) -> None:
    headers = {c.field: c.header for c in build_table(entity_rows).columns}
    assert headers["completion_pct"] == "Completion pct"


# ---------------------------------------------------------------------------
# the contract
# ---------------------------------------------------------------------------

def test_round_trips_through_a_spec(entity_rows) -> None:
    table = build_table(entity_rows, title="Owners")
    spec = table.to_spec()

    assert spec.family == "table"
    restored = TableSpec.from_spec(spec)
    assert [c.render for c in restored.columns] == [c.render for c in table.columns]
    assert restored.rows == table.rows


def test_a_built_table_passes_validation(entity_rows) -> None:
    _, report = validate(build_table(entity_rows).to_spec())
    assert report.ok
    assert report.skipped_tiers == [2, 3]


def test_renderer_and_title_are_accepted_as_aliases() -> None:
    """Both spellings exist in the wild; rejecting either would be pedantry."""
    table = TableSpec.from_columns_schema(
        [{"field": "a", "title": "A", "renderer": "pill"}]
    )
    assert table.columns[0].render == "pill" and table.columns[0].header == "A"


def test_every_corpus_table_pair_parses() -> None:
    pairs = CORPUS.by_family("table-with-cells")
    assert len(pairs) == 30
    for pair in pairs:
        table = TableSpec.from_columns_schema(pair.columns_schema)
        assert table.columns, pair.name
        for column in table.columns:
            assert column.render in RENDERERS, f"{pair.name}: {column.render}"


def test_every_corpus_kpi_pair_parses() -> None:
    pairs = CORPUS.by_family("kpi-card")
    assert len(pairs) == 22
    for pair in pairs:
        kpi = KpiCard.from_columns_schema(pair.columns_schema)
        assert kpi.label, pair.name


def test_kpi_sentiment_separates_direction_from_meaning() -> None:
    """Churn falling is good news with a down arrow."""
    revenue = KpiCard(label="Revenue", value=10, change_pct=4.0, change_direction="up")
    churn = KpiCard(
        label="Churn", value=3, change_pct=2.0, change_direction="down", invert_sentiment=True
    )
    rising_churn = churn.model_copy(update={"change_direction": "up"})

    assert revenue.sentiment == "positive"
    assert churn.sentiment == "positive"
    assert rising_churn.sentiment == "negative"


# ---------------------------------------------------------------------------
# sample rows
# ---------------------------------------------------------------------------

def test_sample_rows_exercise_every_renderer() -> None:
    columns = [Column(field=r, header=r, render=r) for r in RENDERERS]
    rows = sample_rows(columns, count=5)

    assert len(rows) == 5
    assert all(len(row) == len(RENDERERS) for row in rows)
    assert isinstance(rows[0]["sparkline"], list)
    # trend_arrow must produce up, down and flat so all three styles appear.
    directions = {
        "up" if v > 0 else "down" if v < 0 else "flat"
        for v in (row["trend_arrow"] for row in rows)
    }
    assert directions == {"up", "down", "flat"}


def test_sample_rows_sweep_a_scaled_range() -> None:
    column = Column(field="p", header="P", render="progress_bar", min=0, max=100)
    values = [row["p"] for row in sample_rows([column], count=5)]
    assert values == sorted(values)
    assert values[0] < 20 and values[-1] > 80


def test_sample_rows_are_deterministic() -> None:
    columns = [Column(field="p", header="P", render="number")]
    assert sample_rows(columns, count=4) == sample_rows(columns, count=4)


def test_with_sample_rows_leaves_real_rows_alone() -> None:
    table = TableSpec(
        columns=[Column(field="a", header="A", render="text")], rows=[{"a": "real"}]
    )
    assert with_sample_rows(table).rows == [{"a": "real"}]


def test_every_corpus_table_pair_can_be_populated_and_rendered() -> None:
    """The 30 table pairs ship no rows, so nothing could show them before."""
    for pair in CORPUS.by_family("table-with-cells"):
        table = with_sample_rows(TableSpec.from_columns_schema(pair.columns_schema))
        assert table.rows, pair.name
        html = render_table(table)
        assert "<table" in html and "nxv-cell--" in html


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def test_html_uses_the_themed_class_vocabulary(entity_rows) -> None:
    html = render_table(build_table(entity_rows))
    for expected in (
        "nxv-cell--avatar-name", "nxv-cell--progress-bar", "nxv-cell--pill",
        "nxv-cell--sparkline", "nxv-cell--badge", "nxv-cell--trend-arrow",
        "nxv-cell--heatmap-cell", "nxv-cell--date", "nxv-cell--number",
    ):
        assert expected in html, f"missing {expected}"


def test_progress_bar_width_reflects_the_value(entity_rows) -> None:
    html = render_table(build_table(entity_rows))
    assert "width:92.0%" in html and "width:34.0%" in html


def test_progress_bar_tone_follows_the_value(entity_rows) -> None:
    html = render_table(build_table(entity_rows))
    assert "nxv-fill--pass" in html and "nxv-fill--fail" in html


def test_trend_arrow_direction_and_zero(entity_rows) -> None:
    html = render_table(build_table(entity_rows))
    assert "is-up" in html and "is-down" in html and "is-flat" in html


def test_heatmap_intensity_is_emitted_as_a_variable(entity_rows) -> None:
    assert "--nxv-heat:" in render_table(build_table(entity_rows))


def test_null_cells_render_a_dash() -> None:
    table = TableSpec(
        columns=[Column(field="a", header="A", render="number")], rows=[{"a": None}]
    )
    assert "—" in render_table(table)


def test_html_escapes_hostile_values() -> None:
    table = TableSpec(
        columns=[Column(field="a", header="A", render="text")],
        rows=[{"a": "<script>alert(1)</script>"}],
    )
    html = render_table(table)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_row_cap_is_reported() -> None:
    table = TableSpec(
        columns=[Column(field="a", header="A", render="number")],
        rows=[{"a": i} for i in range(100)],
    )
    html = render_table(table, max_rows=10)
    assert "Showing 10 of 100 rows" in html


def test_a_sparkline_with_too_few_points_degrades() -> None:
    table = TableSpec(
        columns=[Column(field="s", header="S", render="sparkline")], rows=[{"s": [1]}]
    )
    assert "<svg" not in render_table(table)


def test_kpi_html_carries_subtype_and_delta() -> None:
    html = render_kpi(
        KpiCard(label="Revenue", value=1240, chart_subtype="percent_change",
                change_pct=4.2, change_direction="up")
    )
    assert "nxv-kpi--percent-change" in html
    assert "nxv-kpi__delta--up" in html
    assert "1,240" in html


def test_kpi_target_renders_a_track() -> None:
    html = render_kpi(KpiCard(label="Completion", value=87.4, unit="%", target=90.0))
    assert "nxv-kpi__track" in html and "nxv-kpi__fill--under" in html


def test_kpi_accepts_a_raw_spec() -> None:
    spec = KpiCard(label="Users", value=10).to_spec()
    assert "Users" in render_kpi(spec)


def test_corpus_kpi_payloads_render() -> None:
    for pair in CORPUS.by_family("kpi-card"):
        kpi = KpiCard.from_columns_schema(pair.columns_schema)
        assert "nxv-kpi__value" in render_kpi(kpi)


def test_a_signed_format_keeps_the_sign_so_the_arrow_agrees() -> None:
    """Formatting the absolute value under `+.1f` produced "▼ +3.1"."""
    from nexcraftviz.render.html import render_cell

    column = Column(field="d", header="D", render="trend_arrow", format="+.1f")
    assert "is-down" in render_cell(column, {"d": -3.1})
    assert "-3.1" in render_cell(column, {"d": -3.1})
    assert "+4.2" in render_cell(column, {"d": 4.2})


def test_an_unsigned_format_leaves_the_sign_to_the_arrow() -> None:
    from nexcraftviz.render.html import render_cell

    column = Column(field="d", header="D", render="trend_arrow", format=".1f")
    html = render_cell(column, {"d": -3.1})
    assert "is-down" in html and "3.1" in html and "-3.1" not in html


def test_a_percent_scaled_progress_bar_labels_its_unit() -> None:
    """A bare "64" against a 0-100 scale is ambiguous."""
    from nexcraftviz.render.html import render_cell

    column = Column(field="p", header="P", render="progress_bar", max=100)
    assert "64%" in render_cell(column, {"p": 64})


def test_number_formats_are_applied() -> None:
    table = TableSpec(
        columns=[
            Column(field="a", header="A", render="number", format=","),
            Column(field="b", header="B", render="trend_arrow", format="+.1f%"),
        ],
        rows=[{"a": 1234567, "b": 4.25}],
    )
    html = render_table(table)
    assert "1,234,567" in html
    assert "4.2%" in html


def test_columns_schema_json_text_is_accepted() -> None:
    schema = json.dumps([{"field": "a", "header": "A", "render": "pill"}])
    assert TableSpec.from_columns_schema(schema).columns[0].render == "pill"
