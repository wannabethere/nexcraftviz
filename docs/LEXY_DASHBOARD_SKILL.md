# Lexy dashboard skill — integration plan

**Status: plan, for the Lexy UI, backend and nexcraftviz teams.** Drafted
2026-09-10 from a read of the code in all three places; every "exists" below
has a file reference in the appendix.

## The ask

An agent builds **dashboards** out of **widgets** and **publishes** them in
Lexy. A widget holds **one chart or several**, and can carry a **narration**
(what the chart shows) and a **data table**. These are workflows on top of
chart generation, and they are **defined as data in a dashboard skill**, not
hardcoded: the user starts the skill with an **intent**, and the intent picks
the workflow.

## Three facts that change the work

1. **The live dashboards page is not the one with the grid.**
   `/dashboards/:id` renders `DashboardsPane.jsx` — CSS masonry, no stored
   coordinates. `DashboardsComponent1.jsx` has the 12-column react-grid-layout,
   the chart / table / overview / insights sections, and the nexcraftviz
   Annotate box and first chart — and is not in any route. Nothing below lands
   for users until one of them is chosen (ticket L0).
2. **Lexy already has a dashboard lifecycle.** Draft → edit → publish, share
   (user, team, project, workspace, email, public link), schedule, versions —
   in workflowservices, reached through the astherabackend gateway. Publishing
   is a host concern; nexcraftviz should drive it, not reimplement it.
3. **nexcraftviz stops at the widget.** It composes charts, KPI cards and tables
   into one widget, but a tile has no narration field, there is no dashboard
   object, no publish, and no workflow-as-data. `docs/DASHBOARDS.md` (D1–D5) is
   design only.

## The model

```
Dashboard            title, template, layout (12-col {i,x,y,w,h}), state draft|published
└── Widget           title, subtitle, layout kind
    ├── charts       1..n — Vega-Lite specs or KPI cards (kpi_metadata), each with a span
    ├── narration    headline, summary, points          (optional)
    └── table        columns (cell renderers) + rows     (optional)
```

### Where each part lives in Lexy

| Widget part | Lexy storage (`ThreadComponent`) | Lexy section | Today |
|---|---|---|---|
| one chart | `chart_schema`, `chart_type` | chart | exists |
| several charts | `component_type: "widget"` + `configuration.nexcraftviz_widget` | chart (multi-chart renderer) | **new** |
| KPI card | `chart_schema` = `{kpi_metadata}` | chart / KPI tile | exists (InlineKpiTile, KpiCard) |
| narration | `overview` (markdown), `executive_summary` | overview / insights | exists |
| table | `table_config`, `sample_data` | table | exists |
| position on dashboard | `content.layout` via `PUT /{wf}/layout` | grid | endpoint exists, **no caller** |

A single-chart widget needs no schema change. Only multi-chart widgets need a
new `component_type`.

### The widget document (nexcraftviz → Lexy), version 2

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

## The dashboard skill

One YAML file, `nexcraftviz/workflows/dashboard.yaml`. **Intents** are what the
user starts with; **workflows** are declared steps; **actions** are the step
vocabulary, each registered in code once.

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
    asks: {widgets: Which widgets should go on it?}       # a picker, not free text
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
      - id: chart
        uses: chart.create                 # plan → generate → gates → critic
        with: {question: $inputs.question, rows: $inputs.rows}
      - id: narration
        uses: widget.narrate
        when: $options.narration
        with: {chart: $steps.chart}
      - id: table
        uses: widget.table
        when: $options.table
        with: {rows: $inputs.rows}
      - id: widget
        uses: widget.compose
        with: {charts: [$steps.chart], narration: $steps.narration, table: $steps.table}

  build_dashboard:
    steps:
      - id: questions
        uses: dashboard.suggest_questions  # the widgets this intent needs
        with: {question: $intent.question}
      - id: pick
        pause: select
        prompt: Which of these should be on the dashboard?
        options: $steps.questions
      - id: rows
        uses: host.query                   # Lexy runs the SQL
        for_each: $steps.pick
      - id: widgets
        uses: workflow.build_widget
        for_each: $steps.rows
      - id: layout
        uses: dashboard.layout
        with: {widgets: $steps.widgets}
      - id: review
        pause: approve
        prompt: Publish this dashboard?
        show: $steps.layout
      - id: publish
        uses: host.publish
        with: {dashboard: $steps.layout}
