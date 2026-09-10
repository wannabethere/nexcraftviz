"""A KPI's numbers come from the data; the model only says which columns.

The generator decides what a KPI *is* — its subtype, label, unit, whether a
rise is bad news — and names the columns that hold its numbers: ``value_field``,
and ``prior_field`` / ``target_field`` when the data has them. This reads them.

A model copying 447 out of a profile can drop a digit; a column name either
exists or fails loudly, and a change arrow computed from two columns is
arithmetic rather than a guess. It is also what makes a KPI rich: the corpus
card with its delta and the target-vs-actual track are only as good as the
columns behind them, and a card with neither is the honest answer to data that
has neither.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from nexcraftviz.spec.model import Spec

FIELD_KEYS = ("value_field", "prior_field", "target_field")


def kpi_fields(spec: Spec) -> set[str]:
    """The columns a KPI payload reads — what `matches_plan` checks it against."""
    meta = spec.raw.get("kpi_metadata")
    if not isinstance(meta, dict):
        return set()
    return {meta[key] for key in FIELD_KEYS if isinstance(meta.get(key), str) and meta[key]}


def bind_kpi(spec: Spec, rows: list[dict[str, Any]]) -> tuple[list[str], str]:
    """Fill ``spec``'s KPI numbers from ``rows[0]``, in place.

    Returns ``(notes, error)``. An error means the payload cannot be shown as
    asked, worded for the generator to fix on its one regeneration.
    """
    from nexcraftviz.table.schema import KpiCard

    meta = spec.raw.get("kpi_metadata")
    if not isinstance(meta, dict):
        return [], "a KPI payload needs a `kpi_metadata` object"
    meta.setdefault("chart_type", "metric_kpi")
    if not meta.get("chart_subtype"):
        meta["chart_subtype"] = "counter"
    if not rows:
        return [], ""

    row = rows[0]
    columns = ", ".join(row)
    notes: list[str] = []

    def number(key: str) -> tuple[float | None, str]:
        name = meta.get(key)
        if name in (None, ""):
            return None, ""
        if not isinstance(name, str) or name not in row:
            return None, f"`{key}` names {name!r}, which is not a column (the data has: {columns})"
        value = row[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, f"`{key}` names {name!r}, which holds {value!r}, not a number"
        return value, ""

    if not meta.get("value_field"):
        # A literal number is accepted only when it IS a number in the row —
        # the deterministic builder and API callers write one — and is bound to
        # that column. One that matches nothing was mistyped, or invented.
        literal = meta.get("value")
        matches = [name for name, cell in row.items()
                   if isinstance(literal, (int, float)) and not isinstance(literal, bool)
                   and isinstance(cell, (int, float)) and not isinstance(cell, bool)
                   and cell == literal]
        if not matches:
            return [], ("a KPI must name the column holding its number in `value_field` "
                        f"(the data has: {columns}); the number itself is read from the data")
        meta["value_field"] = matches[0]
    value, error = number("value_field")
    if error or value is None:
        return [], error
    meta["value"] = value
    notes.append(f"read the value {value:g} from {meta['value_field']!r}")
    if len(rows) > 1:
        notes.append(f"the data has {len(rows)} rows; a KPI shows the first")

    prior, error = number("prior_field")
    if error:
        return [], error
    if prior is not None:
        if prior == 0:
            notes.append("the prior value is 0, so no change can be shown")
        else:
            change = round((value - prior) / abs(prior) * 100, 1)
            meta["change_pct"] = abs(change)
            meta["change_direction"] = "up" if change > 0 else "down" if change < 0 else "flat"
            notes.append(f"computed the change from {meta['prior_field']!r}: {change:+g}%")

    target, error = number("target_field")
    if error:
        return [], error
    if target is not None:
        meta["target"] = target
        notes.append(f"read the target {target:g} from {meta['target_field']!r}")

    if meta.get("chart_subtype") == "percentage" and not meta.get("unit"):
        meta["unit"] = "%"
    try:
        KpiCard.model_validate(meta)
    except ValidationError as exc:
        first = exc.errors()[0]
        return [], f"the KPI payload is not one a host can draw: {first['msg']} at {first['loc']}"
    return notes, ""
