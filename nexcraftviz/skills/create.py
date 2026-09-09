"""``viz.recommend``, ``viz.generate``, ``viz.narrate``, ``viz.theme``.

The creation half of the surface. Two of these need no model at all —
``viz.recommend`` is shape rules and ``viz.theme`` is token application — which
is the point: a host agent that calls them pays nothing and gets the same
answer every time.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from nexcraftviz.data.profile import profile_rows
from nexcraftviz.recommend.rules import recommend as rules_recommend
from nexcraftviz.skills.base import Skill, SkillResult, SkillSpec
from nexcraftviz.spec.model import Spec
from nexcraftviz.spec.validate import validate
from nexcraftviz.theme import apply_theme, available_themes, strip_hardcoded_colours

# ---------------------------------------------------------------------------
# viz.recommend — no model
# ---------------------------------------------------------------------------

class RecommendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[dict[str, Any]] = Field(default_factory=list)
    question: str = ""


class RecommendOut(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecommendSkill(Skill[RecommendIn, RecommendOut]):
    """Rank chart types from the data shape. Deterministic; costs nothing."""

    spec = SkillSpec(
        name="viz.recommend",
        summary=(
            "Rank chart types for a result set, with a reason for each. "
            "Deterministic — no model call."
        ),
        uses_llm=False,
    )
    Input = RecommendIn
    Output = RecommendOut

    def apply(self, inputs: RecommendIn, output: RecommendOut) -> SkillResult[RecommendOut]:
        profile = profile_rows(inputs.rows)
        ranked = rules_recommend(profile, question=inputs.question)
        return SkillResult(
            skill=self.spec.name,
            output=output,
            value=[
                {"chart_type": r.chart_type, "score": round(r.score, 2), "reason": r.reason}
                for r in ranked
            ],
            changes=[f"best: {ranked.best.chart_type}"] if ranked.best else [],
            meta={
                "shape_signature": ranked.shape_signature,
                "profile": profile.to_prompt_dict(),
            },
        )


# ---------------------------------------------------------------------------
# viz.generate
# ---------------------------------------------------------------------------

class GenerateIn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    question: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    language: str = "English"
    chart_type: str = Field(default="", description="Force a chart type, or leave empty.")
    plan: Any = Field(
        default=None,
        description="A ChartPlan to follow. When present it replaces the "
                    "recommendations — the decision has already been made.",
    )
    complaint: str = Field(
        default="",
        description="Why a previous attempt was rejected, for a regeneration.",
    )


class GenerateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_json: str = Field(
        default="",
        description="The Vega-Lite spec as a JSON object encoded in a string.",
    )
    chart_type: str = ""
    reasoning: str = ""


class GenerateSkill(Skill[GenerateIn, GenerateOut]):
    """Question + rows → a Vega-Lite spec."""

    spec = SkillSpec(
        name="viz.generate",
        summary="Create a Vega-Lite chart for a question and a result set.",
        prompt="viz.generate",
    )
    Input = GenerateIn
    Output = GenerateOut

    def user_payload(self, inputs: GenerateIn) -> str:
        profile = profile_rows(inputs.rows)
        payload: dict[str, Any] = {
            "question": inputs.question,
            "language": inputs.language,
            "row_count": profile.row_count,
            "profile": profile.to_prompt_dict(),
        }

        if inputs.plan is not None:
            # A plan supersedes the recommendations: the decision is made, and
            # offering a competing ranking invites the generator to relitigate
            # it, which is exactly what separating the stages was meant to stop.
            plan = inputs.plan
            payload["plan"] = (
                plan.model_dump(mode="json") if hasattr(plan, "model_dump") else plan
            )
            payload["instruction"] = (
                "Build the chart this plan describes. The decision has been made "
                "and reviewed — follow the chart_type, encodings and transforms. "
                "Depart from it only if the plan is impossible against this data, "
                "and say so in `reasoning`."
            )
            chart_type = getattr(plan, "chart_type", "") or (
                plan.get("chart_type", "") if isinstance(plan, dict) else ""
            )
            payload["examples"] = _retrieve_examples(chart_type)
        else:
            ranked = rules_recommend(profile, question=inputs.question)
            payload["recommendations"] = [
                {"chart_type": r.chart_type, "score": round(r.score, 2), "reason": r.reason}
                for r in ranked
            ]
            payload["examples"] = _retrieve_examples(
                ranked.best.chart_type if ranked.best else ""
            )

        if inputs.chart_type:
            payload["required_chart_type"] = inputs.chart_type
        if inputs.complaint:
            payload["previous_attempt_rejected_because"] = inputs.complaint
            payload["retry_instruction"] = (
                "A previous attempt was rejected for the reason above. Fix that "
                "specifically; do not rebuild the chart from scratch."
            )
        return json.dumps(payload, indent=2, default=str)

    def apply(self, inputs: GenerateIn, output: GenerateOut) -> SkillResult[GenerateOut]:
        result: SkillResult[GenerateOut] = SkillResult(skill=self.spec.name, output=output)

        if not output.spec_json.strip():
            result.warnings.append(output.reasoning or "no chart suits this data")
            return result

        spec = Spec.from_json_lenient(output.spec_json)
        if not spec:
            result.failed.append(("viz.generate", "the model's spec was not valid JSON"))
            return result

        # Bind the rows the chart was generated for. The model is given a
        # profile rather than the data, so it cannot embed the values itself —
        # and a spec with no data renders nothing.
        if inputs.rows and not spec.data_values:
            spec.set_data_values(inputs.rows)
        spec.ensure_schema_url()

        # Repair is on: a near-miss field name is the single most common
        # failure here, and it is deterministically fixable.
        spec, report = validate(spec, data=inputs.rows, max_tier=3, repair=True)
        result.value = spec
        result.changes = [f"generated a {output.chart_type or spec.mark_summary} chart"]
        result.changes.extend(f"repaired {note}" for note in report.repaired)
        result.warnings.extend(str(issue) for issue in report.errors)
        result.meta["valid"] = report.ok
        result.meta["chart_type"] = output.chart_type
        if not report.ok:
            result.failed.append(("viz.generate", report.summary()))
        return result


def _retrieve_examples(chart_type: str, limit: int = 2) -> list[dict[str, Any]]:
    """Corpus examples for the recommended type.

    Local lookup for now — the embedding-based retrieval lands with the corpus
    milestone. Even this crude version is worth it: adapting a working spec
    beats writing one, and every pair in the corpus is known to render.
    """
    if not chart_type:
        return []
    try:
        from nexcraftviz.corpus.loader import seed

        pairs = [p for p in seed().by_chart_type(chart_type) if p.has_vega_spec][:limit]
    except Exception:  # noqa: BLE001 — a missing corpus must not break generation
        return []

    return [
        {
            "name": pair.name,
            "chart_type": pair.chart_type,
            "use_when": pair.use_when,
            "spec": pair.spec().raw,
        }
        for pair in pairs
    ]


# ---------------------------------------------------------------------------
# viz.narrate
# ---------------------------------------------------------------------------

class NarrateIn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    spec: Any
    rows: list[dict[str, Any]] = Field(default_factory=list)
    question: str = ""
    language: str = "English"

    def as_spec(self) -> Spec:
        return self.spec if isinstance(self.spec, Spec) else Spec(self.spec or {})


class NarrationPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    kind: Literal["outlier", "trend", "comparison", "threshold", "composition"] = "comparison"
    fields: list[str] = Field(default_factory=list)


class NarrateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    headline: str = ""
    points: list[NarrationPoint] = Field(default_factory=list)


class NarrateSkill(Skill[NarrateIn, NarrateOut]):
    """Chart + data → prose, plus structured points a frontend can style."""

    spec = SkillSpec(
        name="viz.narrate",
        summary="Describe what a chart shows, in the vocabulary of the data.",
        prompt="viz.narrate",
    )
    Input = NarrateIn
    Output = NarrateOut

    def user_payload(self, inputs: NarrateIn) -> str:
        spec = inputs.as_spec()
        rows = inputs.rows or spec.data_values
        return json.dumps(
            {
                "question": inputs.question,
                "language": inputs.language,
                "chart_type": spec.mark_summary,
                "spec": spec.raw,
                "profile": profile_rows(rows).to_prompt_dict(),
                "rows": rows[:40],
            },
            indent=2,
            default=str,
        )

    def apply(self, inputs: NarrateIn, output: NarrateOut) -> SkillResult[NarrateOut]:
        result: SkillResult[NarrateOut] = SkillResult(
            skill=self.spec.name, output=output, value=output.summary
        )
        if output.headline:
            result.changes.append(output.headline)

        # The prompt forbids chart mechanics; check rather than hope, since a
        # narration that says "the x-axis shows" is a regression nobody notices
        # until a customer reads it.
        mechanics = [
            word for word in ("x-axis", "y-axis", "the legend", "this chart", "the axis")
            if word in output.summary.lower()
        ]
        if mechanics:
            result.warnings.append(
                f"narration describes chart mechanics ({', '.join(mechanics)}) "
                "rather than the data"
            )
        return result


# ---------------------------------------------------------------------------
# viz.theme — no model
# ---------------------------------------------------------------------------

class ThemeIn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    spec: Any
    theme: str = Field(default="nexcraftviz-light", description="A preset name.")
    strip_hardcoded: bool = Field(
        default=True,
        description="Clear baked-in mark colours, which otherwise override the theme.",
    )

    def as_spec(self) -> Spec:
        return self.spec if isinstance(self.spec, Spec) else Spec(self.spec or {})


class ThemeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ThemeSkill(Skill[ThemeIn, ThemeOut]):
    """Apply a theme. Deterministic; costs nothing."""

    spec = SkillSpec(
        name="viz.theme",
        summary=(
            "Apply a named theme to a chart. Deterministic — no model call. "
            f"Presets: {', '.join(available_themes())}."
        ),
        uses_llm=False,
    )
    Input = ThemeIn
    Output = ThemeOut

    def apply(self, inputs: ThemeIn, output: ThemeOut) -> SkillResult[ThemeOut]:
        result: SkillResult[ThemeOut] = SkillResult(skill=self.spec.name, output=output)
        spec = inputs.as_spec()

        if inputs.theme not in available_themes():
            result.failed.append(
                ("viz.theme", f"unknown theme {inputs.theme!r}; "
                              f"available: {', '.join(available_themes())}")
            )
            result.value = spec
            return result

        if inputs.strip_hardcoded:
            stripped = strip_hardcoded_colours(spec)
            spec = stripped.spec
            if stripped.changed:
                result.changes.append("cleared hard-coded mark colours")

        applied = apply_theme(spec, inputs.theme)
        result.value = applied.spec
        result.changes.append(f"applied theme {inputs.theme}")
        result.inverse = applied.inverse
        return result
