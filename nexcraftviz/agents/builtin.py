"""The built-in agents — thin wrappers over the skills.

Every agent here delegates to a skill in :mod:`nexcraftviz.skills`. That is
deliberate and worth stating plainly: there is one implementation of each
behaviour, and the agent layer adds only what a *pipeline* needs and a skill
call does not — a role, a model tier, an artifact with a status on it, and a
telemetry record.

If an agent re-implemented its skill, the MCP tool and the pipeline would drift
apart, and the version a user hit would depend on which door they came through.

Roles the chart pipeline uses: ``planner``, ``generator``, ``evaluator``,
``critic``, ``deliverer``. The registry also fills ``editor``, ``placer`` and
``narrator``, which belong to the conversational flow rather than the pipeline;
they are here so a host can override them the same way, and so
``harness check`` reports on every role rather than five of eight.
"""
from __future__ import annotations

import time
from typing import Any

from nexcraftviz.agents.artifacts import (
    ChartPlan,
    Critique,
    DeliveryArtifact,
    EvaluationArtifact,
    GenerateArtifact,
    Telemetry,
)
from nexcraftviz.agents.registry import Agent, AgentSpec, StageContext, StageError
from nexcraftviz.skills import REGISTRY
from nexcraftviz.skills.base import Skill, SkillError, SkillResult


class SkillAgent:
    """Shared machinery: call a skill, time it, record what it cost.

    Not a base class with abstract methods — subclasses differ only in how they
    build the skill's input and how they turn a :class:`SkillResult` into an
    artifact, so those are the only two things they override.
    """

    spec: AgentSpec

    @property
    def skill(self) -> Skill:
        skill = REGISTRY.get(self.spec.skill)
        if skill is None:
            raise StageError(
                f"agent {self.spec.name!r} delegates to skill {self.spec.skill!r}, "
                f"which is not registered"
            )
        return skill

    def build_input(self, ctx: StageContext) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    async def call(self, ctx: StageContext) -> tuple[SkillResult, Telemetry]:
        if ctx.llm is None and self.spec.uses_llm:
            raise StageError(
                f"{self.spec.role} needs a model runner; none was supplied. "
                f"Pass llm=..., or override the {self.spec.role!r} role with an "
                f"implementation that does not need one."
            )
        started = time.perf_counter()
        result = await self.skill.run(self.build_input(ctx), ctx.llm)
        return result, self._telemetry(result, started)

    def _telemetry(self, result: SkillResult, started: float) -> Telemetry:
        meta = result.meta or {}
        return Telemetry(
            wall_ms=int((time.perf_counter() - started) * 1000),
            tokens_in=int(meta.get("tokens_in") or 0),
            tokens_out=int(meta.get("tokens_out") or 0),
            model=str(meta.get("model") or (self.spec.model if self.spec.uses_llm else "")),
            prompt_version=str(meta.get("prompt_version") or ""),
            agent=self.spec.name,
        )


# ---------------------------------------------------------------------------
# planner
# ---------------------------------------------------------------------------

class PlannerAgent(SkillAgent):
    """Question + data → a plan, before anything is drawn."""

    spec = AgentSpec(
        role="planner",
        name="builtin.planner",
        model_tier="smart",
        skill="viz.plan",
        summary="Choose the chart type, encodings, transforms, styling and copy.",
    )

    def build_input(self, ctx: StageContext) -> dict[str, Any]:
        return {
            "question": ctx.question,
            "rows": ctx.rows,
            "language": ctx.language,
            "theme": ctx.theme,
        }

    async def run(self, ctx: StageContext) -> ChartPlan:
        result, telemetry = await self.call(ctx)
        plan: ChartPlan = result.value if isinstance(result.value, ChartPlan) else ChartPlan()

        # A skill-level failure — a plan naming columns the data lacks — is a
        # plan that cannot be built from, so it is a status rather than a
        # warning. The pipeline stops on it instead of generating a chart that
        # is guaranteed to render empty.
        if result.failed:
            plan.status = "failed"
            plan.reason_if_not_ok = "; ".join(detail for _, detail in result.failed)

        plan.telemetry = telemetry
        return plan


