"""A generated KPI is a text mark over one row; hosts draw it as a card.

Found in the live report: `single_number` came back as a bare `text` mark with
no size and an invalid `x: {"signal": ...}` — drawn as a small number in the
corner of an empty canvas — while the playground draws every KPI as a card.
"""
from __future__ import annotations

import pytest

from nexcraftviz.render.html import kpi_from_vega, render_kpi

#: single_number, as the live model wrote it.
LIVE_SINGLE_NUMBER = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "title": "Total revenue", "width": "container", "height": 120,
    "data": {"values": [{"total_revenue": 447}]},
    "mark": {"type": "text", "align": "center", "baseline": "middle", "tooltip": True},
    "encoding": {"text": {"field": "total_revenue", "type": "quantitative", "format": "d"},
                 "x": {"signal": "width/2"}, "y": {"signal": "height/2"}},
}


def test_the_live_text_mark_kpi_reads_back_as_a_card():
    kpi = kpi_from_vega(LIVE_SINGLE_NUMBER)
    assert kpi is not None
    assert (kpi.label, kpi.value) == ("Total revenue", "447")
    assert '<span class="nxv-kpi__value">447</span>' in render_kpi(kpi)


@pytest.mark.parametrize(("fmt", "value", "shown"), [
    ("d", 447.0, "447"),
    (",d", 12850, "12,850"),
    ("$,.0f", 1234.4, "$1,234"),
    (".1%", 0.623, "62.3%"),
])
def test_the_formats_generated_kpis_use(fmt, value, shown):
    spec = {**LIVE_SINGLE_NUMBER, "data": {"values": [{"total_revenue": value}]}}
    spec["encoding"] = {"text": {"field": "total_revenue", "type": "quantitative",
                                 "format": fmt}}
    kpi = kpi_from_vega(spec)
    assert kpi is not None and kpi.value == shown


def test_a_chart_is_not_a_kpi():
    bar = {**LIVE_SINGLE_NUMBER, "mark": "bar"}
    assert kpi_from_vega(bar) is None


def test_text_over_many_rows_is_a_labelled_chart_not_a_kpi():
    many = {**LIVE_SINGLE_NUMBER,
            "data": {"values": [{"total_revenue": 1}, {"total_revenue": 2}]}}
    assert kpi_from_vega(many) is None
