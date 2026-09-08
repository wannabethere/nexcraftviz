"""Rows → a rich table, deterministically.

This is the "render the table" half of the table-first flow: results arrive,
the table is drawn immediately from the rows themselves, and only then does a
chart get generated. No model is involved here, so the table costs nothing and
cannot be wrong about what the data contains.

Renderer choice comes from the profile — roles, cardinality and value ranges —
with column names used only to break ties. That ordering matters: a column
called ``status`` holding 400 distinct free-text values is not a pill, and a
column called ``x`` holding ``Pass``/``Fail`` is.
"""
from __future__ import annotations

import re
from typing import Any

from nexcraftviz.data.profile import ColumnProfile, DataProfile, profile_rows
from nexcraftviz.table.schema import Column, TableSpec, Tone

#: Name fragments that suggest a person or entity worth an avatar chip.
_NAME_HINTS = (
    "name", "owner", "user", "employee", "manager", "person", "rep",
    "assignee", "author", "customer", "account", "learner", "candidate",
)
#: Columns whose values are a state to be judged, not a category to be counted.
_STATUS_HINTS = (
    "status", "state", "health", "risk", "outcome", "result", "flag", "compliance",
)
#: Short classification codes — a badge, not a pill; no judgement implied.
_BADGE_HINTS = (
    "tier", "priority", "level", "grade", "band", "severity", "type", "category", "plan",
)
#: Measures that are a share of a whole, so a progress bar reads naturally.
_PERCENT_HINTS = (
    "pct", "percent", "rate", "completion", "coverage", "utilisation", "utilization",
)
#: Measures that are a signed movement, so an arrow beats a bare number.
_DELTA_HINTS = (
    "change", "delta", "diff", "growth", "variance", "vs_", "_vs", "trend", "yoy", "mom",
)
#: Bounded 0-100 scores where relative intensity is the point.
_SCORE_HINTS = ("score", "index", "rating", "nps", "csat", "engagement")

#: Values that map to a tone without anyone configuring it. Lower-cased keys.
_TONE_BY_VALUE: dict[str, Tone] = {
    # good
    "pass": "pass", "passed": "pass", "ok": "pass", "good": "pass", "green": "pass",
    "healthy": "pass", "active": "pass", "complete": "pass", "completed": "pass",
    "compliant": "pass", "on track": "pass", "ready": "pass", "approved": "pass",
    "resolved": "pass", "success": "pass", "yes": "pass", "true": "pass",
    # needs attention
    "warning": "fix", "warn": "fix", "amber": "fix", "at risk": "fix", "at-risk": "fix",
    "pending": "fix", "in progress": "fix", "in-progress": "fix", "review": "fix",
    "due soon": "fix", "partial": "fix", "degraded": "fix",
    # bad
    "fail": "fail", "failed": "fail", "critical": "fail", "red": "fail",
    "overdue": "fail", "blocked": "fail", "breach": "fail", "non-compliant": "fail",
    "noncompliant": "fail", "error": "fail", "rejected": "fail", "churned": "fail",
    # neutral
    "inactive": "muted", "n/a": "muted", "unknown": "muted", "none": "muted",
    "not started": "muted", "draft": "muted", "archived": "muted", "no": "muted",
}


def build_table(
    rows: list[dict[str, Any]],
    *,
    profile: DataProfile | None = None,
    title: str = "",
    max_columns: int = 12,
) -> TableSpec:
    """Build a :class:`TableSpec` from result rows.

    ``max_columns`` guards against a ``SELECT *`` producing a table nobody can
    read; the first N columns win, since query authors put the important ones
    first.
    """
    if not rows:
        return TableSpec(columns=[], rows=[], title=title)

    profile = profile or profile_rows(rows)
    columns = [
        _column_for(column, rows) for column in profile.columns[:max_columns]
    ]
    return TableSpec(columns=columns, rows=list(rows), title=title)


def build_table_spec(rows: list[dict[str, Any]], **kwargs: Any):
    """Convenience wrapper returning a :class:`~nexcraftviz.spec.model.Spec`."""
    return build_table(rows, **kwargs).to_spec()


# ---------------------------------------------------------------------------
# renderer selection
# ---------------------------------------------------------------------------

def _column_for(column: ColumnProfile, rows: list[dict[str, Any]]) -> Column:
    header = _humanise(column.name)
    lowered = column.name.lower()
    values = [row.get(column.name) for row in rows if row.get(column.name) is not None]

    # A list of numbers is a series, whatever the column is called.
    if values and _is_series(values[0]):
        return Column(field=column.name, header=header, render="sparkline")

    if column.vega_type == "temporal":
        return Column(field=column.name, header=header, render="date")

    if column.role in ("measure", "identifier") and column.vega_type == "quantitative":
        return _numeric_column(column, header, lowered, values)

    return _categorical_column(column, header, lowered, values, rows)


