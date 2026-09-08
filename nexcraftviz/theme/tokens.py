"""Theme tokens — one source of truth, two outputs.

A Vega-Lite ``config`` block styles the *plot*. It does nothing for the payloads
this stack emits that are not Vega at all: KPI tiles (``kpi_metadata``), rich
tables (``table_with_cells``) and the dashboard grid are hand-written frontend
components. A theme system that only speaks Vega leaves those unstyled and
drifting apart from the charts beside them.

So a theme is a set of semantic tokens, and two renderers consume it:

* :mod:`nexcraftviz.theme.vega` → a Vega-Lite ``config`` block
* :mod:`nexcraftviz.theme.css`  → CSS custom properties + component classes

Presets are YAML files, not code, so a tenant theme is a file someone can edit
without touching Python.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

PRESET_DIR = Path(__file__).parent / "presets"

Mode = Literal["light", "dark"]


class Palette(BaseModel):
    """Colour scales. ``categorical`` is a list; the others name a Vega scheme
    or give an explicit ramp."""

    model_config = ConfigDict(extra="forbid")

    categorical: list[str] = Field(default_factory=list)
    sequential: str | list[str] = "blues"
    diverging: str | list[str] = "redblue"
    ordinal: str | list[str] = "blues"


class SemanticColors(BaseModel):
    """Colours with meaning rather than position.

    These are what KPI tiles and conditional formatting reach for — a
    percent-change tile is green or red because of what the number *means*, not
    because of where it sits in a categorical scale.
    """

    model_config = ConfigDict(extra="forbid")

    positive: str = "#16a34a"
    negative: str = "#dc2626"
    warning: str = "#d97706"
    neutral: str = "#64748b"
    target: str = "#94a3b8"
    accent: str = "#0c8ba6"


class Surfaces(BaseModel):
    model_config = ConfigDict(extra="forbid")

    background: str = "#ffffff"
    surface: str = "#ffffff"
    surface_alt: str = "#f8fafc"
    border: str = "#e2e8f0"
    border_strong: str = "#cbd5e1"
    grid: str = "#e2e8f0"


class TextColors(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: str = "#0f172a"
    secondary: str = "#475569"
    muted: str = "#94a3b8"
    inverse: str = "#ffffff"


class Typography(BaseModel):
    model_config = ConfigDict(extra="forbid")

    font_family: str = (
        "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
    )
    mono_family: str = "ui-monospace, SFMono-Regular, Menlo, monospace"
    size_xs: float = 11
    size_sm: float = 12
    size_base: float = 13
    size_lg: float = 16
    size_xl: float = 20
    size_display: float = 34
    weight_normal: int = 400
    weight_medium: int = 500
    weight_bold: int = 700


class Geometry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    radius_sm: float = 3
    radius_md: float = 6
    radius_lg: float = 12
    spacing: float = 8
    border_width: float = 1
    bar_corner_radius: float = 3
    line_width: float = 2
    point_size: float = 60


class ThemeTokens(BaseModel):
    """A complete theme."""

    model_config = ConfigDict(extra="forbid")

    name: str
    mode: Mode = "light"
    description: str = ""
    palette: Palette = Field(default_factory=Palette)
    semantic: SemanticColors = Field(default_factory=SemanticColors)
    surfaces: Surfaces = Field(default_factory=Surfaces)
    text: TextColors = Field(default_factory=TextColors)
    typography: Typography = Field(default_factory=Typography)
    geometry: Geometry = Field(default_factory=Geometry)

    vega_base: Literal["tokens", "overrides"] = Field(
        default="tokens",
        description=(
            "Where the Vega config comes from. 'tokens' generates it and merges "
            "`vega_overrides` on top. 'overrides' emits `vega_overrides` alone — "
            "which is what a preset derived from an existing vega-themes theme "
            "wants, since adding anything of ours to it would change how charts "
            "render and defeat the point of deriving it."
        ),
    )
    vega_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "The Vega config, or an overlay on the generated one, per `vega_base`. "
            "Derived presets put the original vega-themes config here verbatim so "
            "charts render identically to today, while the CSS side still comes "
            "from the tokens above."
        ),
    )
    css_overrides: dict[str, str] = Field(
        default_factory=dict,
        description="Extra CSS custom properties, merged over the generated ones.",
    )

    # -- convenience --------------------------------------------------------

    @property
    def is_dark(self) -> bool:
        return self.mode == "dark"

    def vega_config(self) -> dict[str, Any]:
        from nexcraftviz.theme.vega import to_vega_config

        return to_vega_config(self)

    def css(self, *, selector: str = ":root") -> str:
        from nexcraftviz.theme.css import to_css

        return to_css(self, selector=selector)

    def contrast_report(self):  # noqa: ANN201 - avoids a circular import in the signature
        from nexcraftviz.theme.contrast import audit

        return audit(self)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

class ThemeNotFound(KeyError):
    def __init__(self, name: str, available: list[str]) -> None:
        super().__init__(
            f"unknown theme {name!r}; available: {', '.join(available) or '(none)'}"
        )


def available_themes() -> list[str]:
    if not PRESET_DIR.exists():
        return []
    return sorted(p.stem for p in PRESET_DIR.glob("*.yaml"))


@lru_cache(maxsize=32)
def load(name: str) -> ThemeTokens:
    """Load a preset by name."""
    path = PRESET_DIR / f"{name}.yaml"
    if not path.exists():
        raise ThemeNotFound(name, available_themes())
    return from_yaml(path)


def from_yaml(path: str | Path) -> ThemeTokens:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return ThemeTokens.model_validate(data)


def default(mode: Mode = "light") -> ThemeTokens:
    return load(f"nexcraftviz-{mode}")
