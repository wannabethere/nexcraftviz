"""Pure-regex intent extraction, run before the planner's model call.

Most of what a question says about presentation is said in a handful of stock
phrases: "as a line chart", "top 10", "by month", "in dark mode", "stacked",
"horizontal". Spending a model call to discover that is waste, and worse, it is
*variable* — the same phrasing should produce the same plan every time.

So this runs first and fills what it can. The model then plans over the rest and
may override anything here, which is cp2's pattern (`planner_agent/intent.py`,
applied as a floor after the LLM returns).

Everything here is deliberately conservative. A false positive puts a wrong
constraint into the plan and the model has to fight it, which is worse than
extracting nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Chart type, from an explicit request only. "show me a breakdown" is not a
#: pie chart request, so intent words that merely *suggest* a shape are left to
#: the model.
_CHART_TYPES: dict[str, tuple[str, ...]] = {
    "line": (r"line chart", r"as a line", r"line graph"),
    "bar": (r"bar chart", r"as bars?\b", r"bar graph", r"column chart"),
    "area": (r"area chart", r"as an area"),
    "scatter": (r"scatter ?plot", r"scatter chart", r"as a scatter"),
    "pie": (r"pie chart", r"as a pie"),
    "donut": (r"donut chart", r"doughnut chart", r"as a donut"),
    "heatmap": (r"heat ?map",),
    "histogram": (r"histogram",),
    "boxplot": (r"box ?plot", r"box and whisker"),
    "stacked_bar": (r"stacked bars?", r"stacked bar chart", r"stacked column"),
    "grouped_bar": (r"grouped bars?", r"grouped bar chart", r"side by side bars?"),
    "multi_line": (r"multi[- ]line", r"lines? per\b"),
    "kpi": (r"\bkpi\b", r"single (?:number|value|metric)", r"big number"),
    "table_with_cells": (r"\bas a table\b", r"\bin a table\b", r"tabular"),
    "funnel": (r"funnel chart", r"as a funnel"),
    "waterfall": (r"waterfall chart", r"as a waterfall"),
    "gauge": (r"\bgauge\b",),
}

#: Time grain. Matters because it implies a GROUP BY the generator must honour.
_GRAINS: dict[str, tuple[str, ...]] = {
    "day": (r"\bby day\b", r"\bdaily\b", r"\bper day\b"),
    "week": (r"\bby week\b", r"\bweekly\b", r"\bper week\b"),
    "month": (r"\bby month\b", r"\bmonthly\b", r"\bper month\b"),
    "quarter": (r"\bby quarter\b", r"\bquarterly\b", r"\bper quarter\b"),
    "year": (r"\bby year\b", r"\byearly\b", r"\bannually\b", r"\bper year\b"),
}

_THEMES: dict[str, tuple[str, ...]] = {
    "nexcraftviz-dark": (r"\bdark\b",),
    "nexcraftviz-light": (r"\blight mode\b", r"\blight theme\b"),
    "powerbi": (r"\bpower ?bi\b",),
    "carbon-g90": (r"\bcarbon\b",),
}

#: "top 10", "bottom 5", "the 3 biggest". Bounded because "top 500" is not a
#: chart, and a number that large is more likely a value in the question.
_TOP_N = re.compile(
    r"\b(top|bottom|first|last|worst|best)\s+(\d{1,3})\b"
    r"|\bthe\s+(\d{1,3})\s+(?:biggest|largest|smallest|highest|lowest|worst|best)\b",
    re.IGNORECASE,
)
_MAX_TOP_N = 100

_DESCENDING = re.compile(r"\b(top|biggest|largest|highest|best|most|descending)\b", re.I)
_ASCENDING = re.compile(r"\b(bottom|smallest|lowest|worst|least|ascending)\b", re.I)

_HORIZONTAL = re.compile(r"\bhorizontal\b|\bsideways\b", re.I)
_VERTICAL = re.compile(r"\bvertical\b", re.I)

_NORMALIZE = re.compile(r"\b100\s?%\b|\bnormali[sz]ed?\b|\bas a (?:share|percentage)\b", re.I)

#: A target line. English puts the number on either side of the word — "a
#: threshold at 120" and "a 90% target" are both common — so both orders are
#: matched, with the gap kept short so an unrelated number further along the
#: sentence is not claimed.
_TARGET = re.compile(
    r"\b(?:target|threshold|goal|benchmark|sla)\b[^.\d]{0,20}(\d+(?:\.\d+)?)\s*%?"
    r"|(\d+(?:\.\d+)?)\s*%?[^.\d]{0,15}\b(?:target|threshold|goal|benchmark|sla)\b",
    re.IGNORECASE,
)


@dataclass
class ChartIntent:
    """What the question said outright, before any model reasoned about it."""

    chart_type: str = ""
    time_grain: str = ""
    theme: str = ""
    top_n: int | None = None
    sort_order: str = ""
    horizontal: bool | None = None
    normalize: bool = False
    targets: list[float] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not any(
            (self.chart_type, self.time_grain, self.theme, self.top_n,
             self.sort_order, self.horizontal is not None, self.normalize, self.targets)
        )

    def to_dict(self) -> dict[str, Any]:
        """Only what was actually found — an empty field is not a decision."""
        out: dict[str, Any] = {}
        if self.chart_type:
            out["chart_type"] = self.chart_type
        if self.time_grain:
            out["time_grain"] = self.time_grain
        if self.theme:
            out["theme"] = self.theme
        if self.top_n:
            out["top_n"] = self.top_n
        if self.sort_order:
            out["sort_order"] = self.sort_order
        if self.horizontal is not None:
            out["horizontal"] = self.horizontal
        if self.normalize:
            out["normalize"] = True
        if self.targets:
            out["targets"] = self.targets
        return out


def extract_intent(question: str) -> ChartIntent:
    """Read presentation intent out of a question. No model, no network."""
    if not question:
        return ChartIntent()
    text = f" {question.lower()} "
    intent = ChartIntent()

    intent.chart_type = _first_match(text, _CHART_TYPES)
    intent.time_grain = _first_match(text, _GRAINS)
    intent.theme = _first_match(text, _THEMES)
    intent.top_n = _extract_top_n(text)

    # Sort order is only inferred when a direction word is present. "revenue by
    # region" implies nothing about order, and guessing gives the plan a
    # constraint the user never asked for.
    if _DESCENDING.search(text):
        intent.sort_order = "descending"
    elif _ASCENDING.search(text):
        intent.sort_order = "ascending"

    if _HORIZONTAL.search(text):
        intent.horizontal = True
    elif _VERTICAL.search(text):
        intent.horizontal = False

    intent.normalize = bool(_NORMALIZE.search(text))
    intent.targets = _extract_targets(text)
    return intent


def apply_as_floor(plan: Any, intent: ChartIntent) -> Any:
    """Fill anything the model left blank, without overriding what it decided.

    A floor, not a ceiling: the model has the whole profile and the question,
    so where it made a call it wins. This only catches the cases where it said
    nothing and the question already had.
    """
    if intent.empty:
        return plan

    if not plan.chart_type and intent.chart_type:
        plan.chart_type = intent.chart_type
    if not plan.styling.theme and intent.theme:
        plan.styling.theme = intent.theme
    if plan.styling.horizontal is None and intent.horizontal is not None:
        plan.styling.horizontal = intent.horizontal
    if intent.targets and not plan.styling.reference_lines:
        plan.styling.reference_lines = list(intent.targets)

    if intent.top_n and not any(t.kind == "top_n" for t in plan.transforms):
        from nexcraftviz.agents.artifacts import TransformIntent

        plan.transforms.append(
            TransformIntent(
                kind="top_n",
                detail=f"keep the {intent.sort_order or 'top'} {intent.top_n}",
                value=intent.top_n,
            )
        )

    if intent.sort_order:
        for encoding in plan.encodings:
            if not encoding.sort and encoding.channel in ("x", "y"):
                encoding.sort = intent.sort_order
                break

    return plan


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _first_match(text: str, table: dict[str, tuple[str, ...]]) -> str:
    """The first key whose patterns match.

    Longest pattern first within each key so `stacked bar chart` is not claimed
    by `bar chart`; keys are checked in declaration order, which puts the
    compound types after the simple ones deliberately — a later, more specific
    match wins by being checked against the same text.
    """
    best: tuple[int, str] = (0, "")
    for key, patterns in table.items():
        for pattern in patterns:
            match = re.search(pattern, text)
            if match and len(match.group(0)) > best[0]:
                best = (len(match.group(0)), key)
    return best[1]


def _extract_top_n(text: str) -> int | None:
    match = _TOP_N.search(text)
    if not match:
        return None
    raw = match.group(2) or match.group(3)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if 0 < value <= _MAX_TOP_N else None


def _extract_targets(text: str) -> list[float]:
    out: list[float] = []
    for match in _TARGET.finditer(text):
        try:
            out.append(float(match.group(1) or match.group(2)))
        except (TypeError, ValueError):
            continue
    return out[:3]
