"""Combining charts — two forms, for two different reasons.

``compose.vega`` produces one Vega-Lite spec (shared scales, one render, one
export) but holds Vega views only. ``compose.widget`` holds tiles of any
family — chart, KPI card or rich table — which Vega-Lite cannot express, at the
cost of being several renders in a layout rather than one spec.

``compose.ops`` edits placement the way ``spec.ops`` edits a chart: the model
picks operations, code applies them, and undo comes from the diff.
"""
from nexcraftviz.compose.layout import LAYOUTS, arrange, auto_layout
from nexcraftviz.compose.ops import (
    WIDGET_OP_REGISTRY,
    GroupTiles,
    MoveTile,
    RemoveTile,
    SetLayout,
    SetSpan,
    SetTileTitle,
    SetWidgetTitle,
    UngroupTiles,
    WidgetOpError,
    WidgetOpList,
    WidgetOpResult,
    apply_widget_ops,
    parse_widget_ops,
)
from nexcraftviz.compose.vega import ComposeError, concat, layer, small_multiples
from nexcraftviz.compose.widget import (
    SPAN_COLUMNS,
    Group,
    Stat,
    Tile,
    Widget,
    group,
    render_tile,
    tile,
    widget,
)

__all__ = [
    "LAYOUTS",
    "SPAN_COLUMNS",
    "WIDGET_OP_REGISTRY",
    "ComposeError",
    "Group",
    "GroupTiles",
    "MoveTile",
    "RemoveTile",
    "SetLayout",
    "SetSpan",
    "SetTileTitle",
    "SetWidgetTitle",
    "Stat",
    "Tile",
    "UngroupTiles",
    "Widget",
    "WidgetOpError",
    "WidgetOpList",
    "WidgetOpResult",
    "apply_widget_ops",
    "arrange",
    "auto_layout",
    "concat",
    "group",
    "layer",
    "parse_widget_ops",
    "render_tile",
    "small_multiples",
    "tile",
    "widget",
]
