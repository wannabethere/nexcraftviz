"""Decide, then carry it out — one instruction, several steps, one document.

The steps run in order against a single :class:`~nexcraftviz.session.Session`,
which is what makes a compound instruction behave like a compound instruction:
"make it dark and sort descending" leaves a dark, sorted chart, and undo steps
back through it one change at a time rather than one message at a time.

A step that fails does not stop the ones after it. Half of an instruction
carried out, clearly reported, is more useful than none of it — and the
alternative silently discards work the user can see was possible.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nexcraftviz.agents.artifacts import Telemetry
from nexcraftviz.manager.decision import ManagerDecision, ManagerStep

if TYPE_CHECKING:  # pragma: no cover
    # `skills` imports the manager and `session` imports `skills`, so importing
    # Session for real here would close the loop.
    from nexcraftviz.session import Session


@dataclass
class StepOutcome:
    step: ManagerStep
    ok: bool
    detail: str = ""
    #: The narration text, when the step was a narrate. Nothing else produces
    #: output the caller has to render separately.
    narration: dict[str, Any] | None = None


@dataclass
class ManagerRun:
    """What one instruction did."""

    decision: ManagerDecision
    outcomes: list[StepOutcome] = field(default_factory=list)
    wall_ms: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.outcomes) and all(o.ok for o in self.outcomes)

    @property
    def actions(self) -> list[str]:
        return [o.step.action for o in self.outcomes]

    @property
    def narration(self) -> dict[str, Any] | None:
        return next((o.narration for o in self.outcomes if o.narration), None)

    def failures(self) -> list[str]:
        return [f"{o.step.action}: {o.detail}" for o in self.outcomes if not o.ok]

    def summary(self) -> str:
        if not self.decision.steps:
            return self.decision.reason_if_not_ok or "nothing to do"
        done = ", ".join(o.step.action for o in self.outcomes if o.ok)
        text = f"did: {done}" if done else "nothing succeeded"
        if self.decision.declined:
            text += f"; declined: {'; '.join(self.decision.declined)}"
        return text


async def decide(
    instruction: str,
    *,
    session: Session,
    llm: Any = None,
) -> ManagerDecision:
    """What should this instruction do?

    Skips the model entirely when the rules read every clause, which is most
    annotate messages. That is not a fallback — it is the cheap path being taken
    when it is genuinely sufficient.
    """
    from nexcraftviz.skills.manage import ManageIn, ManageSkill

    skill = ManageSkill()
    inputs = ManageIn(
        instruction=instruction,
        spec=session.document if session.has_chart else None,
        widget=session.document if session.has_widget else None,
        rows=session.rows,
        language=session.language,
    )

    started = time.perf_counter()
    if not inputs.needs_model() or llm is None:
        decision = skill.decide_offline(inputs)
        if llm is None and inputs.needs_model():
            # Honest about which of the two reasons this was: the rules were not
            # sure and there was no model to ask.
            decision.status = "ambiguous"
            decision.reason_if_not_ok = (
                "no model available, and the rules could not read every clause"
            )
        decision.telemetry = Telemetry(
            wall_ms=int((time.perf_counter() - started) * 1000), agent="rules",
        )
        return decision

    result = await skill.run(inputs, llm)
    decision = result.value if isinstance(result.value, ManagerDecision) else ManagerDecision()
    meta = result.meta or {}
    decision.telemetry = Telemetry(
        wall_ms=int((time.perf_counter() - started) * 1000),
        tokens_in=int(meta.get("tokens_in") or 0),
        tokens_out=int(meta.get("tokens_out") or 0),
        model=str(meta.get("model") or ""),
        prompt_version=str(meta.get("prompt_version") or ""),
        agent="viz.manage",
    )
    return decision


async def run_instruction(
    instruction: str,
    *,
    session: Session,
    llm: Any = None,
    decision: ManagerDecision | None = None,
) -> ManagerRun:
    """Decide, then run each step against the session in order."""
    started = time.perf_counter()
    decision = decision or await decide(instruction, session=session, llm=llm)
    run = ManagerRun(decision=decision)

    for step in decision.steps:
        if step.action == "decline":
            run.outcomes.append(StepOutcome(step=step, ok=True, detail=step.why))
            continue
        run.outcomes.append(await _run_step(step, session=session, llm=llm))

    run.wall_ms = int((time.perf_counter() - started) * 1000)
    return run


async def _run_step(step: ManagerStep, *, session: Session, llm: Any) -> StepOutcome:
    """Dispatch one step to the agent registered for its role.

    Through the **registry**, not straight to a skill. A host that overrides the
    `editor` role has to see that override in the instruction box as well as in
    the conversation; going direct would mean the two disagree, and nobody finds
    that out until it has already confused someone.
    """
    if step.action == "recreate":
        return await _recreate(step, session=session, llm=llm)
    if step.action == "widget":
        return await _build_widget(step, session=session, llm=llm)

    if not step.role:
        return StepOutcome(step=step, ok=False, detail=f"no role for {step.action!r}")

    try:
        registry = _registry(session)
        agent = registry.get(step.role)
        result = await agent.run(_context(step, session=session, llm=llm))
    except Exception as exc:  # noqa: BLE001
        # Deliberately broad. A model runner is somebody else's code and can
        # raise anything — a timeout, a provider error, a bad response. Steps
        # are isolated on purpose, so one falling over must not take the
        # others with it. Nothing is swallowed: it lands in run.failures().
        return StepOutcome(step=step, ok=False, detail=f"{type(exc).__name__}: {exc}")

    # The session keys undo and history on the skill name, so the turn is
    # recorded against the skill the role stands for — an overridden role lands
    # in the same history a built-in one would.
    turn = session.adopt_result(
        agent.spec.skill or step.skill,
        result,
        message=step.instruction,
        theme=_theme_for(step, session),
    )

    if step.action == "narrate":
        # Narration is the one action that produces something to render rather
        # than something to redraw, so it is handed back separately.
        return StepOutcome(
            step=step, ok=turn.ok, detail=turn.reply,
            narration={"summary": turn.reply, "changes": turn.changes},
        )
    detail = turn.reply if turn.ok else "; ".join(r for _, r in turn.failed)
    return StepOutcome(step=step, ok=turn.ok, detail=detail)


def _registry(session: Session) -> Any:
    from nexcraftviz.agents import AgentRegistry

    if session.registry is None:
        session.registry = AgentRegistry.default()
    return session.registry


def _context(step: ManagerStep, *, session: Session, llm: Any) -> Any:
    """What a role needs to do its job, whichever role it is."""
    from nexcraftviz.agents.registry import StageContext

    return StageContext(
        question=step.instruction,
        rows=session.rows,
        language=session.language,
        theme=session.theme,
        llm=llm,
        options={
            "spec": session.document if session.has_chart else None,
            "widget": session.document if session.has_widget else None,
            "theme": _theme_for(step, session),
        },
    )


def _theme_for(step: ManagerStep, session: Session) -> str:
    """The theme a `theme` step is asking for.

    Reuses the session's own reader, so "make it dark" means the same thing here
    as it does in a conversation.
    """
    if step.action != "theme":
        return ""
    from nexcraftviz.session.session import _theme_from

    return _theme_from(step.instruction, session.theme)


async def _build_widget(step: ManagerStep, *, session: Session, llm: Any) -> StepOutcome:
    """Several charts, then an arrangement.

    The order is the point. Each chart is planned, generated and gated on its
    own; only then is the composer asked where things go, and it is shown the
    **visualisations** rather than the rows. Composing from data instead would
    mean choosing a layout for charts that do not exist yet, and then either
    re-deriving them or hoping they come out as imagined.
    """
    from nexcraftviz.pipeline import ChartRequest, run_pipeline
    from nexcraftviz.skills.compose import Visualization, build_widget

    asks = step.parts or [step.instruction]
    registry = _registry(session)

    visualizations: list[Visualization] = []
    problems: list[str] = []
    for index, ask in enumerate(asks):
        try:
            run = await run_pipeline(
                ChartRequest(question=ask, rows=session.rows,
                             language=session.language, theme=session.theme),
                llm=llm, registry=registry,
            )
        except Exception as exc:  # noqa: BLE001 — one chart failing is not the widget failing
            problems.append(f"{ask}: {type(exc).__name__}: {exc}")
            continue
        if run.spec is None:
            problems.append(f"{ask}: {run.reason or 'no chart'}")
            continue
        visualizations.append(Visualization(
            id=f"tile_{index + 1}",
            spec=run.spec,
            chart_type=run.stages.generate.chart_type if run.stages.generate else "",
            question=ask,
        ))

    if not visualizations:
        return StepOutcome(step=step, ok=False,
                           detail="; ".join(problems) or "no charts could be built")

    context = _context(step, session=session, llm=llm)
    context.options["visualizations"] = visualizations
    from nexcraftviz.compose.widget import Widget

    existing = session.document if isinstance(session.document, Widget) else None
    context.options["existing"] = (
        [{"id": t.id, "title": t.title, "family": t.family} for t in existing.tiles]
        if existing is not None else []
    )

    try:
        result = await registry.get("composer").run(context)
    except Exception as exc:  # noqa: BLE001
        return StepOutcome(step=step, ok=False, detail=f"{type(exc).__name__}: {exc}")

    design = result.value
    if design is None or not design.ok:
        return StepOutcome(
            step=step, ok=False,
            detail=(design.reason_if_not_ok if design else "") or "no widget design",
        )

    session.document = build_widget(design, visualizations)
    # A widget replaces the document outright rather than editing it, so there
    # is no inverse patch that could reconstruct what was there before.
    session.clear_history()

    detail = f"{design.layout} of {len(design.tiles)} chart(s)"
    if problems:
        detail += f"; could not build: {'; '.join(problems)}"
    return StepOutcome(step=step, ok=True, detail=detail)


async def _recreate(step: ManagerStep, *, session: Session, llm: Any) -> StepOutcome:
    """Build a new chart through the full pipeline.

    Goes through the pipeline rather than straight to `viz.generate` because a
    chart the user is replacing their current one with is exactly the case that
    deserves a plan and the gates.
    """
    from nexcraftviz.pipeline import ChartRequest, run_pipeline

    run = await run_pipeline(
        ChartRequest(
            question=step.instruction,
            rows=session.rows,
            language=session.language,
            theme=session.theme,
        ),
        llm=llm,
        registry=session.registry,
    )
    session.adopt_run(run, message=step.instruction)
    if run.spec is None:
        return StepOutcome(step=step, ok=False, detail=run.reason or "no chart was produced")
    return StepOutcome(step=step, ok=True, detail=run.trace[-1] if run.trace else "")
