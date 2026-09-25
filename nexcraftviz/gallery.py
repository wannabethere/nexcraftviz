"""Generate the playground pages.

Two pages, both built from real package output rather than hand-written markup,
so neither can drift from what the code actually does:

* **gallery** — every corpus pair, rendered *table first, then chart*. The table
  comes from the rows the spec already carries, built deterministically with no
  model involved; the chart follows. It doubles as a visual regression surface:
  if an op or a theme breaks something, it is visible here.
* **use case** — one realistic scenario walked end to end, showing the table
  landing first, the profile, the rules-based recommendation, then natural-
  language edits applied as operations with the resulting diff.

Run with ``nexcraftviz gallery --out playground``.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from nexcraftviz.corpus.loader import ChartPair, seed
from nexcraftviz.data.profile import profile_rows
from nexcraftviz.recommend.rules import recommend
from nexcraftviz.render.html import (
    escape,
    render_card,
    render_chart_mount,
    render_kpi,
    render_table,
)
from nexcraftviz.spec.model import Spec
from nexcraftviz.spec.ops import apply_ops
from nexcraftviz.table import Column, KpiCard, TableSpec, build_table, with_sample_rows
from nexcraftviz.theme import apply_theme, strip_hardcoded_colours

#: Rows shown per table in the gallery. Enough to see the renderers work.
GALLERY_ROWS = 6


# ---------------------------------------------------------------------------
# the use case
# ---------------------------------------------------------------------------

def use_case_rows() -> list[dict[str, Any]]:
    """A compliance-completion result set, shaped like real SQL output.

    Deliberately mixed: an entity column, a share, a bounded score, a judged
    status, a signed movement, a series and a date — so the table builder has
    to make a decision about every renderer it supports.
    """
    base = date(2026, 3, 2)
    raw = [
        ("Field Operations", 1240, 1104, 89.0, 74, "On track", 4.2, [61, 68, 72, 79, 84, 89], 0),
        ("Clinical Services", 980, 905, 92.3, 81, "On track", 2.1, [78, 80, 83, 86, 90, 92], 4),
        ("Corporate Functions", 610, 402, 65.9, 58, "At risk", -3.4, [72, 70, 69, 67, 66, 66], 9),
        ("Retail Network", 2310, 1201, 52.0, 44, "At risk", -1.1, [58, 56, 55, 53, 52, 52], 12),
        ("Manufacturing", 1475, 1401, 95.0, 88, "On track", 5.6, [80, 84, 87, 90, 93, 95], 2),
        ("Logistics", 845, 321, 38.0, 29, "Blocked", -8.2, [55, 50, 46, 42, 40, 38], 21),
        ("Technology", 520, 489, 94.0, 85, "On track", 1.4, [88, 89, 91, 92, 93, 94], 5),
        ("Contact Centre", 1680, 924, 55.0, 47, "At risk", -0.6, [57, 57, 56, 55, 55, 55], 15),
    ]
    return [
        {
            "business_unit": unit,
            "assigned": assigned,
            "completed": completed,
            "completion_pct": pct,
            "readiness_score": score,
            "status": status,
            "vs_target": delta,
            "six_month_trend": trend,
            "next_audit": (base + timedelta(days=days)).isoformat(),
        }
        for unit, assigned, completed, pct, score, status, delta, trend, days in raw
    ]


#: The edits, as a user would phrase them, paired with the operations a
#: language model would emit for each. Keeping both makes the page honest about
#: where the model's job ends and the algebra's begins.
USE_CASE_EDITS: list[tuple[str, list[dict[str, Any]]]] = [
    (
        "sort it worst-first so I can see who needs help",
        [{"op": "sort_by", "channel": "y", "by": "completion_pct", "order": "ascending"}],
    ),
    (
        "just the five furthest behind",
        [{"op": "limit_top_n", "n": 5, "by": "completion_pct", "order": "ascending"}],
    ),
    (
        "mark the 90% compliance target",
        [{
            "op": "add_reference_line",
            "value": 90.0,
            "axis": "x",
            "label": "90% target",
            "color": "#B45309",
        }],
    ),
    (
        "colour by status and give it a title",
        [
            {"op": "set_color_field", "field": "status", "type": "nominal",
             "legend_title": "Status"},
            {"op": "set_title", "text": "Compliance completion by business unit",
             "subtitle": "Below the 90% target, worst first"},
        ],
    ),
]


def _use_case_chart(rows: list[dict[str, Any]]) -> Spec:
    return Spec({
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": rows},
        "width": "container",
        "height": 260,
        "mark": "bar",
        "encoding": {
            "y": {"field": "business_unit", "type": "nominal", "axis": {"title": None}},
            "x": {"field": "completion_pct", "type": "quantitative",
                  "axis": {"title": "Completion %"}},
        },
    })


def build_use_case() -> str:
    rows = use_case_rows()
    profile = profile_rows(rows)
    question = "Which business units are behind on compliance training against the 90% target?"

    sections: list[str] = []

    # 1 — the table, first, with no model involved.
    table = build_table(rows, profile=profile)
    sections.append(_step(
        1,
        "The table renders first",
        "Rows arrive and the table is built straight from them — column roles and "
        "value ranges pick the renderers. No model runs, so this costs nothing and "
        "cannot be wrong about what the data contains. The user is reading real "
        "results while the chart is still being generated.",
        render_card(title="Compliance assignments by business unit",
                    subtitle=f"{len(rows)} rows · built deterministically",
                    body=render_table(table)),
        aside=_renderer_table(table),
    ))

    # 2 — what the profile knows.
    sections.append(_step(
        2,
        "What the profile knows",
        "Roles, not just types. “One dimension, one share, one bounded score, one "
        "signed movement” is what drives both the table renderers above and the "
        "chart recommendation below.",
        f'<pre class="nxv-code">{escape(json.dumps(profile.to_prompt_dict(), indent=2))}</pre>',
    ))

    # 3 — deterministic recommendation.
    recommendations = recommend(profile, question=question)
    rows_html = "".join(
        f"<tr><td><code>{escape(r.chart_type)}</code></td>"
        f'<td class="nxv-num">{r.score:.2f}</td><td>{escape(r.reason)}</td></tr>'
        for r in recommendations
    )
    sections.append(_step(
        3,
        "Rules pick the chart",
        f"Asked: <em>{escape(question)}</em>. The shape settles most of it before a "
        "model is involved; the question only nudges the ranking.",
        f'<table class="nxv-table"><thead><tr><th>Chart</th>'
        f'<th class="nxv-num">Score</th><th>Why</th></tr></thead>'
        f"<tbody>{rows_html}</tbody></table>",
    ))

    # 4 — the base chart, then each edit applied as operations.
    spec = _use_case_chart(rows)
    charts = [(
        "Generated chart",
        "The recommended bar chart, before any editing.",
        spec,
        [],
        [],
    )]
    for instruction, ops in USE_CASE_EDITS:
        result = apply_ops(spec, ops)
        spec = result.spec
        charts.append((instruction, "", spec, ops, result.describe()))

    for index, (title, note, chart_spec, ops, changes) in enumerate(charts):
        mount = f"usecase-chart-{index}"
        body = render_chart_mount(chart_spec, mount)
        aside = ""
        if ops:
            change_list = "".join(f"<li><code>{escape(c)}</code></li>" for c in changes)
            aside = (
                '<p class="nxv-aside__label">Operations the model emits</p>'
                f'<pre class="nxv-code">{escape(json.dumps(ops, indent=2))}</pre>'
                '<p class="nxv-aside__label">What actually changed</p>'
                f'<ul class="nxv-changes">{change_list}</ul>'
            )
        sections.append(_step(
            4 + index,
            title if index == 0 else f"“{title}”",
            note or (
                "The model chose operations, not JSON. Applying them is ordinary "
                "code, so the edit is reproducible and the inverse patch comes free."
            ),
            render_card(title="", body=body),
            aside=aside,
        ))

    # 5 — theme swap, deterministic and undoable.
    themed = apply_theme(strip_hardcoded_colours(spec).spec, "powerbi").spec
    sections.append(_step(
        4 + len(charts),
        "Same chart, different theme",
        "Theming is an operation too — no model, and undoable like any other edit. "
        "This preset is derived from the vega-themes build the product already "
        "ships, so adopting it is not a visual change.",
        render_card(title="", body=render_chart_mount(themed, "usecase-themed")),
    ))

    return _page(
        title="nexcraftviz — table-first walkthrough",
        lede=(
            "One scenario, end to end: the table lands immediately, then the "
            "visualization agent works on the same rows. Every panel below is "
            "generated by the package, not hand-written."
        ),
        body="\n".join(sections),
        nav_active="usecase",
    )


# ---------------------------------------------------------------------------
# the corpus gallery
# ---------------------------------------------------------------------------

#: The pipeline pairs, grouped the way someone reads them: by the thing being
#: tracked, not by chart type. Declared rather than parsed out of the names —
#: `cumulative_flow_hiring_pipeline` is an employee chart and says nothing about
#: it, and a mis-grouping would be silent.
PIPELINE_DOMAINS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "Sprint progress",
        "Is the sprint going to land, and where is work piling up?",
        ("burndown_sprint_remaining", "cumulative_flow_sprint_board"),
    ),
    (
        "Project progress",
        "What runs when, which dates matter, and will the money last?",
        ("gantt_project_plan", "milestone_project_checkpoints", "burndown_project_budget"),
    ),
    (
        "Employee",
        "One person's path — ramping, key dates — and the pipeline they came through.",
        (
            "gantt_employee_onboarding_ramp",
            "milestone_employee_journey",
            "cumulative_flow_hiring_pipeline",
        ),
    ),
)


def build_pipeline() -> str:
    """The progress charts, grouped by what they track.

    Every card shows the rows, the chart, and — the part that makes this a
    *corpus* gallery rather than a sample sheet — the `use_when` and
    `do_not_use_when` clauses that decide when each is the right answer, plus
    the question that actually retrieves it and the score it earns.
    """
    from nexcraftviz.data.profile import profile_rows
    from nexcraftviz.recommend.precedent import precedents

    by_name = {pair.name: pair for pair in seed().pairs}
    grouped = {name for _, _, names in PIPELINE_DOMAINS for name in names}

    # Anything with a progress intent that nobody put in a group still belongs
    # on the page. A pair added later should look out of place, not disappear.
    ungrouped = tuple(
        pair.name
        for pair in seed().pairs
        if (pair.kinds or {}).get("intent") == "progress" and pair.name not in grouped
    )
    domains = list(PIPELINE_DOMAINS)
    if ungrouped:
        domains.append(("Not yet grouped", "Added to the corpus, not to this page.", ungrouped))

    sections: list[str] = []
    total = 0
    for index, (title, note, names) in enumerate(domains, start=1):
        cards = []
        for name in names:
            pair = by_name.get(name)
            if pair is None:
                continue
            total += 1
            cards.append(_pipeline_card(pair, total, precedents, profile_rows))
        if cards:
            sections.append(
                _step(index, title, note,
                      f'<div class="nxv-gallery nxv-gallery--wide">{"".join(cards)}</div>')
            )

    types = sorted({by_name[n].chart_type for _, _, ns in domains for n in ns if n in by_name})
    return _page(
        title="nexcraftviz — pipeline and progress",
        lede=(
            f"{total} worked examples across {len(types)} chart types "
            f"({', '.join(types)}), grouped by what they track. Each card carries "
            "the conditions that select it and the question that retrieves it — "
            "the corpus decides the chart type, so these are the evidence it "
            "decides from."
        ),
        body="".join(sections),
        nav_active="pipeline",
    )


def _pipeline_card(pair: ChartPair, index: int, precedents: Any, profile_rows: Any) -> str:
    spec = pair.spec()
    rows = spec.data_values
    parts: list[str] = []

    # The corpus specs carry a fixed width, tuned for a card of their own.
    # Three to a row they overflow and clip — the last gantt bar and the last
    # milestone simply vanish. A display concern of this page, so it is fixed
    # here rather than in the corpus.
    if "width" in spec.raw:
        spec.raw["width"] = "container"

    if rows:
        parts.append(
            '<details class="nxv-details"><summary>Source rows '
            f"({len(rows)})</summary>{render_table(build_table(rows), max_rows=GALLERY_ROWS)}"
            "</details>"
        )
    parts.append(render_chart_mount(spec, f"pipeline-{index}"))

    if pair.insight:
        parts.append(f'<p class="nxv-overview">{escape(pair.insight)}</p>')

    # What the retrieval actually does with this pair's own first question —
    # computed here rather than asserted, so a page that renders is a page whose
    # claim is true.
    retrieved = ""
    if pair.example_questions and rows:
        question = pair.example_questions[0]
        found = precedents(profile_rows(rows), question=question)
        if found.best is not None:
            hit = "✓" if found.best.chart_type == pair.chart_type else "✗"
            retrieved = (
                f'<p class="nxv-note"><strong>{hit} “{escape(question)}”</strong> → '
                f"{escape(found.best.chart_type)} ({found.best.score:.2f})</p>"
            )

    conditions = "".join(
        f"<li>{escape(clause)}</li>" for clause in pair.use_when
    )
    avoid = "".join(f"<li>{escape(clause)}</li>" for clause in pair.do_not_use_when)
    parts.append(
        f'<details class="nxv-details"><summary>When to use it</summary>'
        f"<ul class=\"nxv-conditions\">{conditions}</ul>"
        f'<p class="nxv-note">Not this chart when:</p>'
        f"<ul class=\"nxv-conditions nxv-conditions--avoid\">{avoid}</ul></details>"
    )

    return (
        f'<div class="nxv-card nxv-gallery__item" data-chart-type="{escape(pair.chart_type)}">'
        f'<div class="nxv-card__header"><h3 class="nxv-card__title">'
        f"{escape(_humanise(pair.name))}</h3>"
        f'<span class="nxv-card__subtitle">{escape(pair.chart_type)}</span></div>'
        f'<div class="nxv-card__body">{"".join(parts)}{retrieved}</div></div>'
    )


def build_gallery(limit: int | None = None) -> str:
    corpus = seed()
    pairs = list(corpus)[:limit] if limit else list(corpus)

    cards: list[str] = []
    counts: dict[str, int] = {}
    for index, pair in enumerate(pairs):
        counts[pair.family] = counts.get(pair.family, 0) + 1
        cards.append(_gallery_card(pair, index))

    summary = " · ".join(f"{count} {family}" for family, count in sorted(counts.items()))
    chart_types = sorted({p.chart_type for p in pairs})
    filters = "".join(
        f'<button type="button" data-filter="{escape(t)}">{escape(t)}</button>'
        for t in chart_types
    )

    body = (
        '<div class="nxv-filters"><button type="button" data-filter="" '
        f'aria-pressed="true">all</button>{filters}</div>'
        f'<div class="nxv-gallery">{"".join(cards)}</div>'
    )
    return _page(
        title="nexcraftviz — corpus gallery",
        lede=(
            f"All {len(pairs)} chart pairs ({summary}), each rendered table first "
            "and chart second. Tables are built from the rows the spec carries; "
            "the 30 table_with_cells pairs ship no rows at all, so theirs are "
            "synthesised and labelled as such."
        ),
        body=body,
        nav_active="gallery",
    )


def _gallery_card(pair: ChartPair, index: int) -> str:
    parts: list[str] = []
    synthetic = False

    if pair.family == "kpi-card":
        kpi = KpiCard.from_columns_schema(pair.columns_schema or {})
        parts.append(render_kpi(kpi))
    elif pair.family == "table-with-cells":
        table = with_sample_rows(TableSpec.from_columns_schema(pair.columns_schema or []))
        synthetic = True
        parts.append(render_table(table, max_rows=GALLERY_ROWS))
    else:
        spec = pair.spec()
        rows = spec.data_values
        if rows:
            parts.append(
                '<details class="nxv-details"><summary>Source rows '
                f"({len(rows)})</summary>{render_table(build_table(rows), max_rows=GALLERY_ROWS)}"
                "</details>"
            )
        parts.append(render_chart_mount(spec, f"gallery-{index}"))

    if pair.overview:
        parts.append(f'<p class="nxv-overview">{escape(pair.overview)}</p>')
    if pair.insight:
        parts.append(f'<p class="nxv-insight">{escape(pair.insight)}</p>')
    if synthetic:
        parts.append('<p class="nxv-warn">Rows synthesised — this pair ships none.</p>')

    subtitle = f"{pair.chart_type} · {pair.intent or pair.family}"
    return (
        f'<div class="nxv-card nxv-gallery__item" data-chart-type="{escape(pair.chart_type)}">'
        f'<div class="nxv-card__header"><h3 class="nxv-card__title">'
        f'{escape(_humanise(pair.name))}</h3>'
        f'<span class="nxv-card__subtitle">{escape(subtitle)}</span></div>'
        f'<div class="nxv-card__body">{"".join(parts)}</div></div>'
    )


# ---------------------------------------------------------------------------
# widgets — combining charts
# ---------------------------------------------------------------------------

#: Placement edits, phrased as a user would, with the operations each becomes.
#: Placement is where people iterate most, so it gets the same treatment as
#: chart editing: the model picks operations, code applies them, undo is free.
WIDGET_EDITS: list[tuple[str, list[dict[str, Any]]]] = [
    (
        "make the funnel wider and shrink the stats beside it",
        [
            {"op": "set_span", "tile": "tile-funnel", "span": "three-quarters"},
            {"op": "set_span", "tile": "tile-conversion", "span": "quarter"},
        ],
    ),
    (
        "put sourcing and time-to-hire in a panel of their own",
        [{
            "op": "group_tiles",
            "tiles": ["tile-sourcing", "tile-time-to-hire"],
            "title": "Channel and speed",
            "id": "group-channels",
        }],
    ),
    (
        "move time-to-hire above sourcing",
        [{"op": "move_tile", "tile": "tile-time-to-hire", "before": "tile-sourcing"}],
    ),
]


def build_widgets() -> str:
    """Two worked widgets, then the same widget edited by placement operations."""
    from nexcraftviz.compose import apply_widget_ops, widget
    from nexcraftviz.compose.vega import concat
    from nexcraftviz.examples import (
        completion_rate_tile,
        sourcing_donut,
        talent_acquisition_widget,
        time_to_hire,
    )

    sections: list[str] = []

    # 1 — the compound tile. It declares span="third", which is right on a
    # dashboard and wrong shown on its own, so widen it for the demo — using
    # the placement op, since that is the point being made.
    compound = apply_widget_ops(
        widget(completion_rate_tile(), title="", layout="single_column"),
        [{"op": "set_span", "tile": "tile-completion-rate", "span": "full"}],
    ).widget
    sections.append(_step(
        1,
        "A compound tile",
        "One card holding a headline with its delta, a multi-ring gauge, and the "
        "three counts the headline is made of. Vega-Lite cannot express this — "
        "the headline and the strip are card furniture around a chart, not marks "
        "inside it — which is the whole reason widgets exist alongside "
        "<code>vconcat</code>.",
        f'<div style="max-width:380px">{compound.to_html(id_prefix="w1-")}</div>',
        aside=(
            '<p class="nxv-aside__label">Two details that silently break this</p>'
            "<ul class=\"nxv-changes\">"
            "<li><code>startAngle</code> belongs on the mark while the end angle is "
            "a <code>theta</code> encoding with <code>scale: null</code>. Setting "
            "both as mark properties validates, compiles, and draws nothing.</li>"
            "<li><code>autosize: none</code>. The default re-fits the view around "
            "the marks, which shifts explicitly positioned arcs off centre.</li>"
            "</ul>"
        ),
    ))

    # 2 — the grouped widget.
    talent = talent_acquisition_widget()
    sections.append(_step(
        2,
        "A grouped widget",
        "A titled panel holding a hero funnel beside its conversion figures, then "
        "two half-width panels below. Groups give a widget internal structure, so "
        "it is not one flat grid of equal cards.",
        talent.to_html(id_prefix="w2-"),
        aside=_widget_structure(talent),
    ))

    # 3 — placement as operations.
    edited = talent
    for index, (instruction, ops) in enumerate(WIDGET_EDITS):
        result = apply_widget_ops(edited, ops)
        edited = result.widget
        changes = "".join(f"<li><code>{escape(c)}</code></li>" for c in result.describe()[:6])
        failures = "".join(
            f'<li class="nxv-warn">{escape(name)}: {escape(reason)}</li>'
            for name, reason in result.failed
        )
        sections.append(_step(
            3 + index,
            f"“{instruction}”",
            "Placement is edited the same way a chart is: operations in, a diff "
            "out, and an inverse patch for free.",
            edited.to_html(id_prefix=f"w{3 + index}-"),
            aside=(
                '<p class="nxv-aside__label">Operations</p>'
                f'<pre class="nxv-code">{escape(json.dumps(ops, indent=2))}</pre>'
                '<p class="nxv-aside__label">What changed</p>'
                f'<ul class="nxv-changes">{changes or "<li>(no change)</li>"}{failures}</ul>'
            ),
        ))

    # 4 — the other way of combining: one spec.
    combined = concat(
        [sourcing_donut(), time_to_hire()],
        direction="horizontal",
        title="Sourcing and speed",
        subtitle="One spec, not two tiles",
    )
    sections.append(_step(
        3 + len(WIDGET_EDITS),
        "The other way: one spec",
        "When everything being combined is a Vega view, <code>concat</code> gives "
        "a single spec instead — one render, one PNG export, and the option of "
        "shared scales so panels can be read against each other. It cannot hold a "
        "KPI tile or a rich table, which is exactly when a widget is needed.",
        render_card(title="", body=render_chart_mount(combined, "widget-concat")),
        aside=(
            '<p class="nxv-aside__label">Shared data hoisted to the root</p>'
            f'<pre class="nxv-code">{escape(json.dumps(sorted(combined.raw), indent=2))}</pre>'
        ),
    ))

    return _page(
        title="nexcraftviz — combining charts",
        lede=(
            "Two ways to put several charts together, and why both exist. Every "
            "panel below is generated by the package."
        ),
        body="\n".join(sections),
        nav_active="widgets",
    )


def _widget_structure(widget_obj) -> str:
    """The widget's own structure, as a small tree."""
    from nexcraftviz.compose.widget import Group

    lines = []
    for node in widget_obj.nodes:
        if isinstance(node, Group):
            lines.append(f"<li><code>{escape(node.id)}</code> · group · {escape(node.span)}<ul>")
            for child in node.tiles:
                lines.append(
                    f"<li><code>{escape(child.id)}</code> · "
                    f"{escape(child.family)} · {escape(child.span)}</li>"
                )
            lines.append("</ul></li>")
        else:
            lines.append(
                f"<li><code>{escape(node.id)}</code> · "
                f"{escape(node.family)} · {escape(node.span)}</li>"
            )
    counts = ", ".join(f"{n} {f}" for f, n in sorted(widget_obj.family_counts().items()))
    return (
        '<p class="nxv-aside__label">Structure</p>'
        f'<ul class="nxv-changes">{"".join(lines)}</ul>'
        f'<p class="nxv-aside__label">Families</p><p class="nxv-step__note">{escape(counts)}</p>'
    )


