"""viz.suggest_questions — the chart questions a dashboard question needs.

The one model call the dashboard skill makes of its own. It does not see the
data: each question it proposes is queried by the host, and a chart is built
for every one that returns rows. A person picks from what it proposes before
anything is queried.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nexcraftviz.skills.base import Skill, SkillResult, SkillSpec


class SuggestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(description="What the dashboard should answer, in the user's words.")
    language: str = "English"
    count: int = Field(default=5, ge=1, le=12)
    context: str = Field(
        default="",
        description="What the host knows about the data — tables, measures — when anything.",
    )


class SuggestedQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="One question one chart can answer.")
    visual: str = Field(default="", description="The chart type that would answer it.")
    why: str = Field(default="", description="What it adds to the dashboard, in one clause.")


class SuggestOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[SuggestedQuestion] = Field(default_factory=list)


class SuggestQuestionsSkill(Skill[SuggestIn, SuggestOut]):
    """A dashboard question → the chart questions that answer it, headline first."""

    spec = SkillSpec(
        name="viz.suggest_questions",
        summary="Break a dashboard's question into the chart questions that answer it.",
        prompt="viz.suggest_questions",
    )
    Input = SuggestIn
    Output = SuggestOut

    def user_payload(self, inputs: SuggestIn) -> str:
        payload: dict[str, Any] = {
            "question": inputs.question,
            "language": inputs.language,
            "count": inputs.count,
        }
        if inputs.context:
            payload["context"] = inputs.context
        return json.dumps(payload, indent=2)

    def apply(self, inputs: SuggestIn, output: SuggestOut) -> SkillResult[SuggestOut]:
        result: SkillResult[SuggestOut] = SkillResult(skill=self.spec.name, output=output)
        seen: set[str] = set()
        kept: list[dict[str, Any]] = []
        for question in output.questions:
            text = question.text.strip()
            key = " ".join(text.lower().split())
            if not text or key in seen:
                continue
            seen.add(key)
            kept.append({"id": f"q{len(kept) + 1}", "text": text,
                         "visual": question.visual.strip(), "why": question.why.strip()})
            if len(kept) == inputs.count:
                break
        dropped = len(output.questions) - len(kept)
        result.value = kept
        result.changes = [f"suggested {len(kept)} question(s)"]
        if dropped:
            result.warnings.append(f"dropped {dropped} empty, repeated or surplus question(s)")
        return result
