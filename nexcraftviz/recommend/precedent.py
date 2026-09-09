"""What was drawn last time for data shaped like this?

:mod:`nexcraftviz.recommend.rules` reasons from the shape alone, and the shape
alone is not enough: one dimension and one measure is a bar, a donut, a funnel,
a waterfall and a treemap, and nothing about the columns says which. The rules
answer "what *can* be drawn"; they cannot answer "what is worth drawing".

The corpus can. Every one of its 200 pairs carries the routing metadata nothing
was reading — the abstract ``data_shape`` it suits, the ``use_when`` conditions
that select it, the ``do_not_use_when`` conditions that rule it out (each naming
the chart to use instead), and real ``example_questions``. That is a body of
worked decisions, and matching against it is retrieval rather than a second
rules engine: adding a pair to the corpus improves selection with no code
change, which is the opposite of how a hand-written table ages.

The output is evidence, not a verdict. It goes to the planner, which decides —
charts come from agents. What this adds is a precedent to decide *from*, quoted
so the reasoning can be checked: "for one dimension and one measure, asked with
'top', the corpus draws a bar — see bar_top_courses_completions."
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from nexcraftviz.data.profile import ColumnProfile, DataProfile

#: The corpus describes columns abstractly. These are the only four kinds it
#: uses, and every profile role maps onto one of them.
Kind = str

#: Words that carry no routing signal. Deliberately short — an aggressive stop
#: list throws away the domain words that make a question distinctive.
_STOPWORDS = frozenset({
    "a", "an", "the", "of", "by", "for", "in", "on", "at", "to", "is", "are",
    "was", "were", "and", "or", "with", "show", "me", "what", "which", "how",
    "our", "we", "us", "this", "that", "these", "those", "it", "its", "per",
    "each", "all", "any", "do", "does", "did", "can", "you", "please",
})

_WORD = re.compile(r"[a-z][a-z0-9_]+")

#: A `do_not_use_when` clause usually names the chart to use instead:
#: "Data has natural order → line". The redirect is worth more than the
#: exclusion, because it says where to go rather than only where not to.
_REDIRECT = re.compile(r"(?:→|->)\s*([a-z_]+(?:\s*/\s*[a-z_]+)*)\s*$", re.IGNORECASE)

#: Keywords quoted inside a `use_when` clause — "Question uses 'top', 'most'".
#: These are the highest-signal thing in the corpus: an explicit statement that
#: a word in the question selects this chart.
_QUOTED = re.compile(r"'([^']{2,30})'")

#: How much each signal is worth. Shape gates; the question decides between the
#: charts a shape allows, so it carries the most weight.
_W_SHAPE = 0.35
_W_QUESTION = 0.45
_W_KEYWORD = 0.20
#: Subtracted when a `do_not_use_when` clause matches.
_CAUTION_PENALTY = 0.45


@dataclass
class Precedent:
    """One chart type, and the worked example that argues for it."""

    chart_type: str
    score: float
    example: str = ""
    why: str = ""
    shape: str = ""
    caution: str = ""
    redirects_to: list[str] = field(default_factory=list)
    #: "words" or "meaning" — which backend read the question. Reported because
    #: the two disagree sometimes, and knowing which one spoke is the first
    #: thing anyone asks when a ranking looks wrong.
    matched_by: str = "words"

    def __str__(self) -> str:
        text = f"{self.chart_type} ({self.score:.2f}) — {self.why}"
        if self.caution:
            text += f"  [caution: {self.caution}]"
        return text

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "chart_type": self.chart_type,
            "score": round(self.score, 2),
            "why": self.why,
            "example": self.example,
        }
        if self.caution:
            out["caution"] = self.caution
        return out


@dataclass
class PrecedentSet:
    """Ranked precedents for one profile and question."""

    signature: str
    precedents: list[Precedent] = field(default_factory=list)
    considered: int = 0
    backend: str = "lexical"
    #: Set when the vector backend was asked for and could not answer.
    fallback_reason: str = ""

    def __iter__(self):
        return iter(self.precedents)

    def __len__(self) -> int:
        return len(self.precedents)

    @property
    def best(self) -> Precedent | None:
        return self.precedents[0] if self.precedents else None

    def to_prompt_list(self, limit: int = 5) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.precedents[:limit]]


def precedents(
    profile: DataProfile,
    *,
    question: str = "",
    limit: int = 8,
    similarity: dict[str, float] | None = None,
) -> PrecedentSet:
    """Rank chart types by what the corpus did for data like this.

    One precedent per chart type — the best-arguing example — because the
    caller is choosing a *type*, and ten near-identical bars crowd out the one
    donut that might have been the better answer.

    ``similarity`` is an optional ``{pair name: score}`` from the vector
    backend. It **re-ranks candidates the shape already permits**; it cannot
    introduce one the data rules out. Semantic similarity has no way to know
    that a trend chart needs a date column, so letting it override the shape
    gate would trade a wrong-but-explicable answer for a wrong one that reads
    as confident.
    """
    signature = shape_signature(profile)
    asked = _tokens(question)
    corpus = _corpus()
    similarity = similarity or {}

    best: dict[str, Precedent] = {}
    considered = 0

    for pair in corpus:
        required = _required_kinds(pair.data_shape)
        shape = _shape_score(required, profile)
        if shape <= 0.0:
            # The data cannot support this chart at all. Not a low score — a
            # disqualification, or a two-column result would rank every
            # four-column chart in the corpus.
            continue
        considered += 1

        keyword_score, keyword = _keyword_score(pair, asked)
        caution, redirects = _caution(pair, asked)

        vector = similarity.get(pair.name)
        if vector is None:
            question_score, matched_clause = _question_score(pair, asked)
            matched_by = "words"
        else:
            # The vector backend read the question; the lexical clause match is
            # still computed so the reason can quote the corpus rather than
            # report a bare number nobody can check.
            _, matched_clause = _question_score(pair, asked)
            question_score, matched_by = vector, "meaning"

        score = (
            _W_SHAPE * shape
            + _W_QUESTION * question_score
            + _W_KEYWORD * keyword_score
            - (_CAUTION_PENALTY if caution else 0.0)
        )
        if score <= 0.0:
            continue

        candidate = Precedent(
            chart_type=pair.chart_type,
            score=score,
            example=pair.name,
            why=_explain(pair, shape, matched_clause, keyword, profile),
            shape="+".join(sorted(required)),
            caution=caution,
            redirects_to=redirects,
            matched_by=matched_by,
        )
        current = best.get(pair.chart_type)
        if current is None or candidate.score > current.score:
            best[pair.chart_type] = candidate

    ranked = sorted(best.values(), key=lambda p: (-p.score, p.chart_type))
    return PrecedentSet(
        signature=signature,
        precedents=ranked[:limit],
        considered=considered,
        backend="qdrant" if similarity else "lexical",
    )


async def precedents_async(
    profile: DataProfile, *, question: str = "", limit: int = 8
) -> PrecedentSet:
    """:func:`precedents`, using the vector backend when one is configured.

    Falls back to lexical matching and says so in the result, rather than
    failing: a chart the user asked for should not be lost to a vector store
    being down, and a silent downgrade is worse than a reported one.
    """
    from nexcraftviz.recommend import retrieval

    config = retrieval.load_config()
    if not config.wants_vectors:
        return precedents(profile, question=question, limit=limit)

    try:
        scores = await retrieval.similar(question, shape_signature(profile))
    except Exception as exc:  # noqa: BLE001 — retrieval is an optimisation
        result = precedents(profile, question=question, limit=limit)
        result.fallback_reason = f"{type(exc).__name__}: {exc}"
        return result
    return precedents(profile, question=question, limit=limit, similarity=scores)


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------

def shape_signature(profile: DataProfile) -> str:
    """The incoming data in the corpus's own vocabulary."""
    return "+".join(sorted(kind_of(column) for column in profile.columns))


