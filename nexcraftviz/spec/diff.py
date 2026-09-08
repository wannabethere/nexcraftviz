"""JSON-Patch diffing between specs — the mechanism behind undo and change summaries.

Operations in :mod:`nexcraftviz.spec.ops` do not each hand-write an inverse.
Instead the engine snapshots the spec, applies the op, and diffs: the reverse
patch *is* the undo. That keeps every op honest — an op cannot forget to
implement its inverse, and a new op gets undo for free.

A subset of RFC 6902 (``add`` / ``remove`` / ``replace``) is enough here; we
never need ``move``, ``copy`` or ``test``, and leaving them out keeps the patch
readable when it is shown to a user as "what changed".
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Literal

from nexcraftviz.spec.model import Spec

PatchOpKind = Literal["add", "remove", "replace"]

_MISSING = object()


@dataclass(frozen=True)
class PatchOp:
    """One RFC-6902-style operation. ``path`` is a JSON Pointer."""

    op: PatchOpKind
    path: str
    value: Any = None
    old_value: Any = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"op": self.op, "path": self.path}
        if self.op != "remove":
            out["value"] = self.value
        return out

    def describe(self) -> str:
        """Human-readable one-liner, for change summaries in the UI."""
        where = self.path.lstrip("/").replace("/", ".") or "(root)"
        if self.op == "add":
            return f"set {where} = {_short(self.value)}"
        if self.op == "remove":
            return f"removed {where}"
        return f"{where}: {_short(self.old_value)} → {_short(self.value)}"


Patch = list[PatchOp]


def diff(before: Spec | dict[str, Any], after: Spec | dict[str, Any]) -> Patch:
    """Compute the patch that turns ``before`` into ``after``."""
    a = before.raw if isinstance(before, Spec) else before
    b = after.raw if isinstance(after, Spec) else after
    ops: Patch = []
    _diff_node(a, b, "", ops)
    return ops


def _diff_node(a: Any, b: Any, path: str, ops: Patch) -> None:
    if a is b or a == b:
        return

    if isinstance(a, dict) and isinstance(b, dict):
        for key in a:
            child = f"{path}/{_escape(str(key))}"
            if key not in b:
                ops.append(PatchOp("remove", child, old_value=a[key]))
            else:
                _diff_node(a[key], b[key], child, ops)
        for key in b:
            if key not in a:
                ops.append(PatchOp("add", f"{path}/{_escape(str(key))}", value=b[key]))
        return

    if isinstance(a, list) and isinstance(b, list):
        # Positional diff. Arrays in Vega-Lite are ordered and short (layers,
        # transforms, tooltip fields), so index-wise comparison reads better in
        # a change summary than a longest-common-subsequence result would.
        for idx in range(min(len(a), len(b))):
            _diff_node(a[idx], b[idx], f"{path}/{idx}", ops)
        # Direction matters, and it differs between the two cases. Appends are
        # emitted ascending so each insert lands at an index that already
        # exists; truncations are emitted descending so removing one does not
        # shift the next. Getting either backwards still applies correctly but
        # produces an inverse that does not — which `invert` relies on, since
        # it only reverses the order and flips each op.
        for idx in range(len(a), len(b)):
            ops.append(PatchOp("add", f"{path}/{idx}", value=b[idx]))
        for idx in range(len(a) - 1, len(b) - 1, -1):
            ops.append(PatchOp("remove", f"{path}/{idx}", old_value=a[idx]))
        return

    ops.append(PatchOp("replace", path, value=b, old_value=a))


def invert(patch: Patch) -> Patch:
    """The patch that undoes ``patch``.

    Applied in reverse order, so index shifts from list adds and removes unwind
    in the order they were made.
    """
    inverted: Patch = []
    for op in reversed(patch):
        if op.op == "add":
            inverted.append(PatchOp("remove", op.path, old_value=op.value))
        elif op.op == "remove":
            inverted.append(PatchOp("add", op.path, value=op.old_value))
        else:
            inverted.append(PatchOp("replace", op.path, value=op.old_value, old_value=op.value))
    return inverted


def apply_patch(spec: Spec | dict[str, Any], patch: Patch) -> Spec:
    """Apply ``patch`` to a clone of ``spec`` and return the result."""
    raw = copy.deepcopy(spec.raw if isinstance(spec, Spec) else spec)
    for op in patch:
        _apply_one(raw, op)
    return Spec(raw)


def _apply_one(root: Any, op: PatchOp) -> None:
    tokens = _parse_pointer(op.path)
    if not tokens:
        raise ValueError("cannot patch the document root in place")

    parent = root
    for token in tokens[:-1]:
        parent = _descend(parent, token)
        if parent is _MISSING:
            raise KeyError(f"patch path not found: {op.path}")

    last = tokens[-1]
    if isinstance(parent, list):
        idx = len(parent) if last == "-" else int(last)
        if op.op == "add":
            parent.insert(min(idx, len(parent)), op.value)
        elif op.op == "remove":
            if 0 <= idx < len(parent):
                parent.pop(idx)
        else:
            if 0 <= idx < len(parent):
                parent[idx] = op.value
    elif isinstance(parent, dict):
        if op.op == "remove":
            parent.pop(last, None)
        else:
            parent[last] = op.value
    else:
        raise TypeError(f"cannot patch through a {type(parent).__name__} at {op.path}")


def _descend(node: Any, token: str) -> Any:
    if isinstance(node, list):
        try:
            idx = int(token)
        except ValueError:
            return _MISSING
        return node[idx] if 0 <= idx < len(node) else _MISSING
    if isinstance(node, dict):
        return node.get(token, _MISSING)
    return _MISSING


def _parse_pointer(pointer: str) -> list[str]:
    if not pointer or pointer == "/":
        return []
    return [_unescape(part) for part in pointer.lstrip("/").split("/")]


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _short(value: Any, limit: int = 40) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"