# ---------------------------------------------------------------------------
# the reference renderer
# ---------------------------------------------------------------------------

#: One KPI per subtype, including the two vocabularies that exist in the wild.
REFERENCE_KPIS: list[KpiCard] = [
    KpiCard(chart_subtype="counter", label="Active users", value=14850,
            change_pct=4.2, change_direction="up"),
    KpiCard(chart_subtype="percentage", label="Completion rate", value=87.4, unit="%",
            change_pct=1.8, change_direction="up"),
    KpiCard(chart_subtype="percent_change", label="Churn vs last quarter", value=3.1,
            unit="%", change_pct=0.4, change_direction="down", invert_sentiment=True),
    KpiCard(chart_subtype="target_vs_actual", label="Compliance coverage", value=87.4,
            unit="%", target=90.0),
]


def build_reference() -> str:
    """Every renderer this package can draw, in one page.

    Exists because ``table_with_cells`` and ``kpi_metadata`` have no renderer in
    production: a frontend adopting them needs one place that shows what the
    markup and the class vocabulary are meant to produce. Generated from
    :mod:`nexcraftviz.render.html`, so it cannot drift from the implementation.
    """
    cells = TableSpec(
        columns=[
            Column(field="avatar_name", header="Owner", render="avatar_name",
                   subtitle_field="text"),
            Column(field="text", header="Team", render="text"),
            Column(field="progress_bar", header="Progress", render="progress_bar", max=100),
            Column(field="heatmap_cell", header="Load", render="heatmap_cell", min=0, max=100),
            Column(field="sparkline", header="Trend", render="sparkline"),
            Column(field="pill", header="Status", render="pill",
                   color_map={"On track": "pass", "At risk": "fix",
                              "Blocked": "fail", "Not started": "muted"}),
            Column(field="badge", header="Tier", render="badge"),
            Column(field="trend_arrow", header="Δ", render="trend_arrow", format="+.1f"),
            Column(field="date", header="Due", render="date"),
            Column(field="number", header="Assigned", render="number", format=","),
        ]
    )
    cells = with_sample_rows(cells, count=4)

    sections = [
        _step(
            1,
            "Rich table — every cell renderer",
            "All ten renderers the corpus defines. Nothing downstream draws these "
            "today: a <code>table_with_cells</code> payload reaches the frontend, "
            "fails to parse as Vega-Lite and degrades to an untyped grid, so "
            "avatars, bars and pills are silently dropped. This is the markup "
            "that fixes it.",
            render_card(title="Team delivery", subtitle="10 renderers",
                        body=render_table(cells)),
        ),
        _step(
            2,
            "KPI tiles — every subtype",
            "Two vocabularies exist in the wild and both are supported. Note the "
            "third tile: churn falling is good news with a down arrow, so "
            "direction and sentiment are tracked separately.",
            '<div class="nxv-grid nxv-grid--kpi-row">'
            + "".join(render_card(title="", body=render_kpi(k)) for k in REFERENCE_KPIS)
            + "</div>",
        ),
        _step(
            3,
            "Charts",
            "The third family, for completeness — these already have a renderer. "
            "Every colour on this page, chart or not, comes from the same theme "
            "tokens; the toggle above proves it.",
            '<div class="nxv-grid">'
            + render_card(title="Revenue by region", subtitle="ranked bar",
                          body=render_chart_mount(_reference_bar(), "reference-bar"))
            + render_card(title="Revenue by quarter", subtitle="multi-series",
                          body=render_chart_mount(_reference_line(), "reference-line"))
            + "</div>",
        ),
    ]

    return _page(
        title="nexcraftviz — reference renderer",
        lede=(
            "The three payload families this stack emits, all drawn from one "
            "theme. Charts go through Vega; KPI tiles and rich tables do not — "
            "which is why theming needs a CSS half."
        ),
        body="\n".join(sections),
        nav_active="index",
    )


