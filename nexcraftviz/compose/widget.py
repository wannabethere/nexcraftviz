"""Widgets — several payloads, of any family, as one addressable thing.

The second way to combine charts, and the one Vega-Lite cannot do at all. A
`concat` holds Vega views; a widget holds *tiles*, and a tile may be a chart, a
KPI card or a rich table. Since two of the four families this stack emits are
not Vega, any real dashboard needs this form.

The trade is explicit:

======================  ==========================  ==========================
                        ``compose.vega.concat``     ``compose.widget.Widget``
======================  ==========================  ==========================
Result                  one Vega-Lite spec          tiles in a layout
Mixed families          no                          yes
Shared scales           yes                         no
Server-render to PNG    yes, in one call            per tile
Responsive layout       fixed at compose time       reflows in the browser
======================  ==========================  ==========================

Reach for the concat when the panels should be *read against each other*, and
for the widget when they should be *read together*.

Three structures make up a widget, and each exists because real dashboards
contain it:

* **Tile** — one card. Beyond its payload it can carry a ``headline`` (a KPI
  shown above the chart) and ``stats`` (a strip of supporting figures below),
  which is how a single card shows "68% completion" over a gauge over
  Completed / Ongoing / Not Started.
* **Group** — a titled panel holding tiles, so a widget can have an internal
  structure rather than one flat grid.
* **Widget** — the whole thing, with a layout.

Placement is edited with :mod:`nexcraftviz.compose.ops`, not by rebuilding the
widget: moving a tile or changing its width is an operation with an inverse,
exactly like editing a chart.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from nexcraftviz.spec.model import Spec

#: How much of the 12-column grid a tile occupies. ``auto`` lets the layout
#: decide, which is what a KPI tile in a headline row wants.
Span = Literal["auto", "quarter", "third", "half", "two-thirds", "three-quarters", "full"]

#: Columns each span takes in the 12-column grid.
SPAN_COLUMNS: dict[str, int] = {
    "quarter": 3,
    "third": 4,
    "half": 6,
    "two-thirds": 8,
    "three-quarters": 9,
    "full": 12,
}


class Stat(BaseModel):
    """One figure in a tile's supporting strip.

    Not a KPI card: a stat has no chart, no target and no sparkline. It is the
    "Completed 7,542 · Ongoing 3,120 · Not Started 1,452" row that gives a
    headline number its composition.
    """

    model_config = ConfigDict(extra="forbid")

    label: str
    value: float | str
    unit: str = ""
    tone: Literal["", "pass", "fix", "fail", "muted"] = ""


class Tile(BaseModel):
    """One card of a widget."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    id: str = Field(description="Stable id — also the chart's DOM mount point.")
    title: str = ""
    subtitle: str = ""
    payload: Any = Field(default=None, description="A Spec, TableSpec or KpiCard.")
    span: Span = "auto"
    note: str = Field(default="", description="Optional caption under the tile.")

    headline: Any = Field(
        default=None,
        description="Optional KpiCard rendered above the payload, in the same card.",
    )
    stats: list[Stat] = Field(
        default_factory=list,
        description="Optional strip of supporting figures below the payload.",
    )

    @property
    def spec(self) -> Spec:
        """The payload as a :class:`Spec`, whatever it arrived as."""
        return _as_spec(self.payload, where=f"tile {self.id!r}")

    @property
    def family(self) -> str:
        """What kind of tile this is, for layout and reporting.

        A tile with no payload is still a real tile: a headline alone is a KPI
        card, and stats alone are the boxed figures that sit beside a hero
        chart. Reporting either as "unknown" makes the layout treat them as
        something to be cautious with, which is how a stat box ends up
        full-width.
        """
        if self.payload is None:
            if self.headline is not None:
                return "kpi"
            return "stats" if self.stats else "unknown"
        return self.spec.family

    @property
    def is_compound(self) -> bool:
        """True when the card holds more than its payload."""
        return self.headline is not None or bool(self.stats)


