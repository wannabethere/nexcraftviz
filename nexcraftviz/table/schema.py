"""The ``table_with_cells`` contract, as the corpus actually defines it.

This is not a new format. 30 of the 200 corpus pairs already emit a
``columns_schema``, and the vocabulary below is taken from them verbatim rather
than invented — 10 renderers, and the per-renderer options each one accepts.

The awkward part is that nothing downstream reads it. A ``table_with_cells``
payload reaches the frontend, fails to parse as Vega-Lite, and falls back to an
untyped grid, so avatars, progress bars and pills are silently dropped. Writing
the contract down as types is the first step to fixing that; the HTML renderer
in :mod:`nexcraftviz.render.html` is the reference implementation.

KPI cards use the same ``columns_schema`` slot but hold an object rather than a
list, so both shapes live here.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from nexcraftviz.spec.model import Spec

#: Cell renderers, in corpus frequency order. `render` is the key, not
#: `renderer` — matching what the corpus and the generator prompt already emit.
Renderer = Literal[
    "number",
    "text",
    "avatar_name",
    "heatmap_cell",
    "progress_bar",
    "pill",
    "sparkline",
    "badge",
    "date",
    "trend_arrow",
]

RENDERERS: tuple[str, ...] = (
    "number", "text", "avatar_name", "heatmap_cell", "progress_bar",
    "pill", "sparkline", "badge", "date", "trend_arrow",
)

#: Semantic tones a `color_map` may assign. Again taken from the corpus, which
#: uses judgement words rather than colours — "fix" is a call to action, not an
#: amber hex, and that is the right level for a spec to speak at.
Tone = Literal["pass", "fix", "fail", "muted"]

TONES: tuple[str, ...] = ("pass", "fix", "fail", "muted")


class Column(BaseModel):
    """One column of a rich table."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="Row key this column reads.")
    header: str = Field(description="Column heading shown to the user.")
    render: Renderer = Field(default="text", description="How the cell is drawn.")

    # -- per-renderer options ------------------------------------------------
    subtitle_field: str = Field(
        default="",
        description="avatar_name: a second row key shown under the name.",
    )
    color_map: dict[str, Tone] = Field(
        default_factory=dict,
        description="pill / badge: value → semantic tone.",
    )
    min: float | None = Field(
        default=None, description="heatmap_cell / progress_bar: scale floor."
    )
    max: float | None = Field(
        default=None, description="heatmap_cell / progress_bar: scale ceiling."
    )
    format: str = Field(
        default="",
        description="number / trend_arrow: a d3-style format, e.g. '.1f' or '+.1f%'.",
    )
    muted: bool = Field(
        default=False, description="number: render in the secondary text colour."
    )

    @property
    def numeric_range(self) -> tuple[float, float]:
        """Floor and ceiling for the scaled renderers, defaulting to 0–100."""
        return (self.min if self.min is not None else 0.0,
                self.max if self.max is not None else 100.0)


class TableSpec(BaseModel):
    """A ``table_with_cells`` payload."""

    model_config = ConfigDict(extra="forbid")

    columns: list[Column] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    title: str = ""

    def to_spec(self) -> Spec:
        """As a :class:`~nexcraftviz.spec.model.Spec` of family ``table``.

        ``columns`` is the key tier-1 validation and the frontend both look for,
        so the payload stays recognisable to everything that already exists.
        """
        raw: dict[str, Any] = {
            "columns": [c.model_dump(exclude_defaults=True) for c in self.columns]
        }
        if self.rows:
            raw["data"] = {"values": self.rows}
        if self.title:
            raw["title"] = self.title
        return Spec(raw)

    @classmethod
    def from_spec(cls, spec: Spec | dict[str, Any]) -> TableSpec:
        raw = spec.raw if isinstance(spec, Spec) else spec
        return cls(
            columns=[Column.model_validate(c) for c in (raw.get("columns") or [])],
            rows=(raw.get("data") or {}).get("values") or [],
            title=str(raw.get("title") or ""),
        )

    @classmethod
    def from_columns_schema(
        cls, columns_schema: str | list[dict[str, Any]], rows: list[dict[str, Any]] | None = None
    ) -> TableSpec:
        """Parse a corpus pair's ``columns_schema``, which may be JSON text."""
        parsed = json.loads(columns_schema) if isinstance(columns_schema, str) else columns_schema
        if not isinstance(parsed, list):
            raise ValueError("columns_schema must be a list of column objects")
        return cls(
            columns=[Column.model_validate(_normalise(c)) for c in parsed],
            rows=list(rows or []),
        )


KpiSubtype = Literal[
    # the corpus vocabulary
    "counter", "percentage", "score",
    # the vocabulary chart.kpi.v1 emits and some hosts special-case
    "target_vs_actual", "percent_change", "comparison_kpi",
]


class KpiCard(BaseModel):
    """A single-value KPI payload.

    Two vocabularies exist in the wild and both are accepted: the corpus uses
    ``counter`` / ``percentage`` / ``score``, while ``chart.kpi.v1`` emits
    ``target_vs_actual`` / ``percent_change``, which hosts' KPI tiles branch
    on. Normalising to one would silently change what
    the frontend renders, so the union is deliberate.
    """

    model_config = ConfigDict(extra="allow")

    chart_type: str = "metric_kpi"
    chart_subtype: KpiSubtype = "counter"
    label: str = ""
    value: float | str = 0
    unit: str = ""
    change_pct: float | None = None
    change_direction: Literal["up", "down", "flat", ""] = ""
    target: float | None = None
    #: True when higher is worse (churn, cost, time-to-hire), so a rise reads red.
    invert_sentiment: bool = False

    @property
    def sentiment(self) -> str:
        """``positive`` / ``negative`` / ``neutral`` for the change indicator.

        Direction and sentiment are separate: churn falling is good news with a
        down arrow, and colouring by direction alone gets that backwards.
        """
        if self.change_direction not in ("up", "down"):
            return "neutral"
        good = (self.change_direction == "down") if self.invert_sentiment else (
            self.change_direction == "up"
        )
        return "positive" if good else "negative"

    def to_spec(self) -> Spec:
        return Spec({"kpi_metadata": self.model_dump(exclude_none=True)})

    @classmethod
    def from_columns_schema(cls, columns_schema: str | dict[str, Any]) -> KpiCard:
        parsed = (
            json.loads(columns_schema) if isinstance(columns_schema, str) else columns_schema
        )
        if not isinstance(parsed, dict):
            raise ValueError("a KPI columns_schema must be an object")
        return cls.model_validate(parsed)


def _normalise(column: dict[str, Any]) -> dict[str, Any]:
    """Accept ``renderer``/``title`` as aliases for ``render``/``header``.

    Both spellings appear in the wild — the corpus says `render`/`header`, some
    hand-written specs say `renderer`/`title`. Rejecting either would be
    pedantry, so they are folded here rather than duplicated in the model.
    """
    out = dict(column)
    if "render" not in out and "renderer" in out:
        out["render"] = out.pop("renderer")
    if "header" not in out and "title" in out:
        out["header"] = out.pop("title")
    out.pop("renderer", None)
    out.pop("title", None)
    return out