def _reference_rows() -> list[dict[str, Any]]:
    return [
        {"region": r, "quarter": q, "revenue": v}
        for r, q, v in (
            ("West", "2026-Q1", 128), ("East", "2026-Q1", 96), ("North", "2026-Q1", 152),
            ("West", "2026-Q2", 141), ("East", "2026-Q2", 88), ("North", "2026-Q2", 175),
        )
    ]


def _reference_bar() -> Spec:
    return Spec({
        "data": {"values": _reference_rows()},
        "width": "container", "height": 200,
        "mark": "bar",
        "encoding": {
            "y": {"field": "region", "type": "nominal", "sort": "-x", "axis": {"title": None}},
            "x": {"field": "revenue", "type": "quantitative", "aggregate": "sum",
                  "axis": {"title": None}},
        },
    })


def _reference_line() -> Spec:
    return Spec({
        "data": {"values": _reference_rows()},
        "width": "container", "height": 200,
        "mark": {"type": "line", "point": True},
        "encoding": {
            # Quarter labels are ordinal, NOT temporal — Vega-Lite cannot parse
            # "2026-Q1" as a date. This is the defect tier-2 validation catches
            # in the shipped corpus.
            "x": {"field": "quarter", "type": "ordinal", "axis": {"title": None}},
            "y": {"field": "revenue", "type": "quantitative", "axis": {"title": None}},
            "color": {"field": "region", "type": "nominal"},
        },
    })