class Group(BaseModel):
    """A titled panel holding tiles — one level of structure inside a widget."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    id: str
    title: str = ""
    subtitle: str = ""
    span: Span = "full"
    tiles: list[Tile] = Field(default_factory=list)

    @property
    def family(self) -> str:
        return "group"

    def tile_ids(self) -> list[str]:
        return [t.id for t in self.tiles]


Node = Tile | Group


class Widget(BaseModel):
    """A titled collection of tiles and groups in a layout."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    title: str = ""
    description: str = ""
    layout: str = "two_column_grid"
    nodes: list[Node] = Field(default_factory=list)

    # -- inspection ---------------------------------------------------------

    @property
    def tiles(self) -> list[Tile]:
        """Every tile, groups flattened — the view most callers want."""
        out: list[Tile] = []
        for node in self.nodes:
            if isinstance(node, Group):
                out.extend(node.tiles)
            else:
                out.append(node)
        return out

    @property
    def groups(self) -> list[Group]:
        return [n for n in self.nodes if isinstance(n, Group)]

    def find(self, node_id: str) -> Node | None:
        for node in self.nodes:
            if node.id == node_id:
                return node
            if isinstance(node, Group):
                for child in node.tiles:
                    if child.id == node_id:
                        return child
        return None

    def parent_of(self, tile_id: str) -> Group | None:
        for group in self.groups:
            if any(t.id == tile_id for t in group.tiles):
                return group
        return None

    def family_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for tile in self.tiles:
            counts[tile.family] = counts.get(tile.family, 0) + 1
        return counts

    @property
    def is_mixed_family(self) -> bool:
        """True when this widget could not have been a single Vega spec."""
        return len(self.family_counts()) > 1

    def chart_tiles(self) -> list[Tile]:
        return [t for t in self.tiles if t.family in ("vega-lite", "vega")]

    def chart_specs(self, *, id_prefix: str = "") -> dict[str, dict[str, Any]]:
        """Mount id → spec, for every chart tile.

        Rendered HTML carries the specs in an inline ``<script>``, which is
        fine for a generated page and useless to a client that injects the
        markup with ``innerHTML`` — scripts inserted that way never run. So the
        specs are also available directly.
        """
        return {
            f"{id_prefix}{t.id}": t.spec.raw
            for t in self.chart_tiles()
        }

    # -- rendering ----------------------------------------------------------

    def to_html(self, *, max_table_rows: int = 8, id_prefix: str = "") -> str:
        """Render the widget.

        ``id_prefix`` namespaces the chart mount points. A page that shows the
        same widget more than once — a before and after, say — would otherwise
        emit duplicate DOM ids, and only the first copy would ever get a chart.
        """
        from nexcraftviz.compose.layout import arrange
        from nexcraftviz.render.html import escape

        arranged = arrange(self.nodes, self.layout)
        body = "".join(
            _render_group(node, max_table_rows, id_prefix)
            if isinstance(node, Group)
            else render_tile(node, max_table_rows=max_table_rows, id_prefix=id_prefix)
            for node in arranged
        )

        header = ""
        if self.title or self.description:
            description = (
                f'<p class="nxv-widget__description">{escape(self.description)}</p>'
                if self.description
                else ""
            )
            header = (
                '<div class="nxv-widget__header">'
                f'<h2 class="nxv-widget__title">{escape(self.title)}</h2>'
                f"{description}</div>"
            )

        return (
            f'<section class="nxv-widget nxv-widget--{escape(self.layout)}">'
            f'{header}<div class="nxv-grid--12">{body}</div></section>'
        )

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe document.

        Deliberately *not* a Vega-Lite spec — a widget is a different kind of
        object, and pretending otherwise is how a KPI tile ends up handed to a
        Vega renderer that cannot draw it.
        """
        return {
            "kind": "nexcraftviz.widget",
            "version": 1,
            "title": self.title,
            "description": self.description,
            "layout": self.layout,
            "nodes": [_node_to_dict(node) for node in self.nodes],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Widget:
        return cls(
            title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            layout=str(data.get("layout") or "two_column_grid"),
            nodes=[_node_from_dict(entry) for entry in (data.get("nodes") or [])],
        )


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render_tile(tile: Tile, *, max_table_rows: int = 8, id_prefix: str = "") -> str:
    """One card, dispatching the payload on its family.

    A compound tile stacks headline → payload → stats inside a single card,
    which is what makes "68% completion" over a gauge over its three component
    counts one thing rather than three.
    """
    from nexcraftviz.render.html import (
        escape,
        render_card,
        render_chart_mount,
        render_kpi,
        render_table,
    )

    parts: list[str] = []
    if tile.headline is not None:
        parts.append(render_kpi(tile.headline))

    if tile.payload is not None:
        family = tile.family
        if family == "kpi":
            parts.append(render_kpi(tile.spec))
        elif family == "table":
            parts.append(render_table(tile.spec, max_rows=max_table_rows))
        else:
            parts.append(render_chart_mount(tile.spec, f"{id_prefix}{tile.id}"))

    if tile.stats:
        parts.append(render_stats(tile.stats))
    if tile.note:
        parts.append(f'<p class="nxv-tile__note">{escape(tile.note)}</p>')

    classes = f"nxv-widget__tile nxv-span--{tile.span}"
    if tile.is_compound:
        classes += " nxv-widget__tile--compound"

    return render_card(
        title=tile.title, subtitle=tile.subtitle, body="".join(parts), extra_class=classes
    )


def render_stats(stats: list[Stat]) -> str:
    """The supporting figure strip under a headline."""
    from nexcraftviz.render.html import _format_number, escape

    cells = []
    for stat in stats:
        tone = f" is-{stat.tone}" if stat.tone else ""
        value = (
            _format_number(stat.value, ",") if isinstance(stat.value, (int, float))
            else stat.value
        )
        unit = f'<span class="nxv-stat__unit">{escape(stat.unit)}</span>' if stat.unit else ""
        cells.append(
            f'<div class="nxv-stat{tone}">'
            f'<span class="nxv-stat__label">{escape(stat.label)}</span>'
            f'<span class="nxv-stat__value">{escape(value)}{unit}</span></div>'
        )
    return f'<div class="nxv-stats">{"".join(cells)}</div>'


def _render_group(group: Group, max_table_rows: int, id_prefix: str = "") -> str:
    from nexcraftviz.render.html import escape

    inner = "".join(
        render_tile(t, max_table_rows=max_table_rows, id_prefix=id_prefix)
        for t in group.tiles
    )
    header = ""
    if group.title:
        subtitle = (
            f'<span class="nxv-card__subtitle">{escape(group.subtitle)}</span>'
            if group.subtitle
            else ""
        )
        header = (
            '<div class="nxv-card__header">'
            f'<h3 class="nxv-card__title">{escape(group.title)}</h3>{subtitle}</div>'
        )
    return (
        f'<section class="nxv-card nxv-group nxv-span--{group.span}">'
        f'{header}<div class="nxv-grid--12 nxv-group__body">{inner}</div></section>'
    )


# ---------------------------------------------------------------------------
# construction
# ---------------------------------------------------------------------------

def widget(
    *nodes: Node,
    title: str = "",
    description: str = "",
    layout: str | None = None,
) -> Widget:
    """Build a widget, choosing a layout from the nodes when none is given."""
    from nexcraftviz.compose.layout import auto_layout

    entries = list(nodes)
    return Widget(
        title=title,
        description=description,
        layout=layout or auto_layout(entries),
        nodes=entries,
    )


def tile(
    payload: Any = None,
    *,
    id: str = "",
    title: str = "",
    subtitle: str = "",
    span: Span = "auto",
    note: str = "",
    headline: Any = None,
    stats: list[Stat] | None = None,
) -> Tile:
    """Convenience constructor; ids are generated from the title when omitted."""
    return Tile(
        id=id or _slug(title),
        title=title,
        subtitle=subtitle,
        payload=payload,
        span=span,
        note=note,
        headline=headline,
        stats=list(stats or []),
    )


def group(
    *tiles: Tile,
    id: str = "",
    title: str = "",
    subtitle: str = "",
    span: Span = "full",
) -> Group:
    return Group(
        id=id or _slug(title, prefix="group"),
        title=title,
        subtitle=subtitle,
        span=span,
        tiles=list(tiles),
    )


def _slug(text: str, *, prefix: str = "tile") -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return f"{prefix}-{cleaned or 'untitled'}"


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _as_spec(payload: Any, *, where: str) -> Spec:
    if isinstance(payload, Spec):
        return payload
    if isinstance(payload, dict):
        return Spec(payload)
    to_spec = getattr(payload, "to_spec", None)
    if callable(to_spec):
        return to_spec()
    raise TypeError(f"{where} holds an unusable payload: {type(payload).__name__}")


def _node_to_dict(node: Node) -> dict[str, Any]:
    if isinstance(node, Group):
        return {
            "kind": "group",
            "id": node.id,
            "title": node.title,
            "subtitle": node.subtitle,
            "span": node.span,
            "tiles": [_tile_to_dict(t) for t in node.tiles],
        }
    return _tile_to_dict(node)


def _tile_to_dict(tile: Tile) -> dict[str, Any]:
    out: dict[str, Any] = {
        "kind": "tile",
        "id": tile.id,
        "title": tile.title,
        "subtitle": tile.subtitle,
        "span": tile.span,
        "note": tile.note,
        "family": tile.family,
        "payload": tile.spec.raw if tile.payload is not None else None,
    }
    if tile.headline is not None:
        out["headline"] = _as_spec(tile.headline, where=f"tile {tile.id!r} headline").raw
    if tile.stats:
        out["stats"] = [s.model_dump() for s in tile.stats]
    return out


def _node_from_dict(entry: Any) -> Node:
    if not isinstance(entry, dict) or not entry.get("id"):
        raise ValueError("every widget node needs an id")
    if entry.get("kind") == "group":
        return Group(
            id=str(entry["id"]),
            title=str(entry.get("title") or ""),
            subtitle=str(entry.get("subtitle") or ""),
            span=entry.get("span") or "full",
            tiles=[_tile_from_dict(t) for t in (entry.get("tiles") or [])],
        )
    return _tile_from_dict(entry)


def _tile_from_dict(entry: dict[str, Any]) -> Tile:
    return Tile(
        id=str(entry["id"]),
        title=str(entry.get("title") or ""),
        subtitle=str(entry.get("subtitle") or ""),
        span=entry.get("span") or "auto",
        note=str(entry.get("note") or ""),
        payload=Spec(entry["payload"]) if entry.get("payload") else None,
        headline=Spec(entry["headline"]) if entry.get("headline") else None,
        stats=[Stat.model_validate(s) for s in (entry.get("stats") or [])],
    )
