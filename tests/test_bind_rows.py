"""A generated chart draws the rows it was given — never values the model wrote.

Found live: a trend spec arrived with its own `data.values`, Feb and Mar
figures that were not in the data. Rows were bound only when the spec had none,
so the invented ones were kept; only the critic noticed, and it cost the retry.
"""
from __future__ import annotations

import json
from typing import Any

from nexcraftviz.skills import REGISTRY

ROWS = [
    {"month": "2026-01-01", "revenue": 120.5},
    {"month": "2026-02-01", "revenue": 141.25},
    {"month": "2026-03-01", "revenue": 133.0},
]
INVENTED = [
    {"month": "2026-01-01", "revenue": 120.5},
    {"month": "2026-02-01", "revenue": 150.0},
    {"month": "2026-03-01", "revenue": 128.0},
]
LINE = {"mark": "line", "encoding": {"x": {"field": "month", "type": "temporal"},
                                     "y": {"field": "revenue", "type": "quantitative"}}}


def _generate(spec: dict[str, Any]):
    skill = REGISTRY["viz.generate"]
    output = skill.parse({"spec_json": json.dumps(spec), "chart_type": "line", "reasoning": ""})
    return skill.apply(skill.coerce_input({"question": "revenue over time", "rows": ROWS}),
                       output)


def test_values_the_model_wrote_are_replaced_by_the_rows_given():
    result = _generate({"$schema": "https://vega.github.io/schema/vega-lite/v5.json",
                        "data": {"values": INVENTED}, **LINE})
    assert result.value is not None
    assert result.value.data_values == ROWS
    assert any("not the 3 the model wrote" in change for change in result.changes)


def test_an_invented_copy_inside_a_layer_is_dropped_so_it_inherits_the_rows():
    result = _generate({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "layer": [{**LINE, "data": {"values": INVENTED}},
                  {"mark": "point", "encoding": LINE["encoding"]}],
    })
    assert result.value is not None
    assert "data" not in result.value.raw["layer"][0]
    assert result.value.data_values == ROWS


def test_a_one_row_constant_in_a_layer_is_a_design_choice_and_stays():
    threshold = {"data": {"values": [{"revenue": 150}]}, "mark": "rule",
                 "encoding": {"y": {"field": "revenue", "type": "quantitative"}}}
    result = _generate({"$schema": "https://vega.github.io/schema/vega-lite/v5.json",
                        "layer": [LINE, threshold]})
    assert result.value is not None
    assert result.value.raw["layer"][1]["data"] == {"values": [{"revenue": 150}]}


def test_a_spec_with_no_data_still_gets_the_rows():
    result = _generate({"$schema": "https://vega.github.io/schema/vega-lite/v5.json", **LINE})
    assert result.value is not None and result.value.data_values == ROWS
    assert not any("model wrote" in change for change in result.changes)
