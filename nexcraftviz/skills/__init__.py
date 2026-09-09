"""Skills — the portable unit of work.

Each splits into render_prompt / parse / apply, so an agent that already owns a
model uses phases 1 and 3 and brings its own middle, while a hosted service
calls `run()` and gets all three. See :mod:`nexcraftviz.skills.base`.
"""
from nexcraftviz.skills.base import (
    LLMRunner,
    Prompt,
    Skill,
    SkillError,
    SkillResult,
    SkillSpec,
    load_prompt,
)
from nexcraftviz.skills.compose import ComposeSkill
from nexcraftviz.skills.create import (
    GenerateSkill,
    NarrateSkill,
    RecommendSkill,
    ThemeSkill,
)
from nexcraftviz.skills.edit import EditSkill, PlaceSkill
from nexcraftviz.skills.manage import ManageSkill
from nexcraftviz.skills.plan import CritiqueSkill, PlanSkill

#: Every skill, by name. The basis of the tool schemas, the MCP server and the
#: Claude Code skill — one registry, three front doors.
REGISTRY: dict[str, Skill] = {
    skill.spec.name: skill
    for skill in (
        RecommendSkill(),
        PlanSkill(),
        ManageSkill(),
        GenerateSkill(),
        CritiqueSkill(),
        EditSkill(),
        PlaceSkill(),
        ComposeSkill(),
        NarrateSkill(),
        ThemeSkill(),
    )
}


def get(name: str) -> Skill:
    """Look up a skill, with a useful error when the name is wrong."""
    try:
        return REGISTRY[name]
    except KeyError:
        raise SkillError(
            f"unknown skill {name!r}; available: {', '.join(sorted(REGISTRY))}"
        ) from None


def names() -> list[str]:
    return sorted(REGISTRY)


__all__ = [
    "REGISTRY",
    "ComposeSkill",
    "CritiqueSkill",
    "EditSkill",
    "GenerateSkill",
    "LLMRunner",
    "ManageSkill",
    "NarrateSkill",
    "Prompt",
    "PlanSkill",
    "RecommendSkill",
    "Skill",
    "SkillError",
    "SkillResult",
    "SkillSpec",
    "ThemeSkill",
    "PlaceSkill",
    "get",
    "load_prompt",
    "names",
]