# ---------------------------------------------------------------------------
# page assembly
# ---------------------------------------------------------------------------

def _step(number: int, title: str, note: str, body: str, aside: str = "") -> str:
    aside_html = f'<aside class="nxv-aside">{aside}</aside>' if aside else ""
    return (
        f'<section class="nxv-step"><h2 class="nxv-step__title">'
        f'<span class="nxv-step__num">{number}</span>{escape(title)}</h2>'
        f'<p class="nxv-step__note">{note}</p>'
        f'<div class="nxv-step__body">{body}{aside_html}</div></section>'
    )


def _renderer_table(table: TableSpec) -> str:
    rows = "".join(
        f"<tr><td><code>{escape(c.field)}</code></td>"
        f"<td><code>{escape(c.render)}</code></td></tr>"
        for c in table.columns
    )
    return (
        '<p class="nxv-aside__label">Renderer chosen per column</p>'
        f'<table class="nxv-table nxv-table--compact"><tbody>{rows}</tbody></table>'
    )


def _page(*, title: str, lede: str, body: str, nav_active: str,
          standalone: bool = False) -> str:
    """The page shell.

    ``standalone`` makes one file that needs nothing beside it: the stylesheet
    inlined, no scripts, no CDN — because GitHub serves a repo's HTML as source
    and the only ways anyone sees it rendered are Pages, a preview proxy or a
    download, none of which will fetch its neighbours.
    """
    links = (
        ("index", "index.html", "Reference renderer"),
        ("usecase", "usecase.html", "Walkthrough"),
        ("widgets", "widgets.html", "Combining charts"),
        ("pipeline", "pipeline.html", "Pipeline & progress"),
        ("gallery", "gallery.html", "Corpus gallery"),
        ("capabilities", "capabilities.html", "What it can do"),
    )
    nav = "" if standalone else "".join(
        '<a href="{href}" class="{cls}">{label}</a>'.format(
            href=href, cls="is-active" if key == nav_active else "", label=label
        )
        for key, href, label in links
    )
    if standalone:
        from nexcraftviz.theme import load, to_bundle

        bundle = to_bundle(load("nexcraftviz-light"), load("nexcraftviz-dark"))
        head = f"<style>{bundle}\n{_playground_css()}\n{_PLATE_CSS}</style>"
        controls = ""
        scripts = ""
    else:
        head = (
            '<link rel="stylesheet" href="../assets/nexcraftviz.css">'
            '<link rel="stylesheet" href="playground.css">'
            '<script src="https://cdn.jsdelivr.net/npm/vega@5"></script>'
            '<script src="https://cdn.jsdelivr.net/npm/vega-lite@5"></script>'
            '<script src="https://cdn.jsdelivr.net/npm/vega-embed@6"></script>'
        )
        controls = (f'<div class="nxv-controls"><nav class="nxv-nav">{nav}</nav>'
                    '<div class="nxv-themes" id="themes"></div></div>')
        scripts = '<script src="playground.js"></script>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
{head}
</head>
<body>
<header class="nxv-page__header">
  <div>
    <h1>{escape(title)}</h1>
    <p class="nxv-lede">{lede}</p>
  </div>
  {controls}
