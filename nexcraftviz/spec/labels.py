"""Titles and axis names — what a reader needs to know what they are looking at.

The corpus the generator adapts from sets an axis ``"title": null`` 272 times:
its charts came from dashboard tiles whose card header carried the name. Copied
into a chart that stands alone, that leaves a bar chart with numbers along one
edge and nothing saying what they count. Every axis in one live harness run was
blanked this way, and ten of eleven specs had no title — though every plan had
written one.

Deterministic and in place: the title the plan already chose, and an axis name
derived from the field. A title the model set on purpose is left alone, and so
is an axis switched off entirely (``"axis": null``), which a KPI or a sparkline
does deliberately.
"""
from __future__ import annotations

import re
from typing import Any

from nexcraftviz.spec.model import Spec

_AXES = ("x", "y")

#: Column-name shorthand, as a reader would say it.
_WORDS = {
    "pct": "%", "percent": "%", "id": "ID", "avg": "average",
    "qty": "quantity", "num": "number", "amt": "amount",
}

#: How an aggregate reads in front of the measure. `sum` is absent on purpose:
#: a sum of revenue is labelled "Revenue", as anyone would write it.
_AGGREGATE_WORDS = {
    "mean": "Average", "average": "Average", "median": "Median",
    "min": "Minimum", "max": "Maximum", "distinct": "Distinct",
}

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def humanize(field: str) -> str:
    """`completion_pct` → "Completion %", `control_id` → "Control ID"."""
    words = [w for w in re.split(r"[_\-\s]+", _CAMEL.sub(" ", field.strip())) if w]
    words = [_WORDS.get(w.lower(), w if w.isupper() else w.lower()) for w in words]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def label_chart(spec: Spec, *, title: str = "") -> list[str]:
    """Give ``spec`` a title and name its blank axes, in place.

    Returns what was changed, one note per change, for the caller to record.
    """
    if spec.family != "vega-lite":
        return []

    notes: list[str] = []
    title = title.strip()
    if title and not _has_title(spec.raw.get("title")):
        spec.raw["title"] = title
        notes.append(f"titled the chart {title!r}")

    for _, channel, definition in spec.encodings():
        if channel not in _AXES or not _names_data(definition):
            continue
        if "axis" in definition and definition["axis"] is None:
            continue  # switched off on purpose

        # Where the effective title lives: `axis.title` overrides the
        # encoding's own `title` whenever the key is present, null included.
        axis = definition.get("axis")
        holder = axis if isinstance(axis, dict) and "title" in axis else definition
        if _has_title(holder.get("title")):
            continue

        name = _axis_title(definition)
        holder["title"] = name
        notes.append(f"named the {channel} axis {name!r}")
    return notes


def _names_data(definition: dict[str, Any]) -> bool:
    field = definition.get("field")
    return (isinstance(field, str) and bool(field)) or definition.get("aggregate") == "count"


def _axis_title(definition: dict[str, Any]) -> str:
    field = definition.get("field")
    aggregate = definition.get("aggregate")
    if aggregate == "count" or not isinstance(field, str) or not field:
        return "Count"
    name = humanize(field)
    word = _AGGREGATE_WORDS.get(aggregate) if isinstance(aggregate, str) else None
    return f"{word} {_lead_lower(name)}" if word else name


def _lead_lower(text: str) -> str:
    """Lower the first letter unless it starts an acronym ("ID", "SLA")."""
    if len(text) > 1 and text[1].isupper():
        return text
    return text[:1].lower() + text[1:]


def _has_title(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(isinstance(v, str) and v.strip() for v in value)
    if isinstance(value, dict):
        return _has_title(value.get("text"))
    return False


def lift_title(raw: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    """``raw`` without its top-level title, plus the title's text and subtitle.

    For hosts whose card header already shows the title — lexy_ui's dashboard
    tiles — where a title drawn inside the chart as well says it twice. Only
    the top level moves: a panel's own title inside a concat labels that panel.
    The input is not modified.
    """
    if "title" not in raw:
        return raw, "", ""
    title = raw["title"]
    rest = {key: value for key, value in raw.items() if key != "title"}
    if isinstance(title, dict):
        return rest, _flat(title.get("text")), _flat(title.get("subtitle"))
    return rest, _flat(title), ""


def _flat(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(part) for part in value if part)
    return value.strip() if isinstance(value, str) else ""
