"""Run a skill: steps in order, pausing for people and for the host.

A run is a small state machine. It advances through its workflow until a step
needs something it cannot do itself:

* a **pause** — ``ask``, ``select`` or ``approve`` — waits for a person;
* a **host step** — ``host.query``, ``host.publish`` … — waits for the host,
  which owns the data and the publishing and does them its own way.

Either way the run comes back ``paused`` or ``needs_host`` with a ``pending``
block saying exactly what it needs; :meth:`Executor.resume` takes the answer
and carries on. A declined ``approve`` stops the run with nothing published.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from nexcraftviz.workflows import refs
from nexcraftviz.workflows.actions import ACTIONS, ActionContext, ActionError
from nexcraftviz.workflows.schema import Intent, SkillFile, Step, Workflow

Status = Literal["running", "paused", "needs_host", "done", "failed", "cancelled"]


class WorkflowError(ValueError):
    """A request that cannot be served: an unknown intent, a missing answer, a
    run that is not waiting for what it was given."""


def _no_artifacts() -> dict[str, Any]:
    return {"widgets": [], "dashboard": None, "published": None}


class Run(BaseModel):
    run_id: str
    skill: str
    intent: str
    workflow: str
    status: Status = "running"
    step: str = ""
    pending: dict[str, Any] | None = None
    answers: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    language: str = "English"
    results: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=_no_artifacts)
    trace: list[str] = Field(default_factory=list)
    error: str = ""
    position: int = 0
    updated: float = Field(default_factory=time.time)

    def public(self) -> dict[str, Any]:
        """What a host sees: the state, what is pending, what was built — not
        the executor's bookkeeping or the rows it was handed."""
        return self.model_dump(mode="json", exclude={"results", "position", "inputs", "answers"})


