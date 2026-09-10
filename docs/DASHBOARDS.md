# Dashboard generation — a plan

**Status: design only.** Nothing here is built. It is written down now because
the pieces it needs have just landed and the shape is clearer while they are
fresh.

## What this is for

A user picks charts one at a time, in a conversation. Today each one lands as
its own dashboard tile and the arrangement is theirs to sort out. What they
actually want is:

> pick a chart → add it → merge two into a widget → drop the widget on the
> dashboard → be told which things belong together

Three levels, and only the middle one exists:

| Level | What it is | Today |
|---|---|---|
| **chart** | one Vega-Lite spec | `viz.plan` → `viz.generate`, gated |
| **widget** | several charts as one card, with panels and spans | `viz.compose` → `Widget` |
| **dashboard** | several widgets on a page, from a layout template | **missing** |

## What is already true

Worth stating, because it decides most of the design.

**The grids already match.** Dashboard hosts commonly render with
`react-grid-layout` at **12 columns**, each widget
carrying `{i, x, y, w, h}`. nexcraftviz's span vocabulary is a 12-column grid
too — `full=12, three-quarters=9, two-thirds=8, half=6, third=4, quarter=3`. A
span converts to a `w` by lookup, not by negotiation.

**Layout templates already exist as data.** `dashboard_templates` carries
`source_id` ("command-center"), `name`, `description`, `category`,
`complexity`, `domains`, `best_for` and the `layout` JSON itself
(a host's template table).

`best_for` and `domains` are the same idea as the corpus's `use_when` and
`kinds` — routing metadata attached to a worked example. **The precedent
machinery built for chart types applies unchanged, one level up.** That is the
central bet of this plan: not a new selection mechanism, the same one pointed at
a different collection.

**Dashboards can live in the vector store** a host already runs, and
the corpus loader already has the two-tier model for exactly this —
`chart_pairs_global` shared, `chart_pairs_<tenant>` as a per-tenant overlay
(`nexcraftviz/corpus/seed.yaml`, header). Dashboard templates become
`dashboard_templates_<tenant>` on the same store, reached through the retrieval
config that already exists (`NEXCRAFTVIZ_RETRIEVAL`, `QDRANT_*`).

**The handoff is a package.** `builtin.DelivererAgent` returns the spec, its
title, the plan and the verdict in one host-agnostic package; a host's bridge
shapes that into the host's own storage rows.

## The design

### 1. A `Dashboard` document — `nexcraftviz/compose/dashboard.py`

One level above `Widget`, and deliberately the same shape of object: a title, a
layout, and a list of placed widgets.

```python
class Placement(BaseModel):
    widget_id: str
    x: int; y: int; w: int; h: int      # the 12-column grid, as grid hosts store it
    title: str = ""

class Dashboard(BaseModel):
    title: str
    description: str = ""
    template: str = ""                   # the source_id it was built from
    widgets: list[Widget]
    placements: list[Placement]
```

`to_grid_layout()` emits `[{i, x, y, w, h}]` — what `react-grid-layout` takes
and what `handleLayoutChange` writes back. Round-tripping that array is the
whole integration; there is no second format to keep in step.

### 2. A template retriever — `nexcraftviz/recommend/template.py`

`precedent.py` with the nouns changed. Given what the user is building and the
widgets they have so far, rank the layout templates:

```python
def templates(widgets, *, question="", limit=5) -> TemplateSet
```

- **shape gate**: a template with four slots and one chart to fill them is a
  page of empty cards. Slot count and slot *kinds* (a KPI strip wants KPIs) gate
  the same way column kinds gate a chart type.
- **question and `best_for` match**: the same IDF-weighted lexical matching, and
  the same optional qdrant backend.
- **the reason quotes the template**, as precedents quote the corpus.

The two gates that earned their place in chart selection have direct analogues,
and both should be written before anyone asks for them:

- *A template with more slots than the user has widgets is not a candidate* —
  the analogue of "a chart with nothing to vary over shows one value".
- *A KPI strip needs KPIs* — the analogue of "not every date is an axis".

### 3. Two new roles

The registry already has eleven; these are the twelfth and thirteenth, filled by
built-ins and overridable like the rest.

| Role | Skill | Does |
|---|---|---|
| `templater` | `viz.template` | Pick the layout template and say why |
| `dashboarder` | `viz.dashboard` | Place widgets into the template's slots |

`viz.dashboard` sees **placements and widget summaries, not specs and not
rows** — the same discipline `viz.compose` follows, and for the same reason: by
then every chart has passed its gates, and showing the data invites the model to
relitigate work it cannot do better.

### 4. Grouping as a workflow

The user's phrase, and the part that is genuinely new. Not "arrange what you
have" but *"these three belong together, shall I merge them?"* — offered while
they are still picking.

```python
class GroupingSuggestion(BaseModel):
    widget_ids: list[str]
    reason: str            # "all three are completion rates by unit"
    merge_as: str          # "panel" | "widget" | "row"
    confidence: float
```

The signals are all deterministic and already computable, which matters: a
suggestion that costs a model call every time a chart is added will be turned
off.

- **shared dimension** — charts encoding the same field are about the same
  thing. `Spec.field_refs()` gives this for free.
- **same measure, different cut** — completion by unit and completion by month
  are one story told twice.
- **KPI adjacency** — KPIs cluster into a strip; the corpus says so and the
  widget layout already prefers it.
- **the plan's `follow_ups`** — `ChartPlan.follow_ups` already records the
  questions a chart provokes. A chart answering one of them belongs beside it,
  and nothing reads that field today.

Surfaced as an offer, never applied: merging is destructive to an arrangement
someone chose, and the manager already has `decline` as a first-class outcome
for the same reason.

### 5. Where it plugs in

The manager grows one action, `dashboard`, beside `widget`. The vocabulary then
runs the whole way up:

```
edit · theme · narrate · place · widget · dashboard · recreate · decline
```

And a new surface call, matching the two that exist:

```
POST /v1/dashboard/build      widgets + ask → a placed dashboard
POST /v1/dashboard/suggest    widgets → grouping suggestions
```

## Sequencing

| # | Scope | Exit criterion |
|---|---|---|
| D1 | `compose/dashboard.py`, grid round-trip | A `Dashboard` survives `to_grid_layout` → a grid host's `onLayoutChange` → back with placements intact |
| D2 | `recommend/template.py` + `dashboard_templates` loader | A template is chosen with a quoted reason; a template with more slots than widgets is not a candidate |
| D3 | `templater` + `dashboarder` roles, `viz.template`, `viz.dashboard` | Widgets are placed into a template's slots; the dashboarder sees no rows |
| D4 | Grouping suggestions | Three completion-rate charts are offered as one panel, with the reason, and nothing merges until accepted |
| D5 | `/v1/dashboard/*` + manager `dashboard` action | "Build me a command centre from these" round-trips end to end |

D1 and D2 are independent and can land in either order. D4 is usable on its own
and is the piece most likely to be wanted first, because it improves the
existing flow without needing a dashboard to exist.

## Open questions

- **Where do templates load from?** `dashboard_templates` is a table in
  the host's backend, not a file nexcraftviz ships. Either it reads the table
  (a database dependency this package has carefully avoided), or the caller
  passes templates in, or they are mirrored into the vector store as the
  dashboards already are. The third keeps the package DB-free and is
  consistent with how the corpus works — but it means a sync step, and a stale
  mirror is a template that no longer matches the one being rendered.

- **Does a dashboard hold widgets or charts?** The plan says widgets, and a
  single chart becomes a one-tile widget. That is one concept fewer to reason
  about, at the cost of a wrapper around every solo chart. Worth confirming
  against how the host groups its stored components today, because the
  answer may already be settled there.

- **Whose grid wins on conflict?** If the user drags a tile and then asks for a
  re-layout, the template's placement and their arrangement disagree. The honest
  default is that a human's explicit placement outranks a generated one, which
  means placements need a flag recording which they were.

- **Is `complexity` worth reading?** `dashboard_templates.complexity` exists and
  nothing in this plan uses it. It may be the signal that stops a four-panel
  command centre being proposed for someone's first two charts — or it may be
  unpopulated. Worth checking before designing around it.
