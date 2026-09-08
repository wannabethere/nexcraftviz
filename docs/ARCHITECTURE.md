# Architecture

## The split that everything else follows

```
      natural language
             │
             ▼
    ┌──────────────────┐      LLM decides WHAT
    │   skills (M3-M5) │      — a small, strictly-typed op list
    └────────┬─────────┘
             │  list[Op]
             ▼
    ┌──────────────────┐      code decides HOW
    │   spec.ops       │      — pure Spec → Spec functions
    └────────┬─────────┘
             │
      ┌──────┴──────┬─────────────┬──────────────┐
      ▼             ▼             ▼              ▼
  spec.validate  spec.diff     theme         render / export
  (3 tiers)      (undo)        (tokens)      (vl-convert)
```

The LLM never emits a Vega-Lite document during an edit. It emits operations.
That single decision is what makes edits cheap, deterministic, testable without
a model, and reversible.

## Layers

### `spec.model` — `Spec`

Dict-backed, because Vega-Lite v5 is too polymorphic to mirror as a Pydantic
model without fighting it. What `Spec` adds is a **uniform view abstraction**:
`Spec.views()` returns every leaf that owns a `mark` + `encoding`, whether the
spec is a bare unit spec or three layers inside a `vconcat`. Every operation and
every validation tier works against that list, so layered charts need no special
cases anywhere else.

`Spec.family` distinguishes the four payload kinds this stack actually produces:
`vega-lite`, `vega`, `kpi` (a `kpi_metadata` block) and `table` (a `columns`
array of per-column renderers). The last two are drawn by hand-written frontend
components, not by Vega — most operations and validation tiers do not apply to
them, and the code says so rather than failing confusingly.

### `spec.ops` — the algebra

21 operations, each a Pydantic model with an `op` discriminator, split into two
bases:

- `BaseOp` — document-level (`set_title`, `set_size`, `apply_config`, layer
  manipulation). Takes no `view` argument.
- `ViewOp` — acts on one or more leaf views (`set_palette`, `sort_by`,
  `facet_by`, …). Takes an optional `view` index.

The split is not cosmetic: it keeps the JSON schema an LLM sees honest. A model
cannot pass `view` to `set_title` and have it silently ignored.

`apply_ops` clones, applies each op, and records failures rather than aborting —
four ops with a bad third argument should still land the other three.

### `spec.diff` — undo, derived not written

Operations do not implement their own inverses. The engine snapshots, applies,
and diffs; the reverse patch *is* the undo. A new operation gets undo for free
and cannot forget to implement it. `test_op_round_trips_via_inverse_patch`
enforces this for every registered op, and a registry check fails if a new op
arrives without a case.

One subtlety worth knowing: list `add` ops are emitted in ascending index order
and `remove` ops in descending order. Either direction applies correctly forward;
only these directions invert correctly.

### `spec.validate` — three tiers

| Tier | Cost | Needs | Catches |
|---|---|---|---|
| 1 structural | µs | nothing | "is there anything renderable here at all" |
| 2 data binding | µs | nothing | fields that don't exist; incompatible types |
| 3 compile + schema | ms | `render` extra | specs Vega-Lite cannot lower; silently-ignored properties |

**Tier 2 is the one that pays.** A spec referencing `total_revenue` when the
column is `revenue_total` passes tier 1, passes the JSON schema, compiles
cleanly — and renders a wall of `NaN`. It is also the only tier that repairs:
near-miss field names are matched back deterministically (case- and
separator-insensitive, similarity ≥ 0.82), and incompatible declared types are
replaced with the profiled one.

Scope resolution follows Vega-Lite's own rules: a field is legal if it comes
from the data *or* was produced by a transform at that view or an ancestor. Gauge
and radial specs depend on this — they compute an angle with a layer-level
`calculate` and encode `theta` on the result.

