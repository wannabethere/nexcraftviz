"""Tokens → Vega-Lite ``config``.

The generated config covers the properties that actually change how a chart
reads: the categorical range, axis and legend colours and fonts, grid weight,
title styling, mark defaults, and the view background. Anything more specific
belongs in a preset's ``vega_overrides``, which is merged last.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from nexcraftviz.theme.tokens import ThemeTokens


def to_vega_config(theme: ThemeTokens) -> dict[str, Any]:
    """Build the ``config`` block for ``theme``.

    A theme with ``vega_base="overrides"`` emits its overrides untouched. That
    is how the derived presets keep exact visual parity with the vega-themes
    build a host renders with — layering our own defaults underneath would
    change the rendering, which is precisely what deriving them avoids.
    """
    if theme.vega_base == "overrides":
        return deepcopy(theme.vega_overrides)

    typo = theme.typography
    surfaces = theme.surfaces
    text = theme.text
    geo = theme.geometry

    config: dict[str, Any] = {
        "background": surfaces.background,
        "font": typo.font_family,
        "view": {
            "stroke": "transparent",
            "continuousWidth": 400,
            "continuousHeight": 240,
        },
        "title": {
            "color": text.primary,
            "subtitleColor": text.secondary,
            "font": typo.font_family,
            "fontSize": typo.size_lg,
            "fontWeight": typo.weight_bold,
            "subtitleFontSize": typo.size_sm,
            "anchor": "start",
            "offset": geo.spacing * 1.5,
        },
        "axis": {
            "labelColor": text.secondary,
            "labelFont": typo.font_family,
            "labelFontSize": typo.size_sm,
            "titleColor": text.secondary,
            "titleFont": typo.font_family,
            "titleFontSize": typo.size_sm,
            "titleFontWeight": typo.weight_medium,
            "titlePadding": geo.spacing,
            "domainColor": surfaces.border,
            "tickColor": surfaces.border,
            "gridColor": surfaces.grid,
            "gridWidth": geo.border_width,
            "labelPadding": geo.spacing * 0.5,
        },
        # Quantitative axes carry the grid; categorical ones do not. Gridlines
        # on a category axis add ink without adding information.
        "axisQuantitative": {"grid": True, "tickCount": 5},
        "axisBand": {"grid": False, "domain": False, "ticks": False},
        "axisTemporal": {"grid": False},
        "legend": {
            "labelColor": text.secondary,
            "labelFont": typo.font_family,
            "labelFontSize": typo.size_sm,
            "titleColor": text.secondary,
            "titleFont": typo.font_family,
            "titleFontSize": typo.size_sm,
            "titleFontWeight": typo.weight_medium,
            "symbolType": "circle",
            "symbolSize": 80,
            "orient": "right",
            "padding": geo.spacing,
        },
        "header": {
            "labelColor": text.secondary,
            "labelFont": typo.font_family,
            "labelFontSize": typo.size_sm,
            "titleColor": text.primary,
            "titleFont": typo.font_family,
            "titleFontSize": typo.size_base,
        },
        "range": _range_block(theme),
        "text": {
            "font": typo.font_family,
            "fontSize": typo.size_sm,
            "fill": text.primary,
        },
    }

    config.update(_mark_defaults(theme))
    return _deep_merge(config, theme.vega_overrides)


def _range_block(theme: ThemeTokens) -> dict[str, Any]:
    palette = theme.palette
    block: dict[str, Any] = {}
    if palette.categorical:
        block["category"] = list(palette.categorical)
    block["ramp"] = _scale_value(palette.sequential)
    block["heatmap"] = _scale_value(palette.sequential)
    block["diverging"] = _scale_value(palette.diverging)
    block["ordinal"] = _scale_value(palette.ordinal)
    return block


def _scale_value(value: str | list[str]) -> Any:
    """A Vega range entry is either a scheme reference or an explicit list."""
    if isinstance(value, list):
        return list(value)
    return {"scheme": value}


def _mark_defaults(theme: ThemeTokens) -> dict[str, Any]:
    """Per-mark defaults.

    The primary colour is the first categorical entry when there is one — a
    single-series chart should look like it belongs to the same family as a
    multi-series one — falling back to the semantic accent.
    """
    primary = theme.palette.categorical[0] if theme.palette.categorical else theme.semantic.accent
    geo = theme.geometry

    return {
        "bar": {"fill": primary, "cornerRadiusEnd": geo.bar_corner_radius},
        "line": {
            "stroke": primary,
            "strokeWidth": geo.line_width,
            "strokeCap": "round",
            "strokeJoin": "round",
        },
        "area": {"fill": primary, "line": True, "opacity": 0.25},
        "point": {"fill": primary, "filled": True, "size": geo.point_size},
        "circle": {"fill": primary},
        "square": {"fill": primary},
        "arc": {"fill": primary},
        "rect": {"fill": primary},
        "tick": {"color": primary},
        "rule": {"color": theme.semantic.target},
        "trail": {"color": primary},
        "shape": {"stroke": primary},
        "symbol": {"fill": primary},
    }


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out