def kind_of(column: ColumnProfile) -> Kind:
    """A profile column in the corpus's four-kind vocabulary."""
    if column.role == "time" or column.vega_type == "temporal":
        return "date"
    if column.python_type == "bool":
        return "boolean"
    if column.role == "measure" or column.vega_type == "quantitative":
        return "number"
    return "string"


def _required_kinds(data_shape: dict[str, Any] | None) -> list[Kind]:
    """The column kinds a chart needs.

    Columns marked ``required: false`` are skipped — a gauge's target band is
    optional, and counting it would disqualify every result set that has no
    target column, which is most of them.
    """
    columns = (data_shape or {}).get("columns") or []
    return [
        str(c.get("type") or "string").lower()
        for c in columns
        if isinstance(c, dict) and c.get("required", True) is not False
    ]


def _shape_score(required: list[Kind], profile: DataProfile) -> float:
    """Can this data support that chart, and how snugly?

    Zero means it cannot, and disqualification matters more than ranking here:
    a chart needing a date has nothing to plot against when the result set has
    none, and no amount of question-matching should rescue it.
    """
    if not required:
        return 0.0

    available: dict[Kind, int] = {}
    for column in profile.columns:
        kind = kind_of(column)
        available[kind] = available.get(kind, 0) + 1

    # Not every date is an axis. `next_audit` on a per-business-unit result is
    # an attribute describing each row, and a chart that plots against it draws
    # a line through unrelated points. `DataProfile.time_axis` already makes
    # that distinction, so a chart requiring a date is only satisfied when the
    # rows are actually observations over one.
    if profile.time_axis is None:
        available.pop("date", None)

    needed: dict[Kind, int] = {}
    for kind in required:
        needed[kind] = needed.get(kind, 0) + 1

    for kind, count in needed.items():
        if available.get(kind, 0) < count:
            # A boolean column is a two-valued dimension, so a chart asking for
            # a string is satisfied by one. The reverse is not true.
            if kind == "string" and available.get("boolean", 0) >= count:
                continue
            return 0.0

    # A chart with nothing to vary over shows ONE value. The corpus says so
    # itself — the KPI pairs' first `use_when` is "Answer is a single scalar" —
    # so a result set with many rows cannot be one of these, however well the
    # words match. Without this, every grouped result ranks `kpi` and `gauge`
    # near the top, because a number is a number.
    varies_over = {"string", "date", "boolean"} & set(needed)
    if not varies_over and profile.row_count > 1:
        return 0.0

    extra = len(profile.columns) - len(required)
    if extra <= 0:
        return 1.0
    # Decay steeply. A chart designed for two columns applied to six is a chart
    # quietly ignoring four of them, and should lose to one that uses them.
    return max(0.2, 1.0 - 0.25 * extra)