Tier 3 runs the **compile first** (fast, authoritative) and JSON Schema second,
reporting schema findings as *warnings*. That is deliberate: Vega-Lite silently
drops properties it does not recognise, so a spec can violate the schema and
still render — just not as intended. `scale: "nonsense"` is the canonical
example, and there is a test that documents it.

### `data.profile`

Column typing plus the things that actually drive chart choice: cardinality
buckets, null rates, monotonicity, and a **role** — `measure` / `dimension` /
`time` / `identifier`. "One time column, one measure, one low-cardinality
dimension" picks a chart far better than "two quantitative, one nominal" does.

Two deliberate calls: booleans profile as `nominal` (Vega-Lite will happily
average a bool, which is never the intent), and a string column is only
`temporal` if most sampled values parse as ISO dates. The second one matters —
`"2025-Q1"` must **not** be temporal, and the shipped corpus contains exactly
that bug.

### `render`

`vl-convert-python`: a self-contained Rust wheel with an embedded JS engine. No
Node, no headless browser. It compiles Vega-Lite to Vega (v6) and rasterises via
`resvg`, which means **a container needs real fonts installed** or PNG labels
render wrong.

Every entry point degrades: without the extra, `available()` is False and calls
raise `RenderUnavailable` with an actionable message.

### `theme`

One token source, two outputs. A Vega `config` block styles the plot and
nothing else — but three of the four payload families here are not Vega. KPI
tiles, rich tables and the dashboard grid are hand-written frontend components,
so a theme system that only speaks Vega leaves them unstyled and drifting.

```
ThemeTokens (YAML)
   ├─ theme/vega.py → Vega-Lite `config`
   └─ theme/css.py  → :root{--nxv-*} + .nxv-card / .nxv-kpi / .nxv-table / .nxv-cell--*
```

`powerbi` and `carbon-g90` are **derived, not hand-reproduced**:
`_generate_presets.py` reads the configs vl-convert bundles — the same
`vega-themes` builds `lexy_ui` hands to `react-vega` today — keeps them verbatim
under `vega_base: "overrides"`, and extracts tokens for the CSS side. A test
asserts the resulting PNG is byte-identical to `to_png(spec, theme="powerbi")`,
so adopting these is not a visual regression.

`strip_hardcoded_colours` matters more than it looks: generated specs bake a
brand hex into `mark.color`, which overrides `config` entirely. Without
stripping it, switching themes appears to do nothing on exactly the charts
people notice.

`contrast.py` audits a theme against WCAG AA (4.5:1 text, 3:1 graphical
objects) plus a CIE76 ΔE check that adjacent categorical colours are
distinguishable from *each other* — which WCAG does not cover and a stacked bar
badly needs. Our own themes must pass outright; the derived ones carry a
recorded deviation list, because preserving upstream's palette is the point.

### `table` and `render.html` — the other two families

Charts have a renderer. The other two payload families do not, and that is a
live defect rather than a gap in this package: a `table_with_cells` payload
reaches the frontend, fails to parse as Vega-Lite, and degrades to an untyped
grid, so avatars, progress bars and pills are silently dropped. 30 of the 200
corpus pairs emit that type.

`table.schema` writes the contract down as types, taken from what the corpus
actually emits rather than invented — ten renderers (`number`, `text`,
`avatar_name`, `heatmap_cell`, `progress_bar`, `pill`, `sparkline`, `badge`,
`date`, `trend_arrow`), keyed on `render`/`header`, with `color_map`,
`min`/`max`, `format`, `subtitle_field` and `muted` as per-renderer options.
Tones are judgements (`pass` / `fix` / `fail` / `muted`), not colours — what
"needs attention" looks like is the theme's business.

`table.build` turns rows into that contract with **no model involved**, which is
what makes table-first output free: results land, the table draws immediately,
and the chart follows. Renderer choice comes from the profile first and column
names second — a column called `status` holding 400 distinct strings is not a
pill, and a column called `x` holding `Pass`/`Fail` is.

