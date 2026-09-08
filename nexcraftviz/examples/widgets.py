"""Worked widget examples, reproducing two real dashboard tiles.

Both are drawn from designs supplied as reference, and each exists to prove a
different thing:

* :func:`completion_rate_tile` — a *compound tile*. One card holding a headline
  with its delta, a multi-ring gauge, and a strip of the three counts that make
  the headline up. Vega-Lite cannot express this: the headline and the strip
  are not marks, they are card furniture around a chart.
* :func:`talent_acquisition_widget` — a *grouped widget*. A titled panel with a
  hero funnel beside stacked stat boxes, then two half-width panels below.
  Nested structure, mixed families, explicit spans.

The specs here are deliberately concrete rather than parameterised. They are
reference material — something to copy and adapt — and a parameterised version
would hide the very details worth reading.
"""
from __future__ import annotations

from typing import Any

from nexcraftviz.compose import Stat, Widget, group, tile, widget
from nexcraftviz.spec.model import Spec
from nexcraftviz.table import KpiCard

# ---------------------------------------------------------------------------
# 1 — compound tile: headline + multi-ring gauge + stat strip
# ---------------------------------------------------------------------------

#: (label, count, colour, outer radius, inner radius) — outermost ring first.
COMPLETION_RINGS: list[tuple[str, int, str, int, int]] = [
    ("Completed", 7542, "#3b82f6", 78, 68),
    ("Ongoing", 3120, "#22d3ee", 64, 54),
    ("Not started", 1452, "#c026d3", 50, 40),
]

_GAUGE_WIDTH = 240
_GAUGE_HEIGHT = 104
#: Vertical anchor for the arc centre. A semicircle centred in its view leaves
#: the whole bottom half empty, so the centre is pushed down and the height
#: trimmed to match.
_GAUGE_CENTRE_Y = 86

#: A half-turn in radians — the sweep of a semicircular gauge.
_HALF_TURN = 3.1416
_QUARTER_TURN = 1.5708


def completion_gauge() -> Spec:
    """Concentric semicircular arcs, one ring per component.

    Two details are load-bearing and easy to get wrong:

    * ``startAngle`` goes on the *mark* while the end angle is a ``theta``
      encoding with ``scale: null``, so the field value is read as radians.
      Setting both angles as mark properties and the value as an encoding
      produces a spec that validates, compiles, and draws nothing at all.
    * ``autosize: none``. Vega's default re-fits the view around the marks,
      which silently shifts explicitly positioned arcs off centre.
    """
    total = sum(count for _, count, _, _, _ in COMPLETION_RINGS)
    headline = round(100 * COMPLETION_RINGS[0][1] / total)
    centre_x = _GAUGE_WIDTH / 2

    layers: list[dict[str, Any]] = []
    for _, count, colour, outer, inner in COMPLETION_RINGS:
        geometry = {
            "innerRadius": inner,
            "outerRadius": outer,
            "startAngle": -_QUARTER_TURN,
            "x": centre_x,
            "y": _GAUGE_CENTRE_Y,
        }
        layers.append({
            "data": {"values": [{"end": _QUARTER_TURN}]},
            "mark": {"type": "arc", **geometry, "color": "#334155", "opacity": 0.45},
            "encoding": {"theta": {"field": "end", "type": "quantitative", "scale": None}},
        })
        layers.append({
            "data": {"values": [{"end": -_QUARTER_TURN + _HALF_TURN * count / total}]},
            "mark": {"type": "arc", **geometry, "color": colour, "cornerRadius": 4},
            "encoding": {"theta": {"field": "end", "type": "quantitative", "scale": None}},
        })

    layers.append({
        "data": {"values": [{"text": f"{headline}%"}]},
        "mark": {
            "type": "text", "fontSize": 26, "fontWeight": "bold",
            "color": "#f1f5f9", "x": centre_x, "y": _GAUGE_CENTRE_Y - 16,
        },
        "encoding": {"text": {"field": "text"}},
    })
    layers.append({
        "data": {"values": [
            {"text": "0", "x": centre_x - 80},
            {"text": "100%", "x": centre_x + 80},
        ]},
        "mark": {"type": "text", "fontSize": 10, "color": "#94a3b8", "y": _GAUGE_CENTRE_Y + 12},
        "encoding": {
            "x": {"field": "x", "type": "quantitative", "scale": None, "axis": None},
            "text": {"field": "text"},
        },
    })

    return Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "background": "transparent",
        "width": _GAUGE_WIDTH,
        "height": _GAUGE_HEIGHT,
        "autosize": {"type": "none"},
        "layer": layers,
    })


def completion_rate_tile():
    """The whole card: headline, gauge, and the counts behind the headline."""
    total = sum(count for _, count, _, _, _ in COMPLETION_RINGS)
    completed = COMPLETION_RINGS[0][1]

    return tile(
        completion_gauge(),
        id="tile-completion-rate",
        title="Course completion rate",
        span="third",
        headline=KpiCard(
            chart_subtype="percentage",
            label="Completion",
            value=round(100 * completed / total, 1),
            unit="%",
            change_pct=4.2,
            change_direction="up",
        ),
        stats=[
            Stat(label="Completed", value=completed, tone="pass"),
            Stat(label="Ongoing", value=COMPLETION_RINGS[1][1], tone="fix"),
            Stat(label="Not started", value=COMPLETION_RINGS[2][1], tone="muted"),
        ],
    )


# ---------------------------------------------------------------------------
# 2 — grouped widget: hero funnel + side stats, then two panels
# ---------------------------------------------------------------------------

