"""Validation tiers, including the data-binding fuzz the design calls for."""
from __future__ import annotations

import pytest

from nexcraftviz.data.profile import profile_rows
from nexcraftviz.spec.model import Spec
from nexcraftviz.spec.validate import is_structurally_valid, repair_data_binding, validate

# ---------------------------------------------------------------------------
# tier 1
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ({}, False),
        ({"mark": "bar"}, True),
        ({"layer": []}, True),
        ({"hconcat": []}, True),
        ({"columns": [{"field": "a", "renderer": "pill"}]}, True),
        ({"columns": []}, False),
        ({"kpi_metadata": {"chart_subtype": "counter"}}, True),
        ({"kpi_metadata": {}}, False),
        ({"title": "no content"}, False),
    ],
)
def test_structural_predicate_matches_the_upstream_one(raw, expected) -> None:
    assert is_structurally_valid(raw) is expected


def test_empty_spec_fails_at_tier_one() -> None:
    _, report = validate(Spec({}))
    assert not report.ok and report.tier_failed == 1


def test_repeat_composition_is_rejected_with_a_reason() -> None:
    _, report = validate(Spec({"repeat": ["a", "b"], "spec": {"mark": "bar"}}))
    assert not report.ok
    assert report.errors[0].code == "unsupported_composition"


# ---------------------------------------------------------------------------
# tier 2 — the one that matters
# ---------------------------------------------------------------------------

def test_unknown_field_is_caught_with_a_suggestion(rows) -> None:
    spec = Spec(
        {
            "data": {"values": rows},
            "mark": "bar",
            "encoding": {"y": {"field": "revenu", "type": "quantitative"}},
        }
    )
    _, report = validate(spec, max_tier=2)
    assert not report.ok and report.tier_failed == 2
    issue = report.errors[0]
    assert issue.code == "unknown_field"
    assert issue.suggestion == "revenue"


def test_type_mismatch_is_caught(rows) -> None:
    spec = Spec(
        {
            "data": {"values": rows},
            "mark": "bar",
            "encoding": {"x": {"field": "region", "type": "quantitative"}},
        }
    )
    _, report = validate(spec, max_tier=2)
    assert report.errors[0].code == "type_mismatch"


def test_a_numeric_field_may_legitimately_be_encoded_as_nominal(rows) -> None:
    """Permissiveness is deliberate — a year used as a category is fine."""
    spec = Spec(
        {
            "data": {"values": rows},
            "mark": "bar",
            "encoding": {"x": {"field": "orders", "type": "nominal"}},
        }
    )
    _, report = validate(spec, max_tier=2)
    assert report.ok


def test_count_aggregate_does_not_trigger_a_type_mismatch(rows) -> None:
    spec = Spec(
        {
            "data": {"values": rows},
            "mark": "bar",
            "encoding": {
                "x": {"field": "region", "type": "nominal"},
                "y": {"field": "region", "aggregate": "count", "type": "quantitative"},
            },
        }
    )
    _, report = validate(spec, max_tier=2)
    assert report.ok


def test_transform_produced_fields_are_in_scope(rows) -> None:
    """A calculate on a layer creates a field the layer may then encode.

    This is the gauge/radial pattern; validating encodings against the root
    data columns alone would reject every one of them.
    """
    spec = Spec(
        {
            "data": {"values": rows},
            "layer": [
                {
                    "transform": [{"calculate": "datum.revenue * 2", "as": "doubled"}],
                    "mark": "bar",
                    "encoding": {"y": {"field": "doubled", "type": "quantitative"}},
                }
            ],
        }
    )
    _, report = validate(spec, max_tier=2)
    assert report.ok, report.summary()


def test_a_field_produced_in_one_layer_is_not_visible_in_a_sibling(rows) -> None:
    spec = Spec(
        {
            "data": {"values": rows},
            "layer": [
                {
                    "transform": [{"calculate": "datum.revenue * 2", "as": "doubled"}],
                    "mark": "bar",
                    "encoding": {"y": {"field": "doubled", "type": "quantitative"}},
                },
                {"mark": "line", "encoding": {"y": {"field": "doubled", "type": "quantitative"}}},
            ],
        }
    )
    _, report = validate(spec, max_tier=2)
    assert not report.ok
    assert report.errors[0].path.startswith("layer[1]")


def test_a_view_local_data_block_replaces_the_inherited_columns() -> None:
    spec = Spec(
        {
            "data": {"values": [{"a": 1}]},
            "layer": [
                {
                    "data": {"values": [{"b": 2}]},
                    "mark": "bar",
                    "encoding": {"y": {"field": "b", "type": "quantitative"}},
                }
            ],
        }
    )
    _, report = validate(spec, max_tier=2)
    assert report.ok


