"""``viz.recommend``, ``viz.generate``, ``viz.narrate``, ``viz.theme``.

The creation half of the surface. Two of these need no model at all —
``viz.recommend`` is shape rules and ``viz.theme`` is token application — which
is the point: a host agent that calls them pays nothing and gets the same
answer every time.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from nexcraftviz.data.profile import profile_rows
from nexcraftviz.recommend.rules import recommend as rules_recommend
from nexcraftviz.skills.base import Skill, SkillResult, SkillSpec
from nexcraftviz.spec.jsonfix import repair_json
from nexcraftviz.spec.kpi import bind_kpi
from nexcraftviz.spec.labels import label_chart
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


def _bind_rows(spec: Spec, rows: list[dict[str, Any]]) -> list[str]:
    """Bind ``rows`` at the root and drop inline copies of the dataset below it.

    A layer's own data is dropped — so it inherits the real rows — when it
    looks like the dataset: more than one row, every key a real column. A
    one-row constant, such as a threshold line's value, is a design choice and
    stays.
    """
    notes: list[str] = []
    columns = set().union(*(row.keys() for row in rows))
    written = spec.data_values
    if written and written != rows:
        notes.append(f"drew the {len(rows)} row(s) given, not the {len(written)} "
                     "the model wrote into the spec")
    spec.set_data_values(rows)
    for view in spec.views():
        if view.path == ():
            continue
        data = view.node.get("data")
        values = data.get("values") if isinstance(data, dict) else None
        if not isinstance(values, list) or len(values) < 2:
            continue
        keys = set().union(*(v.keys() for v in values if isinstance(v, dict)))
        if keys and keys <= columns:
            view.node.pop("data")
            notes.append(f"dropped {len(values)} invented row(s) from "
                         f"{'.'.join(str(step) for step in view.path)}")
    return notes


def chart_type_name(said: str, plan: Any = None) -> str:
    """The chart type as a name, not a description of one.

    The model writes this field freely: a live run returned
    "bar (horizontal, sorted)", which no caller branching on `bar` matches.
    The plan's type wins when the text names it; otherwise the leading word.
    """
    text = (said or "").strip().lower()
    planned = (plan.get("chart_type") if isinstance(plan, dict)
               else getattr(plan, "chart_type", "")) or ""
    if planned and (planned in text.replace(" ", "_") or not text):
        return planned
    match = re.match(r"[a-z][a-z_]*", text.replace(" ", "_"))
    return match.group(0).rstrip("_") if match else text


def _plan_title(plan: Any) -> str:
    """The title the planner wrote, from a ChartPlan or its dict form."""
    metadata = plan.get("metadata") if isinstance(plan, dict) else getattr(plan, "metadata", None)
    title = (
        metadata.get("title") if isinstance(metadata, dict)
        else getattr(metadata, "title", "")
    )
    return title if isinstance(title, str) else ""


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
            if chart_type == "kpi":
                payload["examples"] = _kpi_examples(inputs.question, profile)
                payload["instruction"] += (
                    " This plan is a KPI: return a `kpi_metadata` payload as described "
                    "under WHEN THE PLAN IS A KPI, naming the columns that hold its numbers."
                )
            else:
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
        output.chart_type = chart_type_name(output.chart_type, inputs.plan)
        result: SkillResult[GenerateOut] = SkillResult(skill=self.spec.name, output=output)

        if not output.spec_json.strip():
            result.warnings.append(output.reasoning or "no chart suits this data")
            return result

        spec = Spec.from_json_lenient(output.spec_json)
        json_notes: list[str] = []
        if not spec:
            # The document arrives as a JSON string inside the answer, so strict
            # mode guarantees nothing about it. A live model dropped one brace
            # in a nested KPI spec; closing it has exactly one reading, and every
            # gate still runs on the result. What the balancer will not guess at
            # fails here, with the parse error, so a regeneration can fix it.
            repaired, json_notes = repair_json(output.spec_json)
            if json_notes:
                spec = Spec.from_json_lenient(repaired)
        if not spec:
            result.failed.append((
                "viz.generate",
                f"the model's spec was not valid JSON ({_json_error(output.spec_json)})",
            ))
            return result

        if spec.family == "kpi":
            return self._apply_kpi(spec, inputs, output, result, json_notes)

        # The chart draws the rows it was given, whatever data the model wrote.
        # It sees a profile, not the rows, so values it writes are its own:
        # live, a trend spec arrived with Feb and Mar figures that were not in
        # the data, and only the critic noticed.
        bound = _bind_rows(spec, inputs.rows) if inputs.rows else []
        spec.ensure_schema_url()

        # Repair is on: a near-miss field name is the single most common
        # failure here, and it is deterministically fixable.
        spec, report = validate(spec, data=inputs.rows, max_tier=3, repair=True)
        # Every plan writes a title and none reached the spec; the corpus this
        # adapts from blanks every axis title. See nexcraftviz.spec.labels.
        labelled = label_chart(spec, title=_plan_title(inputs.plan))
        result.value = spec
        result.changes = [f"generated a {output.chart_type or spec.mark_summary} chart"]
        result.changes.extend(f"repaired {note}" for note in report.repaired)
        # After the reassignment above, or the note would be wiped.
        result.changes.extend(f"repaired the model's JSON: {note}" for note in json_notes)
        result.changes.extend(labelled)
        result.changes.extend(bound)
        if json_notes:
            result.meta["json_repaired"] = json_notes
        result.meta["repaired"] = [*json_notes, *report.repaired]
        result.warnings.extend(str(issue) for issue in report.errors)
        result.meta["valid"] = report.ok
        result.meta["chart_type"] = output.chart_type
        if not report.ok:
            result.failed.append(("viz.generate", report.summary()))
        return result

    def _apply_kpi(
        self,
        spec: Spec,
        inputs: GenerateIn,
        output: GenerateOut,
        result: SkillResult[GenerateOut],
        json_notes: list[str],
    ) -> SkillResult[GenerateOut]:
        """A KPI card: its numbers read from the data — see nexcraftviz.spec.kpi.

        No `$schema`, no bound rows: a KPI payload is not a Vega spec, and
        dressing it as one makes every host hand it to Vega, which draws a
        number in the corner of an empty canvas.
        """
        notes, error = bind_kpi(spec, inputs.rows)
        if error:
            result.failed.append(("viz.generate", error))
            return result
        meta = spec.raw["kpi_metadata"]
        if not meta.get("label"):
            meta["label"] = _plan_title(inputs.plan)
        spec, report = validate(spec, data=inputs.rows, max_tier=1)
        result.value = spec
        result.changes = [f"generated a {meta.get('chart_subtype', 'counter')} KPI card", *notes]
        result.changes.extend(f"repaired the model's JSON: {note}" for note in json_notes)
        result.meta["repaired"] = list(json_notes)
        result.meta["valid"] = report.ok
        result.meta["chart_type"] = output.chart_type or "kpi"
        if not report.ok:
            result.failed.append(("viz.generate", report.summary()))
        return result


def _json_error(text: str) -> str:
    """The parser's own words, which say where the document broke."""
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return str(exc)
    return "unreadable"