FUNNEL_STAGES: list[tuple[str, int, str]] = [
    ("Applicants", 2400, "#3b82f6"),
    ("Screening", 812, "#14b8a6"),
    ("Interview", 290, "#a855f7"),
    ("Offer", 115, "#f97316"),
    ("Hired", 88, "#22c55e"),
]


def hiring_funnel() -> Spec:
    """A tapered funnel — bars centred on zero via symmetric ``x``/``x2``.

    The corpus's funnel pairs are plain horizontal bars, which lose the taper
    that makes a funnel legible at a glance. Mirroring each bar around zero
    costs one extra field and gets the real shape.
    """
    widest = FUNNEL_STAGES[0][1]
    rows = [
        {
            "stage": stage,
            "order": index,
            "left": -count / 2,
            "right": count / 2,
            "label": f"{count / 1000:.1f}K" if count >= 1000 else str(count),
        }
        for index, (stage, count, _) in enumerate(FUNNEL_STAGES)
    ]

    shared_y = {"field": "stage", "type": "nominal", "sort": {"field": "order"}}
    return Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "background": "transparent",
        "data": {"values": rows},
        "width": 340,
        "height": 240,
        "layer": [
            {
                "mark": {"type": "bar", "cornerRadius": 3},
                "encoding": {
                    "y": {**shared_y, "axis": None},
                    "x": {
                        "field": "left", "type": "quantitative", "axis": None,
                        "scale": {"domain": [-widest / 2 * 1.05, widest / 2 * 1.05]},
                    },
                    "x2": {"field": "right"},
                    "color": {
                        "field": "stage", "type": "nominal",
                        "scale": {
                            "domain": [s for s, _, _ in FUNNEL_STAGES],
                            "range": [c for _, _, c in FUNNEL_STAGES],
                        },
                        "legend": None,
                    },
                    "tooltip": [{"field": "stage"}, {"field": "right", "title": "count"}],
                },
            },
            {
                "mark": {
                    "type": "text", "fontSize": 15, "fontWeight": "bold", "color": "white"
                },
                "encoding": {"y": shared_y, "x": {"datum": 0}, "text": {"field": "label"}},
            },
        ],
    })


def sourcing_donut() -> Spec:
    rows = [
        {"channel": "LinkedIn", "share": 35},
        {"channel": "Referrals", "share": 28},
        {"channel": "Careers site", "share": 22},
        {"channel": "Agencies", "share": 15},
    ]
    return Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "background": "transparent",
        "data": {"values": rows},
        "width": 260,
        "height": 200,
        "mark": {"type": "arc", "innerRadius": 55, "cornerRadius": 2},
        "encoding": {
            "theta": {"field": "share", "type": "quantitative", "stack": True},
            "color": {
                "field": "channel", "type": "nominal",
                "scale": {"range": ["#3b82f6", "#22c55e", "#a855f7", "#14b8a6"]},
                "legend": {"title": None, "orient": "right"},
            },
            "tooltip": [{"field": "channel"}, {"field": "share", "title": "share %"}],
        },
    })


def time_to_hire() -> Spec:
    rows = [
        {"department": "Tech", "days": 48},
        {"department": "Sales", "days": 32},
        {"department": "Operations", "days": 27},
        {"department": "Finance", "days": 21},
    ]
    shared_y = {"field": "department", "type": "nominal", "sort": "-x"}
    return Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "background": "transparent",
        "data": {"values": rows},
        "width": 260,
        "height": 200,
        "layer": [
            {
                "mark": {"type": "bar", "cornerRadiusEnd": 3},
                "encoding": {
                    "y": {**shared_y, "axis": {"title": None}},
                    "x": {
                        "field": "days", "type": "quantitative",
                        "axis": {"title": "Time to hire (days)"},
                    },
                    "color": {
                        "field": "department", "type": "nominal",
                        "scale": {"range": ["#3b82f6", "#22c55e", "#22d3ee", "#a855f7"]},
                        "legend": None,
                    },
                },
            },
            {
                "mark": {
                    "type": "text", "align": "right", "dx": -6,
                    "color": "white", "fontWeight": "bold", "fontSize": 11,
                },
                "encoding": {
                    "y": shared_y,
                    "x": {"field": "days", "type": "quantitative"},
                    "text": {"field": "days"},
                },
            },
        ],
    })


def talent_acquisition_widget() -> Widget:
    """A grouped widget: hero funnel beside its conversion stats, then two panels."""
    applicants = FUNNEL_STAGES[0][1]
    hired = FUNNEL_STAGES[-1][1]
    screened = FUNNEL_STAGES[1][1]

    return widget(
        group(
            tile(
                hiring_funnel(),
                id="tile-funnel",
                title="Hiring pipeline performance",
                span="two-thirds",
            ),
            tile(
                id="tile-conversion",
                title="Conversion",
                span="third",
                stats=[
                    Stat(
                        label="Applicant to hire",
                        value=f"{100 * hired / applicants:.1f}",
                        unit="%",
                    ),
                    Stat(
                        label="Screen to hire",
                        value=f"{100 * hired / screened:.1f}",
                        unit="%",
                        tone="pass",
                    ),
                    Stat(label="Hired this quarter", value=hired),
                ],
            ),
            id="group-pipeline",
            title="Hiring pipeline performance",
        ),
        tile(sourcing_donut(), id="tile-sourcing", title="Sourcing channels", span="half"),
        tile(time_to_hire(), id="tile-time-to-hire", title="Time to hire by department",
             span="half"),
        title="Talent acquisition funnel",
        description=(
            "A titled panel holding a hero chart beside its supporting figures, "
            "with two half-width panels below."
        ),
        layout="single_column",
    )
