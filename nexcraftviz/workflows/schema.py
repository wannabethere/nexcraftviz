"""The skill-file format.

```yaml
skill: dashboard
intents:
  - id: from_question
    label: Build a dashboard that answers a question
    workflow: build_dashboard
    asks: {question: What should this dashboard answer?}
workflows:
  build_dashboard:
    steps:
      - {id: questions, uses: dashboard.suggest_questions, with: {question: $intent.question}}
      - {id: pick, pause: select, prompt: Which of these?, options: $steps.questions}
```

A step is exactly one of ``uses`` (an action, ``host.*``, or ``workflow.NAME``)
or ``pause``. The loader checks the rest — see :mod:`nexcraftviz.workflows.loader`.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PauseKind = Literal["ask", "select", "approve"]
AskType = Literal["text", "choice", "boolean", "widget", "widgets", "rows"]


class Ask(BaseModel):
    """One thing the user answers when starting an intent."""

    model_config = ConfigDict(extra="forbid")

    prompt: str
    type: AskType = "text"
    options: list[str] = Field(default_factory=list)
    required: bool = True


class Intent(BaseModel):
    """What a user can start, and the workflow it runs."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    workflow: str
    asks: dict[str, Ask] = Field(default_factory=dict)

    @field_validator("asks", mode="before")
    @classmethod
    def _shorthand(cls, value: Any) -> Any:
        """``name: prompt`` is shorthand for ``name: {prompt: prompt}``."""
        if isinstance(value, dict):
            return {k: {"prompt": v} if isinstance(v, str) else v for k, v in value.items()}
        return value


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    uses: str = ""
    pause: PauseKind | None = None
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    when: str = ""
    for_each: str = ""
    prompt: str = ""
    options: Any = None
    show: Any = None

    @model_validator(mode="after")
    def _exactly_one_kind(self) -> Step:
        if bool(self.uses) == bool(self.pause):
            raise ValueError(f"step {self.id!r} needs exactly one of `uses` or `pause`")
        return self

    @property
    def is_host(self) -> bool:
        return self.uses.startswith("host.")

    @property
    def sub_workflow(self) -> str:
        return self.uses.removeprefix("workflow.") if self.uses.startswith("workflow.") else ""

    @property
    def kind(self) -> str:
        if self.pause:
            return self.pause
        if self.is_host:
            return "host"
        return "workflow" if self.sub_workflow else "action"


class Workflow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)
    steps: list[Step]


class SkillFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill: str
    version: int = 1
    summary: str = ""
    intents: list[Intent]
    workflows: dict[str, Workflow]

    def intent(self, intent_id: str) -> Intent | None:
        return next((i for i in self.intents if i.id == intent_id), None)

    def outline(self) -> dict[str, Any]:
        """What a host needs to offer the skill: its intents, their asks, the steps."""
        return {
            "skill": self.skill,
            "version": self.version,
            "summary": self.summary,
            "intents": [
                {
                    "id": intent.id,
                    "label": intent.label,
                    "workflow": intent.workflow,
                    "asks": {name: ask.model_dump() for name, ask in intent.asks.items()},
                    "steps": [
                        {"id": step.id, "kind": step.kind, "uses": step.uses}
                        for step in self.workflows[intent.workflow].steps
                    ],
                }
                for intent in self.intents
            ],
        }