def _kpi_examples(question: str, profile: Any, limit: int = 3) -> list[dict[str, Any]]:
    """The corpus KPI cards closest to this question, in the payload shape.

    The corpus holds 22 KPI cards and none carries a Vega spec, so the generic
    lookup — which keeps only pairs that do — handed a KPI plan nothing, and the
    generator improvised a bare text mark. These are the real thing: the
    best-arguing card first, then one of each other subtype the corpus has.
    """
    try:
        from nexcraftviz.corpus.loader import seed
        from nexcraftviz.recommend.precedent import precedents

        pairs = [p for p in seed().by_chart_type("kpi") if _kpi_schema(p)]
        best = next((p.example for p in precedents(profile, question=question)
                     if p.chart_type == "kpi"), "")
    except Exception:  # noqa: BLE001 — a missing corpus must not break generation
        return []

    pairs.sort(key=lambda pair: pair.name != best)
    chosen: list[Any] = []
    for pair in pairs:
        subtype = _kpi_schema(pair).get("chart_subtype")
        if all(_kpi_schema(c).get("chart_subtype") != subtype for c in chosen):
            chosen.append(pair)
    examples = []
    for pair in chosen[:limit]:
        card = {k: v for k, v in _kpi_schema(pair).items()
                if k not in ("value", "change_pct", "change_direction")}
        # The corpus writes its numbers in; a generated card names its columns.
        card["value_field"] = "value"
        examples.append({"name": pair.name, "use_when": pair.use_when,
                         "kpi_metadata": card})
    return examples


def _kpi_schema(pair: Any) -> dict[str, Any]:
    raw = pair.columns_schema
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


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
