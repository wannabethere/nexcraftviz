# nexcraftviz

Agentic Vega-Lite charting: create, theme, edit, narrate and export charts from
natural language — with the *deterministic* parts kept deterministic.

## The idea

Most LLM charting works by asking a model to emit a whole Vega-Lite document,
then asking it to emit the whole document again for every subsequent change.
"Make the bars teal" costs a full generation, produces a different result each
time, has no undo, and can break a spec that was already correct.

nexcraftviz splits the problem:

```
"make it blue-green and break it down by region"
      │
      ├─ LLM picks operations ──► [SetPalette(scheme="tealblues"),
      │                            FacetBy(field="region", mode="column")]
      │
      └─ code applies them ─────► Spec → Spec, validated, reversible
```

The model chooses *what* to do. Ordinary Python does it. Edits become cheap
(a couple of hundred output tokens), reproducible, unit-testable with no model
in the loop, and undoable — every edit returns its own inverse patch.

## What's here

| Module | Does |
|---|---|
| `spec.model` | `Spec` — dict-backed Vega-Lite, with uniform access to leaf views inside layered/faceted specs |
| `spec.ops` | ~21 pure `Spec → Spec` operations; one strict Pydantic union an LLM can target |
| `spec.validate` | Three-tier validation: structural → data-binding → schema + compile |
| `spec.diff` | JSON-Patch diffing; this is where undo comes from |
| `data.profile` | Column typing, cardinality, roles (measure / dimension / time / identifier) |
| `render` | SVG / PNG / compiled-Vega via `vl-convert` — no Node, no browser |
| `render.html` | Reference HTML for the KPI and rich-table families, which have no renderer in production |
| `table` | The `table_with_cells` contract, plus a builder that turns rows into it with no model involved |
| `theme` | One token source → a Vega config *and* a CSS bundle |
| `recommend.rules` | Deterministic chart ranking from the profile |

Skills, corpus retrieval, composition and BI export land in subsequent
milestones; see `docs/`.

## Validation, and why tier 2 matters

Tier 2 checks that every field an encoding references actually exists in the
data, with a compatible type. It is the cheapest check in the package and it
catches the failure that hurts most: a model that writes `total_revenue` when
the column is `revenue_total` produces a spec that is structurally fine, passes
the Vega-Lite JSON schema, compiles without complaint — and renders an empty
chart or a wall of `NaN`.

Tier 2 is also the only tier that can repair. A near-miss field name is matched
back to the real column deterministically (case- and separator-insensitive,
with a high similarity threshold), and an incompatible declared type is
replaced with the profiled one.

```python
from nexcraftviz import Spec, validate

spec, report = validate(Spec(raw), data=rows, repair=True)
print(report.summary())   # "valid (tier 3)"
print(report.repaired)    # ["encoding.y: field 'total_revenue' → 'revenue_total'"]
```

## Editing

```python
from nexcraftviz import Spec, apply_ops

result = apply_ops(spec, [
    {"op": "set_palette", "scheme": "tealblues"},
    {"op": "sort_by", "channel": "y", "order": "descending"},
    {"op": "limit_top_n", "n": 10, "by": "revenue"},
])

result.spec        # the new Spec
result.describe()  # ["mark.color: '#4c78a8' → '#0c8ba6'", ...]
result.inverse     # patch that undoes the edit
result.failed      # ops that could not apply, with reasons — the rest still applied
```

## Table first

The table costs nothing, so it should not wait for the chart:

```python
from nexcraftviz.table import build_table
from nexcraftviz.render.html import render_table

table = build_table(rows)          # deterministic — no model, no network
html = render_table(table)         # themed by the generated stylesheet
```

Renderers are chosen from the profile: a share becomes a progress bar, a bounded
score a heatmap cell, a judged status a tone-mapped pill, a signed movement an
arrow, a list of numbers a sparkline. The chart is then generated from the same
rows, and lands when it lands.

## Playground

```bash
nexcraftviz gallery --out playground
```

Three generated pages, all built from real package output so none of them can
drift from what the code does:

- **`index.html`** — every renderer in one place. This is the reference
  implementation for the two families that have no renderer in production.
- **`usecase.html`** — one scenario end to end: the table lands, then the
  profile, the rules-based recommendation, and four natural-language edits
  applied as operations with the resulting diff shown beside each chart.
- **`gallery.html`** — all 200 corpus pairs, table first and chart second.

## Install

```bash
pip install nexcraftviz                 # core: spec algebra + validation tiers 1-2
pip install 'nexcraftviz[render]'       # + tier-3 validation, SVG/PNG export
pip install 'nexcraftviz[langchain]'    # + Runnable / StructuredTool adapters
```

The core has two dependencies (`pydantic`, `pyyaml`) and no native code, so the
algebra and the first two validation tiers run anywhere. Rendering pulls
`vl-convert-python`, a self-contained Rust wheel — no Node, no headless
browser. It draws text with `resvg`, so a container that renders PNGs needs
fonts installed.

## A note on AntV

The chart-type taxonomy in the corpus draws on
[antvis/chart-visualization-skills](https://github.com/antvis/chart-visualization-skills).
Their `chart-visualization` skill itself POSTs your data to a hosted endpoint
(`antv-studio.alipay.com`) and returns an image URL. That is fine for authoring
and prototyping; **do not point it at customer data.** nexcraftviz has no
runtime dependency on it and sends nothing anywhere.

## Licence

Apache-2.0
