"""Deterministic data profiling — the substrate for recommendation and validation.

Two jobs:

1. Give the chart LLM a compact, accurate description of the data so it does not
   have to re-infer column types from raw rows. (Types alone are not
   enough; the profile adds cardinality, null rates, ordering and role
   detection.)
2. Give :mod:`nexcraftviz.spec.validate` the column set and types it needs for
   tier-2 data-binding validation — the check that catches the dominant LLM
   failure, where an encoding references a field the rows do not contain.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

#: Vega-Lite's measurement types. ``ordinal`` is inferred, never guessed from
#: dtype alone — see :func:`_infer_vega_type`.
VEGA_TYPES = ("quantitative", "temporal", "nominal", "ordinal")

#: Column names that read as a measure regardless of dtype. Used only to break
#: ties when a numeric column could plausibly be an identifier.
_MEASURE_HINTS = (
    "count", "total", "sum", "avg", "average", "mean", "median", "rate",
    "pct", "percent", "ratio", "amount", "revenue", "cost", "score", "value",
    "qty", "quantity", "duration", "days", "hours", "n_",
)
#: Column names that read as an identifier even when numeric.
_ID_HINTS = ("id", "_id", "key", "code", "number", "no", "guid", "uuid", "sk", "rk")

#: Above this many distinct values, a unique text column is an identifier rather
#: than a label — no categorical axis can usefully show more than this.
_MAX_LABEL_CARDINALITY = 50


@dataclass
class ColumnProfile:
    """Everything we know about one column, from the sample rows alone."""

    name: str
    vega_type: str
    python_type: str
    non_null: int
    null_count: int
    distinct: int
    is_unique: bool
    role: str  # "measure" | "dimension" | "time" | "identifier"
    sample_values: list[Any] = field(default_factory=list)
    min_value: Any = None
    max_value: Any = None
    monotonic: bool = False

    @property
    def null_rate(self) -> float:
        total = self.non_null + self.null_count
        return (self.null_count / total) if total else 0.0

    @property
    def cardinality_bucket(self) -> str:
        if self.distinct <= 1:
            return "constant"
        if self.distinct <= 7:
            return "few"
        if self.distinct <= 25:
            return "moderate"
        return "many"

    def to_prompt_dict(self) -> dict[str, Any]:
        """Compact form for prompt injection — no raw value dumps."""
        out: dict[str, Any] = {
            "name": self.name,
            "type": self.vega_type,
            "role": self.role,
            "distinct": self.distinct,
            "cardinality": self.cardinality_bucket,
        }
        if self.null_rate > 0:
            out["null_rate"] = round(self.null_rate, 3)
        if self.role in ("measure", "time") and self.min_value is not None:
            out["min"] = self.min_value
            out["max"] = self.max_value
        if self.role == "dimension" and self.sample_values:
            out["examples"] = self.sample_values[:5]
        return out


@dataclass
class DataProfile:
    """Profile of a whole result set."""

    columns: list[ColumnProfile]
    row_count: int
    truncated: bool = False

    def __post_init__(self) -> None:
        self._by_name = {c.name: c for c in self.columns}

    # ---- lookups ----------------------------------------------------------

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def get(self, name: str) -> ColumnProfile | None:
        return self._by_name.get(name)

    def by_role(self, role: str) -> list[ColumnProfile]:
        return [c for c in self.columns if c.role == role]

    @property
    def measures(self) -> list[ColumnProfile]:
        return self.by_role("measure")

    @property
    def dimensions(self) -> list[ColumnProfile]:
        return self.by_role("dimension")

    @property
    def times(self) -> list[ColumnProfile]:
        return self.by_role("time")

    @property
    def series(self) -> list[ColumnProfile]:
        return self.by_role("series")

    @property
    def time_axis(self) -> ColumnProfile | None:
        """The temporal column a trend chart should plot against, if any.

        Not every date is an axis. `next_audit` on a per-business-unit result is
        an *attribute* — one date per row, describing that row — and plotting a
        line against it produces a chart nobody asked for. A temporal column
        earns the axis when the rows are observations over it, which shows up as
        either repeated timestamps (several rows per period) or the time column
        being the only thing distinguishing rows.

        Returns the widest qualifying column, or None when every date is an
        attribute.
        """
        candidates = self.times
        if not candidates:
            return None

        entity_column = any(
            column.is_unique and column.role == "dimension" for column in self.columns
        )
        for column in sorted(candidates, key=lambda c: -c.distinct):
            if column.distinct < 3:
                continue
            # Repeated timestamps mean rows are grouped within periods — a
            # series, unambiguously.
            if column.distinct < self.row_count:
                return column
            # One row per timestamp is still a series, unless something else
            # already identifies the row, in which case the date describes it.
            if not entity_column:
                return column
        return None

    # ---- signatures -------------------------------------------------------

    def shape_signature(self) -> str:
        """A compact, stable signature of the result shape.

        Used as a retrieval key against the chart-pair corpus, and as a cache
        key. Deliberately coarse: two results with the same signature should
        suit the same chart shapes.
        """
        parts: list[str] = []
        for role in ("time", "dimension", "measure", "identifier"):
            cols = self.by_role(role)
            if cols:
                parts.append(f"{role[0]}{len(cols)}")
        rows = self.row_count
        bucket = (
            "r1" if rows <= 1
            else "r5" if rows <= 5
            else "r25" if rows <= 25
            else "r100" if rows <= 100
            else "rN"
        )
        parts.append(bucket)
        card = "-".join(sorted({c.cardinality_bucket for c in self.dimensions}))
        if card:
            parts.append(f"c:{card}")
        return "|".join(parts)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "row_count": self.row_count,
            "truncated": self.truncated,
            "shape_signature": self.shape_signature(),
            "columns": [c.to_prompt_dict() for c in self.columns],
        }


def profile_rows(
    rows: Sequence[dict[str, Any]] | None,
    *,
    sample_limit: int = 500,
) -> DataProfile:
    """Profile a list of row dicts.

    Only the first ``sample_limit`` rows are inspected — profiling is a hint
    generator and a validation input, not an analytics pass, and chart payloads
    carry at most a few dozen sample rows anyway.
    """
    clean = [r for r in (rows or []) if isinstance(r, dict)]
    if not clean:
        return DataProfile(columns=[], row_count=0)

    sample = clean[:sample_limit]
    names: dict[str, None] = {}
    for row in sample:
        for key in row:
            names.setdefault(key, None)

    columns = [_profile_column(name, [row.get(name) for row in sample]) for name in names]
    return DataProfile(
        columns=columns,
        row_count=len(clean),
        truncated=len(clean) > sample_limit,
    )


def _profile_column(name: str, values: list[Any]) -> ColumnProfile:
    present = [v for v in values if v is not None]
    null_count = len(values) - len(present)
    vega_type, python_type = _infer_vega_type(name, present)

    distinct_keys = {_hashable(v) for v in present}
    distinct = len(distinct_keys)
    is_unique = bool(present) and distinct == len(present)

    min_value = max_value = None
    monotonic = False
    if vega_type in ("quantitative", "temporal") and present:
        try:
            ordered = sorted(present, key=_sort_key)
            min_value, max_value = ordered[0], ordered[-1]
            monotonic = _is_monotonic(present)
        except TypeError:
            # Mixed types that will not sort — leave the range unset rather
            # than guessing. Tier-2 validation still works without it.
            pass

    role = _infer_role(
        name=name,
        vega_type=vega_type,
        python_type=python_type,
        distinct=distinct,
        n_present=len(present),
        is_unique=is_unique,
    )

    return ColumnProfile(
        name=name,
        vega_type=vega_type,
        python_type=python_type,
        non_null=len(present),
        null_count=null_count,
        distinct=distinct,
        is_unique=is_unique,
        role=role,
        sample_values=_first_distinct(present, 8),
        min_value=min_value,
        max_value=max_value,
        monotonic=monotonic,
    )


def _infer_vega_type(name: str, present: list[Any]) -> tuple[str, str]:
    """Return ``(vega_type, python_type)``.

    Booleans are nominal, not quantitative — Vega-Lite will happily average a
    bool otherwise, which is never what the question meant.
    """
    if not present:
        return "nominal", "NoneType"

    # A list of numbers is a per-row series (a sparkline column). Vega-Lite
    # cannot encode it directly, so the measurement type stays nominal, but the
    # role below records what it really is.
    if all(_is_number_list(v) for v in present):
        return "nominal", "series"

    if all(isinstance(v, bool) for v in present):
        return "nominal", "bool"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in present):
        return "quantitative", "float" if any(isinstance(v, float) for v in present) else "int"
    if all(isinstance(v, (date, datetime)) for v in present):
        return "temporal", "datetime"
    if all(isinstance(v, str) for v in present):
        if _looks_temporal(name, present):
            return "temporal", "str"
        return "nominal", "str"
    return "nominal", type(present[0]).__name__


def _is_number_list(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
    )


def _looks_temporal(name: str, values: list[str]) -> bool:
    """String columns that parse as ISO dates are temporal.

    Checks a handful of values rather than the first one alone — a single
    parseable string in a column of free text should not make it a date axis.
    """
    lowered = name.lower()
    name_hints = any(
        hint in lowered
        for hint in ("date", "day", "month", "week", "year", "time", "_at", "period")
    )
    probe = values[: min(len(values), 8)]
    parsed = 0
    for value in probe:
        if len(value) < 6:
            continue
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            parsed += 1
        except ValueError:
            continue
    if not probe:
        return False
    if parsed == len(probe):
        return True
    # A name that screams "date" plus a majority parsing is enough.
    return name_hints and parsed >= max(1, len(probe) // 2)


def _infer_role(
    *,
    name: str,
    vega_type: str,
    python_type: str,
    distinct: int,
    n_present: int,
    is_unique: bool,
) -> str:
    """Classify a column as time / measure / dimension / identifier / series.

    Role is what recommendation actually reasons about — "one time column, one
    measure, one low-cardinality dimension" picks a chart far better than
    "two quantitative, one nominal" does.
    """
    lowered = name.lower()
    if python_type == "series":
        return "series"
    if vega_type == "temporal":
        return "time"

    if vega_type == "quantitative":
        # A unique integer column named like a key is an identifier, not a
        # measure — charting it as a bar height is meaningless.
        looks_like_id = any(lowered == h or lowered.endswith(h) for h in _ID_HINTS)
        if is_unique and looks_like_id and n_present > 3:
            return "identifier"
        if looks_like_id and not any(h in lowered for h in _MEASURE_HINTS):
            return "identifier"
        return "measure"

    # Nominal. Uniqueness alone is NOT enough to call something an identifier:
    # every `GROUP BY region` result has exactly one row per region, so the most
    # common chart shape there is would be misread as a list of ids and lose its
    # dimension entirely. An identifier is unique *and* either named like a key
    # or high-cardinality enough that no axis could show it.
    looks_like_id = any(lowered == h or lowered.endswith(h) for h in _ID_HINTS)
    if is_unique and (looks_like_id or distinct > _MAX_LABEL_CARDINALITY):
        return "identifier"
    return "dimension"


def _is_monotonic(values: list[Any]) -> bool:
    if len(values) < 3:
        return False
    pairs = list(zip(values, values[1:], strict=False))
    try:
        increasing = all(_sort_key(a) <= _sort_key(b) for a, b in pairs)
        decreasing = all(_sort_key(a) >= _sort_key(b) for a, b in pairs)
    except TypeError:
        return False
    return increasing or decreasing


def _sort_key(value: Any) -> Any:
    """Make dates and datetimes comparable with each other as strings.

    `date` and `datetime` do not compare across types in Python, and a column
    can hold both; ISO strings sort identically to the values themselves.
    """
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _hashable(value: Any) -> Any:
    try:
        hash(value)
        return value
    except TypeError:
        return repr(value)


def _first_distinct(values: list[Any], limit: int) -> list[Any]:
    out: list[Any] = []
    seen: set[Any] = set()
    for value in values:
        key = _hashable(value)
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= limit:
            break
    return out
