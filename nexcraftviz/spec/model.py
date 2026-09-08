"""The ``Spec`` wrapper — a dict-backed Vega-Lite specification.

Vega-Lite v5 is far too polymorphic to mirror as a Pydantic model (that is
exactly why the upstream ``chart.generate_vega`` skill ships the schema as a
JSON *string*). So ``Spec`` stays dict-backed and adds only what the rest of the
package needs: stable hashing, deep cloning, path access, and — the important
one — a uniform way to walk the *view-level* specs inside a composed spec.

A "view" here is a leaf that owns a ``mark`` + ``encoding``. In a unit spec that
is the root; in a ``layer``/``concat``/``facet``/``repeat`` spec there are
several, nested at different depths. Every operation and every validation tier
works against ``Spec.views()`` so that layered charts are handled by the same
code as simple ones.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

VEGA_LITE_V5_SCHEMA_URL = "https://vega.github.io/schema/vega-lite/v5.json"
#: Full-Vega specs are pinned to v6 because that is what ``vl-convert`` compiles
#: Vega-Lite down to. Family detection is version-agnostic (it matches "/vega/"),
#: so a v5 spec authored elsewhere is still recognised.
VEGA_SCHEMA_URL = "https://vega.github.io/schema/vega/v6.json"

#: Keys whose values hold nested specs. Order matters only for readability.
_CONTAINER_KEYS = ("layer", "hconcat", "vconcat", "concat")
#: Keys whose value is a single nested spec.
_SINGLE_SPEC_KEYS = ("spec",)

#: Encoding channels that carry a field reference we can validate and rewrite.
FIELD_CHANNELS = (
    "x", "y", "x2", "y2", "xError", "yError",
    "color", "opacity", "fill", "fillOpacity", "stroke", "strokeOpacity",
    "strokeWidth", "size", "shape", "angle", "radius", "radius2", "theta",
    "theta2", "longitude", "latitude", "longitude2", "latitude2",
    "text", "detail", "order", "row", "column", "facet", "href", "key",
    "tooltip", "description", "url", "xOffset", "yOffset",
)


class SpecError(ValueError):
    """Raised when a spec cannot be parsed or an operation cannot apply."""


@dataclass(frozen=True)
class View:
    """A leaf view inside a spec — the thing that owns ``mark`` + ``encoding``.

    ``path`` is the sequence of dict keys / list indices from the spec root to
    this view, e.g. ``("layer", 0)``. ``node`` is a *live reference* into the
    owning spec's dict, so mutating it mutates the spec. Operations rely on
    that; callers who want isolation should clone the Spec first.
    """

    path: tuple[str | int, ...]
    node: dict[str, Any]

    @property
    def encoding(self) -> dict[str, Any]:
        enc = self.node.get("encoding")
        return enc if isinstance(enc, dict) else {}

    @property
    def mark_type(self) -> str:
        mark = self.node.get("mark")
        if isinstance(mark, str):
            return mark
        if isinstance(mark, dict):
            return str(mark.get("type") or "")
        return ""


class Spec:
    """A Vega-Lite (or Vega) specification with helpers the algebra needs."""

    __slots__ = ("raw",)

    def __init__(self, raw: dict[str, Any] | None = None) -> None:
        if raw is not None and not isinstance(raw, dict):
            raise SpecError(f"spec must be a dict, got {type(raw).__name__}")
        self.raw: dict[str, Any] = raw if raw is not None else {}

    # ---- construction -----------------------------------------------------

    @classmethod
    def from_json(cls, text: str) -> Spec:
        """Parse a JSON string, tolerating the markdown fences LLMs add.

        Mirrors ``chart/utils/postprocess.parse_chart_schema`` but raises
        instead of silently returning ``{}`` — callers that want the lenient
        behaviour should use :meth:`from_json_lenient`.
        """
        raw = _strip_fences(text)
        if not raw:
            raise SpecError("empty spec JSON")
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise SpecError(f"spec is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise SpecError(f"spec must decode to an object, got {type(parsed).__name__}")
        return cls(parsed)

    @classmethod
    def from_json_lenient(cls, text: str) -> Spec:
        """Never raises — returns an empty Spec when the text will not parse."""
        try:
            return cls.from_json(text)
        except SpecError:
            return cls({})

    def clone(self) -> Spec:
        return Spec(copy.deepcopy(self.raw))

    # ---- identity ---------------------------------------------------------

    def to_json(self, *, indent: int | None = None, sort_keys: bool = False) -> str:
        return json.dumps(self.raw, indent=indent, sort_keys=sort_keys, default=str)

    @property
    def hash(self) -> str:
        """Stable content hash — key-order independent, so it survives a
        round-trip through any JSON encoder."""
        canonical = json.dumps(self.raw, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Spec) and self.hash == other.hash

    def __hash__(self) -> int:
        return hash(self.hash)

    def __bool__(self) -> bool:
        return bool(self.raw)

    def __repr__(self) -> str:
        return f"<Spec {self.family}/{self.mark_summary or '?'} {self.hash}>"

    # ---- shape ------------------------------------------------------------

    @property
    def family(self) -> str:
        """``vega-lite`` | ``vega`` | ``table`` | ``kpi`` | ``unknown``.

        Non-Vega families are first-class payloads in this stack: ``kpi`` specs
        carry a ``kpi_metadata`` block and ``table`` specs a ``columns`` array
        of per-column renderers. Both are rendered by hand-written frontend
        components, not by Vega, so most operations do not apply to them.
        """
        schema = str(self.raw.get("$schema") or "")
        if "/vega-lite/" in schema:
            return "vega-lite"
        if "/vega/" in schema:
            return "vega"
        if isinstance(self.raw.get("kpi_metadata"), dict):
            return "kpi"
        if isinstance(self.raw.get("columns"), list) and self.raw["columns"]:
            return "table"
        if self._has_vega_lite_shape():
            return "vega-lite"
        if isinstance(self.raw.get("marks"), list):
            return "vega"
        return "unknown"

    def _has_vega_lite_shape(self) -> bool:
        return any(
            key in self.raw
            for key in (
                "mark", "encoding", "layer", "hconcat",
                "vconcat", "concat", "facet", "repeat",
            )
        )

    @property
    def is_composed(self) -> bool:
        """True when the spec has more than one view (layer / concat / facet)."""
        return len(self.views()) > 1 or any(k in self.raw for k in _CONTAINER_KEYS)

    @property
    def mark_summary(self) -> str:
        marks = [v.mark_type for v in self.views() if v.mark_type]
        if not marks:
            return ""
        if len(set(marks)) == 1:
            return marks[0]
        return "+".join(dict.fromkeys(marks))

    # ---- views ------------------------------------------------------------

    def views(self) -> list[View]:
        """Every leaf view in document order.

        For a unit spec this is a single view at the root. Falls back to the
        root node when nothing looks like a view, so callers always get
        something to work with rather than an empty list.
        """
        found = list(self._walk_views(self.raw, ()))
        if found:
            return found
        return [View(path=(), node=self.raw)]

    def _walk_views(
        self, node: Any, path: tuple[str | int, ...]
    ) -> Iterator[View]:
        if not isinstance(node, dict):
            return
        for key in _CONTAINER_KEYS:
            children = node.get(key)
            if isinstance(children, list):
                for idx, child in enumerate(children):
                    yield from self._walk_views(child, (*path, key, idx))
                return
        for key in _SINGLE_SPEC_KEYS:
            child = node.get(key)
            if isinstance(child, dict):
                yield from self._walk_views(child, (*path, key))
                return
        if "mark" in node or "encoding" in node:
            yield View(path=path, node=node)

    @property
    def primary_view(self) -> View:
        """The view most operations target when the caller does not say which."""
        return self.views()[0]

    # ---- encodings --------------------------------------------------------

    def encodings(self) -> Iterator[tuple[View, str, dict[str, Any]]]:
        """Yield ``(view, channel, definition)`` for every object-valued channel.

        Channels can hold a list (``tooltip``, ``detail``, ``order``); each entry
        is yielded separately so field validation sees all of them.
        """
        for view in self.views():
            for channel, definition in view.encoding.items():
                if isinstance(definition, dict):
                    yield view, channel, definition
                elif isinstance(definition, list):
                    for entry in definition:
                        if isinstance(entry, dict):
                            yield view, channel, entry

    def field_refs(self) -> list[tuple[View, str, str]]:
        """Every ``(view, channel, field_name)`` actually bound to a column.

        Skips ``value``/``datum``/``expr`` channels — those are literals, not
        column references — and skips the ``repeat`` indirection, which resolves
        at compile time rather than against the data.
        """
        refs: list[tuple[View, str, str]] = []
        for view, channel, definition in self.encodings():
            field = definition.get("field")
            if isinstance(field, str) and field:
                refs.append((view, channel, field))
        return refs

    # ---- data -------------------------------------------------------------

    @property
    def data_values(self) -> list[dict[str, Any]]:
        data = self.raw.get("data")
        if isinstance(data, dict):
            values = data.get("values")
            if isinstance(values, list):
                return [row for row in values if isinstance(row, dict)]
        return []

    def set_data_values(self, rows: list[dict[str, Any]]) -> Spec:
        """Bind inline rows at the root. Returns ``self`` for chaining."""
        data = self.raw.get("data")
        if not isinstance(data, dict):
            data = {}
        data.pop("url", None)
        data.pop("name", None)
        data["values"] = rows
        self.raw["data"] = data
        return self

    @property
    def data_columns(self) -> list[str]:
        """Column names visible in the spec's own inline data, in first-row order."""
        rows = self.data_values
        if not rows:
            return []
        seen: dict[str, None] = {}
        for row in rows[:50]:
            for key in row:
                seen.setdefault(key, None)
        return list(seen)

    # ---- path access ------------------------------------------------------

    def get_path(self, path: tuple[str | int, ...], default: Any = None) -> Any:
        node: Any = self.raw
        for step in path:
            if isinstance(step, int):
                if not isinstance(node, list) or step >= len(node):
                    return default
                node = node[step]
            else:
                if not isinstance(node, dict) or step not in node:
                    return default
                node = node[step]
        return node

    def ensure_schema_url(self) -> Spec:
        """Pin ``$schema`` if absent, matching the family we detect."""
        if "$schema" not in self.raw and self.raw:
            family = self.family
            if family == "vega-lite":
                self.raw["$schema"] = VEGA_LITE_V5_SCHEMA_URL
            elif family == "vega":
                self.raw["$schema"] = VEGA_SCHEMA_URL
        return self


def _strip_fences(text: str) -> str:
    """Remove a leading ```json fence and trailing ``` if present."""
    if not isinstance(text, str):
        return ""
    raw = text.strip()
    if not raw.startswith("```"):
        return raw
    lines = raw.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