</header>
<main>{body}</main>
{scripts}
</body>
</html>
"""


def _humanise(name: str) -> str:
    return name.replace("_", " ").capitalize()


# ---------------------------------------------------------------------------
# capabilities — what it draws, what to ask for, what comes back
# ---------------------------------------------------------------------------

#: Per chart type: enough questions and rows to see the shape of the ask.
#: Two hundred questions on one page is a corpus dump, not a guide.
CAPABILITY_QUESTIONS = 3
CAPABILITY_ROWS = 4


def build_capabilities(standalone: bool = False) -> str:
    """Every chart type, the questions it answers, its data, and its output.

    Generated from the corpus, so it cannot drift from what the planner will
    choose: the pairs shown here are the ones the recommender ranks and the
    generator adapts.
    """
    corpus = seed()
    counts = corpus.chart_type_counts()
    ordered = sorted(counts, key=lambda name: (-counts[name], name))
    cards = "".join(
        _capability_card(name, corpus.by_chart_type(name), index, standalone)
        for index, name in enumerate(ordered)
    )
    families = sorted({pair.family for pair in corpus})
    # The filter bar is driven by playground.js; a standalone page runs no
    # script, and a row of buttons that do nothing is worse than none.
    filters = "" if standalone else (
        '<div class="nxv-filters"><button type="button" data-filter="" '
        'aria-pressed="true">all</button>'
        + "".join(
            f'<button type="button" data-filter="{escape(family)}">{escape(family)}</button>'
            for family in families
        )
        + "</div>"
    )
    body = (
        _capability_summary(corpus)
        + filters
        + f'<div class="nxv-gallery">{cards}</div>'
        + _beyond_charts()
    )
    return _page(
        title="nexcraftviz — what it can do",
        lede=(
            f"{len(ordered)} chart types, drawn from {len(corpus)} worked examples. "
            "For each: what to ask for it in plain language, the columns it needs, "
            "and what comes back — a Vega-Lite chart, a KPI card or a rich table."
        ),
        body=body,
        nav_active="capabilities",
        standalone=standalone,
    )


def _capability_summary(corpus: Any) -> str:
    from nexcraftviz.compose.layout import LAYOUTS
    from nexcraftviz.spec.ops import OP_REGISTRY
    from nexcraftviz.theme import available_themes

    stats = (
        (len(corpus.chart_type_counts()), "chart types"),
        (len(corpus), "worked examples"),
        (len(OP_REGISTRY), "edit operations"),
        (len(LAYOUTS), "widget layouts"),
        (len(available_themes()), "themes"),
    )
    cells = "".join(
        f'<div class="nxv-stat"><span class="nxv-stat__label">{escape(label)}</span>'
        f'<span class="nxv-stat__value">{value}</span></div>'
        for value, label in stats
    )
    return f'<div class="nxv-stats">{cells}</div>'


def _capability_card(chart_type: str, pairs: list[ChartPair], index: int,
                     standalone: bool = False) -> str:
    pair = _representative(pairs)
    parts: list[str] = []

    questions = _example_questions(pairs)
    if questions:
        asked = "".join(f"<li>{escape(question)}</li>" for question in questions)
        parts.append('<p class="nxv-card__subtitle">Ask for it like this</p>'
                     f"<ul>{asked}</ul>")

    parts.append('<p class="nxv-card__subtitle">Data in</p>' + _data_in(pair))
    parts.append('<p class="nxv-card__subtitle">What comes back</p>'
                 + _output(pair, index, standalone))

    if pair.use_when:
        good = "".join(f"<li>{escape(line)}</li>" for line in pair.use_when[:3])
        parts.append(f'<p class="nxv-card__subtitle">Use when</p><ul>{good}</ul>')
    if pair.do_not_use_when:
        bad = "".join(f"<li>{escape(line)}</li>" for line in pair.do_not_use_when[:2])
        parts.append(f'<p class="nxv-card__subtitle">Not when</p><ul>{bad}</ul>')
    if pair.insight:
        parts.append(f'<p class="nxv-insight">{escape(pair.insight)}</p>')

    subtitle = f"{pair.family} · {len(pairs)} worked example(s)"
    return (
        f'<div class="nxv-card nxv-gallery__item" data-chart-type="{escape(pair.family)}">'
        f'<div class="nxv-card__header"><h3 class="nxv-card__title">'
        f"{escape(_humanise(chart_type))}</h3>"
        f'<span class="nxv-card__subtitle">{escape(subtitle)}</span></div>'
        f'<div class="nxv-card__body">{"".join(parts)}</div></div>'
    )


def _representative(pairs: list[ChartPair]) -> ChartPair:
    """The example to show: one that ships a spec with rows, if any does."""
    with_rows = [p for p in pairs if p.has_vega_spec and p.spec().data_values]
    return (with_rows or pairs)[0]


def _example_questions(pairs: list[ChartPair]) -> list[str]:
    seen: set[str] = set()
    questions: list[str] = []
    for pair in pairs:
        for question in pair.example_questions:
            key = " ".join(question.lower().split())
            if key and key not in seen:
                seen.add(key)
                questions.append(question)
            if len(questions) == CAPABILITY_QUESTIONS:
                return questions
    return questions


def _data_in(pair: ChartPair) -> str:
    """The columns the chart needs, and a few rows of the worked example."""
    columns = (pair.data_shape or {}).get("columns") or []
    if columns:
        rows = "".join(
            f'<tr><td><code>{escape(column.get("name", ""))}</code></td>'
            f'<td><code>{escape(column.get("type", ""))}</code></td></tr>'
            for column in columns
            if isinstance(column, dict)
        )
        shape = ('<table class="nxv-table"><thead><tr><th>column</th><th>type</th></tr>'
                 f"</thead><tbody>{rows}</tbody></table>")
    else:
        shape = '<p class="nxv-overview">Whatever columns the rows carry.</p>'

    sample = ""
    if pair.has_vega_spec:
        values = pair.spec().data_values
        if values:
            sample = ('<details class="nxv-details"><summary>Sample rows '
                      f"({len(values)})</summary>"
                      f"{render_table(build_table(values), max_rows=CAPABILITY_ROWS)}</details>")
    return shape + sample


#: A drawn chart keeps its light ink, so it sits on a light plate in either
#: theme rather than turning invisible on a dark page.
_PLATE_CSS = """
.nxv-plate { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px;
  padding: 10px; overflow-x: auto; }
