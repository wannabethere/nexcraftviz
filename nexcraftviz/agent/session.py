"""The conversation — one engine, two modes.

A charting conversation is: some rows, a document that evolves, and a history
you can step back through. That is the same whether the model lives in the
embedding tool or in a service here, so it is one class with two entry points:

**Skill mode** — the host owns the model::

    proposal = session.propose("sort worst first")   # prompt + schema, no network
    ...host runs its own model...
    turn = session.commit(proposal, model_output)    # deterministic

**Hosted mode** — nexcraftviz owns the model::

    turn = await session.turn("sort worst first", llm=runner)

`commit` is the whole of the second half of `turn`, so neither path is a
second-class citizen and there is no logic that only one of them exercises.

Undo is free: every skill returns an inverse patch, so the session keeps a
stack of them rather than snapshotting documents.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexcraftviz.agent.route import Route, route
from nexcraftviz.compose.widget import Widget
from nexcraftviz.data.profile import profile_rows
from nexcraftviz.skills import REGISTRY, SkillResult, get
from nexcraftviz.skills.base import LLMRunner, Prompt
from nexcraftviz.spec.diff import apply_patch
from nexcraftviz.spec.model import Spec

#: Skills whose result replaces the session document.
_DOCUMENT_SKILLS = {"viz.generate", "viz.edit", "viz.theme", "viz.place"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Proposal:
    """What a host needs to run the model itself."""

    turn_id: str
    skill: str
    route: Route
    prompt: Prompt | None
    inputs: dict[str, Any]

    @property
    def needs_model(self) -> bool:
        return self.prompt is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "skill": self.skill,
            "route": self.route.to_dict(),
            "needs_model": self.needs_model,
            "prompt": self.prompt.to_dict() if self.prompt else None,
        }


@dataclass
class Turn:
    """One exchange, kept so the conversation can be replayed or audited."""

    id: str
    message: str
    skill: str
    reply: str = ""
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    at: str = field(default_factory=_now)

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "message": self.message,
            "skill": self.skill,
            "reply": self.reply,
            "changes": self.changes,
            "warnings": self.warnings,
            "failed": [{"op": name, "reason": reason} for name, reason in self.failed],
            "ok": self.ok,
            "at": self.at,
        }


class Session:
    """A charting conversation."""

    def __init__(
        self,
        *,
        id: str = "",
        rows: list[dict[str, Any]] | None = None,
        document: Spec | Widget | None = None,
        theme: str = "nexcraftviz-light",
        language: str = "English",
    ) -> None:
        self.id = id or f"sess_{uuid.uuid4().hex[:12]}"
        self.rows = list(rows or [])
        self.document = document
        self.theme = theme
        self.language = language
        self.turns: list[Turn] = []
        self._undo: list[tuple[str, Any]] = []
        self._redo: list[tuple[str, Any]] = []

    # -- state -------------------------------------------------------------

    @property
    def has_chart(self) -> bool:
        return isinstance(self.document, Spec) and bool(self.document)

    @property
    def has_widget(self) -> bool:
        return isinstance(self.document, Widget)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def state(self) -> dict[str, Any]:
        """Everything a client needs to render the session."""
        document: Any = None
        kind = "empty"
        html = ""
        specs: dict[str, Any] = {}
        if isinstance(self.document, Widget):
            document, kind = self.document.to_dict(), "widget"
            # A widget's markup is rendered here so the tile vocabulary has one
            # implementation rather than one per client, and the specs travel
            # alongside it because injected <script> tags never execute.
            html = self.document.to_html()
            specs = self.document.chart_specs()
        elif isinstance(self.document, Spec) and self.document:
            document, kind = self.document.raw, "chart"

        return {
            "session_id": self.id,
            "kind": kind,
            "document": document,
            "html": html,
            "specs": specs,
            "theme": self.theme,
            "row_count": len(self.rows),
            "columns": profile_rows(self.rows).column_names,
            "turns": [t.to_dict() for t in self.turns],
            "can_undo": self.can_undo,
            "can_redo": self.can_redo,
        }

    # -- skill mode --------------------------------------------------------

    def propose(self, message: str, *, skill: str = "") -> Proposal:
        """Decide what to do and build the prompt. No model, no network.

        ``skill`` overrides the router, for a host that already knows what the
        user pressed — a "Explain this" button should not be re-derived from
        the text.
        """
        chosen = (
            Route(skill, "explicitly requested by the caller")
            if skill
            else route(message, has_chart=self.has_chart, has_widget=self.has_widget)
        )
        if chosen.skill not in REGISTRY:
            chosen = Route("viz.edit", f"unknown skill {chosen.skill!r}; falling back")

        handler = get(chosen.skill)
        inputs = self._inputs_for(chosen.skill, message)
        prompt = handler.render_prompt(inputs) if handler.spec.uses_llm else None

        return Proposal(
            turn_id=f"turn_{uuid.uuid4().hex[:8]}",
            skill=chosen.skill,
            route=chosen,
            prompt=prompt,
            inputs=inputs,
        )

    def commit(
        self, proposal: Proposal, model_output: dict[str, Any] | str | None = None
    ) -> Turn:
        """Apply a proposal's outcome to the session.

        ``model_output`` is whatever the host's model returned, and is ignored
        for skills that need no model — so a caller can pass it unconditionally.
        """
        handler = get(proposal.skill)
        parsed = self._inputs_for(proposal.skill, proposal.inputs.get("instruction", ""),
                                  raw=proposal.inputs)

        if handler.spec.uses_llm:
            if model_output is None:
                raise ValueError(f"{proposal.skill} needs model output to commit")
            output = handler.parse(model_output)
        else:
            output = handler.Output()

        result = handler.apply(handler.coerce_input(parsed), output)
        return self._record(proposal, result)

    # -- hosted mode -------------------------------------------------------

    async def turn(
        self, message: str, *, llm: LLMRunner | None = None, skill: str = ""
    ) -> Turn:
        """Route, run the model if one is needed, and apply the result."""
        proposal = self.propose(message, skill=skill)
        handler = get(proposal.skill)

        model_output: dict[str, Any] | None = None
        if handler.spec.uses_llm:
            if llm is None and proposal.skill == "viz.generate":
                # Creating a chart has a deterministic path, so a missing model
                # should degrade rather than fail: the rules pick the type and
                # the builder constructs it. A model refines that; it is not a
                # precondition for getting a chart at all.
                return self._build_without_model(proposal)
            if llm is None:
                raise ValueError(
                    f"{proposal.skill} needs a model. Pass `llm=`, or use "
                    "propose()/commit() and run the model yourself."
                )
            assert proposal.prompt is not None
            model_output, _ = await llm(
                proposal.prompt.system, proposal.prompt.user, proposal.prompt.output_schema
            )

        return self.commit(proposal, model_output)

    def _build_without_model(self, proposal: Proposal) -> Turn:
        """Build a chart from the shape rules, with no model involved."""
        from nexcraftviz.recommend.build import BuildError, build_best

        question = str(proposal.inputs.get("question") or "")
        try:
            spec, chart_type = build_best(self.rows, question=question)
        except BuildError as exc:
            turn = Turn(id=proposal.turn_id, message=question, skill=proposal.skill,
                        reply=str(exc), failed=[("viz.generate", str(exc))])
            self.turns.append(turn)
            return turn

        previous = self._document_dict()
        self.document = spec
        if previous:
            from nexcraftviz.spec.diff import diff

            self._undo.append(("chart", diff(spec.raw, previous)))
            self._redo.clear()

        turn = Turn(
            id=proposal.turn_id,
            message=question,
            skill=proposal.skill,
            reply=f"Built a {chart_type.replace('_', ' ')} chart from the data shape. "
                  f"Configure a model for a more considered chart and for editing.",
            changes=[f"built a {chart_type} chart"],
            warnings=["no model configured — this chart came from the shape rules"],
        )
        self.turns.append(turn)
        return turn

    # -- history -----------------------------------------------------------

    def undo(self) -> bool:
        """Step back one document change. Returns False when there is nothing to undo."""
        if not self._undo:
            return False
        kind, patch = self._undo.pop()
        current = self._document_dict()
        self.document = self._from_dict(kind, apply_patch(current, patch).raw)
        self._redo.append((kind, self._inverse_of(patch, current)))
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        kind, patch = self._redo.pop()
        current = self._document_dict()
        self.document = self._from_dict(kind, apply_patch(current, patch).raw)
        self._undo.append((kind, self._inverse_of(patch, current)))
        return True

    # -- internals ---------------------------------------------------------

    def _inputs_for(
        self, skill: str, message: str, raw: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Build the input payload for a skill from the session state."""
        if raw is not None:
            return raw

        base: dict[str, Any] = {"language": self.language}
        if skill == "viz.generate":
            return {**base, "question": message, "rows": self.rows}
        if skill == "viz.recommend":
            return {"question": message, "rows": self.rows}
        if skill == "viz.place":
            return {**base, "instruction": message, "widget": self.document}
        if skill == "viz.narrate":
            target, note = self._narration_target()
            payload = {**base, "question": message, "spec": target, "rows": self.rows}
            if note:
                payload["question"] = f"{message} ({note})"
            return payload
        if skill == "viz.theme":
            return {"spec": self._themable(), "theme": _theme_from(message, self.theme)}
        return {**base, "instruction": message, "spec": self.document, "rows": self.rows}

    def _record(self, proposal: Proposal, result: SkillResult) -> Turn:
        turn = Turn(
            id=proposal.turn_id,
            message=str(proposal.inputs.get("instruction")
                        or proposal.inputs.get("question") or ""),
            skill=proposal.skill,
            reply=_reply_from(result),
            changes=list(result.changes),
            warnings=list(result.warnings),
            failed=list(result.failed),
        )

        if proposal.skill in _DOCUMENT_SKILLS and result.value is not None:
            previous_kind = "widget" if self.has_widget else "chart"
            if isinstance(result.value, (Spec, Widget)):
                self.document = result.value
                if result.inverse:
                    self._undo.append((previous_kind, result.inverse))
                    self._redo.clear()
            if proposal.skill == "viz.theme":
                self.theme = proposal.inputs.get("theme", self.theme)

        self.turns.append(turn)
        return turn

    def _narration_target(self) -> tuple[Spec, str]:
        """What "what does this show?" should describe.

        A widget is several charts, and the narration prompt describes one. The
        honest resolution is to narrate the widget's principal chart and say
        which — rather than silently describing an arbitrary tile, or refusing
        a question that has an obvious answer.
        """
        if isinstance(self.document, Widget):
            charts = self.document.chart_tiles()
            if not charts:
                return Spec({}), ""
            first = charts[0]
            note = (
                f"describing the {first.title or first.id} tile"
                if len(charts) > 1
                else ""
            )
            return first.spec, note
        return self.document if isinstance(self.document, Spec) else Spec({}), ""

    def _themable(self) -> Any:
        """Theming a widget means theming its charts, which the widget renderer
        already does from CSS — so only a chart document is passed through."""
        return self.document if isinstance(self.document, Spec) else Spec({})

    def _document_dict(self) -> dict[str, Any]:
        if isinstance(self.document, Widget):
            return self.document.to_dict()
        if isinstance(self.document, Spec):
            return self.document.raw
        return {}

    @staticmethod
    def _from_dict(kind: str, raw: dict[str, Any]) -> Spec | Widget:
        return Widget.from_dict(raw) if kind == "widget" else Spec(raw)

    @staticmethod
    def _inverse_of(patch: Any, before: dict[str, Any]) -> Any:
        """The patch that undoes ``patch``, given the state it applied to."""
        from nexcraftviz.spec.diff import diff

        after = apply_patch(before, patch).raw
        return diff(after, before)


def _reply_from(result: SkillResult) -> str:
    """What the assistant says back.

    Prefers the model's own reasoning — it is written for the user — and falls
    back to the change list, which at least says what happened.
    """
    output = result.output
    for attribute in ("reasoning", "summary"):
        text = getattr(output, attribute, "") if output is not None else ""
        if text:
            return str(text)
    if result.failed:
        return "; ".join(f"{name}: {reason}" for name, reason in result.failed)
    if result.changes:
        return "; ".join(result.changes[:3])
    return "No change."


def _theme_from(message: str, current: str) -> str:
    """Read a theme name out of an instruction.

    Deliberately literal: theming is deterministic, and guessing "corporate" ->
    some preset would make the one free operation unpredictable.
    """
    from nexcraftviz.theme import available_themes

    lowered = message.lower()
    for name in available_themes():
        if name.lower() in lowered:
            return name
    if "dark" in lowered:
        return "nexcraftviz-dark"
    if "light" in lowered:
        return "nexcraftviz-light"
    if "power" in lowered and "bi" in lowered:
        return "powerbi"
    return current
