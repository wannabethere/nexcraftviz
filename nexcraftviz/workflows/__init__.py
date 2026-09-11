"""Skills defined as data: intents, workflows and steps in a YAML file.

A skill file says what a user can start (**intents**), and what happens then
(**workflows** of **steps**). Each step either runs an **action** from a fixed
vocabulary registered in code, pauses for a person (``ask``, ``select``,
``approve``), or hands a ``host.*`` step to the host — which owns the data and
the publishing. A new workflow is a change to data, not to code.

  schema.py    the file format
  refs.py      ``$`` references between steps
  loader.py    load and check a skill file before anything runs
  actions.py   the step vocabulary
  executor.py  run a skill: advance, pause, hand off, resume
  store.py     where runs wait between calls
  skills/      the skill files this package ships — ``dashboard.yaml``

See ``docs/DASHBOARD_SKILL.md``.
"""
from nexcraftviz.workflows.actions import ACTIONS, ActionContext, ActionError
from nexcraftviz.workflows.executor import Executor, Run, WorkflowError
from nexcraftviz.workflows.loader import (
    SkillFileError,
    builtin_skills,
    load_skill,
    parse_skill,
)
from nexcraftviz.workflows.schema import SkillFile
from nexcraftviz.workflows.store import RunStore

__all__ = [
    "ACTIONS",
    "ActionContext",
    "ActionError",
    "Executor",
    "Run",
    "RunStore",
    "SkillFile",
    "SkillFileError",
    "WorkflowError",
    "builtin_skills",
    "load_skill",
    "parse_skill",
]
