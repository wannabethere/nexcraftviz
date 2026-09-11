"""The step vocabulary — every action a skill file can use, registered once.

A skill file composes these; it cannot invent new ones. Model-backed work goes
through the same skills and pipeline as everything else, so a chart built by a
workflow is planned, gated and repaired exactly like one built on its own.

Actions named ``host.*`` are deliberately absent: the executor hands those to
the host, which owns the data and the publishing.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ActionContext:
    llm: Any = None
    language: str = "English"
    #: Anything worth telling the person — added to the run's trace.
    notes: list[str] = field(default_factory=list)


class ActionError(RuntimeError):
    """An action that could not do its job. Fails the run, with this reason."""


Action = Callable[[ActionContext, dict[str, Any]], Awaitable[Any]]

ACTIONS: dict[str, Action] = {}


def action(name: str) -> Callable[[Action], Action]:
    def register(fn: Action) -> Action:
        ACTIONS[name] = fn
        return fn
    return register


# ---------------------------------------------------------------------------
# charts
# ---------------------------------------------------------------------------

@action("chart.create")
async def chart_create(ctx: ActionContext, args: dict[str, Any]) -> dict[str, Any]:
    """Question + rows → a chart, through the full pipeline (plan, generate, gates)."""
    from nexcraftviz.pipeline import ChartRequest, run_pipeline
    from nexcraftviz.spec.labels import lift_title

    question = str(args.get("question") or "").strip()
    rows = [row for row in (args.get("rows") or []) if isinstance(row, dict)]
    if not question:
        raise ActionError("chart.create needs a question")
    if not rows:
        ctx.notes.append(f"no rows came back for {question!r}, so no chart")
        return {"question": question, "status": "insufficient_data",
                "reason": "the query returned no rows", "spec": None}
    if ctx.llm is None:
        raise ActionError("chart.create needs a model, and none is configured")

    run = await run_pipeline(
        ChartRequest(question=question, rows=rows, language=ctx.language), llm=ctx.llm
    )
    spec = run.spec
    if spec is None:
        ctx.notes.append(f"no chart for {question!r}: {run.reason}")
        return {"question": question, "status": run.status, "reason": run.reason, "spec": None}
    _, title, _ = lift_title(spec.raw)
    generated = run.stages.generate
    return {
        "question": question,
        "status": run.status,
        "reason": run.reason,
        "spec": spec.raw,
        "chart_type": generated.chart_type if generated else "",
        "title": title,
    }


# ---------------------------------------------------------------------------
# widget parts
# ---------------------------------------------------------------------------

@action("widget.narrate")
async def widget_narrate(ctx: ActionContext, args: dict[str, Any]) -> dict[str, Any] | None:
    """What the chart shows — headline, summary, points — for the widget to carry."""
    from nexcraftviz.skills import REGISTRY, SkillError

    spec, rows, question = _chart_source(args)
    if spec is None:
        return None
    if ctx.llm is None:
        ctx.notes.append("no model is configured, so no narration")
        return None
    try:
        result = await REGISTRY["viz.narrate"].run(
            {"spec": spec, "rows": rows, "question": question, "language": ctx.language},
            llm=ctx.llm,
        )
    except SkillError as exc:
        ctx.notes.append(f"narration failed: {exc}")
        return None
    output = result.output
    if output is None or not (output.headline or output.summary):
        return None
    return output.model_dump(mode="json")


@action("widget.table")
async def widget_table(ctx: ActionContext, args: dict[str, Any]) -> dict[str, Any] | None:
    """The rows as a rich table, cell renderers chosen from the data."""
    from nexcraftviz.table import build_table

    rows = [row for row in (args.get("rows") or []) if isinstance(row, dict)]
    if not rows and args.get("widget"):
        _, rows, _ = _chart_source(args)
    if not rows:
        return None
    return build_table(rows, title=str(args.get("title") or "")).to_spec().raw


@action("widget.compose")
async def widget_compose(ctx: ActionContext, args: dict[str, Any]) -> dict[str, Any] | None:
    """Charts, a narration and a table → one widget document.

    A chart's title moves to its tile's header, so it is not drawn twice; a
    single-chart widget takes the chart's title as its own.
    """
    from nexcraftviz.compose.widget import tile, widget
    from nexcraftviz.spec.labels import lift_title
    from nexcraftviz.spec.model import Spec

    charts = [c for c in (args.get("charts") or []) if isinstance(c, dict) and c.get("spec")]
    narration = args.get("narration") or None
    table = args.get("table") or None
    if not charts and not table:
        return None

    tiles = []
    titles = []
    for index, chart in enumerate(charts):
        body, title, subtitle = lift_title(chart["spec"])
        title = title or str(chart.get("question") or "")
        titles.append(title)
        tiles.append(tile(Spec(body), id=f"chart-{index + 1}",
                          title=title if len(charts) > 1 else "",
                          subtitle=subtitle if len(charts) > 1 else ""))
    name = str(args.get("title") or (titles[0] if len(titles) == 1 else ""))
    built = widget(*tiles, title=name)
    built.narration = narration
    built.table = Spec(table) if table else None
    return built.to_dict()


@action("widget.extend")
async def widget_extend(ctx: ActionContext, args: dict[str, Any]) -> dict[str, Any]:
    """Add charts, a narration or a table to an existing widget document."""
    from nexcraftviz.compose.widget import Widget, tile
    from nexcraftviz.spec.labels import lift_title
    from nexcraftviz.spec.model import Spec

    base = args.get("widget")
    if isinstance(base, list) and base:
        base = base[0]
    if not isinstance(base, dict):
        raise ActionError("widget.extend needs the widget document to extend")
    built = Widget.from_dict(base)
    taken = {t.id for t in built.tiles}
    for chart in (c for c in (args.get("charts") or []) if isinstance(c, dict) and c.get("spec")):
        body, title, _ = lift_title(chart["spec"])
        number = len(taken) + 1
        while f"chart-{number}" in taken:
            number += 1
        taken.add(f"chart-{number}")
        built.nodes.append(tile(Spec(body), id=f"chart-{number}",
                                title=title or str(chart.get("question") or "")))
    if args.get("narration"):
        built.narration = args["narration"]
    if args.get("table"):
        built.table = Spec(args["table"])
    return built.to_dict()


# ---------------------------------------------------------------------------
# dashboards
# ---------------------------------------------------------------------------

@action("dashboard.suggest_questions")
async def suggest_questions(ctx: ActionContext, args: dict[str, Any]) -> list[dict[str, Any]]:
    """The chart questions a dashboard question needs — options for a person to pick."""
    from nexcraftviz.skills import REGISTRY, SkillError

    question = str(args.get("question") or "").strip()
    if not question:
        raise ActionError("dashboard.suggest_questions needs a question")
    if ctx.llm is None:
        ctx.notes.append("no model is configured, so the question is offered as asked")
        return [{"id": "q1", "text": question, "visual": "", "why": "the question as asked"}]
    try:
        result = await REGISTRY["viz.suggest_questions"].run(
            {"question": question, "language": ctx.language,
             "count": int(args.get("count") or 5), "context": str(args.get("context") or "")},
            llm=ctx.llm,
        )
    except SkillError as exc:
        raise ActionError(f"could not suggest questions: {exc}") from exc
    return list(result.value or [])


@action("dashboard.layout")
async def dashboard_layout(ctx: ActionContext, args: dict[str, Any]) -> dict[str, Any]:
    """Widgets → a dashboard document with a 12-column grid layout."""
    widgets = [w for w in _as_list(args.get("widgets"))
               if isinstance(w, dict) and w.get("kind") == "nexcraftviz.widget"]
    if not widgets:
        raise ActionError("there are no widgets to lay out")
    placed = [{**doc, "id": doc.get("id") or f"widget-{index + 1}"}
              for index, doc in enumerate(widgets)]
    return {
        "kind": "nexcraftviz.dashboard",
        "version": 1,
        "title": str(args.get("title") or ""),
        "widgets": placed,
        "layout": grid_layout(placed),
    }


#: The dashboard grid: 12 columns. Row height is the host's.
GRID_COLUMNS = 12


def grid_layout(widgets: list[dict[str, Any]]) -> list[dict[str, int | str]]:
    """``{i, x, y, w, h}`` for each widget, packed left to right, top to bottom.

    Sized from what a widget holds: a strip of KPI cards is short, one chart
    takes half the width, several charts take the whole row, and a narration
    or a table adds height.
    """
    placements: list[dict[str, int | str]] = []
    x = y = row_height = 0
    for doc in widgets:
        w, h = _size(doc)
        if x + w > GRID_COLUMNS:
            x, y, row_height = 0, y + row_height, 0
        placements.append({"i": str(doc["id"]), "x": x, "y": y, "w": w, "h": h})
        x += w
        row_height = max(row_height, h)
    return placements


def _size(doc: dict[str, Any]) -> tuple[int, int]:
    tiles = _tiles(doc)
    extra = (3 if doc.get("table") else 0) + (1 if doc.get("narration") else 0)
    families = {t.get("family") for t in tiles}
    if tiles and families <= {"kpi"}:
        return min(3 * len(tiles), GRID_COLUMNS), 2 + extra
    if len(tiles) <= 1:
        return 6, 4 + extra
    return GRID_COLUMNS, 4 + 3 * ((len(tiles) - 1) // 3) + extra


def _tiles(doc: dict[str, Any]) -> list[dict[str, Any]]:
    tiles: list[dict[str, Any]] = []
    for node in doc.get("nodes") or []:
        if node.get("kind") == "group":
            tiles.extend(node.get("tiles") or [])
        else:
            tiles.append(node)
    return tiles


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _chart_source(args: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict], str]:
    """A spec, its rows and its question — from a chart result or a widget document."""
    chart = args.get("chart")
    if isinstance(chart, dict) and chart.get("spec"):
        spec = chart["spec"]
        rows = _inline_rows(spec) or list(args.get("rows") or [])
        return spec, rows, str(chart.get("question") or "")

    doc = args.get("widget")
    if isinstance(doc, list) and doc:
        doc = doc[0]
    if isinstance(doc, dict):
        for node in _tiles(doc):
            payload = node.get("payload")
            if node.get("family") in ("vega-lite", "vega") and isinstance(payload, dict):
                title = str(doc.get("title") or node.get("title") or "")
                return payload, _inline_rows(payload), title
    return None, [], ""


def _inline_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    data = spec.get("data")
    values = data.get("values") if isinstance(data, dict) else None
    return [row for row in values if isinstance(row, dict)] if isinstance(values, list) else []


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]
