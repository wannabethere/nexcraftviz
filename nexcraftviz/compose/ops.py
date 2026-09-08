"""Placement operations — editing where things sit, deterministically.

The same bargain as :mod:`nexcraftviz.spec.ops`, applied to layout instead of
encoding: a model picks the operation, ordinary code applies it, and the inverse
comes from diffing rather than from each operation implementing its own undo.

That matters more for placement than for charts, because placement is where
people iterate most. "Make the funnel wider", "move the donut up", "put those
two in a panel together" should each cost a handful of tokens and be one
Ctrl-Z away — not a regenerated dashboard that has silently changed three other
things.
"""
from __future__ import annotations

import copy
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nexcraftviz.compose.widget import Group, Span, Tile, Widget, _slug
from nexcraftviz.spec.diff import Patch, diff, invert


class WidgetOpError(ValueError):
    """Raised when a placement operation cannot apply."""


class BaseWidgetOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def apply(self, widget: Widget) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _tile(widget: Widget, tile_id: str) -> Tile:
        node = widget.find(tile_id)
        if not isinstance(node, Tile):
            known = ", ".join(t.id for t in widget.tiles) or "(none)"
            raise WidgetOpError(f"no tile {tile_id!r}; known tiles: {known}")
        return node

    @staticmethod
    def _container(widget: Widget, tile_id: str) -> list[Any]:
        """The list the tile currently lives in — the widget or a group."""
        parent = widget.parent_of(tile_id)
        return parent.tiles if parent else widget.nodes

    @staticmethod
    def _detach(widget: Widget, tile_id: str) -> Tile:
        container = BaseWidgetOp._container(widget, tile_id)
        for index, node in enumerate(container):
            if node.id == tile_id:
                return container.pop(index)
        raise WidgetOpError(f"no tile {tile_id!r}")


class SetSpan(BaseWidgetOp):
    """Resize a tile — "make the funnel wider"."""

    op: Literal["set_span"] = "set_span"
    tile: str = Field(description="Tile id.")
    span: Span = Field(description="quarter | third | half | two-thirds | full | auto")

    def apply(self, widget: Widget) -> None:
        node = widget.find(self.tile)
        if node is None:
            raise WidgetOpError(f"no tile or group {self.tile!r}")
        node.span = self.span


class MoveTile(BaseWidgetOp):
    """Reorder a tile, optionally moving it into or out of a group.

    ``before``/``after`` name another node rather than an index, because that
    is how people describe it — "put the donut before the bar chart" — and an
    index would be invalidated by any earlier edit.
    """

    op: Literal["move_tile"] = "move_tile"
    tile: str
    before: str = ""
    after: str = ""
    into: str = Field(default="", description="Group id to move the tile into.")
    to_root: bool = Field(default=False, description="Move the tile out of its group.")

    def apply(self, widget: Widget) -> None:
        if self.before and self.after:
            raise WidgetOpError("move_tile takes `before` or `after`, not both")
        if self.into and self.to_root:
            raise WidgetOpError("move_tile takes `into` or `to_root`, not both")

        moved = self._detach(widget, self.tile)

        if self.into:
            target = widget.find(self.into)
            if not isinstance(target, Group):
                raise WidgetOpError(f"no group {self.into!r}")
            destination: list[Any] = target.tiles
        elif self.to_root:
            destination = widget.nodes
        else:
            anchor = self.before or self.after
            destination = self._container(widget, anchor) if anchor else widget.nodes

        index = len(destination)
        anchor = self.before or self.after
        if anchor:
            positions = [i for i, node in enumerate(destination) if node.id == anchor]
            if not positions:
                raise WidgetOpError(f"no node {anchor!r} to position against")
            index = positions[0] if self.before else positions[0] + 1

        destination.insert(index, moved)


class GroupTiles(BaseWidgetOp):
    """Wrap tiles in a titled panel — "put those two in a panel together".

    The new group takes the position of the first tile named, so grouping does
    not also reorder the page.
    """

    op: Literal["group_tiles"] = "group_tiles"
    tiles: list[str] = Field(min_length=1)
    title: str = ""
    subtitle: str = ""
    span: Span = "full"
    id: str = ""

    def apply(self, widget: Widget) -> None:
        for tile_id in self.tiles:
            self._tile(widget, tile_id)

        anchor = self.tiles[0]
        positions = [i for i, node in enumerate(widget.nodes) if node.id == anchor]
        insert_at = positions[0] if positions else len(widget.nodes)

        collected = [self._detach(widget, tile_id) for tile_id in self.tiles]
        group = Group(
            id=self.id or _slug(self.title, prefix="group"),
            title=self.title,
            subtitle=self.subtitle,
            span=self.span,
            tiles=collected,
        )
        widget.nodes.insert(min(insert_at, len(widget.nodes)), group)


class UngroupTiles(BaseWidgetOp):
    """Dissolve a panel, leaving its tiles in place."""

    op: Literal["ungroup_tiles"] = "ungroup_tiles"
    group: str

    def apply(self, widget: Widget) -> None:
        for index, node in enumerate(widget.nodes):
            if node.id == self.group and isinstance(node, Group):
                widget.nodes[index : index + 1] = node.tiles
                return
        raise WidgetOpError(f"no group {self.group!r}")