def test_fold_with_an_explicit_as_does_not_also_grant_key_and_value(rows) -> None:
    spec = Spec(
        {
            "data": {"values": rows},
            "transform": [{"fold": ["revenue", "orders"], "as": ["metric", "amount"]}],
            "mark": "bar",
            "encoding": {"y": {"field": "value", "type": "quantitative"}},
        }
    )
    _, report = validate(spec, max_tier=2)
    assert not report.ok, "`value` is not produced when `as` renames the fold outputs"


def test_unknown_sort_field_is_caught(rows) -> None:
    spec = Spec(
        {
            "data": {"values": rows},
            "mark": "bar",
            "encoding": {
                "y": {"field": "region", "type": "nominal", "sort": {"field": "nope"}}
            },
        }
    )
    _, report = validate(spec, max_tier=2)
    assert report.errors[0].code == "unknown_sort_field"


def test_no_data_is_a_warning_not_a_failure() -> None:
    spec = Spec({"mark": "bar", "encoding": {"y": {"field": "whatever"}}})
    _, report = validate(spec, max_tier=2)
    assert report.ok
    assert report.warnings[0].code == "no_data"


# ---------------------------------------------------------------------------
# fuzz — mutate a known-good spec and assert tier 2 catches it every time
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "broken",
    [
        "revenu", "Revenue_", "revenue_total", "total_revenue", "rev", "REVENUE_X",
        "revenue ", " revenue", "revenue.amount", "sales",
    ],
)
def test_every_field_mutation_is_caught(bar_spec: Spec, broken: str) -> None:
    mutated = bar_spec.clone()
    mutated.primary_view.encoding["x"]["field"] = broken

    _, report = validate(mutated, max_tier=2)
    assert not report.ok, f"tier 2 missed a mutated field name: {broken!r}"
    assert report.errors[0].field_name == broken


# ---------------------------------------------------------------------------
# repair
# ---------------------------------------------------------------------------

def test_repair_fixes_a_near_miss_field_name(rows) -> None:
    spec = Spec(
        {
            "data": {"values": rows},
            "mark": "bar",
            "encoding": {"x": {"field": "Revenue", "type": "quantitative"}},
        }
    )
    fixed, report = validate(spec, max_tier=2, repair=True)
    assert report.ok
    assert fixed.primary_view.encoding["x"]["field"] == "revenue"
    assert report.repaired and "→ 'revenue'" in report.repaired[0]


def test_repair_fixes_an_incompatible_declared_type(rows) -> None:
    spec = Spec(
        {
            "data": {"values": rows},
            "mark": "bar",
            "encoding": {"x": {"field": "region", "type": "quantitative"}},
        }
    )
    fixed, report = validate(spec, max_tier=2, repair=True)
    assert report.ok
    assert fixed.primary_view.encoding["x"]["type"] == "nominal"


def test_repair_refuses_a_weak_match(rows) -> None:
    """A bad guess is worse than an honest failure."""
    spec = Spec(
        {
            "data": {"values": rows},
            "mark": "bar",
            "encoding": {"x": {"field": "quarterly_bookings", "type": "quantitative"}},
        }
    )
    _, report = validate(spec, max_tier=2, repair=True)
    assert not report.ok
    assert not report.repaired


def test_repair_does_not_mutate_the_input(bar_spec: Spec, rows) -> None:
    broken = bar_spec.clone()
    broken.primary_view.encoding["x"]["field"] = "Revenue"
    before = broken.hash

    repair_data_binding(broken, profile_rows(rows))
    assert broken.hash == before


# ---------------------------------------------------------------------------
# tier 3 and family handling
# ---------------------------------------------------------------------------

def test_kpi_and_table_families_stop_after_tier_one(kpi_spec: Spec, table_spec: Spec) -> None:
    for spec in (kpi_spec, table_spec):
        _, report = validate(spec)
        assert report.ok
        assert report.skipped_tiers == [2, 3]


def test_a_schema_violation_that_still_compiles_is_a_warning(bar_spec: Spec) -> None:
    """Vega-Lite ignores unknown properties, so this renders — but it is wrong."""
    spec = bar_spec.clone()
    spec.primary_view.encoding["x"]["axis"] = {"labelAngel": 45}

    _, report = validate(spec, max_tier=3)
    assert report.ok
    assert any(i.code == "schema_violation" for i in report.warnings)


def test_strict_schema_promotes_the_violation_to_an_error(bar_spec: Spec) -> None:
    spec = bar_spec.clone()
    spec.primary_view.encoding["x"]["axis"] = {"labelAngel": 45}

    _, report = validate(spec, max_tier=3, strict_schema=True)
    assert not report.ok and report.tier_failed == 3


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param({"transform": [{"filter": "datum."}]}, id="malformed-filter-expression"),
        pytest.param({"transform": [{"calculate": "1 +* 2", "as": "x"}]}, id="malformed-calculate"),
        pytest.param({"mark": "banana"}, id="unknown-mark"),
    ],
)
def test_a_spec_that_cannot_compile_fails_tier_three(rows, broken) -> None:
    spec = Spec(
        {
            "data": {"values": rows},
            "mark": "bar",
            "encoding": {"x": {"field": "revenue", "type": "quantitative"}},
            **broken,
        }
    )
    _, report = validate(spec, max_tier=3)
    assert not report.ok and report.tier_failed == 3
    assert report.errors[0].code == "compile_failed"


