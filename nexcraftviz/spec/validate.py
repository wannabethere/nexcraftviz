"""Three-tier spec validation, cheapest tier first.

The tiers exist because they have wildly different costs and catch different
things:

* **Tier 1 — structural.** Microseconds, no deps. "Is there anything here that
  a renderer could possibly draw?" This is the check the upstream
  ``chart/utils/postprocess.is_structurally_valid`` performs, ported so the
  genieml adapter keeps identical accept/reject behaviour.
* **Tier 2 — data binding.** Microseconds, no deps. "Does every field this spec
  references actually exist in the data, with a compatible type?" This is the
  one that matters most in practice: an LLM that invents ``total_revenue`` when
  the column is ``revenue_total`` produces a spec that passes tier 1, passes the
  Vega-Lite schema, compiles cleanly — and renders an empty chart or a wall of
  NaN. ``DashboardsPane.jsx`` documents exactly this failure in a code comment.
* **Tier 3 — compile + schema.** Milliseconds, needs the ``render`` extra. A
  real Vega-Lite → Vega compile (authoritative: if this fails, nothing renders),
  then JSON Schema against the vendored Vega-Lite v5 schema to catch properties
  Vega-Lite would silently ignore. Schema findings are warnings, not errors —
  see :func:`_validate_schema_and_compile` for why.

Tier 2 is also the only tier that can *repair*: a missing field is usually a
near-miss on a real column name, and matching it back is deterministic.
"""
from __future__ import annotations

import difflib
import gzip
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from nexcraftviz.data.profile import DataProfile, profile_rows
from nexcraftviz.spec.model import Spec, View

Tier = Literal[1, 2, 3]
Severity = Literal["error", "warning"]

#: Aggregate operations that produce a value without reading the named field's
#: own values, so a type mismatch against the source column is not an error.
_TYPE_AGNOSTIC_AGGREGATES = frozenset(
    {"count", "distinct", "valid", "missing", "values"}
)

#: Which Vega-Lite measurement types are acceptable for a profiled column type.
#: Deliberately permissive — a numeric column *can* legitimately be encoded as
#: nominal (e.g. a year used as a category), so only genuinely wrong pairings
#: are flagged.
_TYPE_COMPATIBILITY: dict[str, frozenset[str]] = {
    "quantitative": frozenset({"quantitative", "ordinal", "nominal"}),
    "temporal": frozenset({"temporal", "ordinal", "nominal"}),
    "nominal": frozenset({"nominal", "ordinal"}),
    "ordinal": frozenset({"nominal", "ordinal", "quantitative"}),
}


@dataclass
class ValidationIssue:
    tier: Tier
    code: str
    message: str
    severity: Severity = "error"
    path: str = ""
    field_name: str = ""
    suggestion: str = ""

    def __str__(self) -> str:
        where = f" at {self.path}" if self.path else ""
        hint = f" (did you mean {self.suggestion!r}?)" if self.suggestion else ""
        return f"[tier {self.tier}/{self.code}]{where} {self.message}{hint}"


@dataclass
class ValidationReport:
    """Outcome of validating one spec."""

    ok: bool
    tier_reached: Tier
    tier_failed: Tier | None = None
    issues: list[ValidationIssue] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)
    skipped_tiers: list[Tier] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def summary(self) -> str:
        if self.ok and not self.warnings:
            return f"valid (tier {self.tier_reached})"
        if self.ok:
            return f"valid (tier {self.tier_reached}), {len(self.warnings)} warning(s)"
        first = self.errors[0] if self.errors else None
        return f"invalid at tier {self.tier_failed}: {first}" if first else "invalid"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tier_reached": self.tier_reached,
            "tier_failed": self.tier_failed,
            "errors": [str(i) for i in self.errors],
            "warnings": [str(i) for i in self.warnings],
            "repaired": list(self.repaired),
            "skipped_tiers": list(self.skipped_tiers),
        }


# ---------------------------------------------------------------------------
# Tier 1 — structural
# ---------------------------------------------------------------------------

