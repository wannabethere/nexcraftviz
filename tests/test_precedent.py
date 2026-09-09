"""Choosing a chart type from what the corpus did for data like this.

The rules answer "what *can* be drawn" from the shape. They cannot answer "what
is worth drawing" — one dimension and one measure is a bar, a donut, a funnel
and a waterfall, and the columns say nothing about which. These tests are about
the part that closes that gap.
"""
from __future__ import annotations

from typing import Any

import pytest

from nexcraftviz.data.profile import profile_rows
from nexcraftviz.recommend import retrieval
from nexcraftviz.recommend.precedent import (
    kind_of,
    precedents,
    precedents_async,
    shape_signature,
)

CATEGORIES = [
    {"unit": "Clinical", "completed": 905},
    {"unit": "Retail", "completed": 1201},
    {"unit": "Logistics", "completed": 321},
]
TREND = [{"month": f"2026-0{i}-01", "completed": 100 * i} for i in range(1, 6)]
SINGLE = [{"completion_pct": 87.0}]
#: A date that describes each row rather than ordering them — the trap
#: `DataProfile.time_axis` exists for.
ATTRIBUTE_DATE = [
    {"unit": "Clinical", "completed": 905, "next_audit": "2026-03-14"},
    {"unit": "Retail", "completed": 1201, "next_audit": "2026-04-21"},
    {"unit": "Logistics", "completed": 321, "next_audit": "2026-01-30"},
]


def _types(rows: list[dict[str, Any]], question: str = "", limit: int = 8) -> list[str]:
    return [p.chart_type for p in precedents(profile_rows(rows), question=question, limit=limit)]


# ---------------------------------------------------------------------------
# the shape gate
# ---------------------------------------------------------------------------

def test_the_signature_speaks_the_corpus_vocabulary():
    assert shape_signature(profile_rows(CATEGORIES)) == "number+string"
    assert shape_signature(profile_rows(TREND)) == "date+number"


@pytest.mark.parametrize(
    ("column", "expected"),
    [("completed", "number"), ("unit", "string")],
)
def test_columns_map_onto_the_four_kinds(column, expected):
    profile = profile_rows(CATEGORIES)
    assert kind_of(profile.get(column)) == expected


def test_a_single_value_chart_is_ruled_out_by_many_rows():
    """The corpus says it itself — the KPI pairs' first `use_when` is "Answer is
    a single scalar". Without this every grouped result ranks kpi near the top,
    because a number is a number."""
    assert "kpi" not in _types(CATEGORIES, "how are units doing?")
    assert "gauge" not in _types(CATEGORIES, "how are units doing?")


def test_a_single_value_chart_wins_on_a_single_value():
    ranked = _types(SINGLE, "what is our completion rate?")
    assert ranked[0] == "kpi"
    assert {"gauge", "radial_progress"} & set(ranked)


def test_a_trend_chart_needs_a_time_axis_not_merely_a_date():
    """`next_audit` describes each row; plotting against it draws a line through
    unrelated points. The rows are not observations over that column."""
    ranked = _types(ATTRIBUTE_DATE, "how are units doing?")
    assert not ({"line", "multi_line", "area"} & set(ranked)), ranked


def test_a_trend_chart_is_available_when_the_rows_are_a_series():
    assert {"line", "area"} & set(_types(TREND, "how has completion changed over time?"))


def test_a_chart_needing_a_column_the_data_lacks_is_disqualified_not_ranked_low():
    """No amount of question-matching should rescue a chart the data cannot
    support."""
    ranked = _types(SINGLE, "show the trend over time by department")
    assert not ({"line", "multi_line", "grouped_bar"} & set(ranked)), ranked


# ---------------------------------------------------------------------------
# reading the question
# ---------------------------------------------------------------------------

def test_a_quoted_routing_keyword_selects_its_chart():
    """"Question uses 'top', 'most', 'highest'" is the corpus stating outright
    that a word selects a chart. It is the strongest signal there is."""
    found = precedents(profile_rows(CATEGORIES), question="top 3 units by completions")
    assert found.best is not None
    assert found.best.chart_type == "bar"
    assert "'top'" in found.best.why


def test_the_reason_quotes_the_corpus_rather_than_paraphrasing_it():
    found = precedents(profile_rows(CATEGORIES), question="top units")
    assert found.best is not None
    assert found.best.example, "the precedent names the pair it came from"
    assert found.best.why


def test_a_ranking_question_beats_a_composition_chart():
    """Matching on the domain noun ranked a donut above a bar for "which units
    are behind" — a ranking question. Weighting by how discriminative each word
    is across the corpus is what fixes it."""
    ranked = _types(CATEGORIES, "which units are behind on completions?")
    assert ranked.index("bar") < ranked.index("donut")