class Executor:
    def __init__(self, skills: dict[str, SkillFile], *, llm: Any = None) -> None:
        self.skills = skills
        self.llm = llm

    # -- starting and resuming ----------------------------------------------

    async def start(
        self,
        skill_name: str,
        intent_id: str,
        *,
        answers: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        language: str = "English",
    ) -> Run:
        skill = self.skills.get(skill_name)
        if skill is None:
            raise WorkflowError(f"no skill {skill_name!r}")
        intent = skill.intent(intent_id)
        if intent is None:
            known = ", ".join(i.id for i in skill.intents)
            raise WorkflowError(
                f"skill {skill_name!r} has no intent {intent_id!r}; it has: {known}"
            )

        workflow = skill.workflows[intent.workflow]
        run = Run(
            run_id=f"run_{uuid.uuid4().hex[:12]}",
            skill=skill_name,
            intent=intent_id,
            workflow=intent.workflow,
            answers=_check_answers(intent, dict(answers or {})),
            inputs=dict(inputs or {}),
            options={**workflow.options, **dict(options or {})},
            language=language,
        )
        run.trace.append(f"started: {intent.label}")
        await self._advance(run)
        return run

    async def resume(self, run: Run, *, answer: Any = None, host_result: Any = None) -> Run:
        if run.status not in ("paused", "needs_host"):
            raise WorkflowError(f"run {run.run_id} is {run.status}, not waiting for anything")
        step = self._workflow(run).steps[run.position]
        pending = run.pending or {}

        if run.status == "paused":
            value = _accept_answer(step, pending, answer)
            run.results[step.id] = value
            if step.pause == "approve" and not value:
                run.status, run.step, run.pending = "cancelled", "", None
                run.trace.append(f"{step.id}: not approved — stopped, nothing published")
                return run
            run.trace.append(f"{step.id}: {_describe_answer(step, value)}")
        else:
            run.results[step.id] = _accept_host_result(step, pending, host_result)
            if step.uses == "host.publish":
                run.artifacts["published"] = _raw_host_value(pending, host_result)
            run.trace.append(f"{step.id}: the host answered {step.uses}")

        run.pending = None
        run.position += 1
        await self._advance(run)
        return run

    # -- the loop -----------------------------------------------------------

    async def _advance(self, run: Run) -> None:
        run.status = "running"
        run.updated = time.time()
        workflow = self._workflow(run)
        while run.position < len(workflow.steps):
            step = workflow.steps[run.position]
            scope = self._scope(run)

            if step.when and not refs.resolve(step.when, scope):
                run.results[step.id] = None
                run.trace.append(f"{step.id}: skipped")
                run.position += 1
                continue

            if step.pause:
                run.status, run.step = "paused", step.id
                run.pending = {
                    "kind": step.pause,
                    "prompt": step.prompt,
                    "options": _options(refs.resolve(step.options, scope)),
                    "show": refs.resolve(step.show, scope),
                }
                run.trace.append(f"{step.id}: waiting — {step.prompt or step.pause}")
                return

            if step.is_host:
                requests = self._host_requests(step, scope)
                run.status, run.step = "needs_host", step.id
                run.pending = {"kind": "host", "action": step.uses, "requests": requests}
                run.trace.append(
                    f"{step.id}: waiting for the host — {step.uses} × {len(requests)}"
                )
                return

            ctx = ActionContext(llm=self.llm, language=run.language)
            try:
                value = await self._run_step(step, scope, run, ctx)
            except ActionError as exc:
                run.trace.extend(ctx.notes)
                run.status, run.step, run.error = "failed", step.id, str(exc)
                run.trace.append(f"{step.id}: failed — {exc}")
                return
            run.trace.extend(ctx.notes)
            run.results[step.id] = value
            run.trace.append(f"{step.id}: {_summarise(value)}")
            _collect(run, value)
            run.position += 1

        run.status, run.step, run.pending = "done", "", None
        run.trace.append("done")

    async def _run_step(
        self, step: Step, scope: dict[str, Any], run: Run, ctx: ActionContext
    ) -> Any:
        if step.for_each:
            items = _as_list(refs.resolve(step.for_each, scope))
            return [await self._call(step, {**scope, "item": item}, run, ctx) for item in items]
        return await self._call(step, scope, run, ctx)

    async def _call(self, step: Step, scope: dict[str, Any], run: Run, ctx: ActionContext) -> Any:
        args = refs.resolve(step.with_, scope)
        if step.sub_workflow:
            workflow = self.skills[run.skill].workflows[step.sub_workflow]
            return await self._run_sub(workflow, args, run, ctx)
        return await ACTIONS[step.uses](ctx, args)

    async def _run_sub(self, workflow: Workflow, args: dict[str, Any], run: Run,
                       ctx: ActionContext) -> Any:
        """A workflow called by another runs straight through — the loader
        forbids pauses and host steps in one — and returns its last step's result."""
        local: dict[str, Any] = {}
        scope = {"inputs": args, "options": {**workflow.options, **run.options},
                 "intent": run.answers, "steps": local, "item": None}
        for step in workflow.steps:
            if step.when and not refs.resolve(step.when, scope):
                local[step.id] = None
                continue
            local[step.id] = await self._run_step(step, scope, run, ctx)
        return local[workflow.steps[-1].id] if workflow.steps else None

    def _host_requests(self, step: Step, scope: dict[str, Any]) -> list[dict[str, Any]]:
        if step.for_each:
            items = _as_list(refs.resolve(step.for_each, scope))
            return [
                {"id": f"{step.id}-{index + 1}",
                 **(refs.resolve(step.with_, {**scope, "item": item}) or {})}
                for index, item in enumerate(items)
            ]
        return [{"id": step.id, **(refs.resolve(step.with_, scope) or {})}]

    def _scope(self, run: Run) -> dict[str, Any]:
        return {"inputs": run.inputs, "options": run.options, "intent": run.answers,
                "steps": run.results, "item": None}

    def _workflow(self, run: Run) -> Workflow:
        return self.skills[run.skill].workflows[run.workflow]


# ---------------------------------------------------------------------------
# answers and host results
# ---------------------------------------------------------------------------

def _check_answers(intent: Intent, answers: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(answers) - set(intent.asks))
    if unknown:
        raise WorkflowError(f"intent {intent.id!r} does not ask for: {', '.join(unknown)}")
    missing = [name for name, ask in intent.asks.items()
               if ask.required and answers.get(name) in (None, "", [], {})]
    if missing:
        needs = "; ".join(f"{name} — {intent.asks[name].prompt}" for name in missing)
        raise WorkflowError(f"intent {intent.id!r} needs: {needs}")
    for name, ask in intent.asks.items():
        value = answers.get(name)
        if value is None:
            continue
        if ask.type == "widgets" and isinstance(value, dict):
            answers[name] = [value]
        elif ask.type == "boolean":
            answers[name] = _truthy(value)
        elif ask.type == "choice" and ask.options and value not in ask.options:
            raise WorkflowError(f"{name} must be one of: {', '.join(ask.options)}")
    return answers