def is_structurally_valid(spec: Spec | dict[str, Any]) -> bool:
    """Port of ``chart/utils/postprocess.is_structurally_valid``.

    A spec is renderable when it has a mark or a composition operator, OR is a
    ``table_with_cells`` payload (frontend reads ``columns``), OR is a KPI
    payload (frontend reads ``kpi_metadata``). Kept byte-compatible with the
    upstream predicate so the genieml adapter accepts and rejects the same
    specs it does today.
    """
    raw = spec.raw if isinstance(spec, Spec) else spec
    if not isinstance(raw, dict) or not raw:
        return False
    if any(k in raw for k in ("mark", "layer", "hconcat", "vconcat", "facet")):
        return True
    columns = raw.get("columns")
    if isinstance(columns, list) and columns:
        return True
    kpi_meta = raw.get("kpi_metadata")
    if isinstance(kpi_meta, dict) and kpi_meta.get("chart_subtype"):
        return True
    return False


def _validate_structure(spec: Spec) -> list[ValidationIssue]:
    if not spec.raw:
        return [ValidationIssue(1, "empty_spec", "spec is empty")]
    if is_structurally_valid(spec):
        issues: list[ValidationIssue] = []
        # `concat` alone is legal Vega-Lite but the upstream predicate does not
        # list it; accept it here and note the divergence rather than failing.
        if "concat" in spec.raw and not any(
            k in spec.raw for k in ("mark", "layer", "hconcat", "vconcat", "facet")
        ):
            issues.append(
                ValidationIssue(
                    1,
                    "concat_only",
                    "spec uses `concat`; the legacy frontend predicate does not "
                    "recognise it — prefer hconcat/vconcat for compatibility",
                    severity="warning",
                )
            )
        return issues
    if "repeat" in spec.raw or "spec" in spec.raw:
        return [
            ValidationIssue(
                1,
                "unsupported_composition",
                "spec uses `repeat`/`spec` composition, which the frontend "
                "renderer does not handle — expand it into layer or concat",
            )
        ]
    return [
        ValidationIssue(
            1,
            "no_renderable_content",
            "spec has no mark, no composition operator, no `columns` array and "
            "no `kpi_metadata` block — nothing can render it",
        )
    ]


# ---------------------------------------------------------------------------
# Tier 2 — data binding
# ---------------------------------------------------------------------------

def _scope_for(spec: Spec, view: View, root_columns: set[str]) -> set[str]:
    """Columns visible to ``view``, following Vega-Lite's own scoping rules.

    A field is legal in an encoding if it comes from the data *or* was produced
    by a transform at this view or any ancestor. Gauge and radial-progress specs
    lean on this heavily — they compute an angle with a ``calculate`` transform
    on the layer and then encode ``theta`` on the result — so validating
    encodings against the root data columns alone rejects perfectly good specs.

    A view-local ``data`` block replaces the inherited columns rather than
    adding to them, which is also what Vega-Lite does.
    """
    columns = set(root_columns)
    node: Any = spec.raw
    columns |= _transform_outputs_of(node)

    for step in view.path:
        if isinstance(step, int):
            node = node[step] if isinstance(node, list) and step < len(node) else None
        else:
            node = node.get(step) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            continue
        local = _inline_columns(node)
        if local is not None:
            columns = local
        columns |= _transform_outputs_of(node)

    return columns


def _inline_columns(node: dict[str, Any]) -> set[str] | None:
    """Column names from a node's own inline ``data.values``, or None."""
    data = node.get("data")
    if not isinstance(data, dict):
        return None
    values = data.get("values")
    if not isinstance(values, list):
        # A `url`/`name` source hides its columns from us; treat the scope as
        # unknown rather than empty so we do not report every field missing.
        return None
    columns: set[str] = set()
    for row in values[:50]:
        if isinstance(row, dict):
            columns |= set(row)
    return columns or None


