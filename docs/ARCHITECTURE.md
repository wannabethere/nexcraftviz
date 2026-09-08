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
