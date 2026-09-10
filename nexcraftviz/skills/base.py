"""The portable skill contract.

Every skill here splits into three phases, and keeping them separate is what
lets the same code serve an agent that already owns a model *and* a hosted
service that owns its own:

1. :meth:`Skill.render_prompt` — pure. Builds the system and user text plus the
   JSON schema the model should be constrained to. No network, no model.
2. :meth:`Skill.parse` — pure. Turns raw model output into a typed result.
3. :meth:`Skill.apply` — pure. Does the actual work the model only *decided*:
   applies the operations, builds the spec, assembles the widget.

:meth:`Skill.run` chains all three when a model runner is supplied. An MCP
client, a Claude Code skill, a LangChain agent and a tool-calling loop all use
phases 1 and 3 and bring their own middle. Nothing in this module imports a
provider SDK, and nothing here reaches the network.

Prompts live in ``nexcraftviz/prompts/*.txt`` with a manifest, never as string
literals in Python, so they can be versioned, diffed and overridden without a
release.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Generic, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

PROMPT_DIR = Path(__file__).parent.parent / "prompts"

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)

#: What a model runner must look like: ``(system, user, schema) -> (payload,
#: metadata)`` — a plain shape, so an existing runner can be passed straight in.
LLMRunner = Callable[[str, str, dict[str, Any]], Awaitable[tuple[dict[str, Any], dict[str, Any]]]]


class SkillError(RuntimeError):
    """Raised when a skill cannot complete."""


@dataclass(frozen=True)
class Prompt:
    """Everything a caller needs to run the model themselves."""

    system: str
    user: str
    output_schema: dict[str, Any]
    prompt_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "user": self.user,
            "output_schema": self.output_schema,
            "prompt_version": self.prompt_version,
        }

    def as_openai_messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.user},
        ]


@dataclass
class SkillResult(Generic[OutputT]):
    """What a skill produced.

    ``output`` is what the model said; ``value`` is what that *did* — the new
    spec, the themed widget, the narration. Keeping both means a caller can
    show the reasoning and the result without re-deriving either.
    """

    skill: str
    output: OutputT | None = None
    value: Any = None
    changes: list[str] = field(default_factory=list)
    inverse: Any = None
    warnings: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.value is not None and not self.failed

    def summary(self) -> str:
        if self.failed:
            return f"{self.skill}: {len(self.failed)} step(s) failed"
        if not self.changes:
            return f"{self.skill}: no change"
        return f"{self.skill}: {'; '.join(self.changes[:3])}"


class SkillSpec(BaseModel):
    """A skill's public description — the basis of every tool schema."""

    name: str
    version: str = "1.0.0"
    summary: str
    uses_llm: bool = True
    prompt: str = ""

    model_config = {"extra": "forbid"}