# ---------------------------------------------------------------------------
# generator
# ---------------------------------------------------------------------------

class GeneratorAgent(SkillAgent):
    """Plan → spec. Follows the plan; does not re-decide it."""

    spec = AgentSpec(
        role="generator",
        name="builtin.generator",
        model_tier="fast",
        skill="viz.generate",
        summary="Build a Vega-Lite spec that carries out the plan.",
    )

    def build_input(self, ctx: StageContext) -> dict[str, Any]:
        plan = getattr(ctx.stages, "plan", None) if ctx.stages else None
        payload: dict[str, Any] = {
            "question": ctx.question,
            "rows": ctx.rows,
            "language": ctx.language,
            "complaint": ctx.complaint,
        }
        if plan is not None and plan.drawable:
            payload["plan"] = plan
            # The plan already settled the type; passing it again as
            # `chart_type` would let a disagreement between the two fields
            # silently pick a winner.
            payload["chart_type"] = ""
        return payload

    async def run(self, ctx: StageContext) -> GenerateArtifact:
        started = time.perf_counter()
        try:
            result, telemetry = await self.call(ctx)
        except SkillError as exc:
            return GenerateArtifact(
                status="failed",
                reason_if_not_ok=str(exc),
                defect=True,
                telemetry=Telemetry(
                    wall_ms=int((time.perf_counter() - started) * 1000),
                    agent=self.spec.name,
                    model=self.spec.model,
                ),
            )

        output = result.output
        artifact = GenerateArtifact(
            spec=result.value,
            chart_type=getattr(output, "chart_type", "") or "",
            reasoning=getattr(output, "reasoning", "") or "",
            repaired=list(result.meta.get("repaired") or []),
            telemetry=telemetry,
        )
        if result.value is None:
            artifact.status = "failed"
            # Failed steps mean the attempt was broken; none means the model
            # decided no chart suits the data, which a retry would only repeat.
            artifact.defect = bool(result.failed)
            artifact.reason_if_not_ok = (
                "; ".join(detail for _, detail in result.failed)
                or "the generator produced no spec"
            )
        artifact.telemetry.retry_count = 1 if ctx.complaint else 0
        return artifact


# ---------------------------------------------------------------------------
# evaluator and critic
# ---------------------------------------------------------------------------

class EvaluatorAgent:
    """The deterministic gates. No model, no cost, runs first.

    Kept separate from the critic so a host can keep the free checks and drop
    the paid opinion — which is what most deployments will want.
    """

    spec = AgentSpec(
        role="evaluator",
        name="builtin.evaluator",
        uses_llm=False,
        summary="Run the deterministic gates: validates, renders, matches_plan, "
                "no_baked_styling, data_honesty.",
    )

    async def run(self, ctx: StageContext) -> EvaluationArtifact:
        from nexcraftviz.evaluate.gates import run_gates

        generated = ctx.require("generate")
        if not generated.ok:
            return EvaluationArtifact(
                status="failed",
                telemetry=Telemetry(agent=self.spec.name),
            )

        started = time.perf_counter()
        skip = tuple(ctx.options.get("skip_gates") or ())
        gates = run_gates(
            generated.spec,
            plan=getattr(ctx.stages, "plan", None),
            rows=ctx.rows,
            skip=skip,
        )
        return EvaluationArtifact(
            gates=gates,
            telemetry=Telemetry(
                wall_ms=int((time.perf_counter() - started) * 1000),
                agent=self.spec.name,
            ),
        )


class CriticAgent(SkillAgent):
    """The judgement code cannot make: does this chart answer the question?"""

    spec = AgentSpec(
        role="critic",
        name="builtin.critic",
        model_tier="smart",
        skill="viz.critique",
        summary="Judge whether the chart answers the question and leads to a "
                "true conclusion.",
    )

    def build_input(self, ctx: StageContext) -> dict[str, Any]:
        generated = ctx.require("generate")
        return {
            "question": ctx.question,
            "spec": generated.spec,
            "plan": getattr(ctx.stages, "plan", None),
            "rows": ctx.rows,
            "language": ctx.language,
        }

    async def run(self, ctx: StageContext) -> Critique:
        result, _ = await self.call(ctx)
        return result.value if isinstance(result.value, Critique) else Critique()


