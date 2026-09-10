"""plan → generate → evaluate → deliver.

The sequence is *declared*, not written as control flow, so a caller can run a
prefix and stop for review (``stop_after="plan"``), skip a stage
(``evaluate="off"``), or replace one wholesale by overriding its role in the
registry. Each stage writes its slot on :class:`ChartStages` and the next reads
it; a missing slot raises rather than being quietly treated as empty.

The one piece of real control flow is the retry, and it is deliberately not a
loop: a failed evaluation buys **exactly one** regeneration, with the specific
complaint appended to the generator's input, after which the chart is delivered
with the verdict attached. Unbounded retries burn money and, in practice,
converge on the same answer with more steps.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from nexcraftviz.agents.artifacts import (
    ChartStages,
    DeliveryArtifact,
    EvaluationArtifact,
    GateResult,
)
from nexcraftviz.agents.registry import AgentRegistry, StageContext, StageError
from nexcraftviz.skills.base import SkillError

Stage = Literal["plan", "generate", "evaluate", "deliver"]


class ModelCallError(StageError):
    """The model runner raised — a provider outage, a timeout, a revoked key.

    A StageError, so the pipeline's existing boundary reports it as a failed run
    with a readable reason rather than a 500 with a stack trace. Only the runner
    is wrapped: an exception from nexcraftviz's own code is NOT converted, so a
    genuine bug here still fails loudly in tests instead of reading as a flaky
    provider.
    """


def _guarded(llm: Any) -> Any:
    """The host's runner, with its failures made legible."""
    if llm is None:
        return None

    async def guarded(system: str, user: str, schema: dict[str, Any]) -> Any:
        try:
            return await llm(system, user, schema)
        except Exception as exc:  # noqa: BLE001 — the runner is the host's code
            raise ModelCallError(
                f"the model call failed: {type(exc).__name__}: {exc}"
            ) from exc

    return guarded

STAGES: tuple[Stage, ...] = ("plan", "generate", "evaluate", "deliver")


@dataclass
class ChartRequest:
    """What the caller is asking for."""

    question: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    language: str = "English"
    theme: str = ""
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChartRun:
    """The result: every artifact, plus what happened along the way.

    ``stages`` is the data; ``trace`` is the story. Both are needed — the
    artifacts say what was produced and the trace says which stage was slow,
    which was skipped and why a retry was spent.
    """

    stages: ChartStages
    trace: list[str] = field(default_factory=list)
    status: str = "ok"
    reason: str = ""
    wall_ms: int = 0
    #: Regenerations spent, from one budget shared by every stage that can ask
    #: for one — so "exactly one retry" stays true however the first attempt
    #: went wrong.
    regenerations: int = 0

    @property
    def spec(self) -> Any:
        return self.stages.generate.spec if self.stages.generate else None

    @property
    def delivery(self) -> DeliveryArtifact | None:
        return self.stages.deliver

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "completed": self.stages.completed(),
            "trace": self.trace,
            "wall_ms": self.wall_ms,
            "regenerations": self.regenerations,
            "tokens": self.stages.total_tokens(),
            "telemetry": {
                name: t.model_dump() for name, t in self.stages.telemetry().items()
            },
        }


async def run_pipeline(
    request: ChartRequest,
    *,
    registry: AgentRegistry | None = None,
    llm: Any = None,
    stop_after: Stage | None = None,
    evaluate: Literal["gates", "full", "off"] = "gates",
    max_regenerations: int = 1,
) -> ChartRun:
    """Run the stages in order, stopping early on a stage that cannot proceed.

    ``evaluate`` picks how much judgement to buy: ``"gates"`` runs the free
    deterministic checks only (the default — most callers want the free half),
    ``"full"`` adds the LLM critic, ``"off"`` skips evaluation entirely.
    """
    registry = registry or AgentRegistry.default()
    stages = ChartStages()
    run = ChartRun(stages=stages)
    started = time.perf_counter()

    ctx = StageContext(
        question=request.question,
        rows=request.rows,
        language=request.language,
        theme=request.theme,
        stages=stages,
        llm=_guarded(llm),
        options=dict(request.options),
    )

    try:
        await _plan(ctx, run, registry)
        if run.status != "ok" or stop_after == "plan":
            return _finish(run, started)

        await _generate(ctx, run, registry)
        generated = run.stages.generate
        if (run.status != "ok" and generated is not None and generated.defect
                and run.regenerations < max_regenerations):
            # An attempt that produced something unusable — JSON the balancer
            # would not guess at, say — gets the one regeneration, told exactly
            # what broke. A deliberate "no chart suits this data" does not.
            complaint = (
                f"your previous answer could not be used: {run.reason}. Return "
                f"spec_json as ONE complete JSON document — every brace and "
                f"bracket closed, every string terminated."
            )
            run.trace.append(f"retry: {complaint}")
            run.regenerations += 1
            run.status, run.reason = "ok", ""
            await _generate(_with_complaint(ctx, complaint), run, registry)
        if run.status != "ok" or stop_after == "generate":
            return _finish(run, started)

        if evaluate != "off":
            await _evaluate(ctx, run, registry, full=evaluate == "full",
                            max_regenerations=max_regenerations - run.regenerations)
        if stop_after == "evaluate":
            return _finish(run, started)

        await _deliver(ctx, run, registry)
        plan = run.stages.plan
        if plan is not None and plan.status == "ambiguous" and run.status == "ok":
            # Drawn, and delivered with the caveat — the caller shows the chart
            # and the other reading, rather than nothing.
            run.status = "ambiguous"
            run.reason = (plan.reason_if_not_ok
                          or "the planner saw more than one reading of the question")
    except (StageError, SkillError) as exc:
        # A stage that could not run at all — wiring, or the model provider
        # failing — or a model answer that could not be read. Distinct from a
        # stage that ran and produced a non-ok artifact: this is not a data
        # problem. Either way the caller gets a failed run with the reason, not
        # a 500. A live viz.plan once returned a field placed where it does not
        # exist; before this, that took the whole request down.
        run.status = "failed"
        run.reason = str(exc)
        run.trace.append(f"aborted: {exc}")

    return _finish(run, started)


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------