`render.html` is the reference implementation for both families, in Python so it
is testable and reusable rather than trapped in a demo page. It targets the
`.nxv-*` classes from `theme.css`, so including the generated stylesheet themes
tables and KPI tiles alongside the charts.

`table.sample` exists because the 30 table pairs ship **no rows at all** — so
nothing could show them, not even a design review. Synthesised values are chosen
to exercise the renderer (a progress bar sweeps its full range; a pill column
hits every tone), and every surface that uses them says so.

### `compose` — two ways to combine charts

"Put these charts in one widget" means two different things, and the package
does both because neither covers the other.

**`compose.vega`** concatenates or layers specs into *one Vega-Lite spec*: one
render, one PNG export, and the option of shared scales so panels can be read
against each other. It holds Vega views only — a KPI tile or a rich table
cannot go inside a `vconcat`, and `concat()` raises rather than emitting a spec
that renders a blank panel.

**`compose.widget`** holds *tiles*, of any family, in a 12-column grid. A tile
can be compound (a headline KPI above a chart above a stat strip — one card,
three parts) and tiles can nest inside titled `Group` panels. This is what a
real dashboard needs, since two of the four payload families are not Vega.

| | `compose.vega.concat` | `compose.widget.Widget` |
|---|---|---|
| Result | one spec | tiles in a layout |
| Mixed families | no | yes |
| Shared scales | yes | no |
| PNG in one call | yes | per tile |

**`compose.ops`** edits placement the way `spec.ops` edits a chart: `set_span`,
`move_tile`, `group_tiles`, `ungroup_tiles`, `set_layout`, and so on. Placement
is where people iterate most, so it gets the same bargain — a model picks the
operation, code applies it, and the inverse comes from the diff. Operations are
parsed *per operation* rather than up front, so one malformed argument does not
discard the valid edits queued behind it.

Two things the widget renderer learned the hard way, both now guarded by tests:
`.nxv-grid` and `.nxv-grid--12` each set `grid-template-columns` at equal
specificity, so an element carrying both silently loses every span; and
rendering one widget twice on a page needs an `id_prefix`, or the duplicate DOM
ids mean only the first copy ever gets a chart.

### `examples`

Worked reproductions of two real dashboard designs, kept as code so they stay
runnable. `completion_gauge()` documents the two details that make a
multi-ring gauge draw *nothing* while still validating at tier 3: `startAngle`
belongs on the mark with the end angle as a `theta` encoding with
`scale: null`, and `autosize: none` stops Vega re-fitting the view and shifting
positioned arcs off centre. There is a test asserting the rendered PNG is not
blank, because tier 3 cannot tell the difference.

### `recommend.rules`

Deterministic chart ranking from the profile. Two decisions carry most of the
weight:

- **A unique column is not automatically an identifier.** Every `GROUP BY region`
  result has one row per region; treating uniqueness alone as an id lost the
  dimension on the most common chart shape there is.
- **Not every date is a time axis.** `next_audit` on a per-business-unit result
  is an *attribute*. `DataProfile.time_axis` separates the two, and without it
  the rules recommended a line chart for data containing no series.

### `corpus`

200 hand-authored chart pairs across 20 chart types, each carrying a
customer-facing story (`business_goal` / `overview` / `insight`) and internal
routing metadata (`use_when` / `do_not_use_when` / `kinds` / `data_shape`).

Until retrieval lands, the corpus is the **regression net**: 148 real specs go
through parse → validate tier 3 → compile → PNG on every test run. Known-bad
specs are listed explicitly in `KNOWN_CORPUS_DEFECTS` so the list can only
shrink. `make corpus` prints coverage and validation status.

## Testing philosophy

Nothing in the suite calls an LLM or the network. The op algebra, validation and
profiling are all pure functions, and the corpus supplies real inputs — so a
refactor that breaks view walking or scope resolution fails loudly rather than
quietly degrading charts in production.
