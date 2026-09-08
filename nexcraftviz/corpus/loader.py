"""Read the chart-pair corpus.

The corpus is the package's most valuable asset: 200 hand-authored chart pairs,
each carrying both a customer-facing story (``business_goal`` / ``overview`` /
``insight``) and internal routing metadata (``purpose`` / ``use_when`` /
``do_not_use_when`` / ``kinds`` / ``data_shape``). Retrieval (M4) embeds these
and pulls the closest few as prompt examples; until then they are the offline
regression corpus that every spec-level change is tested against.

Loading is plain YAML with no embedding, no Qdrant and no network, so tests and
the CLI can use it freely.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from nexcraftviz.spec.model import Spec

SEED_PATH = Path(__file__).parent / "seed.yaml"

#: Which renderer handles a pair. ``vega-lite`` pairs carry a Vega-Lite spec;
#: ``kpi-card`` and ``table-with-cells`` pairs are rendered by dedicated
#: frontend components and legitimately have no Vega spec at all.
Family = str


@dataclass
class ChartPair:
    """One retrievable example."""

    name: str
    chart_type: str
    # customer-facing
    business_goal: str = ""
    overview: str = ""
    insight: str = ""
    # internal / routing
    purpose: str = ""
    goal: str = ""
    use_when: list[str] = field(default_factory=list)
    do_not_use_when: list[str] = field(default_factory=list)
    kinds: dict[str, Any] = field(default_factory=dict)
    data_shape: dict[str, Any] = field(default_factory=dict)
    example_questions: list[str] = field(default_factory=list)
    # payload — exactly one of these is meaningful, per family
    vega_lite_spec: str = ""
    columns_schema: Any = None

    @property
    def family(self) -> Family:
        declared = str(self.kinds.get("family") or "").strip()
        if declared:
            return declared
        if self.columns_schema:
            return "table-with-cells"
        if self.vega_lite_spec.strip() not in ("", "{}"):
            return "vega-lite"
        return "kpi-card"

    @property
    def intent(self) -> str:
        return str(self.kinds.get("intent") or "")

    @property
    def shape(self) -> str:
        return str(self.kinds.get("shape") or "")

    @property
    def has_vega_spec(self) -> bool:
        """True when this pair carries a Vega-Lite spec worth validating.

        KPI-card pairs ship ``vega_lite_spec: "{}"`` as a placeholder — their
        real payload is a ``kpi_metadata`` block the frontend renders. Treating
        that placeholder as a broken spec would be a false alarm, so the
        distinction lives here rather than at every call site.
        """
        return self.vega_lite_spec.strip() not in ("", "{}")

    def spec(self) -> Spec:
        """Parse the Vega-Lite payload. Empty Spec when the pair has none."""
        if not self.has_vega_spec:
            return Spec({})
        return Spec.from_json(self.vega_lite_spec)


@dataclass
class Corpus:
    pairs: list[ChartPair]
    version: int = 0
    tenant: str = "global"

    def __len__(self) -> int:
        return len(self.pairs)

    def __iter__(self):
        return iter(self.pairs)

    def by_name(self, name: str) -> ChartPair | None:
        return next((p for p in self.pairs if p.name == name), None)

    def by_chart_type(self, chart_type: str) -> list[ChartPair]:
        return [p for p in self.pairs if p.chart_type == chart_type]

    def by_family(self, family: Family) -> list[ChartPair]:
        return [p for p in self.pairs if p.family == family]

    @property
    def with_vega_specs(self) -> list[ChartPair]:
        return [p for p in self.pairs if p.has_vega_spec]

    def chart_type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for pair in self.pairs:
            counts[pair.chart_type] = counts.get(pair.chart_type, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def coverage_gaps(self, minimum: int = 3) -> list[str]:
        """Chart types with fewer than ``minimum`` examples.

        Retrieval degrades badly for a type with one example — it will either
        always return that one or never return it. This is the metric the
        corpus-extension milestone is measured against.
        """
        return [t for t, n in self.chart_type_counts().items() if n < minimum]


def load(path: str | Path | None = None) -> Corpus:
    """Load a corpus from ``path`` (default: the bundled seed)."""
    target = Path(path) if path else SEED_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    raw_pairs = data.get("pairs") or []

    pairs: list[ChartPair] = []
    for entry in raw_pairs:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        pairs.append(
            ChartPair(
                name=str(entry["name"]),
                chart_type=str(entry.get("chart_type") or ""),
                business_goal=str(entry.get("business_goal") or ""),
                overview=str(entry.get("overview") or ""),
                insight=str(entry.get("insight") or ""),
                purpose=str(entry.get("purpose") or ""),
                goal=str(entry.get("goal") or ""),
                use_when=_str_list(entry.get("use_when")),
                do_not_use_when=_str_list(entry.get("do_not_use_when")),
                kinds=entry.get("kinds") or {},
                data_shape=entry.get("data_shape") or {},
                example_questions=_str_list(entry.get("example_questions")),
                vega_lite_spec=str(entry.get("vega_lite_spec") or ""),
                columns_schema=entry.get("columns_schema"),
            )
        )

    return Corpus(
        pairs=pairs,
        version=int(data.get("version") or 0),
        tenant=str(data.get("tenant") or "global"),
    )


@lru_cache(maxsize=1)
def seed() -> Corpus:
    """The bundled corpus, parsed once."""
    return load()


def _str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    if isinstance(value, str) and value:
        return [value]
    return []
