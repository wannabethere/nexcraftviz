"""Framework-free tool definitions, for any agent that can call tools.

The important design decision here: **in agent mode the agent is the model.**
So the tools are not "generate a chart" and "edit a chart" — asking a model to
call a tool that calls a model is a needless round trip and a second, worse
prompt. What an agent actually needs is:

* the **deterministic** capabilities it cannot do itself — profiling, ranking,
  validating, theming, rendering, composing;
* the **appliers** that turn its decisions into a correct spec; and
* the **guidance** — ``viz_guidance`` hands back the same operating rules the
  hosted prompts use, so the agent reasons with the vocabulary the appliers
  actually accept.

That is why there is no ``viz_generate`` tool. The agent reads the guidance,
decides the operations, and calls ``viz_apply_ops``; the applier validates and
repairs. The model does the judgement, the package does the mechanics — the
same split as everywhere else here.

Nothing in this module imports a provider SDK or a framework.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from nexcraftviz.compose.ops import WIDGET_OP_REGISTRY, WidgetOpList, apply_widget_ops
from nexcraftviz.compose.vega import concat
from nexcraftviz.compose.widget import Widget
from nexcraftviz.data.profile import profile_rows
from nexcraftviz.render.html import render_table
from nexcraftviz.skills import get as get_skill
from nexcraftviz.skills import names as skill_names
from nexcraftviz.spec.model import Spec
from nexcraftviz.spec.ops import OP_REGISTRY, OpList, apply_ops
from nexcraftviz.spec.validate import validate
from nexcraftviz.table import build_table
from nexcraftviz.theme import apply_theme, audit, available_themes, strip_hardcoded_colours

Style = Literal["openai", "anthropic", "mcp"]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., dict[str, Any]]

    def schema(self, style: Style = "openai") -> dict[str, Any]:
        if style == "openai":
            return {
                "type": "function",
                "function": {
                    "name": self.name,
                    "description": self.description,
                    "parameters": self.parameters,
                },
            }
        if style == "anthropic":
            return {
                "name": self.name,
                "description": self.description,
                "input_schema": self.parameters,
            }
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters,
        }


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


_ROWS = {
    "type": "array",
    "items": {"type": "object"},
    "description": "Result rows, as a list of objects.",
}
_SPEC = {"type": "object", "description": "A Vega-Lite specification."}


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------

def _profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    profile = profile_rows(rows)
    return {"profile": profile.to_prompt_dict(), "columns": profile.column_names}


def _recommend(rows: list[dict[str, Any]], question: str = "") -> dict[str, Any]:
    from nexcraftviz.recommend.rules import recommend

    profile = profile_rows(rows)
    ranked = recommend(profile, question=question)
    return {
        "shape_signature": ranked.shape_signature,
        "time_axis": profile.time_axis.name if profile.time_axis else None,
        "recommendations": [
            {"chart_type": r.chart_type, "score": round(r.score, 2), "reason": r.reason}
            for r in ranked
        ],
    }


def _guidance(skill: str = "viz.edit") -> dict[str, Any]:
    """The operating rules and the operation schema for a skill.

    This is what makes agent mode work as well as hosted mode: the agent gets
    the *same* instructions the hosted prompt uses, rather than guessing at the
    vocabulary its output will be validated against.
    """
    if skill not in skill_names():
        return {"error": f"unknown skill {skill!r}", "available": skill_names()}

    handler = get_skill(skill)
    payload: dict[str, Any] = {
        "skill": skill,
        "summary": handler.spec.summary,
        "uses_llm": handler.spec.uses_llm,
    }
    if handler.spec.prompt:
        text, version = handler.system_prompt()
        payload["guidance"] = text
        payload["prompt_version"] = version
    payload["output_schema"] = handler.output_schema()

    if skill == "viz.edit":
        payload["operations"] = sorted(OP_REGISTRY)
        payload["apply_with"] = "viz_apply_ops"
    elif skill == "viz.place":
        payload["operations"] = sorted(WIDGET_OP_REGISTRY)
        payload["apply_with"] = "viz_apply_layout"
    return payload


def _apply_ops(
    spec: dict[str, Any],
    ops: list[dict[str, Any]],
    rows: list[dict[str, Any]] | None = None,
    repair: bool = True,
) -> dict[str, Any]:
    result = apply_ops(Spec(spec), ops)
    validated, report = validate(
        result.spec, data=rows or result.spec.data_values, max_tier=3, repair=repair
    )
    return {
        "spec": validated.raw,
        "changes": result.describe(),
        "applied": result.applied,
        "failed": [{"op": n, "reason": r} for n, r in result.failed],
        "valid": report.ok,
        "repaired": report.repaired,
        "errors": [str(i) for i in report.errors],
        "warnings": [str(i) for i in report.warnings],
    }


def _apply_layout(widget: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    result = apply_widget_ops(Widget.from_dict(widget), ops)
    return {
        "widget": result.widget.to_dict(),
        "changes": result.describe(),
        "applied": result.applied,
        "failed": [{"op": n, "reason": r} for n, r in result.failed],
        "html": result.widget.to_html(),
    }


def _validate(
    spec: dict[str, Any], rows: list[dict[str, Any]] | None = None, repair: bool = False
) -> dict[str, Any]:
    validated, report = validate(Spec(spec), data=rows, max_tier=3, repair=repair)
    payload = report.to_dict()
    if repair:
        payload["spec"] = validated.raw
    return payload


def _theme(spec: dict[str, Any], theme: str = "nexcraftviz-light") -> dict[str, Any]:
    if theme not in available_themes():
        return {"error": f"unknown theme {theme!r}", "available": available_themes()}
    stripped = strip_hardcoded_colours(Spec(spec))
    themed = apply_theme(stripped.spec, theme)
    report = audit_theme(theme)
    return {
        "spec": themed.spec.raw,
        "theme": theme,
        "cleared_hardcoded_colours": stripped.changed,
        "contrast": report,
    }


def audit_theme(theme: str) -> dict[str, Any]:
    from nexcraftviz.theme import load

    report = audit(load(theme))
    return {"ok": report.ok, "issues": [str(i) for i in report.issues]}


def _render(spec: dict[str, Any], format: str = "svg", scale: float = 1.0) -> dict[str, Any]:
    from nexcraftviz.render import RenderUnavailable, available, to_png, to_svg, to_vega

    if not available():
        return {"error": "rendering needs the `render` extra: pip install 'nexcraftviz[render]'"}
    try:
        if format == "png":
            data = to_png(Spec(spec), scale=scale)
            return {"format": "png", "base64": base64.b64encode(data).decode(), "bytes": len(data)}
        if format == "vega":
            return {"format": "vega", "spec": to_vega(Spec(spec))}
        return {"format": "svg", "svg": to_svg(Spec(spec))}
    except (RenderUnavailable, Exception) as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _compose(
    specs: list[dict[str, Any]],
    direction: str = "vertical",
    title: str = "",
    resolve_scales: str = "independent",
) -> dict[str, Any]:
    from nexcraftviz.compose.vega import ComposeError

    try:
        composed = concat(
            [Spec(s) for s in specs],
            direction=direction,  # type: ignore[arg-type]
            title=title,
            resolve_scales=resolve_scales,  # type: ignore[arg-type]
        )
    except ComposeError as exc:
        return {"error": str(exc)}
    _, report = validate(composed, max_tier=3)
    return {"spec": composed.raw, "valid": report.ok, "errors": [str(i) for i in report.errors]}


def _build_chart(
    rows: list[dict[str, Any]], chart_type: str = "", question: str = "", title: str = ""
) -> dict[str, Any]:
    from nexcraftviz.recommend.build import BuildError, build_best, build_chart

    try:
        if chart_type:
            spec = build_chart(rows, chart_type=chart_type, question=question, title=title)
            built = chart_type
        else:
            spec, built = build_best(rows, question=question, title=title)
    except BuildError as exc:
        return {"error": str(exc)}

    _, report = validate(spec, data=rows, max_tier=3)
    return {
        "spec": spec.raw,
        "chart_type": built,
        "valid": report.ok,
        "errors": [str(i) for i in report.errors],
    }


def _table(rows: list[dict[str, Any]], title: str = "") -> dict[str, Any]:
    table = build_table(rows, title=title)
    return {
        "spec": table.to_spec().raw,
        "columns": [{"field": c.field, "render": c.render} for c in table.columns],
        "html": render_table(table),
    }


# ---------------------------------------------------------------------------
# the toolkit
# ---------------------------------------------------------------------------

TOOLS: tuple[Tool, ...] = (
    Tool(
        "viz_profile",
        "Profile result rows: column types, roles (measure / dimension / time / "
        "identifier / series), cardinality and ranges. Call this first — the "
        "roles are what should drive the chart choice.",
        _object({"rows": _ROWS}, ["rows"]),
        _profile,
    ),
    Tool(
        "viz_recommend",
        "Rank chart types for a result set, each with a reason. Deterministic "
        "shape rules — use this rather than guessing a chart type.",
        _object({"rows": _ROWS, "question": {"type": "string"}}, ["rows"]),
        _recommend,
    ),
    Tool(
        "viz_guidance",
        "Get the operating rules and the exact operation vocabulary for a "
        "skill, so your operations will be accepted. Call before viz_apply_ops "
        "or viz_apply_layout the first time.",
        _object({
            "skill": {
                "type": "string",
                "enum": skill_names(),
                "description": "viz.edit for charts, viz.place for layout.",
            }
        }),
        _guidance,
    ),
    Tool(
        "viz_apply_ops",
        "Apply chart operations to a Vega-Lite spec. Validates the result and "
        "repairs near-miss field names. This is how you change a chart — do "
        "not write Vega-Lite by hand.",
        _object({
            "spec": _SPEC,
            "ops": {
                "type": "array",
                "items": {"type": "object"},
                "description": f"Operations. Available: {', '.join(sorted(OP_REGISTRY))}.",
            },
            "rows": _ROWS,
            "repair": {"type": "boolean", "default": True},
        }, ["spec", "ops"]),
        _apply_ops,
    ),
    Tool(
        "viz_apply_layout",
        "Apply placement operations to a widget: resize, move, group, ungroup, "
        "change layout. Returns the new widget and its HTML.",
        _object({
            "widget": {"type": "object", "description": "A nexcraftviz widget document."},
            "ops": {
                "type": "array",
                "items": {"type": "object"},
                "description": f"Operations. Available: {', '.join(sorted(WIDGET_OP_REGISTRY))}.",
            },
        }, ["widget", "ops"]),
        _apply_layout,
    ),
    Tool(
        "viz_validate",
        "Check a spec: structure, that every encoded field exists in the data "
        "with a compatible type, and that it compiles. Set repair to fix "
        "near-miss field names deterministically.",
        _object({"spec": _SPEC, "rows": _ROWS, "repair": {"type": "boolean", "default": False}},
                ["spec"]),
        _validate,
    ),
    Tool(
        "viz_theme",
        f"Apply a theme. Clears hard-coded mark colours first, which otherwise "
        f"override it silently. Presets: {', '.join(available_themes())}.",
        _object({
            "spec": _SPEC,
            "theme": {"type": "string", "enum": available_themes()},
        }, ["spec"]),
        _theme,
    ),
    Tool(
        "viz_render",
        "Render a spec to SVG, PNG (base64) or compiled Vega. Use this to check "
        "a chart actually draws — a spec can validate and still render blank.",
        _object({
            "spec": _SPEC,
            "format": {"type": "string", "enum": ["svg", "png", "vega"], "default": "svg"},
            "scale": {"type": "number", "default": 1.0},
        }, ["spec"]),
        _render,
    ),
    Tool(
        "viz_compose",
        "Combine several Vega-Lite specs into one spec. Vega views only — for a "
        "mix of charts, KPIs and tables build a widget instead.",
        _object({
            "specs": {"type": "array", "items": _SPEC},
            "direction": {"type": "string", "enum": ["vertical", "horizontal", "wrap"]},
            "title": {"type": "string"},
            "resolve_scales": {"type": "string", "enum": ["independent", "shared"]},
        }, ["specs"]),
        _compose,
    ),
    Tool(
        "viz_build_chart",
        "Build a correct chart from rows with no model call. The shape rules "
        "pick the type and the encodings come from the profile, so every field "
        "exists and every type matches. Start here, then refine with "
        "viz_apply_ops rather than writing a spec from scratch.",
        _object({
            "rows": _ROWS,
            "chart_type": {"type": "string", "description": "Force a type, or omit."},
            "question": {"type": "string"},
            "title": {"type": "string"},
        }, ["rows"]),
        _build_chart,
    ),
    Tool(
        "viz_table",
        "Build a rich table from rows — no model needed. Picks a renderer per "
        "column (progress bar, pill, sparkline, trend arrow) from the profile. "
        "Render this immediately; the chart can follow.",
        _object({"rows": _ROWS, "title": {"type": "string"}}, ["rows"]),
        _table,
    ),
)

BY_NAME: dict[str, Tool] = {tool.name: tool for tool in TOOLS}


def tool_schemas(style: Style = "openai") -> list[dict[str, Any]]:
    """Every tool, in the requested provider's format."""
    return [tool.schema(style) for tool in TOOLS]


def call(name: str, arguments: dict[str, Any] | str | None = None) -> dict[str, Any]:
    """Execute a tool call.

    Errors come back as ``{"error": ...}`` rather than raising: a tool-calling
    loop handles a bad result far better than an exception, and the model can
    usually correct itself from the message.
    """
    tool = BY_NAME.get(name)
    if tool is None:
        return {"error": f"unknown tool {name!r}", "available": sorted(BY_NAME)}

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except (json.JSONDecodeError, ValueError) as exc:
            return {"error": f"arguments are not valid JSON: {exc}"}

    try:
        return tool.handler(**(arguments or {}))
    except TypeError as exc:
        return {"error": f"{name}: {exc}"}
    except Exception as exc:  # noqa: BLE001 — a tool must not break the loop
        return {"error": f"{name} failed: {type(exc).__name__}: {exc}"}


def op_schemas() -> dict[str, Any]:
    """The chart and layout operation unions, as JSON Schema.

    For a host that wants to constrain its model's structured output directly
    rather than describing the vocabulary in prose.
    """
    return {
        "chart_ops": OpList.model_json_schema(),
        "layout_ops": WidgetOpList.model_json_schema(),
    }
