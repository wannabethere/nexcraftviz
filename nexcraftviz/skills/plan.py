"""``viz.plan`` and ``viz.critique`` — decide, and judge.

Splitting the decision from the drawing is the centre of the pipeline. A plan
is reviewable, cacheable, diffable and overridable in a way that a finished
Vega-Lite document is not — and the evaluator can check the generator against
it, which is a check that simply does not exist when one call does both.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nexcraftviz.agents.artifacts import ChartPlan, Critique
from nexcraftviz.agents.intent import apply_as_floor, extract_intent
from nexcraftviz.data.profile import profile_rows
from nexcraftviz.recommend.rules import recommend
from nexcraftviz.skills.base import Skill, SkillResult, SkillSpec
from nexcraftviz.spec.model import Spec

# ---------------------------------------------------------------------------
# viz.plan
# ---------------------------------------------------------------------------

class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    language: str = "English"
    theme: str = Field(default="", description="A theme already in force, if any.")


class PlanSkill(Skill[PlanIn, ChartPlan]):
    """Question + data → a chart plan."""

    spec = SkillSpec(
        name="viz.plan",
        summary=(
            "Decide what chart to draw: type, encodings, transforms, styling and "
            "copy — with the reasoning and the rejected alternatives."
        ),
        prompt="viz.plan",
    )
    Input = PlanIn
    Output = ChartPlan

    def user_payload(self, inputs: PlanIn) -> str:
        profile = profile_rows(inputs.rows)
        ranked = recommend(profile, question=inputs.question)
        intent = extract_intent(inputs.question)

        payload: dict[str, Any] = {
            "question": inputs.question,
            "language": inputs.language,
            "row_count": profile.row_count,
            "profile": profile.to_prompt_dict(),
            "recommendations": [
                {"chart_type": r.chart_type, "score": round(r.score, 2), "reason": r.reason}
                for r in ranked
            ],
            # What the question said outright, already parsed. Free, and stable
            # across runs in a way a model's reading of the same phrase is not.
            "extracted": intent.to_dict(),
        }
        if inputs.theme and not intent.theme:
            payload["current_theme"] = inputs.theme
        return json.dumps(payload, indent=2, default=str)

    def apply(self, inputs: PlanIn, output: ChartPlan) -> SkillResult[ChartPlan]:
        result: SkillResult[ChartPlan] = SkillResult(skill=self.spec.name, output=output)

        # The extractor runs as a floor, not a ceiling: where the model made a
        # call it wins, and this only fills what it left blank.
        plan = apply_as_floor(output, extract_intent(inputs.question))
        if inputs.theme and not plan.styling.theme:
            plan.styling.theme = inputs.theme

        result.value = plan
        result.changes = [plan.summary()]

        if not plan.ok:
            result.warnings.append(f"{plan.status}: {plan.reason_if_not_ok}")
            return result

        # A plan naming a column that does not exist produces a chart that
        # renders empty. Catching it here saves a generation.
        known = set(profile_rows(inputs.rows).column_names)
        if known:
            invented = sorted(plan.fields_used() - known)
            if invented:
                result.failed.append(
                    ("viz.plan", f"plans fields the data does not have: {', '.join(invented)}")
                )
        if not plan.encodings and plan.chart_type not in ("kpi", "table_with_cells"):
            result.warnings.append("plan has no encodings — the generator has nothing to build")
        return result


# ---------------------------------------------------------------------------
# viz.critique
# ---------------------------------------------------------------------------

class CritiqueIn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    question: str
    spec: Any
    plan: Any = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
    language: str = "English"

    def as_spec(self) -> Spec:
        return self.spec if isinstance(self.spec, Spec) else Spec(self.spec or {})


class CritiqueSkill(Skill[CritiqueIn, Critique]):
    """Does this chart answer the question? The check code cannot make."""

    spec = SkillSpec(
        name="viz.critique",
        summary=(
            "Judge whether a chart answers the question it was made for, and "
            "whether it leads a reader to a true conclusion."
        ),
        prompt="viz.critique",
    )
    Input = CritiqueIn
    Output = Critique

    def user_payload(self, inputs: CritiqueIn) -> str:
        spec = inputs.as_spec()
        rows = inputs.rows or spec.data_values
        payload: dict[str, Any] = {
            "question": inputs.question,
            "language": inputs.language,
            "spec": spec.raw,
            "profile": profile_rows(rows).to_prompt_dict(),
            "rows": rows[:30],
        }
        if inputs.plan is not None:
            plan = inputs.plan
            payload["plan"] = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else plan
        return json.dumps(payload, indent=2, default=str)

    def apply(self, inputs: CritiqueIn, output: Critique) -> SkillResult[Critique]:
        result: SkillResult[Critique] = SkillResult(
            skill=self.spec.name, output=output, value=output
        )
        if output.answers_question:
            result.changes = ["chart answers the question"]
            return result

        result.changes = [output.complaint or "chart does not answer the question"]
        # A rejection with no actionable complaint cannot drive a regeneration,
        # which makes the critic a cost with no benefit.
        if not output.complaint:
            result.warnings.append(
                "critic rejected the chart without naming a specific fault"
            )
        return result
