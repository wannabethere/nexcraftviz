"""The chart surface: create one, then change it by instruction.

Two calls, built for a host that owns the data and the widget — the dashboard
tile, not the chat panel. Stateless by default: the caller already has a widget
id to key on, and making them mint a session first to draw one chart is
ceremony for nothing. A ``session_id`` is accepted for callers that want undo.

Everything here is a pure function over rows plus an optional model runner; the
HTTP layer in :mod:`nexcraftviz.app.api` only unwraps the request and returns
the envelope.

**The surface declares what it can render.** The consumer draws with
``react-vega``'s ``<VegaLite>``, which takes Vega-Lite and nothing else, so a
full-Vega spec is refused here rather than handed over to render as a blank
rectangle. :func:`capabilities` says so out loud so a caller never has to
hardcode the list.
"""
from __future__ import annotations

from typing import Any

from nexcraftviz.data.profile import profile_rows
from nexcraftviz.spec.model import Spec

#: What this surface can hand back. Vega-Lite only — the consumer's renderer
#: does not take full Vega, and a spec it cannot draw is worse than a refusal
#: because nothing reports it.
RENDERER = "vega-lite"
SUPPORTED_FAMILIES = ("vega-lite", "kpi", "table")


class ChartSurfaceError(ValueError):
    """The request cannot be served — as distinct from a chart that failed."""


# ---------------------------------------------------------------------------
# the envelope
# ---------------------------------------------------------------------------

def envelope(
    spec: Spec | None,
    *,
    rows: list[dict[str, Any]],
    reasoning: str = "",
    chart_type: str = "",
    plan: Any = None,
    verdict: Any = None,
    narration: dict[str, Any] | None = None,
    actions: list[str] | None = None,
    declined: list[str] | None = None,
    degraded: list[str] | None = None,
) -> dict[str, Any]:
    """One response shape for both calls.

    The first three keys are what a Vega-Lite consumer needs; the rest is what
    it can show if it wants to — the plan behind the chart, the verdict on it,
    and what was refused. A caller that only reads the first three keeps
    working when the others grow.
    """
    degraded = list(degraded or [])
    schema: dict[str, Any] = {}

    if spec is not None and spec:
        family = spec.family
        if family not in SUPPORTED_FAMILIES:
            raise ChartSurfaceError(
                f"this surface renders {RENDERER}; the chart came back as "
                f"{family!r}, which the consumer cannot draw"
            )
        schema = spec.raw
        chart_type = chart_type or chart_type_of(spec)

    return {
        "chart_type": chart_type,
        "chart_schema": schema,
        "reasoning": reasoning,
        "renderer": RENDERER,
        # The caller may be charting a sample rather than the whole result set.
        # Saying how many rows were charted lets it say so too.
        "row_count": len(rows),
        "plan": _dump(plan),
        "verdict": _dump(verdict),
        "narration": narration,
        "actions": list(actions or []),
        "declined": list(declined or []),
        "degraded": degraded,
    }


def chart_type_of(spec: Spec) -> str:
    """A name for what this chart is, in the vocabulary the caller speaks.

    Reads the spec rather than trusting a field alongside it: the spec is what
    gets rendered, so it is the only thing that cannot be out of date.
    """
    family = spec.family
    if family == "kpi":
        metadata = spec.raw.get("kpi_metadata")
        declared = metadata.get("chart_type") if isinstance(metadata, dict) else None
        return str(declared or "kpi")
    if family == "table":
        return "table_with_cells"
    return spec.mark_summary or "chart"


def capabilities() -> dict[str, Any]:
    """What this surface can produce, so nobody hardcodes the list.

    Derived from the corpus — the types this package has worked examples of —
    rather than a list written by hand, which goes stale the first time a type
    is added. There are twenty of them; the endpoint this replaces allowed
    seven and returned a 500 for the rest.
    """
    from nexcraftviz.corpus.loader import seed
    from nexcraftviz.theme.tokens import available_themes

    return {
        "renderer": RENDERER,
        "families": list(SUPPORTED_FAMILIES),
        "chart_types": sorted({p.chart_type for p in seed().pairs if p.chart_type}),
        "themes": sorted(available_themes()),
        "actions": list(_ACTIONS),
    }