class SetLayout(BaseWidgetOp):
    """Switch the whole arrangement."""

    op: Literal["set_layout"] = "set_layout"
    layout: str

    def apply(self, widget: Widget) -> None:
        from nexcraftviz.compose.layout import LAYOUTS

        if self.layout not in LAYOUTS:
            raise WidgetOpError(f"unknown layout {self.layout!r}; known: {', '.join(LAYOUTS)}")
        widget.layout = self.layout


class SetTileTitle(BaseWidgetOp):
    op: Literal["set_tile_title"] = "set_tile_title"
    tile: str
    title: str = ""
    subtitle: str = ""

    def apply(self, widget: Widget) -> None:
        node = widget.find(self.tile)
        if node is None:
            raise WidgetOpError(f"no tile or group {self.tile!r}")
        node.title = self.title
        node.subtitle = self.subtitle


class RemoveTile(BaseWidgetOp):
    op: Literal["remove_tile"] = "remove_tile"
    tile: str

    def apply(self, widget: Widget) -> None:
        self._detach(widget, self.tile)


class SetWidgetTitle(BaseWidgetOp):
    op: Literal["set_widget_title"] = "set_widget_title"
    title: str = ""
    description: str = ""

    def apply(self, widget: Widget) -> None:
        widget.title = self.title
        widget.description = self.description


AnyWidgetOp = Annotated[
    SetSpan
    | MoveTile
    | GroupTiles
    | UngroupTiles
    | SetLayout
    | SetTileTitle
    | RemoveTile
    | SetWidgetTitle,
    Field(discriminator="op"),
]

WIDGET_OP_REGISTRY: dict[str, type[BaseWidgetOp]] = {
    cls.model_fields["op"].default: cls  # type: ignore[union-attr]
    for cls in (
        SetSpan, MoveTile, GroupTiles, UngroupTiles,
        SetLayout, SetTileTitle, RemoveTile, SetWidgetTitle,
    )
}


class WidgetOpList(BaseModel):
    """Structured-output target for a placement edit."""

    model_config = ConfigDict(extra="forbid")

    ops: list[AnyWidgetOp] = Field(default_factory=list)


class WidgetOpResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    widget: Any
    patch: Any = Field(default_factory=list)
    inverse: Any = Field(default_factory=list)
    applied: list[str] = Field(default_factory=list)
    failed: list[tuple[str, str]] = Field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.patch)

    def describe(self) -> list[str]:
        return [op.describe() for op in self.patch]


def parse_widget_ops(raw: Any) -> list[BaseWidgetOp]:
    """Coerce loosely-typed op dicts into models, raising on the first bad one."""
    return [
        entry if isinstance(entry, BaseWidgetOp) else _parse_one(entry)
        for entry in _entries(raw)
    ]


def _entries(raw: Any) -> list[Any]:
    if isinstance(raw, dict):
        raw = raw.get("ops", [])
    if not isinstance(raw, list):
        raise WidgetOpError(f"expected a list of ops, got {type(raw).__name__}")
    return raw


def _parse_one(entry: Any) -> BaseWidgetOp:
    if not isinstance(entry, dict):
        raise WidgetOpError(f"op entries must be objects, got {type(entry).__name__}")
    cls = WIDGET_OP_REGISTRY.get(str(entry.get("op")))
    if cls is None:
        raise WidgetOpError(
            f"unknown widget op {entry.get('op')!r}; known ops: {sorted(WIDGET_OP_REGISTRY)}"
        )
    return cls.model_validate(entry)


def _terse(exc: Exception) -> str:
    """Pydantic errors carry a URL and a full model dump; keep the first line."""
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        location = ".".join(str(p) for p in first.get("loc", ())) or "(op)"
        return f"{location}: {first.get('msg', 'invalid')}"
    return str(exc)


def apply_widget_ops(
    widget: Widget,
    ops: list[BaseWidgetOp] | list[dict[str, Any]] | dict[str, Any],
    *,
    strict: bool = False,
) -> WidgetOpResult:
    """Apply placement operations to a copy of ``widget``.

    A failing operation is recorded and skipped by default: three good moves
    and one bad tile id should still land the three.
    """
    before = widget.to_dict()
    working = copy.deepcopy(widget)

    applied: list[str] = []
    failed: list[tuple[str, str]] = []
    # Parsing happens per operation rather than up front: a malformed argument
    # in one edit should be reported like any other failure, not discard the
    # three valid edits queued behind it.
    for entry in _entries(ops):
        name = str(entry.get("op", "?")) if isinstance(entry, dict) else getattr(entry, "op", "?")
        try:
            operation = entry if isinstance(entry, BaseWidgetOp) else _parse_one(entry)
            operation.apply(working)
        except (WidgetOpError, ValidationError, ValueError, TypeError, KeyError, IndexError) as exc:
            if strict:
                raise
            failed.append((name, _terse(exc)))
            continue
        applied.append(name)

    patch: Patch = diff(before, working.to_dict())
    return WidgetOpResult(
        widget=working,
        patch=patch,
        inverse=invert(patch),
        applied=applied,
        failed=failed,
    )


def _is_op_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, BaseWidgetOp) for v in value)
