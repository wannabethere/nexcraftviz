"""Apply a theme to a spec.

Theming is a deterministic operation — there is no reason for a model to be in
the loop when someone says "use the dark theme". It routes through
:class:`~nexcraftviz.spec.ops.ApplyConfig` so the change is diffable and
undoable like any other edit.
"""
from __future__ import annotations

from typing import Any

from nexcraftviz.spec.model import Spec
from nexcraftviz.spec.ops import ApplyConfig, OpResult, apply_ops
from nexcraftviz.theme.tokens import ThemeTokens, load


def resolve(theme: str | ThemeTokens) -> ThemeTokens:
    return theme if isinstance(theme, ThemeTokens) else load(theme)


def apply_theme(
    spec: Spec | dict[str, Any],
    theme: str | ThemeTokens,
    *,
    replace: bool = True,
) -> OpResult:
    """Install ``theme``'s Vega config on ``spec``.

    ``replace`` defaults to True: switching themes should not leave fragments
    of the previous one behind. Pass False to layer a theme over hand-authored
    config a caller wants to keep.
    """
    tokens = resolve(theme)
    return apply_ops(spec, [ApplyConfig(config=tokens.vega_config(), replace=replace)])


def strip_hardcoded_colours(spec: Spec | dict[str, Any]) -> OpResult:
    """Remove per-mark colour literals so a theme can actually take effect.

    Generated specs routinely bake a brand hex into ``mark.color``. That
    overrides anything ``config`` says, so a theme switch appears to do nothing
    on exactly the charts people notice. Clearing those literals is the
    difference between theming that works and theming that looks broken.

    Colour *scales* are left alone — those are data encodings, not styling.
    """
    original = spec if isinstance(spec, Spec) else Spec(spec)
    working = original.clone()

    for view in working.views():
        mark = view.node.get("mark")
        if not isinstance(mark, dict):
            continue
        for key in ("color", "fill", "stroke"):
            mark.pop(key, None)
        # A mark dict reduced to just its type reads better as a bare string.
        if set(mark) == {"type"}:
            view.node["mark"] = mark["type"]

    from nexcraftviz.spec.diff import diff, invert

    patch = diff(original, working)
    return OpResult(
        spec=working,
        patch=patch,
        inverse=invert(patch),
        applied=["strip_hardcoded_colours"] if patch else [],
    )
