"""Load a skill file, and refuse one that is wrong before anything runs.

Every problem is collected and reported together, each naming the workflow
and step — a skill file is written by a person, and fixing one error only to
meet the next is a poor way to spend an afternoon.

The rules:

* every ``uses`` is a registered action, a ``host.*`` step, or
  ``workflow.NAME`` for a workflow in the file;
* ``$steps.x`` names an earlier step; ``$inputs.x`` an input and
  ``$options.x`` an option of the workflow; ``$item`` only inside a
  ``for_each``; ``$intent.x`` an ask of every intent that starts the workflow;
* ``when`` and ``for_each`` are references;
* a workflow another workflow calls runs straight through — no pauses and no
  host steps, which only the top level can wait for;
* **``host.publish`` follows an ``approve`` pause** — nothing is published
  without a person saying yes;
* workflows do not call each other in a cycle.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from nexcraftviz.workflows import refs
from nexcraftviz.workflows.schema import SkillFile, Step, Workflow

SKILLS_DIR = Path(__file__).parent / "skills"


class SkillFileError(ValueError):
    """A skill file that cannot be run as written."""

    def __init__(self, source: str, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{source}: " + "; ".join(problems))


def parse_skill(text: str, *, source: str = "<skill>") -> SkillFile:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SkillFileError(source, [f"not valid YAML: {exc}"]) from exc
    try:
        skill = SkillFile.model_validate(data)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        ]
        raise SkillFileError(source, problems) from exc
    problems = validate(skill)
    if problems:
        raise SkillFileError(source, problems)
    return skill


def load_skill(path: str | Path) -> SkillFile:
    path = Path(path)
    return parse_skill(path.read_text(encoding="utf-8"), source=str(path))


def builtin_skills() -> dict[str, SkillFile]:
    """The skill files this package ships, by skill name."""
    skills: dict[str, SkillFile] = {}
    for path in sorted(SKILLS_DIR.glob("*.yaml")):
        skill = load_skill(path)
        skills[skill.skill] = skill
    return skills


def validate(skill: SkillFile) -> list[str]:
    from nexcraftviz.workflows.actions import ACTIONS

    problems: list[str] = []
    seen_intents: set[str] = set()
    for intent in skill.intents:
        if intent.id in seen_intents:
            problems.append(f"intent {intent.id!r} is defined twice")
        seen_intents.add(intent.id)
        if intent.workflow not in skill.workflows:
            problems.append(f"intent {intent.id!r} runs unknown workflow {intent.workflow!r}")

    called = {
        step.sub_workflow
        for workflow in skill.workflows.values()
        for step in workflow.steps
        if step.sub_workflow
    }
    for name, workflow in skill.workflows.items():
        problems.extend(_check_workflow(skill, name, workflow, set(ACTIONS), called))
    problems.extend(_cycles(skill))
    return problems


def _check_workflow(
    skill: SkillFile, name: str, workflow: Workflow, actions: set[str], called: set[str]
) -> list[str]:
    problems: list[str] = []
    intents = [intent for intent in skill.intents if intent.workflow == name]
    earlier: list[str] = []
    approved = False

    for step in workflow.steps:
        at = f"workflow {name!r} step {step.id!r}"
        if step.id in earlier:
            problems.append(f"{at}: the id is used twice")

        if step.sub_workflow:
            if step.sub_workflow not in skill.workflows:
                problems.append(f"{at}: no workflow {step.sub_workflow!r}")
        elif step.uses and not step.is_host and step.uses not in actions:
            problems.append(
                f"{at}: unknown action {step.uses!r} — known: {', '.join(sorted(actions))}, "
                f"or host.* for the host"
            )

        if name in called and (step.pause or step.is_host):
            problems.append(
                f"{at}: a workflow other workflows call runs straight through — "
                f"no pauses and no host steps"
            )

        if step.pause == "approve":
            approved = True
        if step.uses == "host.publish" and not approved:
            problems.append(
                f"{at}: host.publish must follow an approve pause — nothing is "
                f"published without a person saying yes"
            )

        for field, value in (("when", step.when), ("for_each", step.for_each)):
            if value and refs.parse(value) is None:
                problems.append(f"{at}: `{field}` must be a $ reference, not {value!r}")

        for root, path in _references(step):
            first = path[0] if path else ""
            if root == "steps" and first not in earlier:
                problems.append(f"{at}: $steps.{first} is not an earlier step")
            elif root == "inputs" and first and first not in workflow.inputs:
                problems.append(f"{at}: $inputs.{first} is not an input of {name!r}")
            elif root == "options" and first and first not in workflow.options:
                problems.append(f"{at}: $options.{first} is not an option of {name!r}")
            elif root == "item" and not step.for_each:
                problems.append(f"{at}: $item is only meaningful inside a for_each")
            elif root == "intent":
                if not intents:
                    problems.append(f"{at}: $intent.{first} in a workflow no intent starts")
                for intent in intents:
                    if first and first not in intent.asks:
                        problems.append(
                            f"{at}: $intent.{first} is not an ask of intent {intent.id!r}"
                        )
        earlier.append(step.id)
    return problems


def _references(step: Step) -> Iterator[tuple[str, list[str]]]:
    for value in (step.with_, step.when, step.for_each, step.options, step.show):
        yield from refs.refs_in(value)


def _cycles(skill: SkillFile) -> list[str]:
    calls: dict[str, set[str]] = {
        name: {step.sub_workflow for step in workflow.steps if step.sub_workflow}
        for name, workflow in skill.workflows.items()
    }
    problems: list[str] = []

    def visit(name: str, path: list[str]) -> None:
        for callee in calls.get(name, ()):
            if callee in path:
                cycle = " → ".join([*path, callee])
                problems.append(f"workflows call each other in a cycle: {cycle}")
                continue
            visit(callee, [*path, callee])

    for name in calls:
        visit(name, [name])
    return sorted(set(problems))


def describe(value: Any) -> str:  # pragma: no cover - debugging aid
    return yaml.safe_dump(value, sort_keys=False)
