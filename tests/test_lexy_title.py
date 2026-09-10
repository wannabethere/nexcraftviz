"""On a Lexy tile the title is the card's header, not a line inside the chart.

The labelling pass gave every chart the plan's title, and lexy_ui's tile header
already shows one — so it appeared twice. The chart API and the thread tile now
carry it beside the chart.
"""
from __future__ import annotations

import copy
from typing import Any

from nexcraftviz.agents.builtin import _tile
from nexcraftviz.app.chart_api import envelope
from nexcraftviz.spec.labels import lift_title
from nexcraftviz.spec.model import Spec

ROWS = [{"region": "North", "revenue": 152.0}, {"region": "West", "revenue": 128.0}]
RAW: dict[str, Any] = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "title": {"text": "North leads", "subtitle": "Revenue by region"},
    "data": {"values": ROWS},
    "mark": "bar",
    "encoding": {"x": {"field": "region", "type": "nominal"},
                 "y": {"field": "revenue", "type": "quantitative"}},
}


def test_the_title_travels_beside_the_chart_not_inside_it():
    spec = Spec(copy.deepcopy(RAW))
    out = envelope(spec, rows=ROWS, lift_title=True)
    assert "title" not in out["chart_schema"]
    assert (out["title"], out["subtitle"]) == ("North leads", "Revenue by region")
    assert spec.raw["title"]["text"] == "North leads"  # the session's copy is untouched


def test_without_lifting_the_chart_keeps_its_title():
    out = envelope(Spec(copy.deepcopy(RAW)), rows=ROWS)
    assert out["chart_schema"]["title"]["text"] == "North leads"
    assert out["title"] == ""


def test_panel_titles_inside_a_composition_stay():
    raw = {"title": "Two views", "hconcat": [
        {**{k: v for k, v in RAW.items() if k != "title"}, "title": "Left"},
        {**{k: v for k, v in RAW.items() if k != "title"}, "title": "Right"},
    ]}
    rest, title, _ = lift_title(raw)
    assert title == "Two views" and "title" not in rest
    assert [panel["title"] for panel in rest["hconcat"]] == ["Left", "Right"]


def test_a_lexy_tile_carries_the_title_in_its_header():
    tile = _tile(Spec(copy.deepcopy(RAW)), ROWS, None)
    assert "title" not in tile["chart_schema"]
    assert tile["configuration"]["title"] == "North leads"
    assert tile["configuration"]["subtitle"] == "Revenue by region"


def test_a_kpi_card_has_no_chart_title_to_lift():
    kpi = Spec({"kpi_metadata": {"chart_type": "metric_kpi", "chart_subtype": "counter",
                                 "label": "Total revenue", "value": 447}})
    out = envelope(kpi, rows=[{"total_revenue": 447}], lift_title=True)
    assert out["chart_schema"] == kpi.raw and out["title"] == ""
