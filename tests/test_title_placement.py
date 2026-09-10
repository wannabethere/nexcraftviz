"""Where the title goes is the host's call: drawn in the chart, or beside it.

A dashboard host whose card header shows the title asks for `header`, and the
title comes back beside the chart and out of it. The default is `chart`; the
title is still reported beside it, so any host can put it where it likes.
"""
from __future__ import annotations

import copy
from typing import Any

import pytest

from nexcraftviz.app.chart_api import envelope
from nexcraftviz.pipeline import ChartRequest, run_pipeline
from nexcraftviz.spec.labels import lift_title
from nexcraftviz.spec.model import Spec
from tests.test_ambiguous_plan import _stub
from tests.test_generate_repair import PLAN

ROWS = [{"region": "North", "revenue": 152.0}, {"region": "West", "revenue": 128.0}]
RAW: dict[str, Any] = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "title": {"text": "North leads", "subtitle": "Revenue by region"},
    "data": {"values": ROWS},
    "mark": "bar",
    "encoding": {"x": {"field": "region", "type": "nominal"},
                 "y": {"field": "revenue", "type": "quantitative"}},
}


def test_header_placement_moves_the_title_beside_the_chart():
    spec = Spec(copy.deepcopy(RAW))
    out = envelope(spec, rows=ROWS, title_placement="header")
    assert "title" not in out["chart_schema"]
    assert (out["title"], out["subtitle"]) == ("North leads", "Revenue by region")
    assert spec.raw["title"]["text"] == "North leads"  # the session's copy is untouched


def test_chart_placement_keeps_the_title_and_still_reports_it():
    out = envelope(Spec(copy.deepcopy(RAW)), rows=ROWS)
    assert out["chart_schema"]["title"]["text"] == "North leads"
    assert out["title"] == "North leads"


def test_panel_titles_inside_a_composition_stay():
    panel = {k: v for k, v in RAW.items() if k != "title"}
    raw = {"title": "Two views", "hconcat": [{**panel, "title": "Left"},
                                             {**panel, "title": "Right"}]}
    rest, title, _ = lift_title(raw)
    assert title == "Two views" and "title" not in rest
    assert [view["title"] for view in rest["hconcat"]] == ["Left", "Right"]


def test_a_kpi_card_has_no_chart_title_to_move():
    kpi = Spec({"kpi_metadata": {"chart_type": "metric_kpi", "chart_subtype": "counter",
                                 "label": "Total revenue", "value": 447}})
    out = envelope(kpi, rows=[{"total_revenue": 447}], title_placement="header")
    assert out["chart_schema"] == kpi.raw and out["title"] == ""


@pytest.mark.asyncio
async def test_the_delivered_package_carries_the_title_and_no_host_shape():
    plan = {**PLAN, "metadata": {"title": "Revenue by region"}}
    run = await run_pipeline(ChartRequest(question="revenue by region", rows=ROWS),
                             llm=_stub(plan))
    delivery = run.stages.deliver
    assert delivery is not None
    assert delivery.package["title"] == "Revenue by region"
    assert "tile" not in delivery.model_dump()