def _accept_answer(step: Step, pending: dict[str, Any], answer: Any) -> Any:
    if step.pause == "approve":
        if isinstance(answer, bool):
            return answer
        if isinstance(answer, str):
            return _truthy(answer)
        raise WorkflowError(f"step {step.id!r} needs yes or no")
    if step.pause == "select":
        options = pending.get("options") or []
        by_id = {option["id"]: option for option in options}
        wanted = [str(item) for item in _as_list(answer)]
        unknown = [item for item in wanted if item not in by_id]
        if unknown:
            raise WorkflowError(
                f"step {step.id!r}: no option {', '.join(unknown)}; the options are "
                f"{', '.join(by_id) or 'none'}"
            )
        return [option for option in options if option["id"] in set(wanted)]
    if answer is None or (isinstance(answer, str) and not answer.strip()):
        raise WorkflowError(f"step {step.id!r} needs an answer: {step.prompt}")
    return answer


def _accept_host_result(step: Step, pending: dict[str, Any], host_result: Any) -> Any:
    requests = pending.get("requests") or []
    if step.for_each:
        if not isinstance(host_result, dict):
            raise WorkflowError(f"step {step.id!r}: answer with an object keyed by request id")
        missing = [r["id"] for r in requests if r["id"] not in host_result]
        if missing:
            raise WorkflowError(f"step {step.id!r}: no result for {', '.join(missing)}")
        return [_merge(request, host_result[request["id"]]) for request in requests]
    request = requests[0] if requests else {"id": step.id}
    return _merge(request, _raw_host_value(pending, host_result))


def _raw_host_value(pending: dict[str, Any], host_result: Any) -> Any:
    requests = pending.get("requests") or []
    if len(requests) == 1 and isinstance(host_result, dict) and requests[0]["id"] in host_result:
        return host_result[requests[0]["id"]]
    return host_result


def _merge(request: dict[str, Any], value: Any) -> dict[str, Any]:
    """The request's own arguments with the host's answer on top, so a query
    result still says which question it answers."""
    args = {key: item for key, item in request.items() if key != "id"}
    if isinstance(value, dict):
        return {**args, **value}
    return {**args, "value": value}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _options(value: Any) -> list[dict[str, Any]]:
    options = []
    for index, option in enumerate(_as_list(value)):
        if isinstance(option, dict):
            rest = {key: item for key, item in option.items() if key != "id"}
            options.append({"id": str(option.get("id") or f"o{index + 1}"), **rest})
        elif option is not None:
            options.append({"id": str(option), "text": str(option)})
    return options


def _collect(run: Run, value: Any) -> None:
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        if item.get("kind") == "nexcraftviz.widget":
            run.artifacts["widgets"].append(item)
        elif item.get("kind") == "nexcraftviz.dashboard":
            run.artifacts["dashboard"] = item


def _summarise(value: Any) -> str:
    if value is None:
        return "nothing to do"
    if isinstance(value, list):
        widgets = sum(1 for v in value
                      if isinstance(v, dict) and v.get("kind") == "nexcraftviz.widget")
        if widgets:
            return f"{widgets} widget(s) built"
        if value and all(isinstance(v, dict) and "text" in v for v in value):
            return f"{len(value)} suggested"
        return f"{len(value)} result(s)"
    if isinstance(value, dict):
        if value.get("kind") == "nexcraftviz.widget":
            return "widget built"
        if value.get("kind") == "nexcraftviz.dashboard":
            return f"dashboard laid out — {len(value.get('layout') or [])} widget(s)"
    return "done"


def _describe_answer(step: Step, value: Any) -> str:
    if step.pause == "select":
        return f"{len(value)} selected"
    if step.pause == "approve":
        return "approved"
    return "answered"


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("yes", "y", "true", "1", "approve", "approved")
    return bool(value)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]
