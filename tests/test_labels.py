"""Titles and axis names: a chart says what it shows.

Found live: every axis in a harness run carried `"title": null` — copied from a
corpus that does it 272 times — and ten of eleven specs had no title, though
every plan had written one.
"""
from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from nexcraftviz.skills import REGISTRY
from nexcraftviz.spec.labels import humanize, label_chart
from nexcraftviz.spec.model import Spec

ROWS = [
    {"owner": "Priya", "days_open": 12, "control_id": "C-1"},
    {"owner": "Sam", "days_open": 30, "control_id": "C-2"},
]

#: widget_entity_rows, as the live model wrote it.
LIVE: dict[str, Any] = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "data": {"values": ROWS},
    "mark": "bar",
    "encoding": {
        "y": {"field": "owner", "type": "nominal", "axis": {"title": None}},
        "x": {"field": "days_open", "type": "quantitative", "aggregate": "mean",
              "axis": {"title": None}},
    },
}


def _live() -> Spec:
    return Spec(copy.deepcopy(LIVE))


def test_blanked_axes_are_named_and_the_plan_title_is_used():
    spec = _live()
    notes = label_chart(spec, title="Days open by owner")
    encoding = spec.raw["encoding"]
    assert spec.raw["title"] == "Days open by owner"
    assert encoding["y"]["axis"]["title"] == "Owner"
    assert encoding["x"]["axis"]["title"] == "Average days open"
    assert len(notes) == 3


def test_titles_the_model_chose_are_kept():
    spec = _live()
    spec.raw["title"] = "Who is slowest"
    spec.raw["encoding"]["y"]["axis"]["title"] = "Control owner"
    del spec.raw["encoding"]["x"]["axis"]
    spec.raw["encoding"]["x"]["title"] = "Days"
    assert label_chart(spec, title="From the plan") == []
    assert spec.raw["title"] == "Who is slowest"
    assert spec.raw["encoding"]["y"]["axis"]["title"] == "Control owner"
    assert spec.raw["encoding"]["x"]["title"] == "Days"


def test_a_null_axis_title_beats_an_encoding_title_so_it_is_the_one_filled():
    """`axis.title` overrides the encoding's `title` whenever the key is present
    — filling the encoding's instead would leave the axis blank."""
    spec = _live()
    spec.raw["encoding"]["x"]["title"] = ""
    label_chart(spec)
    assert spec.raw["encoding"]["x"]["axis"]["title"] == "Average days open"


def test_an_axis_switched_off_stays_off():
    spec = _live()
    spec.raw["encoding"]["x"]["axis"] = None
    label_chart(spec)
    assert spec.raw["encoding"]["x"]["axis"] is None
    assert "title" not in spec.raw["encoding"]["x"]


def test_literal_channels_get_no_axis_name():
    spec = Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": [{"total": 47}]},
        "mark": "text",
        "encoding": {"x": {"value": 100}, "y": {"value": 40},
                     "text": {"field": "total", "type": "quantitative"}},
    })
    assert label_chart(spec) == []


def test_no_plan_means_no_invented_title():
    spec = _live()
    label_chart(spec)
    assert "title" not in spec.raw


@pytest.mark.parametrize(("field", "expected"), [
    ("completion_pct", "Completion %"),
    ("days_open", "Days open"),
    ("control_id", "Control ID"),
    ("totalRevenue", "Total revenue"),
    ("region", "Region"),
])
def test_field_names_read_as_words(field, expected):
    assert humanize(field) == expected


def test_the_generate_skill_labels_what_it_builds():
    skill = REGISTRY["viz.generate"]
    output = skill.parse({"spec_json": json.dumps(LIVE), "chart_type": "bar", "reasoning": ""})
    plan = {"metadata": {"title": "Days open by owner"}}
    result = skill.apply(
        skill.coerce_input({"question": "who has controls open longest", "rows": ROWS,
                            "plan": plan}),
        output,
    )
    assert result.value is not None, result.failed
    assert result.value.raw["title"] == "Days open by owner"
    assert result.value.raw["encoding"]["x"]["axis"]["title"] == "Average days open"
    assert any("named the x axis" in change for change in result.changes)