.nxv-plate svg { display: block; max-width: 100%; height: auto; }
"""


def _chart_svg(spec: Spec) -> str:
    """One chart, drawn now — for a page that will run no JavaScript."""
    from nexcraftviz.render import RenderError, available, to_svg
    from nexcraftviz.theme import apply_theme, load

    if not available():
        return '<p class="nxv-warn">Install the render extra to draw this chart.</p>'
    try:
        themed = apply_theme(spec, load("nexcraftviz-light")).spec
        _fill_widths(themed.raw)
        return f'<figure class="nxv-plate">{to_svg(themed)}</figure>'
    except (RenderError, Exception) as exc:  # noqa: BLE001 - one chart must not sink the page
        return f'<p class="nxv-warn">This chart did not draw: {escape(str(exc))}</p>'


def _fill_widths(node: Any, width: int = 460) -> None:
    """`width: "container"` means "fill the card"; a drawn SVG has no card."""
    if isinstance(node, dict):
        if node.get("width") == "container":
            node["width"] = width
        for value in node.values():
            _fill_widths(value, width)
    elif isinstance(node, list):
        for item in node:
            _fill_widths(item, width)


def _output(pair: ChartPair, index: int, standalone: bool = False) -> str:
    if pair.family == "kpi-card":
        return render_kpi(KpiCard.from_columns_schema(pair.columns_schema or {}))
    if pair.family == "table-with-cells":
        table = with_sample_rows(TableSpec.from_columns_schema(pair.columns_schema or []))
        return (render_table(table, max_rows=CAPABILITY_ROWS)
                + '<p class="nxv-warn">Rows synthesised — this pair ships none.</p>')
    if standalone:
        return _chart_svg(pair.spec())
    return render_chart_mount(pair.spec(), f"capability-{index}")


def _beyond_charts() -> str:
    """The rest of the vocabulary: editing, arranging, theming, whole dashboards."""
    from typing import get_args

    from nexcraftviz.compose.layout import LAYOUTS
    from nexcraftviz.manager.decision import Action
    from nexcraftviz.spec.ops import OP_REGISTRY
    from nexcraftviz.theme import available_themes
    from nexcraftviz.workflows import builtin_skills

    operations = "".join(
        f"<li><code>{escape(name)}</code> — "
        f"{escape(_first_line(OP_REGISTRY[name].__doc__))}</li>"
        for name in sorted(OP_REGISTRY)
    )
    said = ", ".join(f"<code>{escape(action)}</code>" for action in get_args(Action))
    layouts = ", ".join(f"<code>{escape(name)}</code>" for name in sorted(LAYOUTS))
    themes = ", ".join(f"<code>{escape(name)}</code>" for name in available_themes())

    intents: list[str] = []
    for skill in builtin_skills().values():
        for intent in skill.intents:
            steps = " → ".join(
                step.uses or f"{step.pause} (a person)"
                for step in skill.workflows[intent.workflow].steps
            )
            intents.append(f"<li><b>{escape(intent.label)}</b><br><code>{escape(steps)}</code></li>")

    return (
        _panel("Editing a chart, by instruction",
               '<p class="nxv-overview">A change is an operation with an inverse, not a '
               "regeneration: undo is free and a correct spec cannot be broken by "
               f'"make the bars teal".</p><ul>{operations}</ul>')
        + _panel("One instruction, several actions",
                 f'<p class="nxv-overview">The manager reads free text and answers with '
                 f"ordered steps: {said}. What it cannot do — a breakdown needing data "
                 "that is not in the rows — it declines, with the reason.</p>")
        + _panel("Arranging and theming",
                 f'<p class="nxv-overview">Widget layouts: {layouts}. Themes: {themes} — '
                 "applied after generation, so a hard-coded colour never defeats them.</p>")
        + _panel("Whole dashboards, as workflows",
                 '<p class="nxv-overview">Declared in a skill file: a person picks and '
                 f"approves, the host queries and publishes.</p><ul>{''.join(intents)}</ul>")
    )


def _panel(title: str, body: str) -> str:
    return (f'<section class="nxv-step"><h2 class="nxv-step__title">{escape(title)}</h2>'
            f'<div class="nxv-step__body">{body}</div></section>')


def _first_line(text: str | None) -> str:
    return (text or "").strip().splitlines()[0] if text else ""


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def write(out_dir: str | Path, *, limit: int | None = None) -> list[Path]:
    """Generate every page plus its supporting assets."""
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)

    written = [
        _write(target / "index.html", build_reference()),
        _write(target / "usecase.html", build_use_case()),
        _write(target / "widgets.html", build_widgets()),
        _write(target / "pipeline.html", build_pipeline()),
        _write(target / "gallery.html", build_gallery(limit)),
        _write(target / "capabilities.html", build_capabilities()),
        _write(target / "playground.css", _playground_css()),
        _write(target / "playground.js", _playground_js()),
    ]
    return written


def write_standalone(path: str | Path) -> Path:
    """The capabilities page as one file that needs nothing beside it."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return _write(target, build_capabilities(standalone=True))


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _playground_css() -> str:
    """Page chrome only. Everything that styles *content* comes from the
    generated theme bundle, which is the point being demonstrated."""
    return """/* Page chrome for the generated playground pages. Content styling comes
   entirely from ../assets/nexcraftviz.css — that separation is deliberate. */
body {
  background: var(--nxv-background);
  color: var(--nxv-text);
  font-family: var(--nxv-font);
  font-size: var(--nxv-size-base);
  margin: 0;
  padding: calc(var(--nxv-space) * 3);
}

.nxv-page__header {
  align-items: flex-start;
  border-bottom: 1px solid var(--nxv-border);
  display: flex;
  flex-wrap: wrap;
  gap: calc(var(--nxv-space) * 2);
  justify-content: space-between;
  margin-bottom: calc(var(--nxv-space) * 3);
  padding-bottom: calc(var(--nxv-space) * 2);
}

h1 { font-size: var(--nxv-size-xl); margin: 0 0 6px; }
.nxv-lede { color: var(--nxv-text-secondary); margin: 0; max-width: 68ch; }

.nxv-controls { display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }
.nxv-nav { display: flex; gap: 4px; }
.nxv-nav a {
  border: 1px solid transparent;
  border-radius: var(--nxv-radius-sm);
  color: var(--nxv-text-secondary);
  padding: 4px 10px;
  text-decoration: none;
}
.nxv-nav a.is-active { border-color: var(--nxv-border-strong); color: var(--nxv-text); }

button {
  background: var(--nxv-surface);
  border: 1px solid var(--nxv-border-strong);
  border-radius: var(--nxv-radius-sm);
  color: var(--nxv-text);
  cursor: pointer;
  font: inherit;
  font-size: var(--nxv-size-sm);
  padding: 4px 10px;
}
button[aria-pressed="true"] {
  background: var(--nxv-accent);
  border-color: var(--nxv-accent);
  color: var(--nxv-text-inverse);
}
.nxv-themes, .nxv-filters { display: flex; flex-wrap: wrap; gap: 4px; }
.nxv-filters { margin-bottom: calc(var(--nxv-space) * 2); }

/* ---- walkthrough ---- */
.nxv-step { margin-bottom: calc(var(--nxv-space) * 5); }
.nxv-step__title {
  align-items: center;
  display: flex;
  font-size: var(--nxv-size-lg);
  gap: var(--nxv-space);
  margin: 0 0 4px;
}
.nxv-step__num {
  align-items: center;
  background: var(--nxv-accent);
  border-radius: 50%;
  color: var(--nxv-text-inverse);
  display: flex;
  font-size: var(--nxv-size-sm);
  height: 24px;
  justify-content: center;
  width: 24px;
}
.nxv-step__note {
  color: var(--nxv-text-secondary);
  margin: 0 0 calc(var(--nxv-space) * 1.5);
  max-width: 72ch;
}
.nxv-step__body { align-items: start; display: grid; gap: calc(var(--nxv-space) * 2); }
.nxv-step__body:has(.nxv-aside) { grid-template-columns: minmax(0, 2fr) minmax(240px, 1fr); }

.nxv-aside {
  background: var(--nxv-surface-alt);
  border-radius: var(--nxv-radius-md);
  padding: calc(var(--nxv-space) * 1.5);
}
.nxv-aside__label {
  color: var(--nxv-text-secondary);
  font-size: var(--nxv-size-xs);
  letter-spacing: .06em;
  margin: 0 0 6px;
  text-transform: uppercase;
}
.nxv-aside .nxv-table { font-size: var(--nxv-size-sm); }
.nxv-aside .nxv-table td { padding: 3px 6px; }

.nxv-code {
  background: var(--nxv-surface);
  border: 1px solid var(--nxv-border);
  border-radius: var(--nxv-radius-sm);
  font-family: var(--nxv-font-mono);
  font-size: var(--nxv-size-xs);
  margin: 0 0 var(--nxv-space);
  max-height: 320px;
  overflow: auto;
  padding: var(--nxv-space);
}
.nxv-changes { margin: 0; padding-left: 18px; }
.nxv-changes li { color: var(--nxv-text-secondary); font-size: var(--nxv-size-sm); }
.nxv-changes code { font-family: var(--nxv-font-mono); }

/* ---- gallery ---- */
.nxv-gallery {
  display: grid;
  gap: calc(var(--nxv-space) * 2);
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
}
/* Time-axis charts need horizontal room: a gantt's last bar and a milestone's
   last label are the first things to go when the card narrows, and both are
   the end of the story. The corpus gallery's rings and donuts do not care. */
.nxv-gallery--wide { grid-template-columns: repeat(auto-fill, minmax(520px, 1fr)); }
.nxv-gallery__item[hidden] { display: none; }
.nxv-overview { color: var(--nxv-text); margin: var(--nxv-space) 0 0; }
.nxv-note {
  color: var(--nxv-text-muted);
  font-size: var(--nxv-font-size-sm);
  margin: var(--nxv-space) 0 2px;
}
/* The routing conditions, set small — they are reference, not prose. */
.nxv-conditions {
  margin: 0 0 var(--nxv-space);
  padding-left: 18px;
  color: var(--nxv-text-muted);
  font-size: var(--nxv-font-size-sm);
  line-height: 1.5;
}
.nxv-conditions--avoid { color: var(--nxv-text-subtle, var(--nxv-text-muted)); }
.nxv-insight {
  border-left: 3px solid var(--nxv-accent);
  color: var(--nxv-text-secondary);
  font-size: var(--nxv-size-sm);
  margin: var(--nxv-space) 0 0;
  padding-left: var(--nxv-space);
}
.nxv-warn { color: var(--nxv-warning); font-size: var(--nxv-size-xs); margin: 8px 0 0; }
.nxv-details { margin-bottom: var(--nxv-space); }
.nxv-details summary {
  color: var(--nxv-text-secondary);
  cursor: pointer;
  font-size: var(--nxv-size-sm);
  margin-bottom: var(--nxv-space);
}
.nxv-table--compact td { padding: 3px 6px; }

@media (max-width: 900px) {
  .nxv-step__body:has(.nxv-aside) { grid-template-columns: 1fr; }
}
"""


