"""``$`` references: where a step's inputs come from.

A value that is exactly ``$root`` or ``$root.path`` is a reference; anything
else is a literal. The roots:

  $inputs.x    an input of the workflow (given by the caller, or by the step
               that called this workflow)
  $options.x   an option — the workflow's defaults, overridden per run
  $intent.x    an answer the user gave when starting the intent
  $steps.x     an earlier step's result; ``$steps.x.field`` reaches inside it
  $item        the current element of a ``for_each``
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

ROOTS = ("inputs", "options", "intent", "steps", "item")

_REF = re.compile(r"^\$(inputs|options|intent|steps|item)(?:\.([A-Za-z0-9_\-.]+))?$")


def parse(value: Any) -> tuple[str, list[str]] | None:
    """``("steps", ["data", "rows"])`` for ``"$steps.data.rows"``; None for a literal."""
    if not isinstance(value, str):
        return None
    match = _REF.match(value.strip())
    if match is None:
        return None
    return match.group(1), match.group(2).split(".") if match.group(2) else []


def refs_in(value: Any) -> Iterator[tuple[str, list[str]]]:
    """Every reference anywhere inside ``value``."""
    if isinstance(value, dict):
        for inner in value.values():
            yield from refs_in(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from refs_in(inner)
    else:
        ref = parse(value)
        if ref is not None:
            yield ref


def resolve(value: Any, scope: dict[str, Any]) -> Any:
    """``value`` with every reference replaced by what it points at.

    A path that leads nowhere resolves to None rather than raising: a step a
    ``when`` skipped has no result, and whatever reads it gets nothing.
    """
    if isinstance(value, dict):
        return {key: resolve(inner, scope) for key, inner in value.items()}
    if isinstance(value, list):
        return [resolve(inner, scope) for inner in value]
    ref = parse(value)
    if ref is None:
        return value
    root, path = ref
    current = scope.get(root)
    for part in path:
        current = _step_into(current, part)
        if current is None:
            return None
    return current


def _step_into(value: Any, part: str) -> Any:
    if isinstance(value, dict):
        return value.get(part)
    if isinstance(value, list) and part.isdigit():
        index = int(part)
        return value[index] if index < len(value) else None
    return getattr(value, part, None)
