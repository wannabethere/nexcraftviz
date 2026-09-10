"""A time axis runs left to right.

Found live: asked for "the most useful view" of a monthly completion trend, the
planner sorted the month axis descending in two runs, and the generator drew it
— once as `sort: descending`, once as `scale.reverse`. Every rise read as a fall.
"""
from __future__ import annotations

from typing import Any

import pytest

from nexcraftviz.evaluate.gates import run_gates
from nexcraftviz.spec.model import Spec

ROWS = [
    {"month": "2026-01-01", "completion_pct": 71.0},
    {"month": "2026-02-01", "completion_pct": 78.5},
    {"month": "2026-03-01", "completion_pct": 82.0},
]


def _trend(**x: Any) -> Spec:
    return Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": ROWS},
        "mark": "line",
        "encoding": {
            "x": {"field": "month", "type": "temporal", **x},
            "y": {"field": "completion_pct", "type": "quantitative", "aggregate": "mean"},
        },
    })


def _honesty(spec: Spec):
    return next(g for g in run_gates(spec, rows=ROWS, skip=("renders",))
                if g.gate == "data_honesty")


@pytest.mark.parametrize("backwards", [
    {"sort": "descending"},
    {"sort": {"order": "descending"}},
    {"scale": {"reverse": True}},
])
def test_time_running_backwards_fails_data_honesty(backwards):
    gate = _honesty(_trend(**backwards))
    assert not gate.passed
    assert "newest-first" in gate.detail


def test_time_running_forwards_passes():
    assert _honesty(_trend()).passed
    assert _honesty(_trend(sort="ascending")).passed


def test_a_category_axis_may_be_sorted_descending():
    """Ranking is what a descending sort is for; only time has a direction."""
    spec = Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": [{"team": "A", "n": 3}, {"team": "B", "n": 5}]},
        "mark": "bar",
        "encoding": {"x": {"field": "team", "type": "nominal", "sort": "descending"},
                     "y": {"field": "n", "type": "quantitative"}},
    })
    gate = next(g for g in run_gates(spec, skip=("renders",)) if g.gate == "data_honesty")
    assert gate.passed