def _playground_js() -> str:
    """Theme switching plus chart mounting.

    Charts read their spec from ``window.__nxvSpecs``, which the generated
    markup populates, and the Vega config is rebuilt from the live CSS custom
    properties on every theme change — so the charts and the DOM around them can
    never disagree about what theme is active.
    """
    return """const THEMES = ['nexcraftviz-light', 'nexcraftviz-dark'];
let current = 'nexcraftviz-light';

const bar = document.getElementById('themes');
if (bar) {
  THEMES.forEach(name => {
    const button = document.createElement('button');
    button.textContent = name.replace('nexcraftviz-', '');
    button.setAttribute('aria-pressed', String(name === current));
    button.onclick = () => {
      current = name;
      document.documentElement.setAttribute(
        'data-nxv-theme', name.endsWith('dark') ? 'dark' : 'light');
      [...bar.children].forEach((b, i) =>
        b.setAttribute('aria-pressed', String(THEMES[i] === name)));
      mountCharts();
    };
    bar.appendChild(button);
  });
}
document.documentElement.setAttribute('data-nxv-theme', 'light');

/** Build a Vega config from the CSS custom properties currently in force. */
function vegaConfig() {
  const style = getComputedStyle(document.documentElement);
  const read = name => style.getPropertyValue(name).trim();
  const count = parseInt(read('--nxv-cat-count') || '0', 10);
  const categorical = Array.from({length: count}, (_, i) => read(`--nxv-cat-${i + 1}`));
  return {
    background: 'transparent',
    font: read('--nxv-font'),
    view: {stroke: 'transparent'},
    range: {category: categorical},
    axis: {
      labelColor: read('--nxv-text-secondary'),
      titleColor: read('--nxv-text-secondary'),
      gridColor: read('--nxv-grid'),
      domainColor: read('--nxv-border'),
      tickColor: read('--nxv-border'),
    },
    axisBand: {grid: false, domain: false, ticks: false},
    legend: {
      labelColor: read('--nxv-text-secondary'),
      titleColor: read('--nxv-text-secondary'),
      symbolType: 'circle',
    },
    title: {color: read('--nxv-text'), subtitleColor: read('--nxv-text-secondary')},
    bar: {fill: categorical[0], cornerRadiusEnd: 3},
    line: {stroke: categorical[0], strokeWidth: 2},
    point: {fill: categorical[0], filled: true},
    arc: {fill: categorical[0]},
    rect: {fill: categorical[0]},
    text: {fill: read('--nxv-text')},
  };
}

function mountCharts() {
  const specs = window.__nxvSpecs || {};
  const config = vegaConfig();
  Object.entries(specs).forEach(([id, spec]) => {
    const el = document.getElementById(id);
    if (!el) return;
    // A spec that already carries a config chose it deliberately (the themed
    // panel in the walkthrough); leave that one alone.
    const merged = spec.config ? spec : {...spec, config};
    vegaEmbed(el, merged, {actions: false}).catch(err => {
      el.innerHTML = '<p class="nxv-warn">Chart failed to render: ' + err.message + '</p>';
    });
  });
}

/** Filter the gallery by chart type. */
const filters = document.querySelector('.nxv-filters');
if (filters) {
  filters.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    const wanted = button.dataset.filter;
    [...filters.children].forEach(b =>
      b.setAttribute('aria-pressed', String(b === button)));
    document.querySelectorAll('.nxv-gallery__item').forEach(item => {
      item.hidden = Boolean(wanted) && item.dataset.chartType !== wanted;
    });
  });
}

mountCharts();
"""
