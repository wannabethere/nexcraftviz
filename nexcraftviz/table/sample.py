"""Synthesise plausible rows for a ``columns_schema`` that has none.

The 30 ``table_with_cells`` pairs in the corpus define their columns but ship no
sample rows, so nothing — not a gallery, not a test, not a design review — can
show what they actually look like. That is a gap in the corpus worth closing
properly; until then this fills it deterministically.

Values are chosen to *exercise the renderer*, not to look realistic: a
progress_bar column gets values spanning its whole range so the bar is visible
at both ends, a pill column gets one value per tone so every colour appears.
Output is seeded, so a gallery page does not reshuffle on every rebuild.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

from nexcraftviz.table.schema import Column, TableSpec

_FIRST = ("Ada", "Grace", "Katherine", "Alan", "Barbara", "Edsger", "Radia", "Tim")
_LAST = ("Lovelace", "Hopper", "Johnson", "Turing", "Liskov", "Dijkstra", "Perlman", "Berners-Lee")
_ROLES = ("Engineer", "Analyst", "Manager", "Director", "Specialist", "Lead")
_TEAMS = ("Platform", "Compliance", "Data", "Field Ops", "Enablement", "Security")
_WORDS = ("Onboarding", "Renewal", "Audit", "Migration", "Rollout", "Review", "Training")

#: One value per tone, so a pill column shows every colour it can produce.
_TONE_WORDS = {
    "pass": "On track",
    "fix": "At risk",
    "fail": "Blocked",
    "muted": "Not started",
}
_BADGES = ("P1", "P2", "P3", "A", "B", "Tier 1", "Tier 2")


def sample_rows(
    columns: list[Column] | TableSpec,
    *,
    count: int = 5,
    seed: int = 7,
) -> list[dict[str, Any]]:
    """Build ``count`` rows that exercise every column's renderer."""
    cols = columns.columns if isinstance(columns, TableSpec) else columns
    rng = random.Random(seed)
    return [{c.field: _value_for(c, index, count, rng) for c in cols} for index in range(count)]


def with_sample_rows(table: TableSpec, *, count: int = 5, seed: int = 7) -> TableSpec:
    """Return ``table`` with synthesised rows, leaving real rows alone."""
    if table.rows:
        return table
    return table.model_copy(update={"rows": sample_rows(table.columns, count=count, seed=seed)})


def _value_for(column: Column, index: int, count: int, rng: random.Random) -> Any:
    """One cell.

    ``index`` walks the row so scaled renderers sweep their full range rather
    than clustering — a progress bar column that is 70%, 71%, 69% tells you
    nothing about whether the renderer works.
    """
    fraction = index / max(count - 1, 1)

    if column.render == "avatar_name":
        return f"{_FIRST[index % len(_FIRST)]} {_LAST[index % len(_LAST)]}"

    if column.render == "sparkline":
        base = 20 + index * 8
        return [round(base + rng.uniform(-6, 10) + step * 3, 1) for step in range(6)]

    if column.render in ("progress_bar", "heatmap_cell"):
        low, high = column.numeric_range
        # Sweep the range end to end, inset slightly so neither extreme is a
        # degenerate empty or full bar.
        return round(low + (high - low) * (0.08 + 0.84 * fraction), 2)

    if column.render == "trend_arrow":
        # Alternate sign and include a zero, so up / down / flat all appear.
        if index == count - 1:
            return 0.0
        return round((1 if index % 2 == 0 else -1) * (1.4 + index * 1.7), 1)

    if column.render == "pill":
        if column.color_map:
            keys = list(column.color_map)
            return keys[index % len(keys)]
        tones = list(_TONE_WORDS.values())
        return tones[index % len(tones)]

    if column.render == "badge":
        return _BADGES[index % len(_BADGES)]

    if column.render == "date":
        return (date(2026, 1, 15) + timedelta(days=index * 11)).isoformat()

    if column.render == "number":
        if column.muted:
            return 1000 + index
        return round(120 + index * 47.5 + rng.uniform(0, 12), 1)

    # text — including the subtitle fields an avatar column points at
    lowered = column.field.lower()
    if "role" in lowered or "title" in lowered:
        return _ROLES[index % len(_ROLES)]
    if "team" in lowered or "department" in lowered or "org" in lowered:
        return _TEAMS[index % len(_TEAMS)]
    return f"{_WORDS[index % len(_WORDS)]} {index + 1}"


def rows_from_data_shape(
    data_shape: dict[str, Any], *, count: int = 5, seed: int = 7
) -> list[dict]:
    """Fallback for pairs that describe columns but define no renderers."""
    columns = []
    for entry in (data_shape or {}).get("columns", []):
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        declared = str(entry.get("type") or "string")
        render = "number" if declared in ("number", "integer", "float") else "text"
        columns.append(Column(field=str(entry["name"]), header=str(entry["name"]), render=render))
    return sample_rows(columns, count=count, seed=seed)
