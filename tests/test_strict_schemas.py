"""Every model-facing schema survives OpenAI's strict mode.

One untyped field anywhere drops a whole schema to non-strict, and it happens
silently: the provider falls back to a looser binding rather than failing. In
non-strict mode the structure is only loosely enforced. A live run of viz.plan
came back with a field placed where it does not exist, and the pipeline crashed
on it — nothing was wrong with the chart; the binding was.

These run offline and would have caught it before the first live call.
"""
from __future__ import annotations

import os
from typing import Any

import pytest

from nexcraftviz.integrations.strict_schema import to_strict_schema
from nexcraftviz.skills import REGISTRY

_TYPED = ("type", "anyOf", "$ref", "enum", "const", "allOf", "oneOf")

MODEL_BACKED = sorted(name for name, skill in REGISTRY.items() if skill.spec.uses_llm)


def _schemas(node: Any, path: str = "$"):
    """Every schema node, reached through schema-bearing keys only.

    A field map — the value of `properties` — is a name-to-schema dict, not a
    schema. Walking it as one misfires on any field that happens to be named
    "properties", and misses free-form objects that declare no fields at all.
    The first version of this test did both: it flagged SetMark at the wrong
    path and passed viz.place, which a live call showed was running non-strict.
    """
    if not isinstance(node, dict):
        return
    yield path, node
    for name, sub in (node.get("properties") or {}).items():
        yield from _schemas(sub, f"{path}.{name}")
    for name, sub in (node.get("$defs") or {}).items():
        yield from _schemas(sub, f"{path}.$defs.{name}")
    if isinstance(node.get("items"), dict):
        yield from _schemas(node["items"], f"{path}[]")
    for key in ("anyOf", "oneOf", "allOf"):
        for index, sub in enumerate(node.get(key) or []):
            yield from _schemas(sub, f"{path}.{key}[{index}]")
    if isinstance(node.get("additionalProperties"), dict):
        yield from _schemas(node["additionalProperties"], f"{path}.<values>")


def _is_object(schema: dict[str, Any]) -> bool:
    kind = schema.get("type")
    return kind == "object" or (isinstance(kind, list) and "object" in kind) or (
        "properties" in schema
    )


def _untyped(strict: dict[str, Any]) -> list[str]:
    """Schemas with no type at all — what strict mode refuses."""
    return [
        path for path, schema in _schemas(strict)
        if path != "$" and not any(k in schema for k in _TYPED)
    ]


def _open_objects(strict: dict[str, Any]) -> list[str]:
    """Objects that accept keys they do not declare — also refused."""
    return [
        f"{path} (additionalProperties={schema.get('additionalProperties')!r})"
        for path, schema in _schemas(strict)
        if _is_object(schema) and schema.get("additionalProperties") is not False
    ]


#: JSON-schema keywords OpenAI strict mode was seen to ACCEPT, each confirmed
#: live on 2026-09-10 — `const`, `minItems` and `exclusiveMinimum` by direct
#: probe, the rest inside schemas it accepted. `oneOf` and `discriminator` were
#: refused. Add a keyword here only after probing it against the live API: this
#: list is the difference between a schema being enforced and silently not.
_ACCEPTED_KEYWORDS = frozenset({
    "type", "properties", "required", "additionalProperties", "items", "anyOf",
    "enum", "const", "$ref", "$defs", "default", "description", "title",
    "minimum", "maximum", "exclusiveMinimum", "minItems",
})


def test_the_model_backed_skills_are_all_here():
    """The parametrised tests below are only as good as this list."""
    assert len(MODEL_BACKED) >= 8


@pytest.mark.parametrize("name", MODEL_BACKED)
def test_every_field_is_typed_so_strict_mode_accepts_the_schema(name):
    strict = to_strict_schema(REGISTRY[name].output_schema())
    holes = _untyped(strict)
    assert not holes, f"{name} would silently fall back to non-strict: {holes}"