async def _plan(ctx: StageContext, run: ChartRun, registry: AgentRegistry) -> None:
    plan = await registry.get("planner").run(ctx)
    run.stages.plan = plan
    run.trace.append(f"plan: {plan.summary()}")
    if not plan.drawable:
        # An unplannable question is a legitimate outcome, not an error: the
        # data genuinely may not answer it. Say which, and stop.
        run.status = plan.status
        run.reason = plan.reason_if_not_ok
    elif not plan.ok:
        run.trace.append("plan is ambiguous — drawing the conservative reading")


async def _generate(ctx: StageContext, run: ChartRun, registry: AgentRegistry) -> None:
    artifact = await registry.get("generator").run(ctx)
    run.stages.generate = artifact
    run.trace.append(
        f"generate: {artifact.chart_type or 'no chart'}"
        + (f" — {artifact.reason_if_not_ok}" if not artifact.ok else "")
    )
    if not artifact.ok:
        run.status = artifact.status
        run.reason = artifact.reason_if_not_ok or "the generator produced no spec"


async def _evaluate(
    ctx: StageContext,
    run: ChartRun,
    registry: AgentRegistry,
    *,
    full: bool,
    max_regenerations: int,
) -> None:
    evaluation = await _judge(ctx, registry, full=full)
    run.stages.evaluate = evaluation
    run.trace.append(_verdict_line(evaluation))

    if evaluation.passed or max_regenerations < 1:
        return

    complaint = evaluation.complaint()
    if not complaint:
        # A rejection with nothing actionable in it cannot drive a better
        # attempt, so spending the retry would be spending it blind.
        run.trace.append("retry skipped: the verdict named no specific fault")
        return

    run.trace.append(f"retry: {complaint}")
    retry_ctx = _with_complaint(ctx, complaint)
    retried = await registry.get("generator").run(retry_ctx)
    run.regenerations += 1

    if not retried.ok:
        # The first spec was flawed but real; the retry produced nothing. Keep
        # the flawed one and its verdict rather than delivering an empty run.
        run.trace.append(f"retry failed: {retried.reason_if_not_ok}")
        run.stages.evaluate.regenerated = True
        return

    run.stages.generate = retried
    second = await _judge(ctx, registry, full=full)
    second.regenerated = True
    run.stages.evaluate = second
    run.trace.append(f"re-{_verdict_line(second)}")

    if not second.passed:
        # Delivered anyway, with the verdict on the artifact — the caller gets
        # the chart and the reason to distrust it.
        run.status = "ambiguous"
        run.reason = second.complaint()


async def _judge(
    ctx: StageContext, registry: AgentRegistry, *, full: bool
) -> EvaluationArtifact:
    evaluation: EvaluationArtifact = await registry.get("evaluator").run(ctx)
    # The critic is asked only when the free checks are already clean. Paying a
    # model to opine on a chart that does not render is waste.
    if full and evaluation.passed and registry.has("critic"):
        try:
            evaluation.critique = await registry.get("critic").run(ctx)
        except (StageError, SkillError) as exc:
            # The chart has already passed every deterministic gate. A critic
            # that cannot answer is a missing opinion, not a failed chart — so
            # it is recorded the way a broken gate is: passing, with the reason.
            evaluation.gates.append(GateResult(
                gate="critic",
                passed=True,
                detail=f"critic unavailable ({type(exc).__name__}: {exc}) — "
                       f"not counted against the chart",
            ))
    return evaluation


async def _deliver(ctx: StageContext, run: ChartRun, registry: AgentRegistry) -> None:
    delivery = await registry.get("deliverer").run(ctx)
    run.stages.deliver = delivery
    run.trace.append(
        f"deliver: {len(delivery.files)} file(s), {len(delivery.exports)} export(s)"
    )
    if delivery.status != "ok" and run.status == "ok":
        run.status = delivery.status


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _with_complaint(ctx: StageContext, complaint: str) -> StageContext:
    """A copy carrying the complaint, so the original context stays clean."""
    return StageContext(
        question=ctx.question,
        rows=ctx.rows,
        language=ctx.language,
        theme=ctx.theme,
        stages=ctx.stages,
        llm=ctx.llm,
        options=ctx.options,
        complaint=complaint,
    )


def _verdict_line(evaluation: EvaluationArtifact) -> str:
    failures = evaluation.failures
    if failures:
        return f"evaluate: {len(failures)} gate(s) failed — {failures[0]}"
    if evaluation.critique and not evaluation.critique.answers_question:
        return f"evaluate: critic rejected — {evaluation.critique.complaint}"
    return f"evaluate: {len(evaluation.gates)} gate(s) passed"


def _finish(run: ChartRun, started: float) -> ChartRun:
    run.wall_ms = int((time.perf_counter() - started) * 1000)
    return run