class Skill(ABC, Generic[InputT, OutputT]):
    """Base class. Subclasses declare their models and implement the phases."""

    spec: SkillSpec
    Input: type[InputT]
    Output: type[OutputT]

    # -- phase 1 -----------------------------------------------------------

    def render_prompt(self, inputs: InputT | dict[str, Any]) -> Prompt:
        """Build the prompt. Pure — no model, no network."""
        parsed = self.coerce_input(inputs)
        system, version = self.system_prompt()
        return Prompt(
            system=system,
            user=self.user_payload(parsed),
            output_schema=self.output_schema(),
            prompt_version=version,
        )

    def system_prompt(self) -> tuple[str, str]:
        if not self.spec.prompt:
            raise SkillError(f"{self.spec.name} declares no prompt")
        return load_prompt(self.spec.prompt)

    def user_payload(self, inputs: InputT) -> str:
        """JSON by default — models follow a labelled object more reliably than
        prose, and it round-trips for logging and replay."""
        return json.dumps(inputs.model_dump(mode="json"), indent=2, default=str)

    @classmethod
    def output_schema(cls) -> dict[str, Any]:
        return cls.Output.model_json_schema()

    @classmethod
    def input_schema(cls) -> dict[str, Any]:
        return cls.Input.model_json_schema()

    # -- phase 2 -----------------------------------------------------------

    def parse(self, raw: dict[str, Any] | str | OutputT) -> OutputT:
        """Coerce raw model output into the typed result.

        Accepts a JSON string because plenty of tool-calling paths hand back
        exactly that, and failing on it would push the same three lines of
        boilerplate into every integration.
        """
        if isinstance(raw, self.Output):
            return raw
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                raise SkillError(f"{self.spec.name}: output is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise SkillError(f"{self.spec.name}: expected an object, got {type(raw).__name__}")
        try:
            return self.Output.model_validate(raw)
        except ValidationError as exc:
            raise SkillError(f"{self.spec.name}: {_terse(exc)}") from exc

    # -- phase 3 -----------------------------------------------------------

    @abstractmethod
    def apply(self, inputs: InputT, output: OutputT) -> SkillResult[OutputT]:
        """Do the work the model decided on. Pure, deterministic, testable."""

    # -- convenience -------------------------------------------------------

    async def run(
        self, inputs: InputT | dict[str, Any], llm: LLMRunner | None = None
    ) -> SkillResult[OutputT]:
        """Prompt → model → parse → apply.

        A skill that needs no model ignores ``llm`` entirely, which is how
        ``viz.theme`` and ``viz.recommend`` cost nothing.
        """
        parsed = self.coerce_input(inputs)

        if not self.spec.uses_llm:
            return self.apply(parsed, self.Output())  # type: ignore[call-arg]

        if llm is None:
            raise SkillError(
                f"{self.spec.name} needs a model. Either pass a runner, or use "
                f"render_prompt()/parse()/apply() and run the model yourself."
            )

        prompt = self.render_prompt(parsed)
        payload, meta = await llm(prompt.system, prompt.user, prompt.output_schema)
        result = self.apply(parsed, self.parse(payload))
        result.meta.update(meta or {})
        result.meta.setdefault("prompt_version", prompt.prompt_version)
        return result

    def coerce_input(self, inputs: InputT | dict[str, Any]) -> InputT:
        if isinstance(inputs, self.Input):
            return inputs
        if isinstance(inputs, dict):
            try:
                return self.Input.model_validate(inputs)
            except ValidationError as exc:
                raise SkillError(f"{self.spec.name}: {_terse(exc)}") from exc
        raise SkillError(
            f"{self.spec.name}: expected {self.Input.__name__} or a dict, "
            f"got {type(inputs).__name__}"
        )


# ---------------------------------------------------------------------------
# prompt loading
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def prompt_manifest() -> dict[str, Any]:
    path = PROMPT_DIR / "manifest.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=32)
def load_prompt(name: str) -> tuple[str, str]:
    """Return ``(text, version)`` for a named prompt.

    Overridable without a release: ``NEXCRAFTVIZ_PROMPT_DIR`` is checked first,
    so an operator can pin or patch a prompt in place.
    """
    import os

    override = os.getenv("NEXCRAFTVIZ_PROMPT_DIR", "").strip()
    roots = [Path(override)] if override else []
    roots.append(PROMPT_DIR)

    entry = prompt_manifest().get("prompts", {}).get(name, {})
    filename = entry.get("file") or f"{name}.txt"
    version = str(entry.get("version") or "1")

    for root in roots:
        candidate = root / filename
        if candidate.exists():
            _, body = split_headers(candidate.read_text(encoding="utf-8"))
            return body, f"{name}@{version}"

    raise SkillError(f"prompt {name!r} not found (looked for {filename} in {roots})")


def split_headers(text: str) -> tuple[dict[str, str], str]:
    """Separate a prompt's leading ``# key: value`` block from its body.

    The headers are metadata for us — the tier the agent registry asserts
    against, the version, when it was last touched — and are stripped before the
    text reaches a model. Leaving them in would mean a bookkeeping edit silently
    changes what the model is asked, which is how a prompt regresses without
    anyone touching the instructions.
    """
    headers: dict[str, str] = {}
    lines = text.splitlines()
    index = 0
    for index, line in enumerate(lines):  # noqa: B007 — index is used after the loop
        match = _HEADER_LINE.match(line)
        if not match:
            break
        headers[match.group(1).strip().lower()] = match.group(2).strip()
    else:
        index = len(lines)
    return headers, "\n".join(lines[index:]).strip()


_HEADER_LINE = re.compile(r"^#\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")


def _terse(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(p) for p in first.get("loc", ())) or "(root)"
    return f"{location}: {first.get('msg', 'invalid')}"