def question_for(rows: list[dict[str, Any]], *, hint: str = "") -> str:
    """A question composed from the data, for a first chart nobody asked for.

    A convenience, never applied on its own: the create call requires a
    question, because a chart built from a question nobody can see is a chart
    nobody can check. A host that wants to compose its own should.
    """
    profile = profile_rows(rows)
    if not profile.column_names:
        return hint or "Summarise this result set"

    measure = next(iter(profile.measures), None)
    time_column = profile.time_axis
    dimension = next(iter(profile.dimensions), None)

    subject = hint.strip()
    if measure is None:
        base = f"How do the records break down by {dimension.name}?" if dimension \
            else "What does this result set contain?"
    elif time_column is not None:
        base = f"How has {measure.name} changed over {time_column.name}?"
    elif dimension is not None:
        base = f"How does {measure.name} compare across {dimension.name}?"
    else:
        base = f"What is the {measure.name}?"
    return f"{subject}: {base}" if subject else base


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

async def create_chart(
    *,
    question: str,
    rows: list[dict[str, Any]],
    theme: str = "",
    language: str = "English",
    llm: Any = None,
    registry: Any = None,
) -> dict[str, Any]:
    """Question + rows → a chart, through the full pipeline.

    The pipeline rather than a bare generate call, because a chart that appears
    without anyone asking for it is exactly the one that should be planned and
    gated: nobody typed the question, so nobody is watching for a wrong answer.
    """
    from nexcraftviz.pipeline import ChartRequest, run_pipeline

    if not (question or "").strip():
        raise ChartSurfaceError("a question is required — compose one from the data")
    if not rows:
        raise ChartSurfaceError("no rows to chart")

    run = await run_pipeline(
        ChartRequest(question=question, rows=rows, language=language, theme=theme),
        llm=llm, registry=registry,
    )
    if run.spec is None:
        raise ChartSurfaceError(run.reason or "no chart could be built from this data")

    spec = _themed(run.spec, theme)
    plan = run.stages.plan
    return envelope(
        spec,
        rows=rows,
        reasoning=(plan.rationale if plan else "") or run.trace[-1] if run.trace else "",
        chart_type=(run.stages.generate.chart_type if run.stages.generate else ""),
        plan=plan,
        verdict=run.stages.evaluate,
        actions=["create"],
    )


# ---------------------------------------------------------------------------
# annotate
# ---------------------------------------------------------------------------

_ACTIONS = ("edit", "theme", "narrate", "place", "recreate", "decline")


async def annotate_chart(
    *,
    instruction: str,
    chart_schema: dict[str, Any] | None,
    rows: list[dict[str, Any]],
    theme: str = "",
    language: str = "English",
    llm: Any = None,
    registry: Any = None,
    session: Any = None,
) -> tuple[dict[str, Any], Any]:
    """An instruction against an existing chart. Returns ``(envelope, session)``.

    The session comes back so a caller keeping one can hold onto it for undo;
    a stateless caller ignores it and it is discarded.
    """
    from nexcraftviz.manager import run_instruction
    from nexcraftviz.session import Session

    if not (instruction or "").strip():
        raise ChartSurfaceError("an instruction is required")

    if session is None:
        session = Session(
            rows=rows,
            document=Spec(dict(chart_schema)) if chart_schema else None,
            theme=theme or "nexcraftviz-light",
            language=language,
            registry=registry,
        )

    run = await run_instruction(instruction, session=session, llm=llm)

    if not run.decision.ok and not run.outcomes:
        raise ChartSurfaceError(
            run.decision.reason_if_not_ok or "the instruction could not be read"
        )

    spec = session.document if session.has_chart else None
    degraded = run.failures()
    return envelope(
        spec if isinstance(spec, Spec) else None,
        rows=rows,
        reasoning=run.summary(),
        narration=run.narration,
        actions=run.actions,
        declined=run.decision.declined,
        degraded=degraded,
        verdict=getattr(session.last_run, "stages", None)
        and session.last_run.stages.evaluate,
    ), session


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _themed(spec: Spec, theme: str) -> Spec:
    if not theme:
        return spec
    from nexcraftviz.theme import apply_theme, load

    try:
        result = apply_theme(spec, load(theme))
    except (KeyError, ValueError):
        # An unknown theme name should not cost the caller their chart.
        return spec
    return result.spec if isinstance(result.spec, Spec) else Spec(result.spec)


def _dump(value: Any) -> Any:
    if value is None:
        return None
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value
