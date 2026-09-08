"""Native Vega-Lite composition — several charts as *one spec*.

This is the first of two ways to combine charts, and the one to reach for when
everything being combined is a Vega view. The result is a single spec: one
render, one export, one PNG, and — the real prize — the option of *shared
scales*, so two panels can be read against each other rather than side by side
with silently different axes.

Its limit is equally sharp: a `concat` can only contain Vega views. A KPI tile
or a `table_with_cells` payload cannot go inside one, and those are two of the
four families this stack emits. When a widget needs to mix families, use
:mod:`nexcraftviz.compose.widget` instead.

Composition takes N specs and returns one, so it does not fit the
`Spec -> Spec` signature of :mod:`nexcraftviz.spec.ops` and lives here rather
than as an operation.
"""
from __future__ import annotations

import copy
from typing import Any, Literal

from nexcraftviz.spec.model import Spec, SpecError

Direction = Literal["vertical", "horizontal", "wrap"]
Resolution = Literal["shared", "independent"]

#: Fallback width when a sub-view has to give up ``"container"``.
DEFAULT_SUBVIEW_WIDTH = 320
DEFAULT_SUBVIEW_HEIGHT = 200

#: Properties that belong to the composed document, not to a sub-view.
_ROOT_ONLY = ("$schema", "config", "background", "padding", "autosize", "usermeta")


class ComposeError(SpecError):
    """Raised when specs cannot be combined."""


def concat(
    specs: list[Spec | dict[str, Any]],
    *,
    direction: Direction = "vertical",
    columns: int | None = None,
    title: str = "",
    subtitle: str = "",
    resolve_scales: Resolution = "independent",
    share_data: bool = True,
) -> Spec:
    """Combine ``specs`` into one concatenated spec.

    ``direction`` picks the operator: ``vertical`` → ``vconcat``, ``horizontal``
    → ``hconcat``, ``wrap`` → ``concat`` with a ``columns`` count.

    ``resolve_scales`` defaults to ``independent`` because concatenated panels
    usually show *different measures*, and forcing a shared scale onto a
    revenue panel and a headcount panel makes one of them unreadable. Pass
    ``shared`` deliberately, when the panels are the same measure across
    different slices and comparing them is the whole point.
    """
    views = _prepare(specs)
    if not views:
        raise ComposeError("concat needs at least one spec")
    if len(views) == 1:
        return Spec(views[0])

    composed: dict[str, Any] = {"$schema": Spec({}).ensure_schema_url().raw.get("$schema")
                               or "https://vega.github.io/schema/vega-lite/v5.json"}

    if share_data:
        shared = _hoist_shared_data(views)
        if shared is not None:
            composed["data"] = shared

    operator = {"vertical": "vconcat", "horizontal": "hconcat", "wrap": "concat"}[direction]
    composed[operator] = views
    if operator == "concat":
        composed["columns"] = columns or 2

    if title:
        composed["title"] = {"text": title, "subtitle": subtitle} if subtitle else title

    # `shared` is the interesting case and must be stated explicitly; Vega-Lite
    # already defaults concat scales to independent.
    if resolve_scales == "shared":
        composed["resolve"] = {"scale": {"x": "shared", "y": "shared", "color": "shared"}}

    return Spec(composed)


def layer(
    specs: list[Spec | dict[str, Any]],
    *,
    title: str = "",
    resolve_scales: Resolution = "independent",
    axis: str = "y",
) -> Spec:
    """Stack ``specs`` on shared axes — the dual-axis / overlay shape.

    Unlike :func:`concat`, layering only makes sense when the panels share a
    positional axis: a bar of volume with a line of rate over the same
    categories. ``resolve_scales="independent"`` on ``axis`` is what makes a
    true dual-axis chart; ``shared`` keeps both on one scale.
    """
    views = _prepare(specs, for_layer=True)
    if len(views) < 2:
        raise ComposeError("layer needs at least two specs")

    composed: dict[str, Any] = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json"
    }
    shared = _hoist_shared_data(views)
    if shared is None:
        raise ComposeError(
            "layered specs must share their data — a layer has one data source, "
            "so combine the rows first or use concat instead"
        )
    composed["data"] = shared

    # Size belongs to the layer, not to each member: sub-views with their own
    # width inside a layer are ignored, which reads as a silent bug.
    for key in ("width", "height"):
        for view in views:
            if key in view:
                composed.setdefault(key, view.pop(key))
            view.pop(key, None)

    composed["layer"] = views
    if title:
        composed["title"] = title
    if resolve_scales == "independent":
        composed["resolve"] = {"scale": {axis: "independent"}}
    return Spec(composed)


