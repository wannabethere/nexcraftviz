"""HTML rendering for all three payload families.

Charts have a renderer. The other two families do not: a ``table_with_cells``
payload reaches the frontend, fails to parse as Vega-Lite, and degrades to an
untyped grid; ``kpi_metadata`` gets a partial one. This module is the reference
implementation for both, written in Python rather than buried in a demo page so
that it is testable, reusable from the CLI, and portable to whichever frontend
adopts it.

Markup targets the class vocabulary in :mod:`nexcraftviz.theme.css`, so a page
that includes the generated stylesheet is themed with no further work — and
switching themes restyles the tables and KPI tiles alongside the charts.
"""
from __future__ import annotations

import html as _html
import json
from typing import Any

from nexcraftviz.spec.model import Spec
from nexcraftviz.table.schema import Column, KpiCard, TableSpec

#: Trend-arrow glyphs. Text, not icons, so the markup stays dependency-free.
_ARROWS = {"up": "▲", "down": "▼", "flat": "—"}


def escape(value: Any) -> str:
    return _html.escape(str(value), quote=True)


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------

def render_table(table: TableSpec | Spec | dict[str, Any], *, max_rows: int = 50) -> str:
    """Render a ``table_with_cells`` payload."""
    table = _coerce_table(table)
    if not table.columns:
        return '<p class="nxv-empty">No columns to render.</p>'

    head = "".join(f"<th{_numeric_class(c)}>{escape(c.header)}</th>" for c in table.columns)
    body = "".join(
        "<tr>"
        + "".join(
            f"<td{_numeric_class(column)}>{render_cell(column, row)}</td>"
            for column in table.columns
        )
        + "</tr>"
        for row in table.rows[:max_rows]
    )

    more = ""
    if len(table.rows) > max_rows:
        more = (
            f'<p class="nxv-table__more">Showing {max_rows} of '
            f"{len(table.rows)} rows.</p>"
        )
    return (
        f'<table class="nxv-table"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table>{more}"
    )


def render_cell(column: Column, row: dict[str, Any]) -> str:
    """One cell, dispatched on the column's ``render``."""
    value = row.get(column.field)
    cls = _cell_class(column.render)

    if value is None:
        return f'<span class="{cls} is-muted">—</span>'

    if column.render == "avatar_name":
        subtitle = row.get(column.subtitle_field) if column.subtitle_field else None
        sub = f'<span class="nxv-avatar__sub">{escape(subtitle)}</span>' if subtitle else ""
        return (
            f'<span class="{cls}"><span class="nxv-avatar">{escape(_initials(value))}</span>'
            f'<span class="nxv-avatar__text">{escape(value)}{sub}</span></span>'
        )

    if column.render == "progress_bar":
        low, high = column.numeric_range
        pct = _scale(value, low, high)
        tone = "pass" if pct >= 80 else "fix" if pct >= 50 else "fail"
        # A bare "64" against a 0-100 scale is ambiguous; the unit is the point.
        default_format = ".0f%" if high == 100 else (".0f" if high > 1 else ".0%")
        label = _format_number(value, column.format or default_format)
        return (
            f'<span class="{cls}"><span class="nxv-track">'
            f'<span class="nxv-fill nxv-fill--{tone}" style="width:{pct:.1f}%"></span>'
            f'</span><span class="nxv-pct">{escape(label)}</span></span>'
        )

    if column.render == "heatmap_cell":
        low, high = column.numeric_range
        intensity = _scale(value, low, high) / 100
        label = _format_number(value, column.format or ".0f")
        return f'<span class="{cls}" style="--nxv-heat:{intensity:.3f}">{escape(label)}</span>'

    if column.render == "sparkline":
        return f'<span class="{cls}">{_sparkline(value)}</span>'

    if column.render in ("pill", "badge"):
        tone = column.color_map.get(str(value), "")
        tone_class = f" is-{tone}" if tone else ""
        return f'<span class="{cls}{tone_class}">{escape(value)}</span>'

    if column.render == "trend_arrow":
        number = _as_number(value)
        direction = "flat" if number is None or number == 0 else ("up" if number > 0 else "down")
        # A signed format keeps the sign; an unsigned one leaves it to the arrow.
        # Formatting the absolute value under a "+.1f" format produced "▼ +3.1",
        # which says the opposite of what it means.
        spec = column.format or ".1f"
        signed = spec.startswith(("+", " "))
        source = number if (signed or number is None) else abs(number)
        label = _format_number(source if number is not None else value, spec)
        return f'<span class="{cls} is-{direction}">{_ARROWS[direction]} {escape(label)}</span>'

    if column.render == "date":
        return f'<span class="{cls}">{escape(_format_date(value))}</span>'

    if column.render == "number":
        muted = " is-muted" if column.muted else ""
        return f'<span class="{cls}{muted}">{escape(_format_number(value, column.format))}</span>'

    return f'<span class="{cls}">{escape(value)}</span>'


