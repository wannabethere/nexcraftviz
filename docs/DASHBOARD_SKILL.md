# The dashboard skill — design

**Status: design.** How nexcraftviz builds dashboards out of widgets and hands
them to a host to publish, with the workflows defined as data rather than code.
Host-specific integration — a host's storage shapes, its UI, its publish calls —
lives in that host's bridge, not here.

## What it is for

An agent builds **dashboards** out of **widgets**. A widget holds **one chart or
several**, and can carry a **narration** (what the charts show) and a **data
table**. Building one is a workflow on top of chart generation — and a
different host, or a different kind of dashboard, wants a different workflow.
So the workflows are **declared in a skill file**, and the user starts the skill
with an **intent** that picks one.

## The model

```
Dashboard            title, template, layout (12-col {i,x,y,w,h}), state draft|published
└── Widget           title, subtitle, layout kind
    ├── charts       1..n — Vega-Lite specs or KPI cards (kpi_metadata), each with a span
    ├── narration    headline, summary, points          (optional)
    └── table        columns (cell renderers) + rows     (optional)
```

Today `Widget` has charts, KPI cards and tables as tiles, but no narration, and
there is no dashboard object (see `DASHBOARDS.md`).

### The widget document, version 2

```json
{
  "kind": "nexcraftviz.widget",
  "version": 2,
  "title": "Completion by business unit",
  "layout": "kpi_row_plus_grid",
  "nodes": [
    {"id": "kpi", "family": "kpi", "span": "quarter", "payload": {"kpi_metadata": {"chart_subtype": "percentage", "label": "Completion", "value": 82.4, "unit": "%"}}},
    {"id": "bar", "family": "vega-lite", "span": "three-quarters", "spec": {"mark": "bar"}}
  ],
  "narration": {"headline": "Logistics is furthest behind", "summary": "…", "points": []},
  "table": {"columns": [{"field": "unit", "header": "Unit", "render": "text"}], "rows": []}
}
```

`narration` and `table` are the additions; `nodes` is today's `Widget.to_dict()`.

## The skill file

One YAML file per skill, e.g. `nexcraftviz/workflows/dashboard.yaml`.
**Intents** are what the user starts with; **workflows** are declared steps;
**actions** are the step vocabulary, each registered in code once — so a new
workflow is a change to data, not to code.

```yaml
skill: dashboard
version: 1
summary: Build widgets, arrange them into a dashboard, and publish it.

intents:
  - id: from_question
    label: Build a dashboard that answers a question
    asks: {question: What should this dashboard answer?}
    workflow: build_dashboard
  - id: from_widgets
    label: Arrange widgets I already have
    asks: {widgets: Which widgets should go on it?}
    workflow: assemble_dashboard
  - id: enrich_widget
    label: Add a narration, a table or another chart to a widget
    asks: {widget: Which widget?, parts: What should it add?}
    workflow: enrich_widget

workflows:
  build_widget:
    inputs: [question, rows]
    options: {narration: true, table: false}
    steps:
      - {id: chart,     uses: chart.create,   with: {question: $inputs.question, rows: $inputs.rows}}
      - {id: narration, uses: widget.narrate, when: $options.narration, with: {chart: $steps.chart}}
      - {id: table,     uses: widget.table,   when: $options.table,     with: {rows: $inputs.rows}}
      - {id: widget,    uses: widget.compose, with: {charts: [$steps.chart], narration: $steps.narration, table: $steps.table}}

  build_dashboard:
    steps:
      - {id: questions, uses: dashboard.suggest_questions, with: {question: $intent.question}}
      - {id: pick,      pause: select, prompt: Which of these should be on the dashboard?, options: $steps.questions}
      - {id: rows,      uses: host.query, for_each: $steps.pick}
      - {id: widgets,   uses: workflow.build_widget, for_each: $steps.rows}
      - {id: layout,    uses: dashboard.layout, with: {widgets: $steps.widgets}}
      - {id: review,    pause: approve, prompt: Publish this dashboard?, show: $steps.layout}
      - {id: publish,   uses: host.publish, with: {dashboard: $steps.layout}}
```

### Step vocabulary