# ---------------------------------------------------------------------------
# deliverer
# ---------------------------------------------------------------------------

class DelivererAgent:
    """Package what was produced, with the verdict attached rather than hidden.

    Minimal by design at this stage: it assembles the package and the dashboard
    tile. Rendered files and BI exports are the other half of delivery and are
    added where the export code lands — a deliverer that silently produced no
    exports would be worse than one that does not claim to.
    """

    spec = AgentSpec(
        role="deliverer",
        name="builtin.deliverer",
        uses_llm=False,
        summary="Assemble the package: spec, plan, verdict, provenance, tile.",
    )

    async def run(self, ctx: StageContext) -> DeliveryArtifact:
        started = time.perf_counter()
        generated = ctx.require("generate")
        plan = getattr(ctx.stages, "plan", None)
        evaluation = getattr(ctx.stages, "evaluate", None)

        spec = generated.spec
        package: dict[str, Any] = {
            "question": ctx.question,
            "spec": spec.raw if spec is not None else None,
            "chart_type": generated.chart_type or (plan.chart_type if plan else ""),
            "plan": plan.model_dump(mode="json") if plan else None,
            "verdict": evaluation.model_dump(mode="json") if evaluation else None,
            "provenance": {
                "stages": ctx.stages.completed() if ctx.stages else [],
                "tokens": ctx.stages.total_tokens() if ctx.stages else 0,
                "wall_ms": ctx.stages.total_ms() if ctx.stages else 0,
            },
        }

        artifact = DeliveryArtifact(
            package=package,
            tile=_tile(spec, ctx.rows, plan) if spec is not None else None,
            telemetry=Telemetry(
                wall_ms=int((time.perf_counter() - started) * 1000),
                agent=self.spec.name,
            ),
        )
        # Delivering a chart the evaluator rejected is deliberate — the caller
        # gets the chart *and* the reason to distrust it — but the status must
        # say so, or "delivered" reads as "passed".
        if evaluation is not None and not evaluation.passed:
            artifact.status = "ambiguous"
        return artifact


def _tile(spec: Any, rows: list[dict[str, Any]], plan: Any) -> dict[str, Any]:
    """Shape a delivered chart for the `thread_components` table.

    Lowercase ``component_type`` and ``sample_data`` wrapped as
    ``{"values": rows}`` because that is what the consumer reads; getting either
    wrong produces a row that inserts cleanly and renders nothing.
    """
    from nexcraftviz.spec.labels import lift_title

    # The tile header shows `configuration.title`; the chart must not draw it
    # again. What the chart was titled wins over the plan: an edit may have
    # changed it since.
    schema, title, subtitle = lift_title(spec.raw)
    return {
        "component_type": "chart",
        "chart_schema": schema,
        "sample_data": {"values": rows or spec.data_values},
        "configuration": {
            "title": title or (plan.metadata.title if plan else "") or "",
            "subtitle": subtitle or (plan.metadata.subtitle if plan else "") or "",
            "chart_type": (plan.chart_type if plan else "") or spec.mark_summary,
        },
    }


# ---------------------------------------------------------------------------
# the conversational roles
# ---------------------------------------------------------------------------

class ManagerAgent(SkillAgent):
    """Instruction → ordered actions. The role behind an instruction box.

    Not part of the chart pipeline — it sits *in front of* it, deciding whether
    an instruction wants an edit, a theme change, a narration, a rearrangement,
    a whole new chart, or an honest refusal.
    """

    spec = AgentSpec(
        role="manager",
        name="builtin.manager",
        model_tier="fast",
        skill="viz.manage",
        summary="Route one instruction to the actions that carry it out, in order.",
    )

    def build_input(self, ctx: StageContext) -> dict[str, Any]:
        return {
            "instruction": ctx.question,
            "spec": ctx.options.get("spec"),
            "widget": ctx.options.get("widget"),
            "rows": ctx.rows,
            "language": ctx.language,
        }

    async def run(self, ctx: StageContext) -> Any:
        result, telemetry = await self.call(ctx)
        decision = result.value
        if decision is not None:
            decision.telemetry = telemetry
        return decision


