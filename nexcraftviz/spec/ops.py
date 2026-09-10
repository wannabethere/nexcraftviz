"""The spec algebra — deterministic, composable Spec → Spec operations.

This is the core idea of the package. Today's chart editing asks an LLM to
re-emit an entire Vega-Lite document for every change: "make the bars teal"
costs a full generation, is non-deterministic, has no undo, and can corrupt a
spec that was already correct.

Here the LLM's only job is to choose *which operations, with which arguments*.
Applying them is ordinary code. That makes edits cheap (a couple of hundred
output tokens), reproducible, unit-testable without a model in the loop, and
reversible — :func:`apply_ops` returns the inverse patch alongside the result.

Every op is a Pydantic model with an ``op`` discriminator, so the whole set
serializes to one strict JSON schema that a structured-output call can target.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from nexcraftviz.spec.diff import Patch, diff, invert
from nexcraftviz.spec.model import Spec, SpecError, View

# Marks that stack by default and therefore accept a stack mode.
_STACKABLE_MARKS = frozenset({"bar", "area"})
# Channels a reference line can sit on.
_RULE_AXES = ("x", "y")
#: Window field LimitTopN installs. Module-level so the "replace my previous
#: limit rather than stacking a second one" check can find it.
_RANK_FIELD = "__nxv_rank"


class OpError(SpecError):
    """Raised when an operation cannot apply to the given spec."""


class BaseOp(BaseModel):
    """Root-level operation — changes the document, not one view inside it.

    Title, size, config and layer manipulation all live here. These ops take no
    ``view`` argument, which keeps the JSON schema an LLM sees honest: a model
    cannot pass ``view`` to ``set_title`` and quietly have it ignored.
    """

    model_config = ConfigDict(extra="forbid")

    def apply(self, spec: Spec) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    @staticmethod
    def _encoding(view: View) -> dict[str, Any]:
        enc = view.node.get("encoding")
        if not isinstance(enc, dict):
            enc = {}
            view.node["encoding"] = enc
        return enc

    @staticmethod
    def _channel(view: View, channel: str) -> dict[str, Any]:
        enc = BaseOp._encoding(view)
        definition = enc.get(channel)
        if not isinstance(definition, dict):
            definition = {}
            enc[channel] = definition
        return definition


class ViewOp(BaseOp):
    """Operation that acts on one or more leaf views.

    ``view`` selects which. Omitted, ops that change one thing act on the
    primary view and ops that are naturally uniform (palette, axis, tooltip)
    act on every view — a layered chart should recolour as a whole.
    """

    view: int | None = Field(
        default=None,
        description="Index into the spec's leaf views. Omit for the default target.",
    )

    def _target(self, spec: Spec) -> View:
        views = spec.views()
        if self.view is None:
            return views[0]
        if not 0 <= self.view < len(views):
            raise OpError(f"view index {self.view} out of range (spec has {len(views)})")
        return views[self.view]

    def _targets(self, spec: Spec) -> list[View]:
        views = spec.views()
        if self.view is None:
            return views
        if not 0 <= self.view < len(views):
            raise OpError(f"view index {self.view} out of range (spec has {len(views)})")
        return [views[self.view]]


# ---------------------------------------------------------------------------
# Marks and geometry
# ---------------------------------------------------------------------------

class MarkProperties(BaseModel):
    """The mark properties a model may set — typed, so strict mode accepts them.

    This was a free-form ``dict``. OpenAI's strict mode refuses an open object,
    and the refusal is silent (the provider falls back to a looser binding), so
    every viz.edit call ran unenforced. Listing the properties closes it for the
    model; ``extra="allow"`` keeps accepting any other key from callers — an MCP
    agent sending ``{"tension": 0.5}`` still works exactly as before.

    Colour is deliberately absent. It belongs to the theme, and a hard-coded
    mark colour is the thing the ``no_baked_styling`` gate exists to catch.
    """

    model_config = ConfigDict(extra="allow")

    point: bool | None = Field(default=None, description="Draw a point at each value (lines).")
    filled: bool | None = None
    cornerRadius: float | None = Field(default=None, description="Rounded bar corners, px.")
    strokeWidth: float | None = None
    strokeDash: list[float] | None = Field(default=None, description="Dash pattern, e.g. [4, 2].")
    interpolate: str | None = Field(
        default=None, description="linear | monotone | step | basis — line and area shape."
    )
    opacity: float | None = None
    size: float | None = None
    orient: str | None = Field(default=None, description="horizontal | vertical")
    innerRadius: float | None = Field(default=None, description="Donut hole, px.")
    tooltip: bool | None = None


class SetMark(ViewOp):
    """Change the mark type, preserving mark properties where they still apply."""

    op: Literal["set_mark"] = "set_mark"
    mark: str = Field(description="Vega-Lite mark type, e.g. bar, line, area, point, arc.")
    properties: MarkProperties = Field(
        default_factory=MarkProperties,
        description="Mark properties to merge, e.g. {'point': true, 'cornerRadius': 3}.",
    )

    def apply(self, spec: Spec) -> None:
        for view in self._targets(spec):
            existing = view.node.get("mark")
            props: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
            props["type"] = self.mark
            props.update(self.properties.model_dump(exclude_none=True))
            # A bare string mark stays a bare string when nothing else is set —
            # it keeps hand-written specs readable.
            view.node["mark"] = props if len(props) > 1 else self.mark


class SetSize(BaseOp):
    """Set width/height. Always root-level: sizing a single layer is a bug."""

    op: Literal["set_size"] = "set_size"
    width: int | str | None = Field(default=None, description="Pixels, or 'container'.")
    height: int | str | None = Field(default=None, description="Pixels, or 'container'.")

    def apply(self, spec: Spec) -> None:
        if self.width is not None:
            spec.raw["width"] = self.width
        if self.height is not None:
            spec.raw["height"] = self.height


class SetTitle(BaseOp):
    op: Literal["set_title"] = "set_title"
    text: str = Field(description="Chart title. Empty string removes the title.")
    subtitle: str = Field(default="", description="Optional subtitle line.")

    def apply(self, spec: Spec) -> None:
        if not self.text and not self.subtitle:
            spec.raw.pop("title", None)
            return
        if self.subtitle:
            spec.raw["title"] = {"text": self.text, "subtitle": self.subtitle}
        else:
            spec.raw["title"] = self.text


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------

class SetPalette(ViewOp):
    """Recolour the chart.

    Three mutually exclusive forms, in precedence order: an explicit ``range``
    of hex colours, a named Vega ``scheme``, or a single flat ``color`` applied
    to the mark. A flat colour only makes sense when nothing is colour-encoded,
    so that form clears any colour scale rather than leaving a stale one.
    """

    op: Literal["set_palette"] = "set_palette"
    scheme: str = Field(default="", description="Named Vega scheme, e.g. 'tealblues'.")
    range: list[str] = Field(default_factory=list, description="Explicit hex colours.")
    color: str = Field(default="", description="Single flat colour for the mark.")

    def apply(self, spec: Spec) -> None:
        if not (self.scheme or self.range or self.color):
            raise OpError("set_palette needs one of: scheme, range, color")

        for view in self._targets(spec):
            enc = self._encoding(view)
            has_color_field = isinstance(enc.get("color"), dict) and (
                "field" in enc["color"] or "condition" in enc["color"]
            )

            if (self.scheme or self.range) and has_color_field:
                scale = enc["color"].get("scale")
                if not isinstance(scale, dict):
                    scale = {}
                    enc["color"]["scale"] = scale
                scale.pop("scheme", None)
                scale.pop("range", None)
                if self.range:
                    scale["range"] = list(self.range)
                else:
                    scale["scheme"] = self.scheme
                continue

            # No colour encoding: paint the mark itself.
            flat = self.color or (self.range[0] if self.range else "")
            if flat:
                mark = view.node.get("mark")
                if isinstance(mark, dict):
                    mark["color"] = flat
                else:
                    view.node["mark"] = {"type": mark or "bar", "color": flat}
            elif self.scheme:
                # A scheme with nothing to map it over is meaningless; record it
                # at config level so a later SetColorField picks it up.
                config = spec.raw.setdefault("config", {})
                if isinstance(config, dict):
                    config.setdefault("range", {})["category"] = {"scheme": self.scheme}


class SetColorField(ViewOp):
    """Colour marks by a column — the usual way to add a series breakdown."""

    op: Literal["set_color_field"] = "set_color_field"
    field: str = Field(description="Column to colour by. Empty string removes colour encoding.")
    type: str = Field(default="nominal", description="quantitative | temporal | nominal | ordinal.")
    legend_title: str = Field(default="", description="Optional legend title.")

    def apply(self, spec: Spec) -> None:
        for view in self._targets(spec):
            enc = self._encoding(view)
            if not self.field:
                enc.pop("color", None)
                continue
            definition = self._channel(view, "color")
            definition["field"] = self.field
            definition["type"] = self.type
            if self.legend_title:
                legend = definition.get("legend")
                if not isinstance(legend, dict):
                    legend = {}
                    definition["legend"] = legend
                legend["title"] = self.legend_title


# ---------------------------------------------------------------------------
# Ordering and filtering
# ---------------------------------------------------------------------------

class SortBy(ViewOp):
    """Sort a categorical axis.

    ``by`` names the measure to sort on; omit it to sort by the channel's own
    values. Vega-Lite's shorthand ``"-x"`` (descending by the x encoding) is
    what most ranked bar charts want, and is what you get by default.
    """

    op: Literal["sort_by"] = "sort_by"
    channel: str = Field(default="y", description="Channel whose axis is being sorted.")
    by: str = Field(default="", description="Field to sort by. Empty = the channel's own field.")
    order: Literal["ascending", "descending"] = "descending"

    def apply(self, spec: Spec) -> None:
        view = self._target(spec)
        definition = self._channel(view, self.channel)
        if self.by:
            definition["sort"] = {"field": self.by, "order": self.order}
            return
        opposite = "x" if self.channel == "y" else "y"
        definition["sort"] = f"{'-' if self.order == 'descending' else ''}{opposite}"


class LimitTopN(ViewOp):
    """Keep only the top (or bottom) N rows.

    Implemented as a window + filter transform rather than by truncating the
    data, so the limit survives a data refresh — the whole point of keeping it
    in the spec.
    """

    op: Literal["limit_top_n"] = "limit_top_n"
    n: int = Field(gt=0, description="How many rows to keep.")
    by: str = Field(description="Measure field to rank on.")
    order: Literal["ascending", "descending"] = "descending"
    groupby: list[str] = Field(default_factory=list, description="Rank within these groups.")

    def apply(self, spec: Spec) -> None:
        view = self._target(spec)
        transforms = view.node.get("transform")
        if not isinstance(transforms, list):
            transforms = []
            view.node["transform"] = transforms

        # Replace any limit we previously installed rather than stacking them.
        transforms[:] = [t for t in transforms if not _is_nxv_rank(t, _RANK_FIELD)]

        window: dict[str, Any] = {
            "window": [{"op": "row_number", "as": _RANK_FIELD}],
            "sort": [{"field": self.by, "order": self.order}],
        }
        if self.groupby:
            window["groupby"] = list(self.groupby)
        transforms.insert(0, window)
        transforms.insert(1, {"filter": f"datum.{_RANK_FIELD} <= {self.n}"})


def _is_nxv_rank(transform: Any, rank_field: str) -> bool:
    if not isinstance(transform, dict):
        return False
    window = transform.get("window")
    if isinstance(window, list) and any(
        isinstance(w, dict) and w.get("as") == rank_field for w in window
    ):
        return True
    filter_expr = transform.get("filter")
    return isinstance(filter_expr, str) and rank_field in filter_expr


class StackMode(ViewOp):
    """Set or clear stacking. ``none`` is how you turn a stacked bar into an
    overlapping one; use :class:`GroupBy` for side-by-side instead."""

    op: Literal["stack_mode"] = "stack_mode"
    mode: Literal["zero", "normalize", "center", "none"] = "zero"
    channel: str = Field(default="y", description="The quantitative channel being stacked.")

    def apply(self, spec: Spec) -> None:
        for view in self._targets(spec):
            if view.mark_type and view.mark_type not in _STACKABLE_MARKS:
                continue
            definition = self._channel(view, self.channel)
            definition["stack"] = None if self.mode == "none" else self.mode


# ---------------------------------------------------------------------------
# Breakdown — the "show me more detail" family
# ---------------------------------------------------------------------------

class GroupBy(ViewOp):
    """Side-by-side grouping via ``xOffset`` — the grouped-bar shape.

    Also sets the colour encoding, because a grouped bar chart without colour
    is unreadable, and clears stacking, because the two are mutually exclusive.
    """

    op: Literal["group_by"] = "group_by"
    field: str = Field(description="Column to group by. Empty string removes grouping.")
    type: str = Field(default="nominal")

    def apply(self, spec: Spec) -> None:
        for view in self._targets(spec):
            enc = self._encoding(view)
            if not self.field:
                enc.pop("xOffset", None)
                continue
            enc["xOffset"] = {"field": self.field, "type": self.type}
            color = enc.get("color")
            if not isinstance(color, dict) or "field" not in color:
                enc["color"] = {"field": self.field, "type": self.type}
            for channel in ("y", "x"):
                definition = enc.get(channel)
                if isinstance(definition, dict) and definition.get("type") == "quantitative":
                    definition["stack"] = None


class FacetBy(ViewOp):
    """Small multiples — one panel per value of ``field``.

    Uses the ``row``/``column`` encoding channels rather than the top-level
    ``facet`` operator, because the frontend renderer handles encoding-level
    faceting and does not handle the ``facet``+``spec`` composition form.
    """

    op: Literal["facet_by"] = "facet_by"
    field: str = Field(description="Column to facet by. Empty string removes faceting.")
    mode: Literal["row", "column"] = "column"
    type: str = Field(default="nominal")
    columns: int | None = Field(default=None, description="Wrap after this many panels.")

    def apply(self, spec: Spec) -> None:
        for view in self._targets(spec):
            enc = self._encoding(view)
            if not self.field:
                enc.pop("row", None)
                enc.pop("column", None)
                continue
            enc.pop("row" if self.mode == "column" else "column", None)
            enc[self.mode] = {"field": self.field, "type": self.type}
        if self.columns and self.field:
            spec.raw["columns"] = self.columns


class BinField(ViewOp):
    """Bin a quantitative field — turns a scatter into a histogram."""

    op: Literal["bin_field"] = "bin_field"
    channel: str = Field(default="x")
    maxbins: int | None = Field(default=None, gt=0)
    step: float | None = Field(default=None, gt=0)
    enabled: bool = True

    def apply(self, spec: Spec) -> None:
        view = self._target(spec)
        definition = self._channel(view, self.channel)
        if not self.enabled:
            definition.pop("bin", None)
            return
        if self.step is not None:
            definition["bin"] = {"step": self.step}
        elif self.maxbins is not None:
            definition["bin"] = {"maxbins": self.maxbins}
        else:
            definition["bin"] = True
        definition.setdefault("type", "quantitative")


class Aggregate(ViewOp):
    """Set an aggregate on a channel (sum, mean, count, …)."""

    op: Literal["aggregate"] = "aggregate"
    channel: str = Field(default="y")
    aggregate: str = Field(description="sum | mean | median | min | max | count | distinct")
    field: str = Field(default="", description="Field to aggregate. Omit for count.")

    def apply(self, spec: Spec) -> None:
        view = self._target(spec)
        definition = self._channel(view, self.channel)
        definition["aggregate"] = self.aggregate
        if self.aggregate == "count":
            definition.pop("field", None)
        elif self.field:
            definition["field"] = self.field
        definition.setdefault("type", "quantitative")


# ---------------------------------------------------------------------------
# Layers — series, reference lines, annotations
# ---------------------------------------------------------------------------

def _ensure_layered(spec: Spec) -> list[dict[str, Any]]:
    """Convert a unit spec into a one-element layer, returning the layer list.

    Root-level ``data``, ``width``, ``height``, ``title`` and ``config`` stay at
    the root — Vega-Lite requires shared data above the layer, and duplicating
    size into each layer produces a chart that will not resolve.
    """
    if isinstance(spec.raw.get("layer"), list):
        return spec.raw["layer"]
    if not ("mark" in spec.raw or "encoding" in spec.raw):
        raise OpError("cannot add a layer to a spec that has no mark or encoding")

    shared = ("$schema", "data", "width", "height", "title", "config", "background", "padding")
    unit = {k: v for k, v in spec.raw.items() if k not in shared}
    for key in list(spec.raw):
        if key not in shared:
            spec.raw.pop(key)
    spec.raw["layer"] = [unit]
    return spec.raw["layer"]


class AddReferenceLine(BaseOp):
    """Add a target/threshold rule — the single most-requested chart annotation."""

    op: Literal["add_reference_line"] = "add_reference_line"
    value: float | None = Field(default=None, description="Constant position for the rule.")
    field: str = Field(default="", description="Field whose aggregate positions the rule.")
    aggregate: str = Field(default="mean", description="Used with `field`, e.g. mean/median.")
    axis: Literal["x", "y"] = "y"
    label: str = Field(default="", description="Optional text label drawn at the rule.")
    color: str = Field(default="#94a3b8")
    stroke_dash: list[float] = Field(default_factory=lambda: [4.0, 4.0])

    def apply(self, spec: Spec) -> None:
        if self.value is None and not self.field:
            raise OpError("add_reference_line needs either `value` or `field`")
        if self.axis not in _RULE_AXES:
            raise OpError(f"axis must be one of {_RULE_AXES}")

        layers = _ensure_layered(spec)
        encoding: dict[str, Any] = {}
        if self.value is not None:
            encoding[self.axis] = {"datum": self.value, "type": "quantitative"}
        else:
            encoding[self.axis] = {
                "field": self.field,
                "aggregate": self.aggregate,
                "type": "quantitative",
            }

        layers.append(
            {
                "mark": {
                    "type": "rule",
                    "color": self.color,
                    "strokeDash": list(self.stroke_dash),
                    "strokeWidth": 1.5,
                },
                "encoding": encoding,
            }
        )
        if self.label:
            layers.append(
                {
                    "mark": {
                        "type": "text",
                        "align": "right",
                        "baseline": "bottom",
                        "dx": -4,
                        "dy": -4,
                        "color": self.color,
                    },
                    "encoding": {
                        **encoding,
                        "text": {"value": self.label},
                    },
                }
            )


class AddAnnotation(BaseOp):
    """Drop a text callout at a data position."""

    op: Literal["add_annotation"] = "add_annotation"
    text: str
    # Typed, not `Any`: one untyped field drops viz.edit's whole schema out of
    # OpenAI's strict mode. A datum is a category, a number or a date string.
    x: str | int | float | None = Field(
        default=None, description="Datum value on x, or None to omit."
    )
    y: str | int | float | None = Field(
        default=None, description="Datum value on y, or None to omit."
    )
    color: str = Field(default="#334155")

    def apply(self, spec: Spec) -> None:
        if self.x is None and self.y is None:
            raise OpError("add_annotation needs at least one of x / y")
        layers = _ensure_layered(spec)
        encoding: dict[str, Any] = {"text": {"value": self.text}}
        if self.x is not None:
            encoding["x"] = {"datum": self.x}
        if self.y is not None:
            encoding["y"] = {"datum": self.y}
        layers.append(
            {
                "mark": {"type": "text", "color": self.color, "fontWeight": "bold", "dy": -8},
                "encoding": encoding,
            }
        )


class AddSeries(BaseOp):
    """Add a second measure as its own layer — the dual-measure shape.

    When ``independent_scale`` is set the layers get separate y scales, which
    is the dual-axis chart Vega-Lite expresses as ``resolve.scale.y='independent'``.
    """

    op: Literal["add_series"] = "add_series"
    field: str = Field(description="Measure to add.")
    mark: str = Field(default="line")
    axis: Literal["x", "y"] = "y"
    color: str = Field(default="")
    independent_scale: bool = False

    def apply(self, spec: Spec) -> None:
        layers = _ensure_layered(spec)
        template = layers[0] if layers else {}
        base_encoding = template.get("encoding", {}) if isinstance(template, dict) else {}

        encoding: dict[str, Any] = {}
        # Carry the positional channel we are *not* replacing, so the new series
        # lines up with the existing one.
        keep = "x" if self.axis == "y" else "y"
        if isinstance(base_encoding.get(keep), dict):
            encoding[keep] = dict(base_encoding[keep])
        encoding[self.axis] = {"field": self.field, "type": "quantitative"}

        mark: dict[str, Any] = {"type": self.mark}
        if self.color:
            mark["color"] = self.color
        layers.append({"mark": mark, "encoding": encoding})

        if self.independent_scale:
            resolve = spec.raw.setdefault("resolve", {})
            if isinstance(resolve, dict):
                resolve.setdefault("scale", {})[self.axis] = "independent"


class DropSeries(BaseOp):
    """Remove a layer by index, collapsing back to a unit spec when one remains."""

    op: Literal["drop_series"] = "drop_series"
    index: int = Field(ge=0, description="Layer index to remove.")

    def apply(self, spec: Spec) -> None:
        layers = spec.raw.get("layer")
        if not isinstance(layers, list):
            raise OpError("spec has no layers to drop")
        if not 0 <= self.index < len(layers):
            raise OpError(f"layer index {self.index} out of range ({len(layers)} layers)")
        layers.pop(self.index)
        if len(layers) == 1 and isinstance(layers[0], dict):
            only = layers.pop()
            spec.raw.pop("layer")
            spec.raw.update(only)


# ---------------------------------------------------------------------------
# Axes, scales, tooltips
# ---------------------------------------------------------------------------

class SetAxis(ViewOp):
    """Adjust axis presentation. ``None`` leaves a property untouched;
    an explicit empty title (``""``) hides it, which is what ranked bar charts
    normally want on the category axis."""

    op: Literal["set_axis"] = "set_axis"
    channel: str = Field(default="x")
    title: str | None = None
    format: str | None = Field(default=None, description="d3-format string, e.g. '.1%'.")
    grid: bool | None = None
    label_angle: float | None = None
    tick_count: int | None = None

    def apply(self, spec: Spec) -> None:
        for view in self._targets(spec):
            enc = view.encoding
            if self.channel not in enc:
                continue
            definition = self._channel(view, self.channel)
            axis = definition.get("axis")
            if not isinstance(axis, dict):
                axis = {}
            if self.title is not None:
                axis["title"] = self.title or None
            if self.format is not None:
                axis["format"] = self.format
            if self.grid is not None:
                axis["grid"] = self.grid
            if self.label_angle is not None:
                axis["labelAngle"] = self.label_angle
            if self.tick_count is not None:
                axis["tickCount"] = self.tick_count
            definition["axis"] = axis


class SetScale(ViewOp):
    op: Literal["set_scale"] = "set_scale"
    channel: str = Field(default="y")
    type: str | None = Field(default=None, description="linear | log | sqrt | pow | time | band")
    zero: bool | None = None
    nice: bool | None = None
    # Typed items: `list[Any]` puts an untyped schema in viz.edit, and strict
    # mode refuses it. A domain is numbers, categories or date strings.
    domain: list[str | int | float] | None = None

    def apply(self, spec: Spec) -> None:
        for view in self._targets(spec):
            if self.channel not in view.encoding:
                continue
            definition = self._channel(view, self.channel)
            scale = definition.get("scale")
            if not isinstance(scale, dict):
                scale = {}
            if self.type is not None:
                scale["type"] = self.type
            if self.zero is not None:
                scale["zero"] = self.zero
            if self.nice is not None:
                scale["nice"] = self.nice
            if self.domain is not None:
                scale["domain"] = list(self.domain)
            definition["scale"] = scale


class ResolveScale(BaseOp):
    """Share or separate scales across layers/facets — the dual-axis switch."""

    op: Literal["resolve_scale"] = "resolve_scale"
    channel: str = Field(default="y")
    resolution: Literal["shared", "independent"] = "independent"

    def apply(self, spec: Spec) -> None:
        resolve = spec.raw.get("resolve")
        if not isinstance(resolve, dict):
            resolve = {}
            spec.raw["resolve"] = resolve
        scale = resolve.get("scale")
        if not isinstance(scale, dict):
            scale = {}
            resolve["scale"] = scale
        scale[self.channel] = self.resolution


class SetTooltip(ViewOp):
    """Set the hover tooltip. An empty ``fields`` list turns tooltips off."""

    op: Literal["set_tooltip"] = "set_tooltip"
    fields: list[str] = Field(default_factory=list)
    #: Hidden from the model's schema, still accepted from callers. A model
    #: should not have to know measurement types — the spec already does — and
    #: an open ``dict`` here kept viz.edit out of strict mode. Absent a type,
    #: `apply` reads it from the spec instead of assuming "nominal", which was
    #: wrong for every number.
    types: SkipJsonSchema[dict[str, str]] = Field(default_factory=dict)

    def apply(self, spec: Spec) -> None:
        for view in self._targets(spec):
            enc = self._encoding(view)
            if not self.fields:
                enc.pop("tooltip", None)
                continue
            enc["tooltip"] = [
                {"field": name, "type": self._type_of(name, view, spec)} for name in self.fields
            ]

    def _type_of(self, name: str, view: View, spec: Spec) -> str:
        """A caller's type, else the one this field is already encoded with,
        else the type its values imply."""
        if self.types.get(name):
            return self.types[name]
        for definition in view.encoding.values():
            entries = definition if isinstance(definition, list) else [definition]
            for entry in entries:
                if isinstance(entry, dict) and entry.get("field") == name and entry.get("type"):
                    return str(entry["type"])
        for row in spec.data_values:
            value = row.get(name)
            if value is None:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return "quantitative"
            return "nominal"
        return "nominal"


class ApplyConfig(BaseOp):
    """Merge a ``config`` block — how a theme reaches the spec.

    Kept generic rather than theme-aware so :mod:`nexcraftviz.theme` owns what a
    theme *is* and this module only knows how to install one.
    """

    op: Literal["apply_config"] = "apply_config"
    config: dict[str, Any] = Field(default_factory=dict)
    replace: bool = Field(
        default=False, description="Replace the whole config instead of deep-merging."
    )

    def apply(self, spec: Spec) -> None:
        if self.replace or not isinstance(spec.raw.get("config"), dict):
            spec.raw["config"] = dict(self.config)
            return
        spec.raw["config"] = _deep_merge(spec.raw["config"], self.config)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# The union — this is what an LLM emits
# ---------------------------------------------------------------------------

AnyOp = Annotated[
    SetMark
    | SetSize
    | SetTitle
    | SetPalette
    | SetColorField
    | SortBy
    | LimitTopN
    | StackMode
    | GroupBy
    | FacetBy
    | BinField
    | Aggregate
    | AddReferenceLine
    | AddAnnotation
    | AddSeries
    | DropSeries
    | SetAxis
    | SetScale
    | ResolveScale
    | SetTooltip
    | ApplyConfig,
    Field(discriminator="op"),
]

#: What a MODEL may emit — every operation except `ApplyConfig`.
#:
#: `ApplyConfig` is how a theme reaches a spec: the theme system builds it from
#: tokens. A model hand-writing a config block is exactly the baked-in styling
#: the theme exists to prevent, and the edit prompt never asks for it. It is
#: also an open ``dict``, which OpenAI's strict mode refuses — leaving it in the
#: union made every viz.edit call silently non-strict. `AnyOp` keeps it, so the
#: theme system and MCP callers are unaffected.
ModelOp = Annotated[
    SetMark
    | SetSize
    | SetTitle
    | SetPalette
    | SetColorField
    | SortBy
    | LimitTopN
    | StackMode
    | GroupBy
    | FacetBy
    | BinField
    | Aggregate
    | AddReferenceLine
    | AddAnnotation
    | AddSeries
    | DropSeries
    | SetAxis
    | SetScale
    | ResolveScale
    | SetTooltip,
    Field(discriminator="op"),
]

OP_REGISTRY: dict[str, type[BaseOp]] = {
    cls.model_fields["op"].default: cls  # type: ignore[union-attr]
    for cls in (
        SetMark, SetSize, SetTitle, SetPalette, SetColorField, SortBy, LimitTopN,
        StackMode, GroupBy, FacetBy, BinField, Aggregate, AddReferenceLine,
        AddAnnotation, AddSeries, DropSeries, SetAxis, SetScale, ResolveScale,
        SetTooltip, ApplyConfig,
    )
}


class OpList(BaseModel):
    """Wrapper so the op union can be a structured-output target."""

    model_config = ConfigDict(extra="forbid")

    ops: list[AnyOp] = Field(default_factory=list)


class OpResult(BaseModel):
    """What :func:`apply_ops` returns.

    ``inverse`` is a patch, not a list of ops — see :mod:`nexcraftviz.spec.diff`
    for why undo is derived rather than hand-written per operation.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    spec: Any
    patch: Any = Field(default_factory=list)
    inverse: Any = Field(default_factory=list)
    applied: list[str] = Field(default_factory=list)
    failed: list[tuple[str, str]] = Field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.patch)

    def describe(self) -> list[str]:
        return [op.describe() for op in self.patch]


