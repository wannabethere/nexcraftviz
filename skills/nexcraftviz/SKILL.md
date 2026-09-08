---
name: nexcraftviz
description: >-
  Create, edit, theme, combine and export data charts. Use whenever the task
  involves a chart, graph, plot, dashboard, KPI tile or data table — building
  one, changing one ("sort it descending", "colour by region", "add a target
  line", "make it dark"), arranging several into a widget, or checking that a
  chart is actually correct. Also use when a Vega-Lite spec renders empty,
  blank or full of NaN, which usually means an encoding references a column
  that does not exist.
---

# Charting

Charts have deterministic parts and judgement parts. This skill keeps them
apart: you make the judgements, the tools do the mechanics.

**Never hand-write a Vega-Lite specification to change a chart.** Emit
operations instead. It is cheaper, it is reproducible, it is reversible, and it
cannot corrupt a chart that was already correct.

## The failure worth knowing about

A spec that references a column the data does not have is *structurally valid*,
passes the Vega-Lite JSON schema, and compiles without complaint — then renders
an empty chart or a wall of `NaN`. It is the most common charting bug there is,
and no amount of staring at the JSON catches it. `viz_apply_ops` and
`viz_validate` do, and can repair a near-miss name deterministically.

A second one: a spec can pass every check and still draw *nothing* (a gauge
with its angles on the wrong side of the mark/encoding line, for instance).
Only a render proves a chart draws. Use `viz_render`.

## Sequence

1. **`viz_profile`** — the columns, and more usefully their **roles**:
   `measure`, `dimension`, `time`, `identifier`, `series`. Roles pick charts
   far better than dtypes: "one time column, one measure, one low-cardinality
   dimension" is a multi-line chart; "two quantitative, one nominal" tells you
   nothing.
2. **`viz_recommend`** — deterministic shape rules, ranked, each with a reason.
   Take the top one unless the question clearly asks for something else, and
   say why when you depart from it.
3. **`viz_guidance`** with `viz.edit` (or `viz.place` for layout) — the
   operation vocabulary and the rules for using it. Read it before your first
   apply call.
4. **`viz_apply_ops`** — your operations, applied, validated and repaired.
5. **`viz_render`** — confirm it draws.

## Choosing operations

| The user says | Operation |
|---|---|
| sort, rank, biggest first | `sort_by` |
| top / bottom N | `limit_top_n` |
| colour by X, break down by X | `set_color_field`, or `group_by` for side-by-side bars, or `facet_by` for separate panels |
| make it a line / bar / area | `set_mark` |
| recolour, use our brand colours | `set_palette` — or `viz_theme` for a whole theme |
| add a target / threshold line | `add_reference_line` |
| overlay another measure | `add_series`, with `independent_scale` when the units differ |
| stack, 100%, share | `stack_mode` |
| log scale, start at zero | `set_scale` |

If the instruction needs a column that is not in the profile, **say so and stop**.
Substituting a column that happens to be present produces a chart that answers a
question nobody asked.

## Table first

`viz_table` builds a rich table from rows with no model call at all — it picks a
renderer per column (progress bar, tone-mapped pill, sparkline, trend arrow)
from the profile. Show it immediately; the chart can follow. The table costs
nothing and cannot be wrong about what the data contains.

## Combining charts

Two forms, and they are not interchangeable:

- **`viz_compose`** → one Vega-Lite spec. One render, one export, and optional
  shared scales so panels can be read against each other. **Vega views only** —
  it refuses a KPI card or a rich table rather than emitting a blank panel.
- **`viz_apply_layout`** → a widget: tiles of any family on a 12-column grid,
  nestable in titled panels. Use this whenever the mix includes a KPI or a
  table, which is most real dashboards.

Widths in a row should total 12 or less. Widen one tile and narrow its
neighbour in the same call, or the row wraps.

## Themes

`viz_theme` is deterministic and free. It clears hard-coded mark colours first —
without that, a baked-in `mark.color` silently overrides the theme and the
switch appears to do nothing on exactly the charts people notice.

See `references/operations.md` for the full operation list with arguments.
