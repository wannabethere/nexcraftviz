"""Server-side rendering via ``vl-convert``.

``vl-convert-python`` is a self-contained Rust wheel that embeds a JavaScript
engine — no Node, no headless browser, no system dependency beyond fonts. That
is what makes server-side rendering practical here: PNG export, dashboard
thumbnails, and — most valuable — an authoritative compile check that no amount
of JSON-schema validation can replace.

Everything in this module degrades gracefully: without the ``render`` extra
installed, :func:`available` returns False and the render functions raise a
clear :class:`RenderUnavailable` rather than an ImportError from deep inside a
call stack.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from nexcraftviz.spec.model import Spec

Format = Literal["svg", "png", "vega", "html"]

#: PNG scale factor for thumbnails and retina exports.
DEFAULT_SCALE = 1.0


class RenderUnavailable(RuntimeError):
    """Raised when a render is requested but ``vl-convert-python`` is missing."""

    def __init__(self) -> None:
        super().__init__(
            "server-side rendering needs the `render` extra: "
            "pip install 'nexcraftviz[render]'"
        )


class RenderError(RuntimeError):
    """The spec reached vl-convert but could not be rendered."""


@lru_cache(maxsize=1)
def _vlc() -> Any | None:
    try:
        import vl_convert
    except ImportError:
        return None
    return vl_convert


def available() -> bool:
    """True when server-side rendering is usable in this environment."""
    return _vlc() is not None


def _require() -> Any:
    module = _vlc()
    if module is None:
        raise RenderUnavailable
    return module


def to_vega(spec: Spec | dict[str, Any]) -> dict[str, Any]:
    """Compile Vega-Lite down to Vega.

    Doubles as the authoritative validity check — anything that survives this
    will render, and anything that does not, will not.
    """
    import json

    vlc = _require()
    payload = spec.to_json() if isinstance(spec, Spec) else json.dumps(spec, default=str)
    try:
        compiled = vlc.vegalite_to_vega(payload)
    except Exception as exc:  # noqa: BLE001 — vl_convert raises bare exceptions
        raise RenderError(f"Vega-Lite compile failed: {exc}") from exc
    return compiled if isinstance(compiled, dict) else json.loads(compiled)


def to_svg(spec: Spec | dict[str, Any], *, theme: str | None = None) -> str:
    """Render to an SVG string."""
    import json

    vlc = _require()
    payload = spec.to_json() if isinstance(spec, Spec) else json.dumps(spec, default=str)
    kwargs: dict[str, Any] = {}
    if theme:
        kwargs["theme"] = theme
    try:
        if _is_vega(spec):
            return vlc.vega_to_svg(payload, **kwargs)
        return vlc.vegalite_to_svg(payload, **kwargs)
    except Exception as exc:  # noqa: BLE001
        raise RenderError(f"SVG render failed: {exc}") from exc


def to_png(
    spec: Spec | dict[str, Any],
    *,
    scale: float = DEFAULT_SCALE,
    ppi: float | None = None,
    theme: str | None = None,
) -> bytes:
    """Render to PNG bytes.

    Text is drawn by ``resvg`` using the fonts available to the process — a
    container without fonts installed produces a chart with missing or
    substituted labels, so make sure the image ships some.
    """
    import json

    vlc = _require()
    payload = spec.to_json() if isinstance(spec, Spec) else json.dumps(spec, default=str)
    kwargs: dict[str, Any] = {"scale": scale}
    if ppi is not None:
        kwargs["ppi"] = ppi
    if theme:
        kwargs["theme"] = theme
    try:
        if _is_vega(spec):
            return vlc.vega_to_png(payload, **kwargs)
        return vlc.vegalite_to_png(payload, **kwargs)
    except Exception as exc:  # noqa: BLE001
        raise RenderError(f"PNG render failed: {exc}") from exc


def save(
    spec: Spec | dict[str, Any],
    path: str | Path,
    *,
    fmt: Format | None = None,
    scale: float = DEFAULT_SCALE,
) -> Path:
    """Render and write to ``path``; the format is inferred from the suffix."""
    target = Path(path)
    resolved: Format = fmt or _format_from_suffix(target.suffix)

    if resolved == "png":
        target.write_bytes(to_png(spec, scale=scale))
    elif resolved == "svg":
        target.write_text(to_svg(spec), encoding="utf-8")
    elif resolved == "vega":
        import json

        target.write_text(json.dumps(to_vega(spec), indent=2), encoding="utf-8")
    elif resolved == "html":
        target.write_text(to_html(spec), encoding="utf-8")
    else:  # pragma: no cover - guarded by _format_from_suffix
        raise ValueError(f"unsupported format: {resolved}")
    return target


def to_html(spec: Spec | dict[str, Any], *, title: str = "Chart") -> str:
    """A self-contained HTML page that renders the spec with vega-embed.

    Used by the CLI's ``--open`` flow and by the playground. Deliberately
    CDN-based and dependency-free so the output is one portable file.
    """
    import json

    payload = (
        spec.to_json(indent=2)
        if isinstance(spec, Spec)
        else json.dumps(spec, indent=2, default=str)
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/vega@5"></script>
<script src="https://cdn.jsdelivr.net/npm/vega-lite@5"></script>
<script src="https://cdn.jsdelivr.net/npm/vega-embed@6"></script>
<style>
  body {{ margin: 0; padding: 24px; font: 14px/1.5 system-ui, -apple-system, sans-serif; }}
  #chart {{ max-width: 100%; overflow-x: auto; }}
</style>
</head>
<body>
<div id="chart"></div>
<script>
  vegaEmbed('#chart', {payload}, {{actions: true}}).catch(console.error);
</script>
</body>
</html>
"""


def _is_vega(spec: Spec | dict[str, Any]) -> bool:
    if isinstance(spec, Spec):
        return spec.family == "vega"
    return "/vega/" in str(spec.get("$schema") or "") or isinstance(spec.get("marks"), list)


def _format_from_suffix(suffix: str) -> Format:
    mapping: dict[str, Format] = {
        ".png": "png",
        ".svg": "svg",
        ".json": "vega",
        ".html": "html",
        ".htm": "html",
    }
    resolved = mapping.get(suffix.lower())
    if resolved is None:
        raise ValueError(
            f"cannot infer format from suffix {suffix!r}; pass fmt= explicitly "
            f"(one of: png, svg, vega, html)"
        )
    return resolved