def test_the_compiler_is_lenient_which_is_why_the_schema_check_exists(rows) -> None:
    """`scale: "nonsense"` is not valid Vega-Lite, yet it compiles and renders.

    Vega-Lite drops what it does not understand. That is the whole argument for
    running JSON Schema alongside the compile — and for reporting its findings
    as warnings, since the chart does render, just not as written.
    """
    spec = Spec(
        {
            "data": {"values": rows},
            "mark": "bar",
            "encoding": {"x": {"field": "revenue", "type": "quantitative", "scale": "nonsense"}},
        }
    )
    _, report = validate(spec, max_tier=3)
    assert report.ok
    assert any(i.code == "schema_violation" for i in report.warnings)


def test_max_tier_stops_early(bar_spec: Spec) -> None:
    _, report = validate(bar_spec, max_tier=1)
    assert report.tier_reached == 1


def test_report_serialises(bar_spec: Spec) -> None:
    _, report = validate(bar_spec)
    payload = report.to_dict()
    assert payload["ok"] is True
    assert payload["tier_reached"] == 3


def test_an_aggregate_prefixed_sort_field_is_repaired_to_its_column():
    """Found live: a grouped bar sorted by `total_findings` over data with
    `findings`. The retry was told, suggestion and all, and returned the same
    name."""
    from nexcraftviz.spec.model import Spec

    rows = [
        {"business_unit": "Retail", "severity": "High", "findings": 12},
        {"business_unit": "Ops", "severity": "Low", "findings": 3},
    ]
    spec = Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": rows},
        "mark": "bar",
        "encoding": {
            "x": {"field": "business_unit", "type": "nominal",
                  "sort": {"field": "total_findings", "order": "descending"}},
            "y": {"field": "findings", "type": "quantitative", "aggregate": "sum"},
            "xOffset": {"field": "severity", "type": "nominal"},
        },
    })
    fixed, report = validate(spec, data=rows, max_tier=2, repair=True)
    assert report.ok, report.summary()
    assert fixed.raw["encoding"]["x"]["sort"] == {
        "field": "findings", "order": "descending", "op": "sum",
    }
    assert any("total_findings" in note for note in report.repaired)


def test_an_aggregate_prefixed_encoding_field_becomes_that_aggregate():
    """Also seen live: `mean_days_open` on the x axis over `days_open`."""
    from nexcraftviz.spec.model import Spec

    rows = [{"owner": "Priya", "days_open": 12}, {"owner": "Sam", "days_open": 30}]
    spec = Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": rows},
        "mark": "bar",
        "encoding": {"y": {"field": "owner", "type": "nominal"},
                     "x": {"field": "mean_days_open", "type": "quantitative"}},
    })
    fixed, report = validate(spec, data=rows, max_tier=2, repair=True)
    assert report.ok, report.summary()
    assert fixed.raw["encoding"]["x"]["field"] == "days_open"
    assert fixed.raw["encoding"]["x"]["aggregate"] == "mean"


def test_a_prefix_over_no_real_column_is_not_guessed():
    from nexcraftviz.spec.model import Spec

    rows = [{"owner": "Priya", "findings": 12}]
    spec = Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": rows},
        "mark": "bar",
        "encoding": {"y": {"field": "owner", "type": "nominal"},
                     "x": {"field": "total_widgets", "type": "quantitative"}},
    })
    _, report = validate(spec, data=rows, max_tier=2, repair=True)
    assert not report.ok
    assert report.errors[0].code == "unknown_field"


@pytest.mark.parametrize("transform", [
    {"window": [{"op": "sum", "field": "findings", "as": "unit_total"}],
     "groupby": ["business_unit"]},
    {"joinaggregate": [{"op": "sum", "field": "findings", "as": "unit_total"}],
     "groupby": ["business_unit"]},
])
def test_a_field_a_transform_creates_inside_its_entries_is_known(transform):
    """Found live: widget_two_dimensions sorted by `unit_total`, which its own
    `window` created, and failed `validates` twice — first attempt and retry."""
    from nexcraftviz.spec.model import Spec

    rows = [
        {"business_unit": "Retail", "severity": "High", "findings": 12},
        {"business_unit": "Ops", "severity": "Low", "findings": 3},
    ]
    spec = Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": rows},
        "transform": [transform],
        "mark": "bar",
        "encoding": {
            "x": {"field": "business_unit", "type": "nominal",
                  "sort": {"field": "unit_total", "order": "descending"}},
            "y": {"field": "findings", "type": "quantitative", "aggregate": "sum"},
        },
    })
    _, report = validate(spec, data=rows, max_tier=2)
    assert report.ok, report.summary()