```

### Step vocabulary

| Action | Runs in | Does | Today |
|---|---|---|---|
| `chart.create` | nexcraftviz | plan → generate → gates → critic | exists (`/v1/chart/create`) |
| `chart.edit` | nexcraftviz | one instruction against a chart | exists (`/v1/chart/annotate`) |
| `widget.compose` | nexcraftviz | charts, KPIs, table → one widget | exists (`viz.compose`); needs narration + table parts |
| `widget.narrate` | nexcraftviz | headline, summary, points | skill exists (`viz.narrate`); result not stored on the widget |
| `widget.table` | nexcraftviz | rows → rich table (cell renderers) | `TableSpec` exists; no action |
| `widget.place` | nexcraftviz | re-span, reorder tiles | exists (`viz.place`) |
| `dashboard.suggest_questions` | nexcraftviz or genieml | the questions an intent needs | genieml `SuggestRelatedQuestionsSkill` exists |
| `dashboard.layout` | nexcraftviz | widgets → 12-col grid, from a template | new (DASHBOARDS.md D1–D3) |
| `dashboard.suggest_groups` | nexcraftviz | "these three belong together" | new (D4) |
| `host.query` | Lexy | question → SQL → rows | exists (Lexy SQL path) |
| `host.save_draft` | Lexy | create workflow, PATCH edit | exists (gateway) |
| `host.publish` | Lexy | PUT layout, POST publish | exists (gateway) |
| `host.share` | Lexy | share configuration | exists |

### Rules the loader enforces

- Every `uses` is a registered action or a workflow in the file.
- Every `$steps.x` names an earlier step; every `$intent.x` an ask of the intent.
- `host.*` steps never run inside nexcraftviz — the run pauses and hands them
  to Lexy. nexcraftviz stays DB-free and never publishes on its own.
- **`host.publish` must follow an `approve` pause.** A dashboard is never
  published without a person saying yes.
- Pauses are `ask` (free text), `select` (pick from options), `approve`
  (yes / no with a preview).

Borrowed vocabulary: `analysis-workflows` already has a full YAML playbook
engine with these ideas (depends-on, fan-out, ask / approval / selection
pauses, publish actions). It was abandoned on 2026-08-20 as over-engineered, so
this plan takes its words, not its engine: the executor here is small and only
has to run the steps above.

## How a run flows

```
Lexy UI                      nexcraftviz                         Lexy backend
  | GET /v1/workflows  ───────▶ intents for the picker
  | POST …/runs {intent, answers}
  |                  ◀─────── paused: select {questions}
  | POST …/resume {answer: picked}
  |                  ◀─────── needs host: host.query [q1, q2, q3]
  | runs SQL ───────────────────────────────────────────────────▶ rows
  | POST …/resume {host_result: rows}
  |                  ◀─────── paused: approve {dashboard preview}
  | POST …/resume {answer: yes}
  |                  ◀─────── needs host: host.publish {dashboard}
  | create → PATCH edit → PUT layout → POST publish ───────────▶ published
  | POST …/resume {host_result: dashboard_id}
  |                  ◀─────── done
