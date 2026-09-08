"""Widget layouts.

The four names are taken from ``genieml_skills/skills/writers/dashboard.py``'s
``LayoutKindLiteral`` rather than invented, so a widget composed here can be
handed to the existing dashboard writer without a translation step.

Layout does two things: it orders the tiles, and it assigns each one a span.
Both are deterministic — there is no judgement in "KPIs go in a row across the
top" that needs a model.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from nexcraftviz.compose.widget import Node

#: Layout names, matching the existing dashboard writer's vocabulary.
LAYOUTS: tuple[str, ...] = (
    "kpi_row_plus_grid",
    "two_column_grid",
    "single_column",
    "executive_summary",
)

DEFAULT_LAYOUT = "two_column_grid"

#: Tiles beyond this many make a single column an unreasonably long page.
_MAX_SINGLE_COLUMN = 3


def auto_layout(nodes: list[Node]) -> str:
    """Pick a layout from what the tiles actually are.

    The strongest signal is a set of KPI tiles: headline numbers belong in a
    row across the top, with everything else below. After that it is just a
    question of how many tiles there are.
    """
    if not nodes:
        return DEFAULT_LAYOUT

    families = [node.family for node in nodes]
    kpis = families.count("kpi")

    # A group carries its own internal structure, so the outer layout should
    # keep out of the way and just stack the panels.
    if "group" in families:
        return "single_column"
    if kpis >= 2 and kpis < len(nodes):
        return "kpi_row_plus_grid"
    if len(nodes) <= _MAX_SINGLE_COLUMN:
        return "single_column"
    return DEFAULT_LAYOUT


def arrange(nodes: list[Node], layout: str) -> list[Node]:
    """Order the tiles and assign spans, returning copies.

    Tiles that already declare an explicit span keep it — an author who said
    "this one is full width" outranks the layout.
    """
    if layout not in LAYOUTS:
        layout = DEFAULT_LAYOUT

    ordered = _order(nodes, layout)
    return [
        entry if entry.span != "auto" else entry.model_copy(update={"span": _span(entry, layout)})
        for entry in ordered
    ]


def _order(nodes: list[Node], layout: str) -> list[Node]:
    if layout == "kpi_row_plus_grid":
        # KPIs first, everything else in the order it was given.
        kpis = [n for n in nodes if n.family == "kpi"]
        return kpis + [n for n in nodes if n.family != "kpi"]

    if layout == "executive_summary":
        # Headline numbers, then the one chart that carries the story, then the
        # supporting detail. Tables last: they are for looking things up, which
        # is not what an executive summary is for.
        rank = {"kpi": 0, "vega-lite": 1, "vega": 1, "group": 1, "table": 2}
        return sorted(nodes, key=lambda n: rank.get(n.family, 3))

    return list(nodes)


def _span(node: Node, layout: str) -> str:
    """The width a node gets when it has not asked for one.

    A compound tile — a headline over a chart over a stat strip — needs more
    room than a bare KPI number, so it is treated as a chart rather than as a
    KPI whatever its payload happens to be.
    """
    family = node.family
    if family == "group":
        return "full"

    compound = bool(getattr(node, "is_compound", False))
    # A bare KPI or a lone stat box is a small thing; a compound tile that
    # wraps a chart is not, whatever its payload happens to be.
    headline_only = family in ("kpi", "stats") and not (compound and family != "stats")

    if layout == "single_column":
        return "full"

    if layout == "kpi_row_plus_grid":
        # A bare KPI is a single number and needs no room; a table needs it all.
        if headline_only:
            return "quarter"
        return "full" if family == "table" else "half"

    if layout == "executive_summary":
        if headline_only:
            return "quarter"
        # One chart, told large. Everything after it is supporting detail.
        return "full" if family in ("vega-lite", "vega") else "half"

    # two_column_grid
    if headline_only:
        return "quarter"
    return "full" if family == "table" else "half"
