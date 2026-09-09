"""Deterministic chart recommendation from the data profile.

Most chart choices are not a judgement call. One time column plus one measure is
a line; a categorical dimension plus one measure is a bar; a single row of
numbers is a KPI. Rules settle those instantly, for free, and identically every
time — which matters more than it sounds, because a chart type that changes
between two runs of the same question erodes trust faster than a mediocre chart
does.

The model's job is the residue: choosing between close candidates when the
question implies something the data shape cannot, and naming the result. Rules
produce the candidate list and the reasons; retrieval and the LLM re-rank it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from nexcraftviz.data.profile import DataProfile

#: Question words that shift the ranking. A user asking for a breakdown wants
#: composition even when the shape would default to a plain bar.
INTENT_HINTS: dict[str, tuple[str, ...]] = {
    "trend": ("trend", "over time", "growth", "trajectory", "history", "monthly", "weekly"),
    "ranking": ("top", "bottom", "highest", "lowest", "rank", "best", "worst", "leaders"),
    "part-of-whole": ("share", "split", "breakdown", "composition", "proportion", "mix", "%"),
    "comparison": ("compare", "versus", " vs ", "against", "difference between"),
    "distribution": ("distribution", "spread", "histogram", "outlier", "range", "variance"),
    "target-vs-actual": ("target", "goal", "quota", "sla", "threshold", "on track"),
    "flow": ("funnel", "conversion", "drop-off", "dropoff", "pipeline", "stage"),
    "progress": ("burndown", "burn rate", "sprint", "remaining", "schedule",
                 "timeline", "gantt", "milestone", "on track", "runway",
                 "cumulative flow", "bottleneck", "onboarding", "ramp"),
}

#: Above this many category combinations, grouped bars stop being legible and a
#: heatmap becomes the better default.
MAX_GROUPED_BARS = 30

#: How much a matching question intent adds to a candidate's score.
INTENT_BOOST = 0.15


@dataclass
class Recommendation:
    chart_type: str
    score: float
    reason: str
    intent: str = ""

    def __str__(self) -> str:
        return f"{self.chart_type} ({self.score:.2f}) — {self.reason}"


@dataclass
class RecommendationSet:
    recommendations: list[Recommendation] = field(default_factory=list)
    shape_signature: str = ""

    @property
    def best(self) -> Recommendation | None:
        return self.recommendations[0] if self.recommendations else None

    @property
    def alternatives(self) -> list[Recommendation]:
        return self.recommendations[1:4]

    def __iter__(self):
        return iter(self.recommendations)

    def __len__(self) -> int:
        return len(self.recommendations)


def recommend(profile: DataProfile, *, question: str = "") -> RecommendationSet:
    """Rank chart types for ``profile``, nudged by any intent in ``question``."""
    candidates: list[Recommendation] = []

    # `time_axis`, not `times`: a per-row date attribute (a due date, a renewal
    # date) is not something to plot a trend against, and treating it as one
    # recommends a line chart for data containing no series at all.
    axis = profile.time_axis
    dims = profile.dimensions
    measures = profile.measures
    rows = profile.row_count

    _single_value(candidates, measures, rows)
    if axis is not None and measures:
        _over_time(candidates, dims, measures)
    if dims and measures and axis is None:
        _by_category(candidates, dims, measures)
    if not dims and axis is None:
        _no_dimension(candidates, measures, rows)
    _entity_rows(candidates, profile, rows)

    if not candidates:
        candidates.append(
            Recommendation("table_with_cells", 0.4, "no shape rule matched — show the rows")
        )

    _apply_question_hints(candidates, question)
    _dedupe(candidates)
    candidates.sort(key=lambda c: -c.score)
    return RecommendationSet(
        recommendations=candidates, shape_signature=profile.shape_signature()
    )


def _single_value(candidates: list[Recommendation], measures: list, rows: int) -> None:
    """A single row of numbers is a headline, not a plot.

    Checked first because it also matches "one dimension, one measure" and
    would otherwise lose to a bar chart consisting of one bar.
    """
    if rows > 1 or not measures:
        return
    candidates.append(
        Recommendation(
            "kpi",
            0.95,
            f"a single row with {len(measures)} measure(s) is a headline number",
        )
    )
    if len(measures) >= 2:
        candidates.append(
            Recommendation(
                "bullet", 0.6, "two measures on one row often read as actual vs target"
            )
        )


def _over_time(candidates: list[Recommendation], dims: list, measures: list) -> None:
    several_series = bool(dims and dims[0].distinct > 1)
    if several_series:
        candidates.append(
            Recommendation(
                "multi_line",
                0.9,
                f"time plus a {dims[0].cardinality_bucket}-valued dimension is several series",
                "trend",
            )
        )
        candidates.append(
            Recommendation("line", 0.5, "collapse the series to a single trend", "trend")
        )
    else:
        candidates.append(
            Recommendation("line", 0.9, "one time column and one measure is a trend", "trend")
        )
        candidates.append(
            Recommendation("area", 0.55, "same trend, emphasising volume", "trend")
        )

    if len(measures) >= 2:
        candidates.append(
            Recommendation("multi_line", 0.6, f"{len(measures)} measures over time", "trend")
        )


def _by_category(candidates: list[Recommendation], dims: list, measures: list) -> None:
    primary = dims[0]

    if len(dims) == 1 and len(measures) == 1:
        candidates.append(
            Recommendation(
                "bar",
                0.85,
                f"one dimension ({primary.cardinality_bucket}) and one measure ranks cleanly",
                "ranking",
            )
        )
        if primary.distinct <= 6:
            candidates.append(
                Recommendation(
                    "donut", 0.45, "few categories can read as a composition", "part-of-whole"
                )
            )
            candidates.append(
                Recommendation(
                    "pie", 0.35, "same composition, without the centre label", "part-of-whole"
                )
            )
        return

    if len(dims) >= 2:
        _two_dimensions(candidates, primary, dims[1])
        return

    if len(measures) >= 2:
        candidates.append(
            Recommendation(
                "grouped_bar", 0.75, f"{len(measures)} measures per category", "comparison"
            )
        )
        candidates.append(
            Recommendation(
                "scatter", 0.5, "two measures may relate to each other", "distribution"
            )
        )


def _two_dimensions(candidates: list[Recommendation], primary, secondary) -> None:
    """Grouped bars draw one bar per combination.

    They stop being readable well before the data stops being interesting, so
    past a few dozen cells a matrix has to *outrank* them rather than merely
    appear alongside them. A plain bar on the primary dimension stays on the
    table either way — it is the most common answer to "how do these compare",
    and omitting it made the ranking disagree with what anyone would draw.
    """
    combinations = primary.distinct * secondary.distinct
    crowded = combinations > MAX_GROUPED_BARS

    candidates.append(
        Recommendation(
            "bar",
            0.72,
            f"rank by {primary.name} alone and ignore the second dimension",
            "ranking",
        )
    )

    if crowded:
        candidates.append(
            Recommendation(
                "heatmap",
                0.85,
                f"{combinations} category combinations — a matrix stays readable, bars do not",
                "comparison",
            )
        )
        candidates.append(
            Recommendation(
                "grouped_bar", 0.4, f"{combinations} bars is a lot to read", "comparison"
            )
        )
        candidates.append(
            Recommendation(
                "stacked_bar",
                0.35,
                "composition within each category, though there are many categories",
                "part-of-whole",
            )
        )
        return

    candidates.append(
        Recommendation("grouped_bar", 0.8, "two dimensions compare side by side", "comparison")
    )
    candidates.append(
        Recommendation(
            "stacked_bar",
            0.7,
            "two dimensions as composition within each category",
            "part-of-whole",
        )
    )
    candidates.append(
        Recommendation("heatmap", 0.5, "the same two dimensions as a matrix", "comparison")
    )


def _no_dimension(candidates: list[Recommendation], measures: list, rows: int) -> None:
    if len(measures) == 1 and rows > 5:
        candidates.append(
            Recommendation(
                "histogram",
                0.8,
                "one measure and no dimension is a distribution",
                "distribution",
            )
        )
        candidates.append(
            Recommendation("boxplot", 0.5, "same distribution, summarised", "distribution")
        )

    if len(measures) == 2:
        candidates.append(
            Recommendation(
                "scatter",
                0.85,
                "two measures and no dimension is a relationship",
                "distribution",
            )
        )
        if rows > 20:
            candidates.append(Recommendation("bubble", 0.4, "add a third measure as size"))


def _entity_rows(candidates: list[Recommendation], profile: DataProfile, rows: int) -> None:
    """A per-row series or an identifier means the rows are things to list,
    with any chart living inside the row rather than replacing it."""
    if profile.series and rows > 2:
        candidates.append(
            Recommendation(
                "table_with_cells",
                0.8,
                f"{len(profile.series)} per-row series column(s) belong in a rich table",
            )
        )
    elif profile.by_role("identifier") and rows > 5:
        candidates.append(
            Recommendation(
                "table_with_cells",
                0.65,
                "an identifier column means rows are entities worth listing",
            )
        )


def _apply_question_hints(candidates: list[Recommendation], question: str) -> None:
    """Boost candidates whose intent the question asked for.

    A nudge, not an override: "show me the trend" should promote a line above a
    bar, but must never conjure a line out of data with no time axis.
    """
    lowered = f" {question.lower()} "
    wanted = {
        intent
        for intent, hints in INTENT_HINTS.items()
        if any(hint in lowered for hint in hints)
    }
    if not wanted:
        return
    for candidate in candidates:
        if candidate.intent and candidate.intent in wanted:
            candidate.score = min(1.0, candidate.score + INTENT_BOOST)
            candidate.reason += " (matches the question's intent)"


def _dedupe(candidates: list[Recommendation]) -> None:
    """Keep the highest-scoring entry per chart type, in place."""
    best: dict[str, Recommendation] = {}
    for candidate in candidates:
        current = best.get(candidate.chart_type)
        if current is None or candidate.score > current.score:
            best[candidate.chart_type] = candidate
    candidates[:] = list(best.values())