class EditorAgent(SkillAgent):
    """An edit needs no plan — the chart already exists and the change is named."""

    spec = AgentSpec(
        role="editor",
        name="builtin.editor",
        model_tier="fast",
        skill="viz.edit",
        summary="Apply a natural-language change to an existing chart.",
    )

    def build_input(self, ctx: StageContext) -> dict[str, Any]:
        return {
            "instruction": ctx.question,
            "spec": ctx.options.get("spec"),
            "rows": ctx.rows,
            "language": ctx.language,
        }

    async def run(self, ctx: StageContext) -> SkillResult:
        result, _ = await self.call(ctx)
        return result


class ThemerAgent(SkillAgent):
    """Applying a theme needs no model, and a role for it still earns its place:
    a host with brand tokens of its own substitutes here."""

    spec = AgentSpec(
        role="themer",
        name="builtin.themer",
        uses_llm=False,
        skill="viz.theme",
        summary="Apply a named theme to a chart.",
    )

    def build_input(self, ctx: StageContext) -> dict[str, Any]:
        return {
            "spec": ctx.options.get("spec"),
            "theme": ctx.options.get("theme") or ctx.theme,
        }

    async def run(self, ctx: StageContext) -> SkillResult:
        result, _ = await self.call(ctx)
        return result


class ComposerAgent(SkillAgent):
    """Finished charts → a widget design.

    Sees visualisations, never rows. By the time it runs each chart has been
    planned, generated and gated on its own, so the only open question is
    layout — and handing it the data too would invite it to relitigate charts
    that have already passed.
    """

    spec = AgentSpec(
        role="composer",
        name="builtin.composer",
        model_tier="fast",
        skill="viz.compose",
        summary="Arrange finished charts into one widget: order, spans, panels, titles.",
    )

    def build_input(self, ctx: StageContext) -> dict[str, Any]:
        return {
            "ask": ctx.question,
            "visualizations": ctx.options.get("visualizations") or [],
            "existing": ctx.options.get("existing") or [],
            "language": ctx.language,
        }

    async def run(self, ctx: StageContext) -> SkillResult:
        result, telemetry = await self.call(ctx)
        if result.value is not None:
            result.value.telemetry = telemetry
        return result


class PlacerAgent(SkillAgent):
    spec = AgentSpec(
        role="placer",
        name="builtin.placer",
        model_tier="fast",
        skill="viz.place",
        summary="Rearrange tiles on a widget.",
    )

    def build_input(self, ctx: StageContext) -> dict[str, Any]:
        return {
            "instruction": ctx.question,
            "widget": ctx.options.get("widget"),
            "language": ctx.language,
        }

    async def run(self, ctx: StageContext) -> SkillResult:
        result, _ = await self.call(ctx)
        return result


class NarratorAgent(SkillAgent):
    spec = AgentSpec(
        role="narrator",
        name="builtin.narrator",
        model_tier="fast",
        skill="viz.narrate",
        summary="Describe what a chart shows, in the plan's angle where there is one.",
    )

    def build_input(self, ctx: StageContext) -> dict[str, Any]:
        spec = ctx.options.get("spec")
        if spec is None and ctx.stages is not None:
            generated = getattr(ctx.stages, "generate", None)
            spec = generated.spec if generated is not None else None
        return {
            "spec": spec,
            "rows": ctx.rows,
            "question": ctx.question,
            "language": ctx.language,
        }

    async def run(self, ctx: StageContext) -> SkillResult:
        result, _ = await self.call(ctx)
        return result


# ---------------------------------------------------------------------------

_BUILTINS: tuple[type, ...] = (
    ManagerAgent, PlannerAgent, GeneratorAgent, EvaluatorAgent, CriticAgent,
    DelivererAgent, EditorAgent, ThemerAgent, PlacerAgent, ComposerAgent,
    NarratorAgent,
)


def build_default_agents() -> dict[str, Agent]:
    """One instance per role. Cheap — agents hold no state between runs."""
    agents: dict[str, Agent] = {}
    for cls in _BUILTINS:
        instance: Agent = cls()
        agents[instance.spec.role] = instance
    return agents
