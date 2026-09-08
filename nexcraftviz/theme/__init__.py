"""Themes: one token source, two outputs (Vega config + CSS).

See :mod:`nexcraftviz.theme.tokens` for why this is a subsystem rather than a
dict of colours.
"""
from nexcraftviz.theme.apply import apply_theme, resolve, strip_hardcoded_colours
from nexcraftviz.theme.contrast import ContrastIssue, ContrastReport, audit, contrast_ratio
from nexcraftviz.theme.css import to_bundle, to_css, to_variables
from nexcraftviz.theme.tokens import (
    ThemeNotFound,
    ThemeTokens,
    available_themes,
    default,
    from_yaml,
    load,
)
from nexcraftviz.theme.vega import to_vega_config

__all__ = [
    "ContrastIssue",
    "ContrastReport",
    "ThemeNotFound",
    "ThemeTokens",
    "apply_theme",
    "audit",
    "available_themes",
    "contrast_ratio",
    "default",
    "from_yaml",
    "load",
    "resolve",
    "strip_hardcoded_colours",
    "to_bundle",
    "to_css",
    "to_variables",
    "to_vega_config",
]