# ---------------------------------------------------------------------------
# KPI cards
# ---------------------------------------------------------------------------

def render_kpi(kpi: KpiCard | Spec | dict[str, Any]) -> str:
    """Render a ``kpi_metadata`` payload."""
    kpi = _coerce_kpi(kpi)
    parts = [f'<span class="nxv-kpi__label">{escape(kpi.label)}</span>']

    unit = f'<span class="nxv-kpi__unit">{escape(kpi.unit)}</span>' if kpi.unit else ""
    value = _format_number(kpi.value, ",") if isinstance(kpi.value, (int, float)) else kpi.value
    parts.append(f'<span class="nxv-kpi__value">{escape(value)}{unit}</span>')

    if kpi.target is not None:
        number = _as_number(kpi.value)
        pct = _scale(number if number is not None else 0, 0, max(kpi.target, 1))
        tone = "over" if number is not None and number >= kpi.target else "under"
        parts.append(
            f'<span class="nxv-kpi__track"><span class="nxv-kpi__fill '
            f'nxv-kpi__fill--{tone}" style="width:{min(pct, 100):.1f}%"></span></span>'
            f'<span class="nxv-kpi__target">target {escape(kpi.target)}{escape(kpi.unit)}</span>'
        )

    if kpi.change_pct is not None and kpi.change_direction:
        arrow = _ARROWS.get(kpi.change_direction, "—")
        tone = {"positive": "up", "negative": "down"}.get(kpi.sentiment, "flat")
        parts.append(
            f'<span class="nxv-kpi__delta nxv-kpi__delta--{tone}">'
            f"{arrow} {escape(_format_number(abs(kpi.change_pct), '.1f'))}%</span>"
        )

    subtype = kpi.chart_subtype.replace("_", "-")
    return f'<div class="nxv-kpi nxv-kpi--{escape(subtype)}">{"".join(parts)}</div>'


def kpi_from_vega(spec: Spec | dict[str, Any]) -> KpiCard | None:
    """A Vega-Lite spec that only prints one number, read back as a KPI card.

    Generated KPIs arrive as a bare ``text`` mark over a single row: valid,
    compiled, and drawn as a small number in the corner of an empty canvas.
    The playground draws a KPI as card furniture — label, value, unit, delta —
    and so do dashboard hosts, so this reads the value back out for any host
    that wants the same. ``None`` when the spec draws anything more than that.
    """
    spec = spec if isinstance(spec, Spec) else Spec(spec)
    if spec.family != "vega-lite":
        return None
    views = spec.views()
    if not views or any(view.mark_type != "text" for view in views):
        return None
    rows = spec.data_values or _view_rows(views[0].node)
    if len(rows) != 1:
        return None

    for _, channel, definition in spec.encodings():
        field = definition.get("field")
        if channel != "text" or not isinstance(field, str) or field not in rows[0]:
            continue
        value = rows[0][field]
        if _as_number(value) is None:
            continue
        fmt = definition.get("format")
        shown: float | str = _d3_number(value, fmt) if isinstance(fmt, str) and fmt else value
        label = _title_text(spec.raw.get("title")) or field.replace("_", " ").capitalize()
        return KpiCard(label=label, value=shown)
    return None


def _view_rows(node: dict[str, Any]) -> list[dict[str, Any]]:
    data = node.get("data")
    values = data.get("values") if isinstance(data, dict) else None
    return [row for row in values if isinstance(row, dict)] if isinstance(values, list) else []


def _title_text(title: Any) -> str:
    if isinstance(title, dict):
        title = title.get("text")
    if isinstance(title, list):
        title = " ".join(str(part) for part in title)
    return title.strip() if isinstance(title, str) else ""


def _d3_number(value: Any, fmt: str) -> str:
    """The d3 formats a generated KPI uses — ``d``, ``,d``, ``.0f``, ``$,.0f``,
    ``.1%`` — on top of :func:`_format_number`, which has no ``d`` and no
    currency."""
    currency = fmt.startswith("$")
    body = fmt[1:] if currency else fmt
    if body.endswith("d"):
        body = body[:-1] + ".0f"
    text = _format_number(value, body or ",")
    return f"${text}" if currency else text


