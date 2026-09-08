"""Data profiling — column typing, roles and shape signatures."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from nexcraftviz.data.profile import profile_rows


def test_empty_input_profiles_cleanly() -> None:
    profile = profile_rows([])
    assert profile.row_count == 0 and profile.columns == []
    assert profile.shape_signature() == "r1"


def test_basic_typing(rows) -> None:
    profile = profile_rows(rows)
    types = {c.name: c.vega_type for c in profile.columns}
    assert types == {
        "region": "nominal",
        "month": "temporal",
        "revenue": "quantitative",
        "orders": "quantitative",
        "active": "nominal",
    }


def test_booleans_are_nominal_not_quantitative(rows) -> None:
    """Vega-Lite will happily average a bool, which is never the intent."""
    assert profile_rows(rows).get("active").vega_type == "nominal"


def test_roles_drive_chart_choice(rows) -> None:
    profile = profile_rows(rows)
    assert [c.name for c in profile.times] == ["month"]
    assert [c.name for c in profile.measures] == ["revenue", "orders"]
    assert [c.name for c in profile.dimensions] == ["region", "active"]


def test_a_unique_numeric_key_is_an_identifier_not_a_measure() -> None:
    rows = [{"employee_id": 100 + i, "score": i} for i in range(10)]
    profile = profile_rows(rows)
    assert profile.get("employee_id").role == "identifier"
    assert profile.get("score").role == "measure"


def test_a_measure_named_like_a_count_stays_a_measure() -> None:
    rows = [{"order_count": i} for i in range(10)]
    assert profile_rows(rows).get("order_count").role == "measure"


def test_a_unique_low_cardinality_label_is_a_dimension_not_an_identifier() -> None:
    """Every `GROUP BY region` result has one row per region.

    Treating uniqueness alone as an identifier lost the dimension on the single
    most common chart shape there is, and recommended a list instead of a bar.
    """
    rows = [{"region": name, "revenue": 1} for name in ("West", "East", "North", "South")]
    assert profile_rows(rows).get("region").role == "dimension"


def test_a_unique_id_named_column_is_still_an_identifier() -> None:
    rows = [{"order_code": f"ORD-{i}", "n": 1} for i in range(10)]
    assert profile_rows(rows).get("order_code").role == "identifier"


def test_a_unique_high_cardinality_column_is_an_identifier() -> None:
    """No categorical axis can show 60 labels, so it is not a dimension."""
    rows = [{"label": f"row-{i}", "n": 1} for i in range(60)]
    assert profile_rows(rows).get("label").role == "identifier"


def test_a_list_valued_column_is_a_series() -> None:
    rows = [{"name": "a", "trend": [1, 2, 3]}, {"name": "b", "trend": [4, 5, 6]}]
    assert profile_rows(rows).get("trend").role == "series"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-01-01", "temporal"),
        ("2026-01-01T10:30:00", "temporal"),
        ("2026-01-01T10:30:00Z", "temporal"),
        ("2025-Q1", "nominal"),
        ("January", "nominal"),
        ("West", "nominal"),
    ],
)
def test_string_temporal_detection(value: str, expected: str) -> None:
    """Quarter labels must NOT profile as temporal.

    Encoding `"2025-Q1"` as temporal is the bug tier 2 catches in the shipped
    corpus: Vega-Lite cannot parse it and renders an Invalid Date axis.
    """
    rows = [{"col": value} for _ in range(4)]
    assert profile_rows(rows).get("col").vega_type == expected


def test_a_single_parseable_value_does_not_make_a_column_temporal() -> None:
    rows = [{"note": "2026-01-01"}, {"note": "free text"}, {"note": "more text"}]
    assert profile_rows(rows).get("note").vega_type == "nominal"


def test_real_datetimes_are_temporal() -> None:
    rows = [{"d": date(2026, 1, 1), "ts": datetime(2026, 1, 1, 10, 0)}]
    profile = profile_rows(rows)
    assert profile.get("d").vega_type == "temporal"
    assert profile.get("ts").vega_type == "temporal"


def test_nulls_are_counted_not_typed(rows) -> None:
    rows.append({"region": None, "month": None, "revenue": None, "orders": None, "active": None})
    profile = profile_rows(rows)
    assert profile.get("revenue").null_count == 1
    assert profile.get("revenue").vega_type == "quantitative"
    assert 0 < profile.get("revenue").null_rate < 1


def test_ragged_rows_union_their_columns() -> None:
    profile = profile_rows([{"a": 1}, {"b": 2}, {"a": 3, "c": 4}])
    assert set(profile.column_names) == {"a", "b", "c"}


def test_cardinality_buckets() -> None:
    assert profile_rows([{"x": 1}] * 5).get("x").cardinality_bucket == "constant"
    assert profile_rows([{"x": i} for i in range(5)]).get("x").cardinality_bucket == "few"
    assert profile_rows([{"x": i} for i in range(20)]).get("x").cardinality_bucket == "moderate"
    assert profile_rows([{"x": i} for i in range(50)]).get("x").cardinality_bucket == "many"


def test_shape_signature_is_stable_and_discriminating(rows) -> None:
    first = profile_rows(rows).shape_signature()
    assert profile_rows(list(rows)).shape_signature() == first
    # A different shape must not collide.
    other = profile_rows([{"only": 1.0}]).shape_signature()
    assert other != first


def test_min_max_and_monotonicity() -> None:
    profile = profile_rows([{"n": i} for i in range(10)])
    column = profile.get("n")
    assert (column.min_value, column.max_value) == (0, 9)
    assert column.monotonic is True


def test_mixed_type_columns_do_not_crash() -> None:
    profile = profile_rows([{"x": 1}, {"x": "text"}, {"x": None}])
    assert profile.get("x").vega_type == "nominal"


def test_sampling_is_bounded_but_row_count_is_not() -> None:
    profile = profile_rows([{"n": i} for i in range(1200)], sample_limit=100)
    assert profile.row_count == 1200
    assert profile.truncated is True


def test_prompt_dict_is_compact(rows) -> None:
    payload = profile_rows(rows).to_prompt_dict()
    assert set(payload) == {"row_count", "truncated", "shape_signature", "columns"}
    # Raw values must not be dumped wholesale into a prompt.
    assert all(len(c.get("examples", [])) <= 5 for c in payload["columns"])
