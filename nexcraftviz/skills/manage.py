"""``viz.manage`` — one instruction in, an ordered list of actions out.

The skill the annotate box needs. Everything else here acts on a chart; this
decides *which* of those things to do, and it is the only place that can carry
more than one of them.

Two behaviours worth stating, because both are load-bearing:

* **The rules run first and their labels are kept.** They come from the same
  table that routes the conversation (:mod:`nexcraftviz.session.route`), so a
  phrase means the same thing in the annotate box and in the chat. The model is
  asked only about clauses the rules could not read.
* **A message the rules fully labelled costs nothing.** ``needs_model`` is False
  for those and the caller skips the call entirely — which is most annotate
  messages, because most are one plain clause.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nexcraftviz.data.profile import profile_rows
from nexcraftviz.manager.decision import ManagerDecision, ManagerStep
from nexcraftviz.manager.split import LabelledClause, label_all, widget_parts
from nexcraftviz.skills.base import Skill, SkillResult, SkillSpec
from nexcraftviz.spec.model import Spec


class ManageIn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    instruction: str
    spec: Any = Field(default=None, description="The chart as it stands, if any.")
    widget: Any = Field(default=None, description="The widget, when tiles are in play.")
    rows: list[dict[str, Any]] = Field(default_factory=list)
    language: str = "English"

    def as_spec(self) -> Spec | None:
        if self.spec is None:
            return None
        return self.spec if isinstance(self.spec, Spec) else Spec(self.spec or {})

    @property
    def has_chart(self) -> bool:
        spec = self.as_spec()
        return spec is not None and bool(spec)

    @property
    def has_widget(self) -> bool:
        return self.widget is not None

    def clauses(self) -> list[LabelledClause]:
        return label_all(
            self.instruction, has_chart=self.has_chart, has_widget=self.has_widget
        )

    def needs_model(self) -> bool:
        """False when the rules read every clause — the common case, and free."""
        clauses = self.clauses()
        return not clauses or any(c.needs_model for c in clauses)


class ManageSkill(Skill[ManageIn, ManagerDecision]):
    """Instruction → ordered actions."""

    spec = SkillSpec(
        name="viz.manage",
        summary=(
            "Route one instruction to the actions that carry it out, in order — "
            "and say plainly what cannot be done here."
        ),
        prompt="viz.manage",
    )
    Input = ManageIn
    Output = ManagerDecision

    def user_payload(self, inputs: ManageIn) -> str:
        spec = inputs.as_spec()
        payload: dict[str, Any] = {
            "instruction": inputs.instruction,
            "language": inputs.language,
            "has_chart": inputs.has_chart,
            "has_widget": inputs.has_widget,
            "clauses": [
                {"text": c.text, "action": c.action, "why": c.why or None}
                for c in inputs.clauses()
            ],
            "columns": profile_rows(inputs.rows).column_names,
        }
        if spec is not None and spec:
            payload["chart_summary"] = {
                "chart_type": spec.mark_summary,
                "fields": sorted({f for _, _, f in spec.field_refs()}),
            }
        return json.dumps(payload, indent=2, default=str)

    def apply(self, inputs: ManageIn, output: ManagerDecision) -> SkillResult[ManagerDecision]:
        result: SkillResult[ManagerDecision] = SkillResult(
            skill=self.spec.name, output=output
        )
        decision = _repair(output, inputs)
        result.value = decision
        result.changes = [decision.summary()]

        if not decision.ok:
            result.warnings.append(decision.reason_if_not_ok or decision.status)
        if not decision.steps:
            result.warnings.append(
                "nothing to do — the instruction produced no actions"
            )
        return result

    def decide_offline(self, inputs: ManageIn) -> ManagerDecision:
        """The rules' own answer, for when every clause was labelled.

        Not a fallback for a missing model: the caller checks ``needs_model``
        first and only lands here when there was genuinely nothing to ask.
        """
        return _from_clauses(inputs.clauses())


# ---------------------------------------------------------------------------
# repair
# ---------------------------------------------------------------------------

def _from_clauses(clauses: list[LabelledClause]) -> ManagerDecision:
    steps = [
        ManagerStep(action=c.action, instruction=c.text, why=c.why, parts=list(c.parts))
        for c in clauses
        if c.action is not None
    ]
    return ManagerDecision(
        steps=_ordered(steps),
        declined=[f"{c.text}: {c.why}" for c in clauses if c.action == "decline"],
    )


def _repair(decision: ManagerDecision, inputs: ManageIn) -> ManagerDecision:
    """Fix what the model can get wrong without noticing.

    Each of these is a case where the returned decision is well-formed and still
    would not work, so nothing downstream would catch it.
    """
    for step in decision.steps:
        # A widget step with no parts builds one chart from the whole sentence,
        # which for "a dashboard of X and Y" silently drops Y. The rules split
        # the ask deterministically, so use theirs when the model gave none.
        if step.action == "widget" and not step.parts:
            step.parts = widget_parts(step.instruction)

    if not decision.steps:
        # A model that returned no steps has said the instruction means nothing,
        # which is rarely true and never useful — the user typed something. The
        # rules are the floor here as they are everywhere else: fall back to
        # whatever they read, even tentatively, rather than doing nothing at all.
        fallback = _from_clauses(inputs.clauses())
        if fallback.steps:
            decision.steps = fallback.steps
            decision.declined = decision.declined or fallback.declined

    steps: list[ManagerStep] = []
    for step in decision.steps:
        # `place` with no widget has nothing to move. The instruction is real,
        # so treat it as a chart change rather than dropping it silently.
        if step.action == "place" and not inputs.has_widget:
            steps.append(ManagerStep(
                action="edit", instruction=step.instruction,
                why="sounds like layout, but there is no widget to rearrange",
            ))
            continue
        # Editing nothing is impossible; with no chart every change is a build.
        if step.action in ("edit", "theme", "place") and not inputs.has_chart:
            steps.append(ManagerStep(
                action="recreate", instruction=step.instruction,
                why="there is no chart yet, so this builds one",
            ))
            continue
        steps.append(step)

    decision.steps = _ordered(steps)
    for step in decision.steps:
        if step.action == "decline":
            entry = f"{step.instruction}: {step.why}" if step.why else step.instruction
            if entry not in decision.declined:
                decision.declined.append(entry)
    return decision


def _ordered(steps: list[ManagerStep]) -> list[ManagerStep]:
    """Narration last.

    "What does this show, and sort it descending" should describe the sorted
    chart. Narrating first describes something the user is about to stop
    looking at.
    """
    if len(steps) < 2:
        return steps
    return [s for s in steps if s.action != "narrate"] + [
        s for s in steps if s.action == "narrate"
    ]