| Action | Runs in | Does | Today |
|---|---|---|---|
| `chart.create` | nexcraftviz | plan → generate → gates → critic | exists (`/v1/chart/create`) |
| `chart.edit` | nexcraftviz | one instruction against a chart | exists (`/v1/chart/annotate`) |
| `widget.compose` | nexcraftviz | charts, KPIs, table → one widget | exists (`viz.compose`); needs narration + table parts |
| `widget.narrate` | nexcraftviz | headline, summary, points, stored on the widget | skill exists (`viz.narrate`); result not stored |
| `widget.table` | nexcraftviz | rows → rich table with cell renderers | `TableSpec` exists; no action |
| `widget.place` | nexcraftviz | re-span and reorder tiles | exists (`viz.place`) |
| `dashboard.suggest_questions` | nexcraftviz or host | the questions an intent needs | new |
| `dashboard.layout` | nexcraftviz | widgets → 12-col grid, from a template | new (`DASHBOARDS.md` D1–D3) |
| `dashboard.suggest_groups` | nexcraftviz | "these three belong together" — offered, never applied | new (D4) |
| `host.query` | host | question → SQL → rows | the host's |
| `host.save_draft` | host | persist a draft dashboard | the host's |
| `host.publish` | host | publish it | the host's |
| `host.share` | host | share it | the host's |

### Rules the loader enforces

- Every `uses` is a registered action or a workflow in the file; every
  `$steps.x` names an earlier step; every `$intent.x` is an ask of the intent.
- `host.*` steps never run inside nexcraftviz. The run pauses and hands them to
  the host — nexcraftviz stays DB-free and never publishes on its own.
- **`host.publish` must follow an `approve` pause.** A dashboard is never
  published without a person saying yes.
- Pauses are `ask` (free text), `select` (pick from options) and `approve`
  (yes / no, with a preview).

## How a run flows

```
Host UI                      nexcraftviz                        Host backend
  | GET /v1/workflows  ───────▶ intents for the picker
  | POST …/runs {intent, answers}
  |                  ◀─────── paused: select {questions}
  | POST …/resume {answer: picked}
  |                  ◀─────── needs host: host.query [q1, q2, q3]
  | runs SQL ─────────────────────────────────────────────────▶ rows
  | POST …/resume {host_result: rows}
  |                  ◀─────── paused: approve {dashboard preview}
  | POST …/resume {answer: yes}
  |                  ◀─────── needs host: host.publish {dashboard}
  | publish ──────────────────────────────────────────────────▶ published
  | POST …/resume {host_result: dashboard_id}
  |                  ◀─────── done
```

## API contract

| Method | Path | Body → returns |
|---|---|---|
| GET | `/v1/workflows` | skills, their intents, asks and step outline |
| POST | `/v1/workflows/{skill}/runs` | `{intent, answers, inputs, options}` → run |
| GET | `/v1/workflows/runs/{run_id}` | run |
| POST | `/v1/workflows/runs/{run_id}/resume` | `{answer}` or `{host_result}` → run |
| DELETE | `/v1/workflows/runs/{run_id}` | cancel |

```json
{
  "run_id": "run_7f3a",
  "skill": "dashboard",
  "intent": "from_question",
  "status": "paused",
  "step": "pick",
  "pending": {
    "kind": "select",
    "prompt": "Which of these should be on the dashboard?",
    "options": [{"id": "q1", "text": "Completion rate by business unit", "visual": "bar"}]
  },
  "artifacts": {"widgets": [], "dashboard": null},
  "trace": ["questions: 5 suggested"]
}
```

`status` is `running | paused | needs_host | done | failed`. A `needs_host`
run's `pending` is `{kind: "host", action: "host.query", requests: [...]}`; the
host resumes with `{host_result: {...}}` keyed by request id.

## Work

| # | Scope | Exit criterion |
|---|---|---|
| N1 | Widget parts: `narration` and `table` on `Widget` (document v2); `widget.table`; narration stored on the widget | A widget with two charts, a narration and a table round-trips through `to_dict` / `from_dict` |
| N2 | Skill format: schema, loader, validator (the rules above) | A publish with no approve pause is rejected at load |
| N3 | Executor: steps, pauses, host hand-offs; run store (in-memory, TTL); the five endpoints | `from_widgets` runs end to end over the API with a stub model and a stub host |
| N4 | `workflows/dashboard.yaml` with three intents; `dashboard.layout`; `dashboard.suggest_questions` | Each intent has a harness scenario that passes offline and live |
| N5 | Grouping suggestions (`DASHBOARDS.md` D4) | A grouping offer appears while picking; nothing merges until accepted |

## Open

- **Run state.** In-memory with a TTL is enough for one instance; more than one
  needs a shared store or sticky routing.
- **Prior art.** YAML playbook engines exist elsewhere with these ideas
  (depends-on, fan-out, ask / approval / selection pauses). This design takes
  the vocabulary and keeps the executor small: it only has to run the steps
  above.