def small_multiples(
    spec: Spec | dict[str, Any],
    field: str,
    *,
    columns: int = 3,
    title: str = "",
) -> Spec:
    """One panel per value of ``field``, via the ``column`` encoding channel.

    Uses encoding-level faceting rather than the top-level ``facet`` operator on
    purpose: the frontend renderer handles ``row``/``column`` channels and does
    not handle the ``facet`` + ``spec`` composition form, so the operator
    version would validate, compile, and then fail to appear.
    """
    raw = copy.deepcopy(spec.raw if isinstance(spec, Spec) else spec)
    if "layer" in raw or "vconcat" in raw or "hconcat" in raw or "concat" in raw:
        raise ComposeError(
            "small_multiples needs a single view — facet the panels before combining them"
        )

    encoding = raw.setdefault("encoding", {})
    if not isinstance(encoding, dict):
        raise ComposeError("spec has no usable encoding to facet")
    encoding["column"] = {"field": field, "type": "nominal"}

    # "container" resolves against the top-level view only, so a faceted chart
    # sized that way collapses. Give it a real number.
    if raw.get("width") == "container":
        raw["width"] = DEFAULT_SUBVIEW_WIDTH // 2
    raw["columns"] = columns
    if title:
        raw["title"] = title
    return Spec(raw)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _prepare(
    specs: list[Spec | dict[str, Any]], *, for_layer: bool = False
) -> list[dict[str, Any]]:
    """Copy each spec and strip what only belongs at the document root."""
    views: list[dict[str, Any]] = []
    for entry in specs:
        raw = entry.raw if isinstance(entry, Spec) else entry
        if not isinstance(raw, dict) or not raw:
            raise ComposeError("every spec must be a non-empty object")
        family = Spec(raw).family
        if family not in ("vega-lite", "unknown"):
            raise ComposeError(
                f"cannot concat a {family!r} payload — Vega composition holds Vega "
                "views only; use compose.widget for a mixed-family widget"
            )
        view = copy.deepcopy(raw)
        for key in _ROOT_ONLY:
            view.pop(key, None)
        if not for_layer:
            _normalise_size(view)
        views.append(view)
    return views


def _normalise_size(view: dict[str, Any]) -> None:
    """Replace ``"container"`` sizing inside a sub-view with real numbers.

    ``"width": "container"`` is a top-level-only feature. Left in place on a
    concatenated panel it does not error — it silently falls back to a default,
    so the panels come out a size nobody chose.
    """
    if view.get("width") == "container":
        view["width"] = DEFAULT_SUBVIEW_WIDTH
    if view.get("height") == "container":
        view["height"] = DEFAULT_SUBVIEW_HEIGHT
    view.setdefault("width", DEFAULT_SUBVIEW_WIDTH)
    view.setdefault("height", DEFAULT_SUBVIEW_HEIGHT)


def _hoist_shared_data(views: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Move ``data`` to the root when every view carries the same rows.

    Worth doing: the rows are inline, so N panels over one result set otherwise
    embed that result N times. On the corpus's larger pairs that is the
    difference between a 40 KB spec and a 300 KB one.
    """
    datas = [view.get("data") for view in views]
    if any(d is None for d in datas):
        return None
    first = datas[0]
    if not all(d == first for d in datas[1:]):
        return None
    for view in views:
        view.pop("data", None)
    return copy.deepcopy(first)
