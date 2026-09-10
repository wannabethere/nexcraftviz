"""The OpenAI runner and the strict-schema transform — no network.

Every test here uses a fake client. The point is to pin the provider-specific
details that are easy to get wrong and expensive to discover live: strict-mode
schema shape, the nulls it forces back, the models that reject a temperature,
and the fallback chain when a binding is refused.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nexcraftviz.integrations.providers import (
    DEFAULT_OPENAI_MODEL,
    FIXED_TEMPERATURE_PREFIXES,
    ProviderError,
    default_runner,
    openai_runner,
)
from nexcraftviz.integrations.strict_schema import drop_nulls, to_strict_schema
from nexcraftviz.skills import get, names

SCHEMA = {
    "title": "EditOut",
    "type": "object",
    "properties": {"reasoning": {"type": "string"}, "needs_data": {"type": "string"}},
    "required": ["reasoning"],
}

REPLY = {
    "ops": [{"op": "sort_by", "channel": "x", "by": "revenue", "order": "descending"}],
    "reasoning": "North leads.",
    "needs_data": None,
}


class FakeClient:
    """Records calls and replays canned responses or errors."""

    def __init__(self, *, content: str | None = None, errors: list[Exception] | None = None):
        self.calls: list[dict] = []
        self._content = content if content is not None else json.dumps(REPLY)
        self._errors = list(errors or [])
        client = self

        class Completions:
            async def create(self, **kwargs):
                client.calls.append(kwargs)
                if client._errors:
                    raise client._errors.pop(0)
                return SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content=client._content)
                    )],
                    usage=SimpleNamespace(prompt_tokens=120, completion_tokens=40),
                )

        self.chat = SimpleNamespace(completions=Completions())


# ---------------------------------------------------------------------------
# strict schema
# ---------------------------------------------------------------------------

def _violations(schema, path="root", found=None):
    found = found if found is not None else []
    if isinstance(schema, dict):
        if schema.get("type") == "object" and "properties" in schema:
            props = set(schema["properties"])
            required = set(schema.get("required", []))
            if schema.get("additionalProperties") is not False:
                found.append(f"{path}: additionalProperties is not false")
            if props - required:
                found.append(f"{path}: not required: {sorted(props - required)}")
        for key, value in schema.items():
            _violations(value, f"{path}.{key}", found)
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            _violations(value, f"{path}[{index}]", found)
    return found


@pytest.mark.parametrize("name", [n for n in names() if get(n).spec.uses_llm])
def test_every_skill_schema_survives_strict_mode(name: str) -> None:
    """Pydantic leaves optional fields out of `required`; strict mode rejects that."""
    strict = to_strict_schema(get(name).output_schema())
    assert not _violations(strict), _violations(strict)[:3]


def test_optional_fields_become_nullable_rather_than_absent() -> None:
    strict = to_strict_schema(SCHEMA)
    assert set(strict["required"]) == {"reasoning", "needs_data"}
    assert "null" in strict["properties"]["needs_data"]["type"]
    assert strict["properties"]["reasoning"]["type"] == "string"


def test_a_ref_is_wrapped_rather_than_given_a_type() -> None:
    """A `$ref` cannot carry sibling keywords — getting this wrong produces a
    schema the API accepts and then ignores."""
    strict = to_strict_schema({
        "type": "object",
        "properties": {"point": {"$ref": "#/$defs/Point"}},
        "required": [],
    })
    assert "anyOf" in strict["properties"]["point"]
    assert {"type": "null"} in strict["properties"]["point"]["anyOf"]


def test_the_transform_does_not_mutate_its_input() -> None:
    original = json.dumps(SCHEMA, sort_keys=True)
    to_strict_schema(SCHEMA)
    assert json.dumps(SCHEMA, sort_keys=True) == original


def test_nulls_are_stripped_recursively() -> None:
    cleaned = drop_nulls({"a": None, "b": 1, "c": {"d": None, "e": [{"f": None, "g": 2}]}})
    assert cleaned == {"b": 1, "c": {"e": [{"g": 2}]}}


def test_a_skill_parses_output_that_strict_mode_shaped() -> None:
    """Strict mode makes the model send every key, so absent values arrive as
    null — and Pydantic rejects None for a str field with a default."""
    parsed = get("viz.edit").parse(drop_nulls(REPLY))
    assert parsed.needs_data == ""
    assert len(parsed.ops) == 1


# ---------------------------------------------------------------------------
# the runner
# ---------------------------------------------------------------------------

async def test_it_asks_for_strict_structured_output_first() -> None:
    client = FakeClient()
    payload, meta = await openai_runner(model="gpt-5-mini", client=client)("s", "u", SCHEMA)

    assert meta["structured_output"] == "json_schema_strict"
    request = client.calls[0]["response_format"]
    assert request["type"] == "json_schema"
    assert request["json_schema"]["strict"] is True
    assert payload["reasoning"] == "North leads."


async def test_nulls_are_stripped_before_the_caller_sees_them() -> None:
    payload, _ = await openai_runner(model="gpt-5-mini", client=FakeClient())("s", "u", SCHEMA)
    assert "needs_data" not in payload


@pytest.mark.parametrize("model", ["gpt-5-mini", "gpt-5", "o3-mini", "o1-preview"])
async def test_no_temperature_for_models_that_reject_one(model: str) -> None:
    """Sending one is a hard 400, not a warning."""
    client = FakeClient()
    await openai_runner(model=model, client=client, temperature=0.2)("s", "u", SCHEMA)
    assert "temperature" not in client.calls[0]


async def test_temperature_is_sent_for_models_that_accept_it() -> None:
    client = FakeClient()
    await openai_runner(model="gpt-4o-mini", client=client, temperature=0.2)("s", "u", SCHEMA)
    assert client.calls[0]["temperature"] == 0.2


def test_the_fixed_temperature_list_matches_the_provider() -> None:
    assert "gpt-5" in FIXED_TEMPERATURE_PREFIXES
    assert DEFAULT_OPENAI_MODEL == "gpt-5-mini"


async def test_a_rejected_binding_falls_back_rather_than_retrying() -> None:
    """Retrying a refused schema burns the budget; loosening the binding works."""
    client = FakeClient(errors=[
        RuntimeError("response_format json_schema is not supported for this model"),
        RuntimeError("invalid schema: strict mode unsupported"),
    ])
    _, meta = await openai_runner(model="gpt-5-mini", client=client)("s", "u", SCHEMA)

    assert meta["structured_output"] == "json_object"
    assert [c["response_format"]["type"] for c in client.calls] == [
        "json_schema", "json_schema", "json_object"
    ]


async def test_the_json_object_fallback_carries_the_schema_in_the_prompt() -> None:
    client = FakeClient(errors=[
        RuntimeError("response_format not supported"),
        RuntimeError("invalid schema"),
    ])
    await openai_runner(model="gpt-5-mini", client=client)("SYSTEM", "u", SCHEMA)

    system_text = client.calls[-1]["messages"][0]["content"]
    assert "SYSTEM" in system_text
    assert "OUTPUT SCHEMA" in system_text


async def test_a_transient_error_is_retried() -> None:
    client = FakeClient(errors=[RuntimeError("connection reset")])
    _, meta = await openai_runner(model="gpt-5-mini", client=client, max_retries=1)(
        "s", "u", SCHEMA
    )
    assert meta["retry_count"] == 1


async def test_markdown_fences_are_tolerated() -> None:
    client = FakeClient(content=f"```json\n{json.dumps(REPLY)}\n```")
    payload, _ = await openai_runner(model="gpt-5-mini", client=client)("s", "u", SCHEMA)
    assert payload["reasoning"] == "North leads."


async def test_unparseable_output_is_a_provider_error() -> None:
    client = FakeClient(content="I'm afraid I can't do that")
    with pytest.raises(ProviderError, match="not valid JSON"):
        await openai_runner(model="gpt-5-mini", client=client)("s", "u", SCHEMA)


async def test_an_empty_response_is_a_provider_error() -> None:
    with pytest.raises(ProviderError, match="empty"):
        await openai_runner(model="gpt-5-mini", client=FakeClient(content=""))("s", "u", SCHEMA)


async def test_persistent_failure_names_the_model() -> None:
    client = FakeClient(errors=[RuntimeError("boom")] * 10)
    with pytest.raises(ProviderError, match="gpt-5-mini"):
        await openai_runner(model="gpt-5-mini", client=client)("s", "u", SCHEMA)


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

def test_no_key_gives_an_actionable_error(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="propose"):
        openai_runner()


def test_default_runner_returns_none_without_a_key(monkeypatch) -> None:
    """So a server can start and report hosted_mode false honestly."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert default_runner() is None


def test_the_model_comes_from_the_common_env_var(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    client = FakeClient()
    openai_runner(client=client)
    # Resolution happens at construction; assert via a call.
    import asyncio

    asyncio.run(openai_runner(client=client)("s", "u", SCHEMA))
    assert client.calls[0]["model"] == "gpt-4o-mini"


def test_the_core_never_imports_a_provider_sdk() -> None:
    """The whole package must stay usable with no provider installed."""
    import pathlib
    import re

    # Module scope only, and anchored to a real import statement.
    #
    # The rule being enforced is "the package stays importable with no provider
    # SDK installed", so a lazy import inside a function is the *fix*, not a
    # violation — it is how every provider call here is written. Allowing any
    # leading whitespace flagged those; matching a substring also flagged
    # `from ...providers import openai_runner`, our own symbol.
    imports_sdk = re.compile(r"^(?:import\s+openai|from\s+openai)(?:[.\s]|$)", re.MULTILINE)

    root = pathlib.Path(__file__).parent.parent / "nexcraftviz"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name in ("providers.py",) or "__pycache__" in str(path):
            continue
        if imports_sdk.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(root)))
    assert not offenders, offenders
