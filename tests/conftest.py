"""Shared fixtures. Nothing here touches the network or an LLM."""
from __future__ import annotations

from typing import Any

import pytest

from nexcraftviz.spec.model import Spec

ROWS: list[dict[str, Any]] = [
    {"region": "West", "month": "2026-01-01", "revenue": 120.5, "orders": 12, "active": True},
    {"region": "East", "month": "2026-01-01", "revenue": 98.0, "orders": 9, "active": False},
    {"region": "West", "month": "2026-02-01", "revenue": 141.25, "orders": 15, "active": True},
    {"region": "East", "month": "2026-02-01", "revenue": 88.75, "orders": 8, "active": True},
    {"region": "North", "month": "2026-02-01", "revenue": 175.0, "orders": 21, "active": False},
]


@pytest.fixture
def rows() -> list[dict[str, Any]]:
    return [dict(row) for row in ROWS]


@pytest.fixture
def bar_spec(rows: list[dict[str, Any]]) -> Spec:
    """A ranked bar chart — the single most common shape in the corpus."""
    return Spec(
        {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "width": 420,
            "height": 260,
            "data": {"values": rows},
            "mark": {"type": "bar", "cornerRadius": 3, "color": "#0C8BA6"},
            "encoding": {
                "y": {"field": "region", "type": "nominal", "sort": "-x"},
                "x": {"field": "revenue", "type": "quantitative", "aggregate": "sum"},
            },
        }
    )


@pytest.fixture
def line_spec(rows: list[dict[str, Any]]) -> Spec:
    return Spec(
        {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": rows},
            "mark": "line",
            "encoding": {
                "x": {"field": "month", "type": "temporal"},
                "y": {"field": "revenue", "type": "quantitative"},
                "color": {"field": "region", "type": "nominal"},
            },
        }
    )


@pytest.fixture
def layered_spec(rows: list[dict[str, Any]]) -> Spec:
    return Spec(
        {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": rows},
            "layer": [
                {
                    "mark": "bar",
                    "encoding": {
                        "x": {"field": "region", "type": "nominal"},
                        "y": {"field": "revenue", "type": "quantitative"},
                    },
                },
                {
                    "mark": {"type": "rule", "color": "red"},
                    "encoding": {"y": {"datum": 100, "type": "quantitative"}},
                },
            ],
        }
    )


@pytest.fixture
def kpi_spec() -> Spec:
    """A KPI payload — not Vega at all; the frontend renders `kpi_metadata`."""
    return Spec(
        {
            "kpi_metadata": {
                "chart_type": "kpi",
                "chart_subtype": "target_vs_actual",
                "vega_lite_compatible": False,
                "kpi_data": {"values": [87.4], "units": ["%"], "targets": [90.0]},
            }
        }
    )


@pytest.fixture
def table_spec() -> Spec:
    """A `table_with_cells` payload — per-column renderers, no Vega mark."""
    return Spec(
        {
            "columns": [
                {"field": "region", "renderer": "avatar_name", "title": "Region"},
                {"field": "revenue", "renderer": "progress_bar", "title": "Revenue"},
            ]
        }
    )
