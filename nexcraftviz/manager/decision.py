"""What the manager decided: an ordered list of steps, and what it refused.

The shape that matters is the **list**. `nexcraftviz.session.route` picks
exactly one skill, so "make it dark and sort descending" keeps the theme change
and silently drops the sort — the user gets half of what they asked for and no
indication that the other half was discarded. A manager that returns steps can
carry both, in order.

Declining is a first-class outcome for the same reason. "Break this down by
month" needs a different query, which nexcraftviz has no way to run; answering
it with a chart of the rows we happen to hold would be answering a question
nobody asked. Saying so lets the caller offer the drilldown that *can* re-query.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from nexcraftviz.agents.artifacts import STRICT, Status, Telemetry

#: What a step can ask for. Each maps to something that already exists — the
#: manager decides, it does not draw.
Action = Literal[
    "edit", "theme", "narrate", "place", "widget", "recreate", "decline"
]

ACTIONS: tuple[Action, ...] = (
    "edit", "theme", "narrate", "place", "widget", "recreate", "decline",
)

#: action → the registry role that carries it out.
#:
#: Roles, not skills. Going straight to a skill would mean a host that overrode
#: the `editor` role changed the conversation and not the instruction box, which
#: is the kind of split nobody discovers until it has already confused someone.
#: `recreate` and `widget` run several roles between them and are dispatched
#: separately; `decline` runs nothing.
ROLE_FOR: dict[str, str] = {
    "edit": "editor",
    "theme": "themer",
    "narrate": "narrator",
    "place": "placer",
    "widget": "composer",
}

#: action → the skill the built-in role delegates to. Kept for the session
#: bookkeeping, which keys undo and history on the skill name.
SKILL_FOR: dict[str, str] = {
    "edit": "viz.edit",
    "theme": "viz.theme",
    "narrate": "viz.narrate",
    "place": "viz.place",
    "widget": "viz.compose",
}


class ManagerStep(BaseModel):
    """One thing to do, and the part of the message that asked for it."""

    model_config = STRICT

    action: Action
    instruction: str = Field(
        description="The clause this step covers — not the whole message. A "
                    "skill given the whole sentence acts on the wrong half of it.",
    )
    why: str = ""
    parts: list[str] = Field(
        default_factory=list,
        description="For `widget` only: the individual charts the widget should "
                    "hold, one ask each. A widget is built from finished "
                    "visualisations, so these are planned and drawn first and "
                    "the arrangement is decided from what they turned out to be.",
    )

    @property
    def role(self) -> str:
        return ROLE_FOR.get(self.action, "")

    @property
    def skill(self) -> str:
        return SKILL_FOR.get(self.action, "")


class ManagerDecision(BaseModel):
    """The plan for one instruction."""

    model_config = STRICT

    status: Status = "ok"
    reason_if_not_ok: str = ""
    steps: list[ManagerStep] = Field(default_factory=list)
    declined: list[str] = Field(
        default_factory=list,
        description="What was asked for and will not be done, each with its reason.",
    )
    telemetry: Telemetry = Field(default_factory=Telemetry)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def actionable(self) -> list[ManagerStep]:
        return [s for s in self.steps if s.action != "decline"]

    def summary(self) -> str:
        if not self.steps:
            return self.reason_if_not_ok or "nothing to do"
        return " → ".join(f"{s.action}({s.instruction})" for s in self.steps)