```

## API contract (nexcraftviz)

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
run's `pending` is `{kind: "host", action: "host.query", requests: [...]}`;
Lexy resumes with `{host_result: {...}}` keyed by request id. Same auth as
today (`x-nexcraftviz-key`).

## Work, by team

### Lexy UI

- **L0 — pick the dashboards page.** Route the grid page (DashboardsComponent1)
  behind a flag, or port its grid, sections and nexcraftviz calls into
  DashboardsPane. The Annotate box and first chart already built live only in
  the unrouted page.
- **L1 — persist layout.** Save `{i,x,y,w,h}` with `PUT /{wf}/layout`
  (`saveDashboardLayout` exists, has no callers); remove the
  `PATCH /dashboards/{id}/edit` call, which has no gateway route.
- **L2 — WidgetCard.** Render a widget document: charts on the widget's own
  12-col grid by span, KPI cards via `kpi_metadata`, narration in the overview
  section, table via `TableWidget`, `title` / `subtitle` in the header.
- **L3 — "Build with Lexy" entry and intent picker** (from `GET /v1/workflows`).
- **L4 — run panel.** Progress from `trace`; `ask` / `select` / `approve`
  cards inline; host actions: `host.query` through the existing SQL path,
  `host.save_draft` / `host.publish` through the gateway.
- **L5 — publish.** Reuse DashboardPublishModal's sequence (create → PATCH edit
  → PUT layout → POST publish), with the skill's approve pause in front.
- **L6 — `nexcraftvizApi.js`.** `listWorkflows`, `startRun`, `getRun`,
  `resumeRun`.

### Backend (workflowservices + gateway)

- **B1 —** `ThreadComponent.component_type` gains `widget`; add-component,
  edit and publish accept `configuration.nexcraftviz_widget`.
- **B2 — gateway gaps.** Forward `thread_message_id` on add-component; add
  `DELETE` for a dashboard; add or drop `PATCH /dashboards/{id}/edit`.
- **B3 —** turn `DASHBOARD_STUB_ENABLED` off where this is tested; the creator
  returns a fake dashboard while it is on.
- **B4 (optional) —** normalise `dashboard_templates.layout` to `{i,x,y,w,h}`;
  its two UI readers disagree today.

### nexcraftviz

- **N1 — widget parts.** `narration` and `table` on `Widget` (document v2);
  `widget.table`; `widget.narrate` stores its result on the widget.
- **N2 — workflow format.** Schema, loader, validator (the rules above).
- **N3 — executor.** Runs steps, pauses, hands off host actions; run store
  (in-memory with a TTL); the five endpoints.
- **N4 — the skill.** `workflows/dashboard.yaml` with the three intents;
  `dashboard.layout` (DASHBOARDS.md D1–D2); `dashboard.suggest_questions`.
- **N5 — proof.** A harness scenario per intent, run with a stub model and
  live; evals for any new model step.

## Phases

| # | Tickets | Exit criterion |
|---|---|---|
| 1 | L0, L1, B2, B3 | A dashboard's layout survives a reload on the live page |
| 2 | N1, B1, L2 | A widget with two charts, a narration and a table is saved, reloaded and drawn |
| 3 | N2, N3, N4, L6 | `from_widgets` runs end to end over the API with a stub model; the validator rejects a publish with no approve pause |
| 4 | L3, L4, L5 | A user starts "Build a dashboard that answers a question", picks widgets, approves, and the published dashboard appears in the list |
| 5 | D4 grouping, templates | A grouping offer appears while picking; nothing merges until accepted |

Phases 1 and 3 can run in parallel — one is Lexy, the other nexcraftviz.

## Decisions to make

1. **Which dashboards page is canonical?** Recommend the grid page: it already
   has the shared 12-col grid, the sections and the nexcraftviz integration.
2. **Multi-chart storage.** Recommend one `ThreadComponent` with
   `component_type: "widget"` holding the document, over one row per tile —
   the widget is the unit a user moves, retitles and deletes.
3. **Widget rendering.** Recommend native React from the document, reusing
   VegaLiteCharts / KpiCard / TableWidget, over injecting nexcraftviz's server
   HTML with `/v1/theme.css`.
4. **Run state.** In-memory with a TTL is enough for one nexcraftviz instance;
   more than one needs a shared store or sticky routing.
5. **`suggest_questions`.** Call genieml's deterministic
   `SuggestRelatedQuestionsSkill` as a host action, or port it. Recommend
   calling it: it exists and is tested.
6. **Publishing always behind approval.** Recommend yes, enforced by the loader.
7. **Narration's home in Lexy.** Overview (markdown) for the summary, insights
   for the points.

## Constraints carried over

- nexcraftviz stays DB-free: Lexy supplies rows, runs SQL and publishes.
- Charts are generated by agents; the workflow orchestrates, it does not draw.
- A breakdown by a column the rows do not have is a data question — it goes to
  `host.query`, never a chart substituted from what is there.

## Appendix — where things are

- Live dashboards route: `lexy_ui/src/App.jsx:157` → `components/Dashboards/DashboardsPane.jsx` (masonry, `:1030-1047`)
- Grid page (unrouted): `components/analyticsComoponent/Dashboards/DashboardsComponent1.jsx` — grid `:2645-2648`, sections `:3542`, nexcraftviz first chart `:3628-3652`, Annotate `:3728`
- Gateway: `astherabackend/app/routes/workflow/dashboards.py` → workflowservices `routers/workflow_routers.py` (publish `:318`, layout `:1470`, edit `:1440`, add-component `:169`)
- Lifecycle: `workflowmodels.py` (`WorkflowState :31`, `ShareConfiguration :278`, `ScheduleConfiguration :295`, `WorkflowVersion :340`, `dashboard_templates :119-137`)
- Publish wizard: `DashboardPublishModal.jsx:112-285`, template step `:463`
- Creator stub: `dashboard_creator.py:102-132`
- nexcraftviz widget: `nexcraftviz/compose/widget.py` (`Tile :79-124`, `Widget :149-285`), layouts `compose/layout.py:19-24`
- nexcraftviz skills: `nexcraftviz/skills/__init__.py:29-43`; manager actions `manager/decision.py:24-43`; routes `app/api.py`, `app/chart_api.py`
- genieml: `genieml-skills/.../framework/suggest_related_questions.py:360`, `writers/dashboard.py:151`
- Prior art: `genieml/analysis-workflows/analysis_workflows/types.py:112-178` (PlaybookStep, Playbook), `executor.py:62`