def parse_ops(raw: Any) -> list[BaseOp]:
    """Coerce loosely-typed op dicts (an LLM's output) into op models."""
    if isinstance(raw, dict):
        raw = raw.get("ops", [])
    if not isinstance(raw, list):
        raise OpError(f"expected a list of ops, got {type(raw).__name__}")
    parsed: list[BaseOp] = []
    for entry in raw:
        if isinstance(entry, BaseOp):
            parsed.append(entry)
            continue
        if not isinstance(entry, dict):
            raise OpError(f"op entries must be objects, got {type(entry).__name__}")
        name = entry.get("op")
        cls = OP_REGISTRY.get(str(name))
        if cls is None:
            raise OpError(f"unknown op {name!r}; known ops: {sorted(OP_REGISTRY)}")
        parsed.append(cls.model_validate(entry))
    return parsed


def apply_ops(
    spec: Spec | dict[str, Any],
    ops: list[BaseOp] | list[dict[str, Any]] | dict[str, Any],
    *,
    strict: bool = False,
) -> OpResult:
    """Apply operations to a clone of ``spec``.

    By default a failing op is recorded and skipped so one bad argument does not
    discard the whole edit — an LLM emitting four ops where the third is wrong
    should still get the other three. Pass ``strict=True`` to raise instead.
    """
    original = spec if isinstance(spec, Spec) else Spec(spec)
    working = original.clone()
    parsed = ops if _is_op_list(ops) else parse_ops(ops)

    applied: list[str] = []
    failed: list[tuple[str, str]] = []
    for operation in parsed:  # type: ignore[union-attr]
        name = getattr(operation, "op", operation.__class__.__name__)
        try:
            operation.apply(working)
        except (OpError, SpecError, ValueError, TypeError, KeyError) as exc:
            if strict:
                raise
            failed.append((name, str(exc)))
            continue
        applied.append(name)

    patch: Patch = diff(original, working)
    return OpResult(
        spec=working,
        patch=patch,
        inverse=invert(patch),
        applied=applied,
        failed=failed,
    )


def _is_op_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, BaseOp) for v in value)