def test_with_no_question_the_shape_still_ranks():
    ranked = _types(CATEGORIES)
    assert ranked, "a chart should be suggested even with nothing asked"


def test_one_precedent_per_chart_type():
    """Ten near-identical bars would crowd out the one donut that might have
    been the better answer."""
    found = precedents(profile_rows(CATEGORIES), question="top units")
    types = [p.chart_type for p in found]
    assert len(types) == len(set(types))


# ---------------------------------------------------------------------------
# retrieval configuration
# ---------------------------------------------------------------------------

def test_lexical_is_the_default_and_needs_nothing(monkeypatch):
    for name in ("NEXCRAFTVIZ_RETRIEVAL", "RETRIEVAL_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    state = retrieval.describe()
    assert state["backend"] == "lexical"
    assert state["effective"] == "lexical"
    assert state["available"]


def test_genielms_own_switch_turns_it_on(monkeypatch):
    """One environment configures both stacks."""
    monkeypatch.delenv("NEXCRAFTVIZ_RETRIEVAL", raising=False)
    monkeypatch.setenv("RETRIEVAL_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.internal:6333")
    config = retrieval.load_config()
    assert config.wants_vectors and config.is_configured
    assert config.client_kwargs() == {"url": "http://qdrant.internal:6333"}


def test_an_unconfigured_vector_store_is_reported_not_hidden(monkeypatch):
    """A misconfigured store silently degrading to token matching would look
    like a working system giving worse answers for no visible reason."""
    monkeypatch.setenv("NEXCRAFTVIZ_RETRIEVAL", "qdrant")
    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.delenv("QDRANT_HOST", raising=False)
    state = retrieval.describe()
    assert not state["available"]
    assert any("QDRANT_URL" in r for r in state["reasons"])


def test_an_unknown_backend_falls_back_and_says_so(monkeypatch):
    monkeypatch.setenv("NEXCRAFTVIZ_RETRIEVAL", "pinecone")
    config = retrieval.load_config()
    assert config.backend == "lexical"
    assert any("unknown backend" in r for r in config.reasons)


# ---------------------------------------------------------------------------
# the vector backend
# ---------------------------------------------------------------------------

class FakeHit:
    def __init__(self, name: str, score: float) -> None:
        self.payload = {"name": name}
        self.score = score


class FakeQdrant:
    """Stands in for the store, so these run with no network and no key."""

    def __init__(self, hits: list[FakeHit]) -> None:
        self._hits = hits
        self.searched: list[dict[str, Any]] = []

    def search(self, **kwargs: Any) -> list[FakeHit]:
        self.searched.append(kwargs)
        return self._hits


@pytest.mark.asyncio
async def test_similarity_reranks_but_cannot_promote_an_impossible_chart(monkeypatch):
    """A vector store will happily return a trend chart for date-free data
    because the words matched. The shape gate is deterministic in both backends
    for exactly this reason."""
    trend_pairs = {
        p.name: 0.99
        for p in retrieval.corpus_pairs()
        if p.chart_type in ("line", "multi_line", "area")
    }
    found = precedents(
        profile_rows(CATEGORIES), question="how has this changed", similarity=trend_pairs
    )
    assert not ({"line", "multi_line", "area"} & {p.chart_type for p in found})


@pytest.mark.asyncio
async def test_similarity_changes_the_ranking_among_permitted_charts():
    donut_pairs = {
        p.name: 0.99 for p in retrieval.corpus_pairs() if p.chart_type == "donut"
    }
    found = precedents(
        profile_rows(CATEGORIES), question="top units", similarity=donut_pairs
    )
    assert found.best is not None and found.best.chart_type == "donut"
    assert found.best.matched_by == "meaning"
    assert found.backend == "qdrant"


@pytest.mark.asyncio
async def test_a_vector_store_that_is_down_falls_back_and_reports_why(monkeypatch):
    """A chart the user asked for should not be lost to a store being down."""
    monkeypatch.setenv("NEXCRAFTVIZ_RETRIEVAL", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://nowhere.invalid:6333")

    async def explode(*args: Any, **kwargs: Any):
        raise retrieval.RetrievalError("connection refused")

    monkeypatch.setattr(retrieval, "similar", explode)
    found = await precedents_async(profile_rows(CATEGORIES), question="top units")
    assert found.precedents, "still ranked"
    assert "connection refused" in found.fallback_reason
    assert found.backend == "lexical"


@pytest.mark.asyncio
async def test_similar_asks_the_store_for_the_configured_collection(monkeypatch):
    monkeypatch.setenv("NEXCRAFTVIZ_CORPUS_COLLECTION", "charts_v2")

    async def fake_embed(texts: list[str], *, model: str = ""):
        return [[0.1, 0.2, 0.3]]

    monkeypatch.setattr(retrieval, "embed", fake_embed)
    store = FakeQdrant([FakeHit("bar_top_courses_completions", 0.87)])
    scores = await retrieval.similar("top units", "number+string", qdrant=store)

    assert scores == {"bar_top_courses_completions": 0.87}
    assert store.searched[0]["collection_name"] == "charts_v2"


def test_the_document_describes_the_decision_not_the_data():
    """Embedding somebody else's numbers would match on their domain, not on
    when to use the chart."""
    pair = next(p for p in retrieval.corpus_pairs() if p.chart_type == "bar")
    document = retrieval.document_for(pair)
    assert pair.purpose in document
    assert pair.use_when[0] in document
    assert pair.vega_lite_spec not in document


# ---------------------------------------------------------------------------
# pipeline and progress charts
# ---------------------------------------------------------------------------

SPRINT = [{"day": f"2026-02-0{i}", "remaining": 90 - 9 * i, "ideal": 84 - 10 * i}
          for i in range(1, 8)]
SCHEDULE = [
    {"task": "Build", "start": "2026-01-19", "end": "2026-03-06", "status": "In progress"},
    {"task": "UAT", "start": "2026-03-02", "end": "2026-03-27", "status": "Not started"},
    {"task": "Rollout", "start": "2026-03-23", "end": "2026-04-17", "status": "Not started"},
]
MILESTONES = [
    {"milestone": "Joined", "date": "2026-01-12", "status": "Met"},
    {"milestone": "Probation", "date": "2026-04-12", "status": "Met"},
    {"milestone": "Review", "date": "2026-11-20", "status": "Not started"},
]
BOARD = [
    {"day": "2026-02-02", "status": "To do", "items": 24},
    {"day": "2026-02-02", "status": "Done", "items": 0},
    {"day": "2026-02-06", "status": "To do", "items": 15},
    {"day": "2026-02-06", "status": "Done", "items": 6},
    {"day": "2026-02-10", "status": "To do", "items": 6},
    {"day": "2026-02-10", "status": "Done", "items": 16},
]


@pytest.mark.parametrize(
    ("rows", "question", "expected"),
    [
        (SPRINT, "are we on track to finish the sprint?", "burndown"),
        (SPRINT, "what is our burn rate?", "burndown"),
        (SCHEDULE, "show the delivery timeline", "gantt"),
        (SCHEDULE, "which phases overlap?", "gantt"),
        (MILESTONES, "what key dates are coming up for this person?", "milestone_timeline"),
        (BOARD, "where is work getting stuck on the board?", "cumulative_flow"),
    ],
)
def test_progress_questions_find_their_chart(rows, question, expected):
    found = precedents(profile_rows(rows), question=question)
    assert found.best is not None, f"nothing suggested for {question!r}"
    assert found.best.chart_type == expected


def test_an_event_chart_accepts_a_date_that_is_not_an_axis():
    """A gantt bar spans one task's dates and a milestone marks one checkpoint.
    For these the per-row attribute date is the subject, not a trap — the gate
    that protects trend charts must not disqualify them."""
    profile = profile_rows(MILESTONES)
    assert profile.time_axis is None, "the fixture is the attribute-date case"
    assert "milestone_timeline" in {p.chart_type for p in precedents(profile)}


def test_a_series_chart_still_needs_a_real_axis():
    """The protection that event charts opt out of must still hold for the
    charts it was written for."""
    attribute_dates = [
        {"unit": "Clinical", "completed": 905, "next_audit": "2026-03-14"},
        {"unit": "Retail", "completed": 1201, "next_audit": "2026-04-21"},
        {"unit": "Logistics", "completed": 321, "next_audit": "2026-01-30"},
    ]
    ranked = _types(attribute_dates, "which units are behind?")
    assert not ({"line", "multi_line", "area"} & set(ranked)), ranked


def test_every_new_type_has_enough_examples_to_be_retrieved():
    """A chart type with one example cannot be retrieved in any useful way."""
    from nexcraftviz.corpus.loader import seed

    counts: dict[str, int] = {}
    for pair in seed().pairs:
        counts[pair.chart_type] = counts.get(pair.chart_type, 0) + 1
    for chart_type in ("gantt", "burndown", "milestone_timeline", "cumulative_flow"):
        assert counts.get(chart_type, 0) >= 2, chart_type
