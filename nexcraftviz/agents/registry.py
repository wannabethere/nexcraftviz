"""The agent registry — one role, one implementation, swappable.

A *role* is a job in the pipeline: planning, generating, evaluating,
delivering. An *agent* is something that does that job. Keeping them separate
is the point: a host with their own planner registers it against the `planner`
role and the pipeline uses it, with no fork and no subclassing.

This sits above :mod:`nexcraftviz.skills`, which stays the skill-level surface
for MCP and tool-calling. A skill is a unit of work; an agent is a participant
in a pipeline. The built-in agents are thin wrappers over the skills, so there
is one implementation of each behaviour.

Model tiers are declared on the spec **and** parsed from the prompt header, with
a test asserting the two agree. cp2 has a `# model_tier:` header in every prompt
that nothing reads and a hardcoded model pick at each call site; the header
being decorative is exactly the failure worth not repeating.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:  # pragma: no cover
    pass

Role = Literal[
    "manager", "planner", "generator", "evaluator", "critic", "deliverer",
    "editor", "themer", "placer", "composer", "narrator",
]

ROLES: tuple[Role, ...] = (
    "manager", "planner", "generator", "evaluator", "critic", "deliverer",
    "editor", "themer", "placer", "composer", "narrator",
)

Tier = Literal["fast", "smart"]

#: Resolution order per tier, matching cp2's so one environment configures both
#: stacks. "fast" and "smart" are tiers, not vendors — both may resolve to the
#: same model, which is the current default.
_TIER_ENV: dict[str, tuple[str, ...]] = {
    "fast": ("NEXCRAFTVIZ_FAST_MODEL", "OPENAI_MODEL"),
    "smart": ("NEXCRAFTVIZ_SMART_MODEL", "OPENAI_MODEL"),
}

def resolve_model(tier: Tier) -> str:
    """The concrete model id for a tier."""
    from nexcraftviz.integrations.providers import DEFAULT_OPENAI_MODEL

    for name in _TIER_ENV.get(tier, ()):
        value = os.getenv(name, "").strip()
        if value:
            return value
    return DEFAULT_OPENAI_MODEL


class AgentSpec(BaseModel):
    """An agent's public description."""

    model_config = ConfigDict(extra="forbid")

    role: Role
    name: str = Field(description="Implementation name — appears in telemetry.")
    model_tier: Tier = "fast"
    uses_llm: bool = True
    skill: str = Field(default="", description="The skill this agent delegates to.")
    summary: str = ""

    @property
    def model(self) -> str:
        return resolve_model(self.model_tier)

    def declared_tier_in_prompt(self) -> str | None:
        """The tier the prompt file claims, or None when it says nothing.

        Exists so a test can assert the header and the spec agree. A header
        nobody reads drifts from reality and then misleads whoever reads it.
        """
        if not self.skill:
            return None
        from nexcraftviz.skills import REGISTRY

        skill = REGISTRY.get(self.skill)
        if skill is None or not skill.spec.prompt:
            return None
        from nexcraftviz.skills.base import PROMPT_DIR, prompt_manifest

        entry = prompt_manifest().get("prompts", {}).get(skill.spec.prompt, {})
        path = PROMPT_DIR / (entry.get("file") or f"{skill.spec.prompt}.txt")
        if not path.exists():
            return None
        from nexcraftviz.skills.base import split_headers

        headers, _ = split_headers(path.read_text(encoding="utf-8"))
        return headers.get("model_tier")