# ---------------------------------------------------------------------------
# cards and pages
# ---------------------------------------------------------------------------

def render_card(*, title: str, subtitle: str = "", body: str, extra_class: str = "") -> str:
    header = ""
    if title or subtitle:
        sub = f'<span class="nxv-card__subtitle">{escape(subtitle)}</span>' if subtitle else ""
        header = (
            f'<div class="nxv-card__header"><h3 class="nxv-card__title">'
            f"{escape(title)}</h3>{sub}</div>"
        )
    classes = f"nxv-card {extra_class}".strip()
    return f'<div class="{classes}">{header}<div class="nxv-card__body">{body}</div></div>'


def render_chart_mount(spec: Spec | dict[str, Any], mount_id: str) -> str:
    """A div plus the vega-embed call that fills it.

    The spec is embedded rather than fetched so a generated page is one
    self-contained file that works from ``file://``.
    """
    raw = spec.raw if isinstance(spec, Spec) else spec
    payload = json.dumps(raw, default=str)
    return (
        f'<div id="{escape(mount_id)}" class="nxv-chart"></div>'
        f'<script>window.__nxvSpecs=window.__nxvSpecs||{{}};'
        f'window.__nxvSpecs[{json.dumps(mount_id)}]={payload};</script>'
    )


# ---------------------------------------------------------------------------
# coercion and formatting
# ---------------------------------------------------------------------------

def _coerce_table(value: TableSpec | Spec | dict[str, Any]) -> TableSpec:
    if isinstance(value, TableSpec):
        return value
    return TableSpec.from_spec(value)


def _coerce_kpi(value: KpiCard | Spec | dict[str, Any]) -> KpiCard:
    if isinstance(value, KpiCard):
        return value
    raw = value.raw if isinstance(value, Spec) else value
    meta = raw.get("kpi_metadata") if isinstance(raw, dict) else None
    return KpiCard.model_validate(meta if isinstance(meta, dict) else raw)


def _cell_class(render: str) -> str:
    return f"nxv-cell--{render.replace('_', '-')}"


def _numeric_class(column: Column) -> str:
    """Right-align the renderers that are fundamentally numbers."""
    numeric = {"number", "progress_bar", "heatmap_cell", "trend_arrow"}
    return ' class="nxv-num"' if column.render in numeric else ""


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _scale(value: Any, low: float, high: float) -> float:
    """Position ``value`` within ``[low, high]`` as a 0-100 percentage."""
    number = _as_number(value)
    if number is None or high == low:
        return 0.0
    return max(0.0, min(100.0, (number - low) / (high - low) * 100))


def _format_number(value: Any, spec: str) -> str:
    """A small subset of d3-format: ``,``  ``.1f``  ``+.1f``  ``.0%``, with an
    optional trailing ``%`` meaning "append a percent sign"."""
    number = _as_number(value)
    if number is None:
        return str(value)
    if not spec:
        if float(number).is_integer():
            return f"{number:,.0f}"
        return f"{number:,.2f}".rstrip("0").rstrip(".")

    suffix = ""
    if spec.endswith("f%"):
        # "+.1f%" / ".0f%" — a fixed-point number with a literal percent sign
        # appended, as distinct from d3's ".0%" which multiplies by 100.
        spec, suffix = spec[:-1], "%"

    try:
        if spec == ",":
            return f"{number:,.0f}"
        return format(number, spec) + suffix
    except (ValueError, TypeError):
        return str(value)


def _format_date(value: Any) -> str:
    text = str(value)
    return text[:10] if len(text) > 10 and text[4:5] == "-" else text


def _initials(value: Any) -> str:
    words = [w for w in str(value).split() if w]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def _sparkline(values: Any) -> str:
    """An inline SVG polyline. Colours come from CSS, not from attributes."""
    numbers = [n for n in (_as_number(v) for v in (values or [])) if n is not None]
    if len(numbers) < 2:
        return '<span class="nxv-cell--text">—</span>'

    low, high = min(numbers), max(numbers)
    span = (high - low) or 1
    points = [
        (index / (len(numbers) - 1) * 78 + 1, 23 - (value - low) / span * 22)
        for index, value in enumerate(numbers)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return (
        '<svg viewBox="0 0 80 24" preserveAspectRatio="none" aria-hidden="true">'
        f'<polygon class="nxv-spark-area" points="1,23 {line} 79,23"></polygon>'
        f'<polyline class="nxv-spark-line" points="{line}"></polyline></svg>'
    )
