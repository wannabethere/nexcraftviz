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
| `compose` | Two ways to combine charts, plus placement operations with undo |
| `skills` | The portable contract: render_prompt / parse / apply, prompts as data |
| `agent` | The conversation — routing, sessions, undo, in either execution mode |
| `integrations` | Agent tools, an MCP server, and a Claude Code skill package |
| `app` + `embed` | HTTP API and a `<nexcraftviz-chat>` custom element |

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

## Combining charts

Two forms, because neither covers the other:

```python
from nexcraftviz.compose import concat, group, tile, widget

# One spec: one render, one PNG, optional shared scales. Vega views only.
combined = concat([revenue, orders], direction="horizontal", resolve_scales="shared")

# One widget: any family, nested panels, a 12-column grid.
board = widget(
    group(
        tile(funnel, title="Pipeline", span="two-thirds"),
        tile(stats=[...], title="Conversion", span="third"),
        title="Hiring pipeline performance",
    ),
    tile(donut, title="Sourcing", span="half"),
    tile(bars, title="Time to hire", span="half"),
)
```

A tile can be *compound* — a headline KPI above a chart above a stat strip, in
one card — which is not something Vega-Lite can express, since the headline and
the strip are card furniture rather than marks.

Placement is edited like anything else here:

```python
from nexcraftviz.compose import apply_widget_ops

result = apply_widget_ops(board, [
    {"op": "set_span", "tile": "tile-funnel", "span": "three-quarters"},
    {"op": "group_tiles", "tiles": ["tile-sourcing", "tile-time-to-hire"],
     "title": "Channel and speed"},
])
result.describe()   # what moved
result.inverse      # patch that puts it back
```

## Using it from another agent

**In agent mode the agent is the model.** There is deliberately no
"generate a chart" tool — asking a model to call a tool that calls a model is a
round trip and a second, worse prompt. An agent gets the deterministic
capabilities, the appliers, and the guidance:

```
viz_profile    → columns and their roles (measure / dimension / time / …)
viz_recommend  → chart types ranked by shape rules, with reasons
viz_guidance   → the operation vocabulary and the rules for using it
viz_apply_ops  → your operations, applied, validated, repaired
viz_render     → proof it actually draws
```

Three front doors, one implementation:

```bash
nexcraftviz tools --style openai     # or anthropic, or mcp
nexcraftviz mcp                      # MCP server over stdio
```

For Claude Code, `skills/nexcraftviz/` is a skill package and
`.claude-plugin/plugin.json` registers both it and the MCP server.

## Embedding the conversation

One custom element, in either mode:

```html
<script type="module" src="https://your-host/embed/nexcraftviz.js"></script>
<nexcraftviz-chat endpoint="https://your-host"></nexcraftviz-chat>
```

That is **hosted** mode — the server owns the model. In **driven** mode your
tool owns it, and nexcraftviz never needs a provider key:

```js
el.setAttribute('mode', 'driven');
el.onPropose = async ({ prompt, skill }) => myModel(prompt);
```

`propose` returns a prompt and an output schema with no model called anywhere;
`commit` applies whatever you return. It is a custom element rather than an
iframe because CSS custom properties inherit through a shadow root — the widget
picks up your page's `--nxv-*` tokens, which an iframe cannot do.

```bash
nexcraftviz serve      # API + embed on :8180
```

## Models and testing

The core imports no provider SDK and reads no API key — there is a test
asserting that. A host passes its own runner, and the signature deliberately
matches genieml's: `(system, user, schema) -> (payload, meta)`.

When nexcraftviz owns the model, configuration is genieml's, so there is
nothing new to set:

| | |
|---|---|
| `OPENAI_API_KEY` | the key |
| `OPENAI_MODEL` | model id, default `gpt-5-mini` |

```bash
pip install 'nexcraftviz[openai]'
nexcraftviz serve                    # picks up a key if one is set, says so if not
```

Two provider details worth knowing, both pinned by tests. `gpt-5*` and the
`o*` families **reject a custom temperature** — sending one is a hard 400, not
a warning. And OpenAI strict mode requires every property in `required`, which
Pydantic's optional fields violate; `integrations/strict_schema.py` rewrites the
schema (optional → nullable) and strips the nulls that come back, since Pydantic
rejects `None` for a field with a default.

### What the tests cover, and what they don't

`make check` runs 794 tests offline and free — no model, no network. That
covers all the *mechanics*: prompt assembly, schema generation, op parsing,
application, validation, repair, undo, rendering.

It does **not** measure whether the prompts are any good. That needs a real
model:

```bash
make eval-live      # needs OPENAI_API_KEY; costs money
```

The eval cases are in `nexcraftviz/evals/cases.py`, scored on the *operation
sequence* rather than the resulting JSON — two specs can differ in whitespace
and be identical, two op lists that differ are different decisions. The cases
that matter most:

- **`missing-column`** — asked to break down by a column that does not exist,
  the model must refuse rather than substitute one that does.
- **`generate-quarter-labels`** — `"2025-Q1"` must not be encoded as `temporal`.
  This is graded on the model's *raw* output, because tier-2 repair silently
  fixes it and would otherwise mask the prompt failure.
- **`place-widen`** — widening one tile must narrow its neighbour, or the row
  wraps.

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
- **`widgets.html`** — combining charts: a compound tile, a grouped widget,
  the same widget edited by placement operations, and the single-spec form.
- **`embed.html`** — the widget embedded twice, in driven mode, one of them
  restyled entirely by host CSS tokens.
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
