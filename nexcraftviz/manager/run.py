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
    if step.action == "recreate":
        return await _recreate(step, session=session, llm=llm)

    skill = step.skill
    if not skill:
        return StepOutcome(step=step, ok=False, detail=f"no skill for {step.action!r}")

    try:
        turn = await session.turn(step.instruction, llm=llm, skill=skill)
    except Exception as exc:  # noqa: BLE001
        # Deliberately broad. A model runner is somebody else's code and can
        # raise anything — a timeout, a provider error, a bad response. Steps
        # are isolated on purpose, so one falling over must not take the
        # others with it. Nothing is swallowed: it lands in run.failures().
        return StepOutcome(step=step, ok=False, detail=f"{type(exc).__name__}: {exc}")

    if step.action == "narrate":
        # Narration is the one action that produces something to render rather
        # than something to redraw, so it is handed back separately.
        return StepOutcome(
            step=step, ok=turn.ok, detail=turn.reply,
            narration={"summary": turn.reply, "changes": turn.changes},
        )
    detail = turn.reply if turn.ok else "; ".join(r for _, r in turn.failed)
    return StepOutcome(step=step, ok=turn.ok, detail=detail)


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