def _transform_outputs_of(node: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    transforms = node.get("transform")
    if isinstance(transforms, list):
        for step in transforms:
            if isinstance(step, dict):
                out |= _transform_outputs(step)
    return out


def _validate_data_binding(
    spec: Spec, profile: DataProfile
) -> list[ValidationIssue]:
    if not profile.column_names:
        return [
            ValidationIssue(
                2,
                "no_data",
                "no data columns available to validate field references against",
                severity="warning",
            )
        ]

    issues: list[ValidationIssue] = []
    root_columns = set(profile.column_names)
    scopes: dict[tuple[str | int, ...], set[str]] = {
        view.path: _scope_for(spec, view, root_columns) for view in spec.views()
    }

    for view, channel, definition in spec.encodings():
        field_name = definition.get("field")
        if not isinstance(field_name, str) or not field_name:
            continue
        path = _path_str(view.path, channel)
        known = scopes.get(view.path, root_columns)

        if field_name not in known:
            # Vega-Lite treats dots as nested-object access; a literal column
            # named "a.b" must be escaped. Check the unescaped form too before
            # calling it missing.
            unescaped = field_name.replace("\\.", ".")
            if unescaped in known:
                continue
            issues.append(
                ValidationIssue(
                    2,
                    "unknown_field",
                    f"encoding references field {field_name!r}, which is not in "
                    f"the data ({len(known)} columns available)",
                    path=path,
                    field_name=field_name,
                    suggestion=_nearest(field_name, profile.column_names),
                )
            )
            continue

        column = profile.get(field_name)
        declared = definition.get("type")
        aggregate = definition.get("aggregate")
        if (
            column is not None
            and isinstance(declared, str)
            and declared
            and not (isinstance(aggregate, str) and aggregate in _TYPE_AGNOSTIC_AGGREGATES)
        ):
            allowed = _TYPE_COMPATIBILITY.get(column.vega_type, frozenset())
            if declared not in allowed:
                issues.append(
                    ValidationIssue(
                        2,
                        "type_mismatch",
                        f"field {field_name!r} is {column.vega_type} in the data "
                        f"but encoded as {declared!r}",
                        path=path,
                        field_name=field_name,
                        suggestion=column.vega_type,
                    )
                )

    issues.extend(_validate_transform_fields(spec, root_columns))
    issues.extend(_validate_sort_fields(spec, scopes, root_columns))
    return issues


def _validate_transform_fields(spec: Spec, known: set[str]) -> list[ValidationIssue]:
    """Check field references inside `transform` blocks.

    Transforms both *consume* and *produce* columns, so a field is fine if it
    is either in the data or was created by an earlier transform in the array.
    """
    issues: list[ValidationIssue] = []
    for view in spec.views():
        available = set(known) | _transform_outputs_of(spec.raw)
        local = _inline_columns(view.node)
        if local is not None:
            available = set(local)
        transforms = view.node.get("transform")
        if not isinstance(transforms, list):
            continue
        for idx, step in enumerate(transforms):
            if not isinstance(step, dict):
                continue
            path = _path_str(view.path, f"transform[{idx}]")
            for consumed in _transform_inputs(step):
                if consumed not in available:
                    issues.append(
                        ValidationIssue(
                            2,
                            "unknown_transform_field",
                            f"transform reads field {consumed!r}, which is not in "
                            f"the data and is not produced by an earlier transform",
                            path=path,
                            field_name=consumed,
                            suggestion=_nearest(consumed, sorted(available)),
                        )
                    )
            available |= _transform_outputs(step)
    return issues


def _transform_inputs(step: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("field", "groupby", "sort", "on", "key", "density", "extent"):
        value = step.get(key)
        if isinstance(value, str) and key in ("field", "on", "key", "density"):
            out.append(value)
        elif isinstance(value, list) and key == "groupby":
            out.extend(v for v in value if isinstance(v, str))
    for aggregate in step.get("aggregate", []) or []:
        if isinstance(aggregate, dict):
            src = aggregate.get("field")
            if isinstance(src, str):
                out.append(src)
    return out


def _transform_outputs(step: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    as_value = step.get("as")
    if isinstance(as_value, str):
        out.add(as_value)
    elif isinstance(as_value, list):
        out |= {v for v in as_value if isinstance(v, str)}
    # These three name each output inside its entry. Only `aggregate` was read,
    # so a live grouped bar sorted by `unit_total` — created by its own
    # `window` — was rejected as naming a column that does not exist, and a
    # retry cannot fix a chart that was never wrong.
    for key in ("aggregate", "window", "joinaggregate"):
        for entry in step.get(key, []) or []:
            if isinstance(entry, dict) and isinstance(entry.get("as"), str):
                out.add(entry["as"])
    if isinstance(step.get("calculate"), str) and isinstance(step.get("as"), str):
        out.add(step["as"])
    # `fold` emits key/value pairs, renamed by `as` when given — which the
    # branch above has already handled, so only add the defaults when it wasn't.
    if isinstance(step.get("fold"), list) and as_value is None:
        out |= {"key", "value"}
    if isinstance(step.get("bin"), (bool, dict)) and isinstance(step.get("field"), str):
        base = step["field"]
        out |= {f"bin_{base}", f"bin_{base}_end"}
    return out


def _validate_sort_fields(
    spec: Spec,
    scopes: dict[tuple[str | int, ...], set[str]],
    root_columns: set[str],
) -> list[ValidationIssue]:
    """`sort: {field: ...}` silently no-ops on an unknown field."""
    issues: list[ValidationIssue] = []
    for view, channel, definition in spec.encodings():
        known = scopes.get(view.path, root_columns)
        sort = definition.get("sort")
        if isinstance(sort, dict):
            sort_field = sort.get("field")
            if isinstance(sort_field, str) and sort_field and sort_field not in known:
                issues.append(
                    ValidationIssue(
                        2,
                        "unknown_sort_field",
                        f"sort references field {sort_field!r}, which is not in the data",
                        path=_path_str(view.path, f"{channel}.sort"),
                        field_name=sort_field,
                        suggestion=_nearest(sort_field, sorted(known)),
                    )
                )
    return issues


def repair_data_binding(spec: Spec, profile: DataProfile) -> tuple[Spec, list[str]]:
    """Deterministically fix what tier 2 can fix, in place on a clone.

    Three repairs, all safe:

    * **Field rename** — an unknown field that is a close string match for a
      real column is rewritten to that column. The threshold is deliberately
      high (0.82); a weak match is worse than an honest failure. Sort fields
      get the same treatment.
    * **Aggregate prefix** — `total_findings` over data with `findings` is the
      name of a sum the model meant to take, not a column. It becomes the
      column plus the aggregate the prefix names — only when what remains is a
      real column, and never overriding an aggregate the model wrote.
    * **Type coercion** — a declared measurement type incompatible with the
      profiled column type is replaced with the profiled type.

    Returns the repaired clone and a human-readable list of what changed.
    """
    if not profile.column_names:
        return spec, []

    repaired = spec.clone()
    notes: list[str] = []
    root_columns = set(profile.column_names)
    scopes = {v.path: _scope_for(repaired, v, root_columns) for v in repaired.views()}

    for view, channel, definition in repaired.encodings():
        known = scopes.get(view.path, root_columns)
        notes.extend(_repair_sort_field(definition, known, _path_str(view.path, channel)))
        field_name = definition.get("field")
        if isinstance(field_name, str) and field_name and field_name not in known:
            match = _nearest(field_name, profile.column_names, cutoff=0.82)
            stem = None if match else _aggregate_stem(field_name, known)
            if match:
                definition["field"] = match
                notes.append(
                    f"{_path_str(view.path, channel)}: field {field_name!r} → {match!r}"
                )
                field_name = match
            elif stem:
                column, op = stem
                definition["field"] = column
                if not definition.get("aggregate"):
                    definition["aggregate"] = op
                notes.append(
                    f"{_path_str(view.path, channel)}: field {field_name!r} → "
                    f"{definition['aggregate']} of {column!r}"
                )
                field_name = column

        if not isinstance(field_name, str) or field_name not in known:
            continue
        column = profile.get(field_name)
        declared = definition.get("type")
        aggregate = definition.get("aggregate")
        if (
            column is not None
            and isinstance(declared, str)
            and declared
            and not (isinstance(aggregate, str) and aggregate in _TYPE_AGNOSTIC_AGGREGATES)
            and declared not in _TYPE_COMPATIBILITY.get(column.vega_type, frozenset())
        ):
            definition["type"] = column.vega_type
            notes.append(
                f"{_path_str(view.path, channel)}: type {declared!r} → {column.vega_type!r}"
            )

    return repaired, notes


#: Prefixes a model writes on a measure it means to aggregate — `total_findings`
#: for the sum of `findings` — mapped to the aggregate each one names.
_AGGREGATE_PREFIXES = {
    "total": "sum", "sum": "sum", "mean": "mean", "avg": "mean", "average": "mean",
    "median": "median", "min": "min", "max": "max", "count": "count",
}


def _aggregate_stem(name: str, known: set[str]) -> tuple[str, str] | None:
    """`total_findings` → (`findings`, "sum"), when `findings` is a real column."""
    head, sep, stem = name.partition("_")
    op = _AGGREGATE_PREFIXES.get(head.lower())
    if sep and op and stem in known:
        return stem, op
    return None


def _repair_sort_field(definition: dict[str, Any], known: set[str], where: str) -> list[str]:
    """A `sort: {field: ...}` naming a column that is not there does nothing.

    Found live: a grouped bar sorted by `total_findings` over data with
    `findings`. The validator said so and suggested the column; the retry
    returned the same name. So it is fixed here rather than by asking again.
    """
    sort = definition.get("sort")
    if not isinstance(sort, dict):
        return []
    name = sort.get("field")
    if not isinstance(name, str) or not name or name in known:
        return []
    match = _nearest(name, sorted(known), cutoff=0.82)
    op = ""
    if not match and (stem := _aggregate_stem(name, known)):
        match, op = stem
    if not match:
        return []
    sort["field"] = match
    if op and not sort.get("op"):
        sort["op"] = op
    suffix = f" ({sort['op']})" if sort.get("op") else ""
    return [f"{where}.sort: field {name!r} → {match!r}{suffix}"]


# ---------------------------------------------------------------------------
# Tier 3 — schema + compile
# ---------------------------------------------------------------------------

#: Vendored, gzipped Vega-Lite v5 JSON Schema. Vendored rather than fetched so
#: validation works offline and pins to a known version; ~140 KB compressed.
_SCHEMA_PATH = Path(__file__).parent / "schemas" / "vega-lite-v5.schema.json"


@lru_cache(maxsize=1)
def _vega_lite_schema() -> dict[str, Any] | None:
    """Load the pinned Vega-Lite v5 JSON Schema, or ``None`` if unavailable."""
    for candidate in (_SCHEMA_PATH.with_suffix(".json.gz"), _SCHEMA_PATH):
        if not candidate.exists():
            continue
        try:
            if candidate.suffix == ".gz":
                with gzip.open(candidate, "rt", encoding="utf-8") as handle:
                    return json.load(handle)
            return json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


@lru_cache(maxsize=1)
def _schema_validator() -> Any | None:
    schema = _vega_lite_schema()
    if schema is None:
        return None
    try:
        import jsonschema
    except ImportError:
        return None
    return jsonschema.Draft7Validator(schema)


def _validate_schema_and_compile(
    spec: Spec, *, strict_schema: bool
) -> tuple[list[ValidationIssue], bool]:
    """Returns ``(issues, ran)``. ``ran`` is False when the extra is missing.

    Order matters: the compile runs first because it is fast and authoritative —
    if Vega-Lite cannot lower this to Vega, nothing will render it, and the
    compiler's message is more useful than a schema traversal.

    JSON Schema runs only *after* a successful compile, and its findings are
    warnings by default. That is not laziness: Vega-Lite silently ignores
    properties it does not recognise, so a spec can violate the schema (a
    misspelled ``labelAngel``, a stray key) and still compile and render — just
    without the effect the author intended. Surfacing that as a warning tells
    the truth; calling it an error would reject charts that do render.
    """
    try:
        import vl_convert as vlc
    except ImportError:
        return [], False

    try:
        vlc.vegalite_to_vega(spec.to_json())
    except Exception as exc:  # noqa: BLE001 — vl_convert raises bare exceptions
        return [ValidationIssue(3, "compile_failed", f"Vega-Lite compile failed: {exc}")], True

    validator = _schema_validator()
    if validator is None:
        return [], True

    issues: list[ValidationIssue] = []
    severity: Severity = "error" if strict_schema else "warning"
    for error in sorted(validator.iter_errors(spec.raw), key=lambda e: list(e.path))[:10]:
        issues.append(
            ValidationIssue(
                3,
                "schema_violation",
                _terse_schema_message(error),
                severity=severity,
                path="/".join(str(p) for p in error.path),
            )
        )
    return issues, True


def _terse_schema_message(error: Any) -> str:
    """Trim jsonschema's message.

    Vega-Lite's schema is a wall of ``anyOf`` branches, so a failure produces a
    message that inlines hundreds of alternatives. The first line carries the
    signal; the rest is noise in a log.
    """
    message = str(error.message)
    if len(message) > 240:
        message = message[:237] + "..."
    return message


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def validate(
    spec: Spec | dict[str, Any],
    *,
    data: list[dict[str, Any]] | None = None,
    profile: DataProfile | None = None,
    max_tier: Tier = 3,
    repair: bool = False,
    strict_schema: bool = False,
) -> tuple[Spec, ValidationReport]:
    """Validate ``spec`` up to ``max_tier``, optionally repairing tier-2 issues.

    Data for tier 2 comes from ``profile``, else ``data``, else the spec's own
    inline ``data.values`` — which is where the pipeline puts result rows, so
    the common case needs no extra argument.

    Returns ``(spec, report)``. The spec is a repaired clone when ``repair`` is
    on and something was fixed; otherwise it is the input unchanged.

    Non-Vega families (``kpi``, ``table``) stop after tier 1 — there is no
    Vega-Lite schema to check them against, and their field references are
    interpreted by frontend components, not by Vega.
    """
    spec = spec if isinstance(spec, Spec) else Spec(spec)

    issues = _validate_structure(spec)
    if any(i.severity == "error" for i in issues):
        return spec, ValidationReport(ok=False, tier_reached=1, tier_failed=1, issues=issues)
    if max_tier < 2:
        return spec, ValidationReport(ok=True, tier_reached=1, issues=issues)

    family = spec.family
    if family in ("kpi", "table"):
        return spec, ValidationReport(
            ok=True, tier_reached=1, issues=issues, skipped_tiers=[2, 3]
        )

    if profile is None:
        profile = profile_rows(data if data is not None else spec.data_values)

    repaired_notes: list[str] = []
    binding_issues = _validate_data_binding(spec, profile)
    if repair and any(i.severity == "error" for i in binding_issues):
        spec, repaired_notes = repair_data_binding(spec, profile)
        if repaired_notes:
            binding_issues = _validate_data_binding(spec, profile)
    issues.extend(binding_issues)

    if any(i.tier == 2 and i.severity == "error" for i in issues):
        return spec, ValidationReport(
            ok=False, tier_reached=2, tier_failed=2, issues=issues, repaired=repaired_notes
        )
    if max_tier < 3:
        return spec, ValidationReport(
            ok=True, tier_reached=2, issues=issues, repaired=repaired_notes
        )

    if family == "vega":
        # Full Vega specs are valid targets for the renderer but not for the
        # Vega-Lite schema or compiler. Nothing further to check here.
        return spec, ValidationReport(
            ok=True, tier_reached=2, issues=issues, repaired=repaired_notes, skipped_tiers=[3]
        )

    tier3_issues, ran = _validate_schema_and_compile(spec, strict_schema=strict_schema)
    issues.extend(tier3_issues)
    if not ran:
        return spec, ValidationReport(
            ok=True, tier_reached=2, issues=issues, repaired=repaired_notes, skipped_tiers=[3]
        )
    if any(i.tier == 3 and i.severity == "error" for i in issues):
        return spec, ValidationReport(
            ok=False, tier_reached=3, tier_failed=3, issues=issues, repaired=repaired_notes
        )
    return spec, ValidationReport(
        ok=True, tier_reached=3, issues=issues, repaired=repaired_notes
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _nearest(name: str, candidates: list[str], *, cutoff: float = 0.6) -> str:
    """Closest column name, comparing case- and separator-insensitively.

    ``total_revenue`` vs ``TotalRevenue`` vs ``total revenue`` should all match;
    plain ``difflib`` on the raw strings would miss those.
    """
    if not candidates:
        return ""
    normalized = {_normalize(c): c for c in candidates}
    matches = difflib.get_close_matches(_normalize(name), list(normalized), n=1, cutoff=cutoff)
    return normalized[matches[0]] if matches else ""


def _normalize(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _path_str(view_path: tuple[str | int, ...], channel: str) -> str:
    prefix = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in view_path)
    return f"{prefix.lstrip('.')}.encoding.{channel}".lstrip(".")