# ---------------------------------------------------------------------------
# the question
# ---------------------------------------------------------------------------

def _question_score(pair: Any, asked: set[str]) -> tuple[float, str]:
    """Overlap between the question and what this pair says it answers.

    Weighted by how discriminative each word is across the whole corpus, not by
    raw overlap. Every pair in a compliance corpus says "training" and
    "completion", so an unweighted match rewards the domain noun that every
    chart shares and ignores "share", "top" and "over time" — the words that
    actually choose between charts. Matching on the noun ranked a donut above a
    bar for "which units are behind", which is the wrong chart for a ranking
    question.

    Keeps the best-matching clause so the reason can quote the corpus rather
    than paraphrase it.
    """
    if not asked:
        return 0.0, ""

    weights = _idf()
    best = 0.0
    clause = ""
    for candidate in list(pair.example_questions) + list(pair.use_when):
        tokens = _tokens(candidate)
        if not tokens:
            continue
        total = sum(weights.get(w, _DEFAULT_IDF) for w in tokens)
        if total <= 0:
            continue
        shared = sum(weights.get(w, _DEFAULT_IDF) for w in asked & tokens)
        score = shared / total
        if score > best:
            best, clause = score, candidate
    return min(best, 1.0), clause


#: For a word the corpus has never seen — a domain term from the user's own
#: data. Treated as fairly discriminative, since it is rare by definition.
_DEFAULT_IDF = 2.0


@lru_cache(maxsize=1)
def _idf() -> dict[str, float]:
    """How discriminative each word is, measured over the corpus itself.

    A word in every pair says nothing about which chart to draw; a word in two
    says a great deal. Derived rather than hand-listed, so a corpus for a
    different domain re-weights itself with no code change — which is the whole
    reason selection lives in the corpus and not in a table here.
    """
    import math

    corpus = _corpus()
    seen: dict[str, int] = {}
    for pair in corpus:
        words: set[str] = set()
        for clause in list(pair.example_questions) + list(pair.use_when):
            words |= _tokens(clause)
        for word in words:
            seen[word] = seen.get(word, 0) + 1

    total = max(len(corpus), 1)
    return {word: math.log(total / count) for word, count in seen.items()}


def _keyword_score(pair: Any, asked: set[str]) -> tuple[float, str]:
    """Explicitly quoted routing words — the strongest signal in the corpus."""
    for clause in pair.use_when:
        for keyword in _QUOTED.findall(clause):
            if _tokens(keyword) & asked:
                return 1.0, keyword
    return 0.0, ""


def _caution(pair: Any, asked: set[str]) -> tuple[str, list[str]]:
    """A `do_not_use_when` clause the question trips, and where it points."""
    for clause in pair.do_not_use_when:
        body = _REDIRECT.sub("", clause).strip()
        tokens = _tokens(body)
        if not tokens or not asked:
            continue
        # Two shared words, not one: "data" alone should not veto a chart.
        if len(asked & tokens) >= 2:
            match = _REDIRECT.search(clause)
            targets = (
                [t.strip() for t in match.group(1).split("/")] if match else []
            )
            return clause, targets
    return "", []


def _explain(
    pair: Any, shape: float, clause: str, keyword: str, profile: DataProfile
) -> str:
    """Why this precedent, in the corpus's own words rather than paraphrased."""
    if keyword:
        return f"the question says {keyword!r}, which selects this — {pair.purpose or pair.goal}"
    if clause:
        return f"{clause.rstrip('.')} — {pair.goal or pair.purpose}"
    if shape >= 1.0:
        return f"exactly this shape — {pair.goal or pair.purpose}"
    return pair.goal or pair.purpose or "the shape supports it"


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOPWORDS}


@lru_cache(maxsize=1)
def _corpus() -> tuple[Any, ...]:
    """The corpus, loaded once. It is a shipped data file, not state."""
    from nexcraftviz.corpus.loader import seed

    return tuple(seed().pairs)
