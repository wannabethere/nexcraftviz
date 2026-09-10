"""A KPI card's numbers come from the data; the generator names the columns.

Found in the live report: every KPI was a bare counter. The corpus has 22 KPI
cards and none carries a Vega spec, so the generator was shown none of them and
improvised a text mark — which could only ever be read back as a number.
"""
from __future__ import annotations

import json
from typing import Any

from nexcraftviz.evaluate.gates import fields_read
from nexcraftviz.render.html import render_kpi
from nexcraftviz.skills import REGISTRY
from nexcraftviz.spec.kpi import bind_kpi
from nexcraftviz.spec.model import Spec

QUARTER = [{"revenue_this_quarter": 447.0, "revenue_last_quarter": 402.0}]


def _kpi(**meta: Any) -> Spec:
    return Spec({"kpi_metadata": {"chart_type": "metric_kpi", "chart_subtype": "counter",
                                  "label": "Revenue", **meta}})


def test_value_change_and_target_are_read_from_the_data():
    spec = _kpi(value_field="now", prior_field="then", target_field="goal")
    notes, error = bind_kpi(spec, [{"now": 447.0, "then": 402.0, "goal": 500.0}])
    assert error == "", error
    meta = spec.raw["kpi_metadata"]
    assert (meta["value"], meta["target"]) == (447.0, 500.0)
    assert (meta["change_pct"], meta["change_direction"]) == (11.2, "up")
    assert len(notes) == 3


def test_a_fall_points_down():
    spec = _kpi(value_field="now", prior_field="then")
    bind_kpi(spec, [{"now": 90, "then": 100}])
    assert (spec.raw["kpi_metadata"]["change_pct"],
            spec.raw["kpi_metadata"]["change_direction"]) == (10.0, "down")


def test_a_typed_number_that_is_not_in_the_data_is_refused():
    _, error = bind_kpi(_kpi(value=474), [{"total_revenue": 447.0}])
    assert "value_field" in error and "total_revenue" in error


def test_a_literal_number_that_is_in_the_data_is_bound_to_its_column():
    """What the deterministic builder and API callers send."""
    spec = _kpi(value=447)
    _, error = bind_kpi(spec, [{"total_revenue": 447.0}])
    assert error == ""
    assert spec.raw["kpi_metadata"]["value_field"] == "total_revenue"


def test_a_column_that_is_not_there_is_refused():
    _, error = bind_kpi(_kpi(value_field="revenue"), [{"total_revenue": 447.0}])
    assert "'revenue'" in error and "total_revenue" in error


def test_a_percentage_gets_its_percent_sign():
    spec = _kpi(chart_subtype="percentage", value_field="coverage_pct")
    bind_kpi(spec, [{"coverage_pct": 87.4}])
    assert spec.raw["kpi_metadata"]["unit"] == "%"


def test_matches_plan_sees_the_columns_a_kpi_reads():
    assert fields_read(_kpi(value_field="now", prior_field="then")) == {"now", "then"}


def _generate(payload: dict[str, Any], rows: list[dict[str, Any]]):
    skill = REGISTRY["viz.generate"]
    output = skill.parse({"spec_json": json.dumps(payload), "chart_type": "kpi",
                          "reasoning": ""})
    return skill.apply(skill.coerce_input({"question": "q", "rows": rows}), output)


def test_the_generator_delivers_a_rich_card_not_a_vega_spec():
    result = _generate({"kpi_metadata": {
        "chart_type": "metric_kpi", "chart_subtype": "counter", "label": "Revenue",
        "value_field": "revenue_this_quarter", "prior_field": "revenue_last_quarter"}}, QUARTER)
    assert result.value is not None and not result.failed, result.failed
    assert result.value.family == "kpi"
    assert "$schema" not in result.value.raw and "data" not in result.value.raw
    html = render_kpi(result.value)
    assert "nxv-kpi__delta--up" in html and "11.2%" in html


def test_a_kpi_naming_no_real_column_fails_so_it_is_regenerated():
    result = _generate({"kpi_metadata": {"chart_subtype": "counter",
                                         "value_field": "revenue"}}, QUARTER)
    assert result.value is None
    assert "not a column" in result.failed[0][1]


def test_a_kpi_plan_is_shown_the_corpus_kpi_cards():
    skill = REGISTRY["viz.generate"]
    inputs = skill.coerce_input({
        "question": "What is total revenue?", "rows": [{"total_revenue": 447.0}],
        "plan": {"chart_type": "kpi", "metadata": {"title": "Total revenue"}},
    })
    payload = json.loads(skill.user_payload(inputs))
    examples = payload["examples"]
    assert examples, "no KPI examples reached the generator"
    assert all("value_field" in e["kpi_metadata"] for e in examples)
    assert len({e["kpi_metadata"]["chart_subtype"] for e in examples}) == len(examples)
    assert "WHEN THE PLAN IS A KPI" in payload["instruction"]


def test_a_described_chart_type_becomes_its_name():
    """Live: widget_ranked_comparison came back as "bar (horizontal, sorted)"."""
    from nexcraftviz.skills.create import chart_type_name

    assert chart_type_name("bar (horizontal, sorted)") == "bar"
    assert chart_type_name("Horizontal bar chart", {"chart_type": "bar"}) == "bar"
    assert chart_type_name("grouped bar", {"chart_type": "grouped_bar"}) == "grouped_bar"
    assert chart_type_name("", {"chart_type": "kpi"}) == "kpi"
    assert chart_type_name("line") == "line"
