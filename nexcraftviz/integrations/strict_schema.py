"""Make a Pydantic JSON Schema acceptable to OpenAI structured outputs.

Two rules trip up every schema generated from a model with optional fields:

1. **Every property must appear in ``required``.** Optionality is expressed by
   making the *type* nullable, not by omitting the key. A schema with
   ``reasoning`` optional is rejected outright.
2. **``additionalProperties`` must be ``false`` on every object.** Pydantic's
   ``extra="forbid"`` already does this, but nested ``$defs`` generated from
   unions do not always inherit it.

3. **No ``oneOf`` and no ``discriminator``.** Pydantic writes a discriminated
   union — every operation list here — as ``oneOf`` plus a ``discriminator``
   block, and strict mode refuses both ("'oneOf' is not permitted", confirmed
   against the live API). Until this rule existed, every skill whose output is
   a list of operations ran silently non-strict: the provider falls back to a
   looser binding rather than failing, so nothing said so.

4. **A ``$ref`` stands alone.** ``{"$ref": ..., "description": ...}`` is
   refused ("$ref cannot have keywords {'description'}", again from the live
   API). The reference goes inside an ``anyOf`` and its description stays on
   the wrapper, so the model still reads it.

5. **A union's tag comes first, and is never null.** Strict mode writes keys
   in schema order, and pydantic lists inherited fields first — so every
   view-scoped operation put ``view`` before ``op``. A model opening with
   ``"op"``, the natural first key, was then allowed only the branches whose
   first key is ``op``: the view-less ones. "Sort it descending" came back as
   ``set_size(null, null)`` and ``set_title("")``, and viz.edit fell from 10/10
   to 4/10 the moment it ran strict. The tag also stops being nullable — a tag
   that may be null identifies nothing.

The transformation is mechanical, so it lives here rather than distorting the
models: forcing every field to be required in Pydantic would make the *Python*
API worse to serve a provider's wire format, and the models are also the schema
an MCP client and a LangChain agent see.

The inverse matters too. A model told everything is required will send
``"needs_data": null``, and Pydantic rejects ``None`` for a ``str`` field with
a default — so :func:`drop_nulls` strips those before validation and the
defaults apply as intended.
"""
from __future__ import annotations

import copy
from typing import Any


def to_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Rewrite ``schema`` to satisfy OpenAI structured-output strict mode."""
    return _walk(copy.deepcopy(schema))


#: Keys whose value is a name-to-schema map rather than a schema. Walking the
#: map itself as a schema misfires on any field that happens to be named
#: "type", "oneOf" or "properties" — harmless so far only by luck.
_SCHEMA_MAPS = ("properties", "$defs", "definitions")


def _walk(node: Any) -> Any:
    if isinstance(node, list):
        return [_walk(item) for item in node]
    if not isinstance(node, dict):
        return node

    walked: dict[str, Any] = {}
    for key, value in node.items():
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            walked[key] = {name: _walk(sub) for name, sub in value.items()}
        else:
            walked[key] = _walk(value)
    node = walked

    # Rule 3. `anyOf` means the same thing here: every branch pins its own `op`
    # with a `const`, so at most one can ever match — and pydantic still parses
    # with its own discriminator, so nothing downstream changes. Pydantic only
    # emits `oneOf` for discriminated unions; a plain union is already `anyOf`.
    if "oneOf" in node:
        node["anyOf"] = [*node.get("anyOf", []), *node.pop("oneOf")]
    node.pop("discriminator", None)

    # Rule 4. `default` is dropped rather than moved: strict mode ignores it,
    # and a model-object default beside a reference is what broke this before.
    if "$ref" in node and len(node) > 1:
        siblings = {k: v for k, v in node.items() if k not in ("$ref", "default")}
        node = {"anyOf": [{"$ref": node["$ref"]}], **siblings}

    if node.get("type") == "object" and isinstance(node.get("properties"), dict):
        properties: dict[str, Any] = node["properties"]
        required = set(node.get("required", []))

        for name, definition in properties.items():
            if _is_tag(definition):
                # Rule 5: required, non-null, and no default to fall back on.
                definition.pop("default", None)
                if isinstance(definition.get("type"), list):
                    kinds = [k for k in definition["type"] if k != "null"]
                    definition["type"] = kinds[0] if len(kinds) == 1 else kinds
                continue
            if name not in required:
                properties[name] = _nullable(definition)

        # Rule 5: the tag first, then everything else in its original order.
        ordered = {n: d for n, d in properties.items() if _is_tag(d)}
        ordered.update({n: d for n, d in properties.items() if not _is_tag(d)})
        node["properties"] = ordered
        node["required"] = list(ordered)
        node["additionalProperties"] = False

    return node


def _is_tag(definition: Any) -> bool:
    """A property holding a `const` — the tag that says which branch this is."""
    return isinstance(definition, dict) and "const" in definition


def _nullable(definition: dict[str, Any]) -> dict[str, Any]:
    """Allow null, which is how strict mode expresses "optional".

    A ``$ref`` cannot carry sibling keywords, so it has to be wrapped in an
    ``anyOf`` rather than gaining a ``type`` — getting this wrong produces a
    schema the API accepts and then ignores.
    """
    if not isinstance(definition, dict):
        return definition
    if "anyOf" in definition:
        branches = definition["anyOf"]
        if not any(b.get("type") == "null" for b in branches if isinstance(b, dict)):
            definition["anyOf"] = [*branches, {"type": "null"}]
        return definition
    if "$ref" in definition:
        siblings = {k: v for k, v in definition.items() if k not in ("$ref", "default")}
        return {"anyOf": [{"$ref": definition["$ref"]}, {"type": "null"}], **siblings}

    declared = definition.get("type")
    if declared is None:
        return definition
    if isinstance(declared, list):
        if "null" not in declared:
            definition["type"] = [*declared, "null"]
        return definition
    if declared != "null":
        definition["type"] = [declared, "null"]
    return definition


def drop_nulls(payload: Any) -> Any:
    """Remove null-valued keys, recursively.

    Strict mode makes the model send every key, so absent values arrive as
    ``null``. Pydantic rejects ``None`` for a ``str`` field that has a default,
    so the nulls have to go before validation or every optional field becomes
    an error.
    """
    if isinstance(payload, dict):
        return {
            key: drop_nulls(value)
            for key, value in payload.items()
            if value is not None
        }
    if isinstance(payload, list):
        return [drop_nulls(item) for item in payload]
    return payload
