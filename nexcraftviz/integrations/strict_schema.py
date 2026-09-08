"""Make a Pydantic JSON Schema acceptable to OpenAI structured outputs.

Two rules trip up every schema generated from a model with optional fields:

1. **Every property must appear in ``required``.** Optionality is expressed by
   making the *type* nullable, not by omitting the key. A schema with
   ``reasoning`` optional is rejected outright.
2. **``additionalProperties`` must be ``false`` on every object.** Pydantic's
   ``extra="forbid"`` already does this, but nested ``$defs`` generated from
   unions do not always inherit it.

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


def _walk(node: Any) -> Any:
    if isinstance(node, list):
        return [_walk(item) for item in node]
    if not isinstance(node, dict):
        return node

    node = {key: _walk(value) for key, value in node.items()}

    if node.get("type") == "object" and isinstance(node.get("properties"), dict):
        properties: dict[str, Any] = node["properties"]
        required = set(node.get("required", []))

        for name, definition in properties.items():
            if name not in required:
                properties[name] = _nullable(definition)

        node["required"] = list(properties)
        node["additionalProperties"] = False

    return node


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
        ref = {k: v for k, v in definition.items() if k != "default"}
        return {"anyOf": [ref, {"type": "null"}]}

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