@dataclass
class StageContext:
    """Everything a stage needs, and the stages already done.

    One object rather than a long signature, because stages are pluggable —
    a host's planner should not break when a later stage starts needing
    something new.
    """

    question: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    language: str = "English"
    theme: str = ""
    stages: Any = None            # ChartStages, typed loosely to avoid a cycle
    llm: Any = None               # LLMRunner | None
    options: dict[str, Any] = field(default_factory=dict)
    #: Populated on a retry so the generator can see what was wrong.
    complaint: str = ""

    def profile(self):
        from nexcraftviz.data.profile import profile_rows

        return profile_rows(self.rows)

    def require(self, stage: str) -> Any:
        """Read an earlier stage's artifact, failing loud when it is missing."""
        artifact = getattr(self.stages, stage, None) if self.stages else None
        if artifact is None:
            raise StageError(f"stage {stage!r} has not run — nothing to read")
        return artifact


class StageError(RuntimeError):
    """Raised when a stage cannot run at all (as distinct from producing a
    ``status != "ok"`` artifact, which is a result, not an error)."""


@runtime_checkable
class Agent(Protocol):
    """What an implementation must provide to fill a role."""

    spec: AgentSpec

    async def run(self, ctx: StageContext) -> Any: ...


class AgentRegistry:
    """Role → implementation, with overrides.

    Deliberately a small mutable object rather than a module-level dict: a host
    embedding this in a server wants its own registry per tenant or per request,
    not a global everything shares.
    """

    def __init__(self, agents: dict[str, Agent] | None = None) -> None:
        self._agents: dict[str, Agent] = dict(agents or {})

    # -- construction ------------------------------------------------------

    @classmethod
    def default(cls) -> AgentRegistry:
        """The built-in agents. Imported lazily to avoid an import cycle."""
        from nexcraftviz.agents.builtin import build_default_agents

        return cls(build_default_agents())

    def clone(self) -> AgentRegistry:
        """A copy, so an override does not leak into another caller's registry."""
        return AgentRegistry(dict(self._agents))

    # -- registration ------------------------------------------------------

    def register(self, agent: Agent, *, replace: bool = False) -> AgentRegistry:
        role = agent.spec.role
        if role in self._agents and not replace:
            raise ValueError(
                f"role {role!r} is already filled by {self._agents[role].spec.name!r}; "
                f"pass replace=True or use override()"
            )
        self._agents[role] = agent
        return self

    def override(self, role: Role, agent: Agent) -> AgentRegistry:
        """Substitute an implementation. The agent's own role must match."""
        if agent.spec.role != role:
            raise ValueError(
                f"cannot register a {agent.spec.role!r} agent as {role!r} — "
                "an agent's spec declares the one role it fills"
            )
        self._agents[role] = agent
        return self

    # -- lookup ------------------------------------------------------------

    def get(self, role: Role) -> Agent:
        agent = self._agents.get(role)
        if agent is None:
            raise StageError(
                f"no agent registered for role {role!r}; "
                f"filled roles: {', '.join(sorted(self._agents)) or '(none)'}"
            )
        return agent

    def has(self, role: Role) -> bool:
        return role in self._agents

    @property
    def roles(self) -> list[str]:
        return sorted(self._agents)

    def describe(self) -> list[dict[str, Any]]:
        """Every filled role, for a `harness check` or a /v1/agents endpoint."""
        return [
            {
                "role": agent.spec.role,
                "name": agent.spec.name,
                "model_tier": agent.spec.model_tier if agent.spec.uses_llm else "",
                # A model id on an agent that never calls one is a lie a
                # `harness check` would print as fact.
                "model": agent.spec.model if agent.spec.uses_llm else "",
                "uses_llm": agent.spec.uses_llm,
                "skill": agent.spec.skill,
                "summary": agent.spec.summary,
            }
            for _, agent in sorted(self._agents.items())
        ]

    def needs_model(self) -> list[str]:
        """Roles that cannot run without a provider — what `check` reports."""
        return sorted(r for r, a in self._agents.items() if a.spec.uses_llm)

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, role: object) -> bool:
        return role in self._agents

    def __repr__(self) -> str:
        filled = ", ".join(f"{r}={a.spec.name}" for r, a in sorted(self._agents.items()))
        return f"<AgentRegistry {filled}>"