@pytest.mark.parametrize("name", MODEL_BACKED)
def test_every_object_forbids_extra_properties(name):
    """What strict mode actually enforces, and what stops a model placing a
    field somewhere it does not exist. A free-form `dict[str, Any]` fails this:
    it declares no fields, so it goes out open, and strict mode refuses it."""
    strict = to_strict_schema(REGISTRY[name].output_schema())
    loose = _open_objects(strict)
    assert not loose, f"{name} would silently fall back to non-strict: {loose}"


@pytest.mark.parametrize("name", MODEL_BACKED)
def test_only_keywords_strict_mode_accepts(name):
    """Types and closed objects are not the whole story: viz.place passed both
    checks above and was still refused, for using `oneOf`. Only a keyword
    allow-list catches that class of refusal before a live call does."""
    strict = to_strict_schema(REGISTRY[name].output_schema())
    used = {key for _, schema in _schemas(strict) for key in schema}
    refused = sorted(used - _ACCEPTED_KEYWORDS)
    assert not refused, (
        f"{name} uses keywords strict mode was not seen to accept: {refused}"
    )


@pytest.mark.parametrize("name", MODEL_BACKED)
def test_no_reference_carries_sibling_keywords(name):
    """OpenAI's words for viz.edit, after every check above had passed:
    "$ref cannot have keywords {'description'}". Each keyword was allowed on its
    own; the combination was not, and no per-keyword check can see that."""
    strict = to_strict_schema(REGISTRY[name].output_schema())
    crowded = [
        f"{path}: {sorted(set(schema) - {'$ref'})}"
        for path, schema in _schemas(strict)
        if "$ref" in schema and len(schema) > 1
    ]
    assert not crowded, f"{name}: references with sibling keywords: {crowded}"


# ---------------------------------------------------------------------------
# the check that actually found every hole: ask OpenAI
# ---------------------------------------------------------------------------

_LIVE = os.getenv("NEXCRAFTVIZ_LIVE_EVAL", "").strip().lower() in ("1", "true", "yes")


@pytest.mark.skipif(not _LIVE, reason="set NEXCRAFTVIZ_LIVE_EVAL=1 and OPENAI_API_KEY")
@pytest.mark.asyncio
@pytest.mark.parametrize("name", MODEL_BACKED)
async def test_openai_accepts_every_schema_in_strict_mode(name):
    """The offline checks above are necessary and were not sufficient: three
    separate refusals got past them, each found only by asking the API. A
    refusal costs nothing — the request fails before any generation — so this
    is cheap enough to run before every release."""
    from nexcraftviz import env

    env.load()
    from openai import AsyncOpenAI

    strict = to_strict_schema(REGISTRY[name].output_schema())
    client = AsyncOpenAI()
    try:
        await client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
            messages=[{"role": "user", "content": "Return an empty result."}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "Probe", "schema": strict, "strict": True}},
            max_completion_tokens=16,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"{name} is refused in strict mode, so it would run silently "
                    f"non-strict: {exc}")


@pytest.mark.parametrize("name", MODEL_BACKED)
def test_a_union_tag_comes_first_and_is_never_null(name):
    """Strict mode writes keys in schema order. With `view` before `op`, a model
    that opened with "op" could only reach the view-less branches, and "sort it
    descending" came back as set_title(""). viz.edit went from 10/10 to 4/10 —
    every other check here passed."""
    strict = to_strict_schema(REGISTRY[name].output_schema())
    problems: list[str] = []
    for path, schema in _schemas(strict):
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            continue
        names = list(properties)
        for tag in (n for n, d in properties.items() if isinstance(d, dict) and "const" in d):
            if names.index(tag) != 0:
                problems.append(f"{path}.{tag} is key #{names.index(tag) + 1}, not first")
            kind = properties[tag].get("type")
            if kind == "null" or (isinstance(kind, list) and "null" in kind):
                problems.append(f"{path}.{tag} is nullable")
    assert not problems, f"{name}: {problems}"
