"""Regenerate the Claude Code skill's operation reference from the code.

Run via `make skill-reference`. Keeping this generated means the reference an
agent reads can never drift from the operations the appliers actually accept.
"""
from __future__ import annotations

import pathlib

from nexcraftviz.compose.ops import WIDGET_OP_REGISTRY
from nexcraftviz.spec.ops import OP_REGISTRY
from nexcraftviz.theme import available_themes

_ROOT = pathlib.Path(__file__).parent.parent
OUT = _ROOT / "skills" / "nexcraftviz" / "references" / "operations.md"


def section(registry: dict, title: str, note: str) -> str:
    lines = [f"## {title}", "", note, ""]
    for name in sorted(registry):
        cls = registry[name]
        doc = (cls.__doc__ or "").strip().splitlines()
        lines.append(f"### `{name}`")
        lines.append("")
        if doc:
            lines.append(doc[0])
            lines.append("")
        for field, info in cls.model_fields.items():
            if field == "op":
                continue
            required = "required" if info.is_required() else "optional"
            lines.append(f"- `{field}` ({required}) {info.description or ''}".rstrip())
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    body = (
        "# Operations reference\n\n"
        "GENERATED from the code by `make skill-reference`. Do not edit by hand.\n\n"
        + section(
            OP_REGISTRY,
            "Chart operations",
            "Applied with `viz_apply_ops`. `view` selects one leaf view of a "
            "layered chart; omit it for the default target.",
        )
        + section(
            WIDGET_OP_REGISTRY,
            "Layout operations",
            "Applied with `viz_apply_layout`. Spans: quarter (3), third (4), "
            "half (6), two-thirds (8), three-quarters (9), full (12) of a "
            "12-column grid.",
        )
        + "## Themes\n\n"
        + ", ".join(f"`{t}`" for t in available_themes())
        + "\n"
    )
    OUT.write_text(body, encoding="utf-8")
    print(f"wrote {OUT} ({len(body)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