def _numeric_column(
    column: ColumnProfile, header: str, lowered: str, values: list[Any]
) -> Column:
    # A signed movement reads as an arrow, not a number — but only when the
    # values actually move both ways, or the name says so outright.
    if _matches(lowered, _DELTA_HINTS) and _has_negative(values):
        return Column(field=column.name, header=header, render="trend_arrow", format="+.1f")

    # A share of a whole gets a bar. Requires the values to sit in a plausible
    # range, so a column called `completion_days` does not become a progress bar.
    if _matches(lowered, _PERCENT_HINTS) and _within(values, 0, 100):
        return Column(field=column.name, header=header, render="progress_bar", max=100)
    if _matches(lowered, _PERCENT_HINTS) and _within(values, 0, 1):
        return Column(field=column.name, header=header, render="progress_bar", max=1)

    # A bounded score is about relative intensity, which a heatmap cell shows
    # better than a bar — a bar implies progress toward completion.
    if _matches(lowered, _SCORE_HINTS) and _within(values, 0, 100):
        return Column(field=column.name, header=header, render="heatmap_cell", min=0, max=100)

    # An identifier is a number we must not format as a measure: no thousands
    # separator on an employee id, and it belongs in the secondary colour.
    if column.role == "identifier":
        return Column(field=column.name, header=header, render="number", muted=True)

    return Column(field=column.name, header=header, render="number", format=_format_for(values))


def _categorical_column(
    column: ColumnProfile,
    header: str,
    lowered: str,
    values: list[Any],
    rows: list[dict[str, Any]],
) -> Column:
    # Cardinality first, name second. A pill or badge only makes sense for a
    # small closed set; applying one to 400 distinct strings produces 400 chips.
    small_set = column.distinct <= 8 and column.vega_type in ("nominal", "ordinal")

    if small_set:
        tones = _tone_map(values)
        # Judged states become pills; a pill without a tone is just a grey chip,
        # so require either a recognised vocabulary or a status-ish name.
        if tones and (_matches(lowered, _STATUS_HINTS) or len(tones) == column.distinct):
            return Column(field=column.name, header=header, render="pill", color_map=tones)
        if _matches(lowered, _STATUS_HINTS):
            return Column(field=column.name, header=header, render="pill", color_map=tones)
        if _matches(lowered, _BADGE_HINTS) or _all_short_codes(values):
            return Column(field=column.name, header=header, render="badge")

    if _matches(lowered, _NAME_HINTS) and column.vega_type == "nominal":
        return Column(
            field=column.name,
            header=header,
            render="avatar_name",
            subtitle_field=_subtitle_for(column.name, rows),
        )

    return Column(field=column.name, header=header, render="text")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _matches(lowered: str, hints: tuple[str, ...]) -> bool:
    return any(hint in lowered for hint in hints)


def _is_series(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
    )


def _has_negative(values: list[Any]) -> bool:
    return any(isinstance(v, (int, float)) and v < 0 for v in values)


def _within(values: list[Any], low: float, high: float) -> bool:
    numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not numbers:
        return False
    return min(numbers) >= low and max(numbers) <= high


def _format_for(values: list[Any]) -> str:
    """Pick a number format from magnitude.

    Large counts want thousands separators; small measures want a decimal.
    Integers that are already whole should not grow a ``.0``.
    """
    numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not numbers:
        return ""
    if all(float(v).is_integer() for v in numbers):
        return "," if max(abs(v) for v in numbers) >= 1000 else ""
    return ".1f"


def _tone_map(values: list[Any]) -> dict[str, Tone]:
    """Map recognised status words to tones, ignoring the rest."""
    mapping: dict[str, Tone] = {}
    for value in values:
        if not isinstance(value, str):
            continue
        tone = _TONE_BY_VALUE.get(value.strip().lower())
        if tone:
            mapping[value] = tone
    return mapping


def _all_short_codes(values: list[Any]) -> bool:
    """``P1`` / ``A`` / ``SEV2`` — tokens that read as classification codes.

    Deliberately strict. An earlier, looser rule ("short and unspaced") turned
    job titles into badges, because "Engineer" and "Manager" are short and
    unspaced. A code is short *and* either contains a digit or is all-caps —
    that is what separates `P1` from `Analyst`.
    """
    strings = [v.strip() for v in values if isinstance(v, str)]
    if not strings:
        return False
    return all(
        len(v) <= 5 and " " not in v and (any(ch.isdigit() for ch in v) or v.isupper())
        for v in strings
    )


def _subtitle_for(name: str, rows: list[dict[str, Any]]) -> str:
    """A second line for an avatar cell — a role or team, if one is present."""
    if not rows:
        return ""
    for candidate in ("role", "title", "job_title", "team", "department", "email", "org"):
        if candidate in rows[0] and candidate != name:
            return candidate
    return ""


def _humanise(name: str) -> str:
    """``completion_pct`` → ``Completion pct``; ``completionPct`` → ``Completion pct``."""
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", name).replace("_", " ").replace("-", " ")
    collapsed = " ".join(spaced.split())
    return collapsed[:1].upper() + collapsed[1:] if collapsed else name
