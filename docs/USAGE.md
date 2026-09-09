# Passing data in and getting a chart out

Five ways in, depending on where you are calling from. They all do the same
thing underneath, so pick the one that matches your context rather than the one
that looks most powerful.

**The short version:** data goes in as **a list of row objects** — the shape
`cursor.fetchall()` gives you with `DictCursor`, or `df.to_dict("records")`, or
a JSON array. Everything else is inferred from it.

```python
rows = [
    {"region": "West",  "revenue": 128.0, "orders": 41},
    {"region": "East",  "revenue": 96.0,  "orders": 33},
    {"region": "North", "revenue": 152.0, "orders": 48},
]
```

You do **not** need an API key to get a chart. The shape rules pick the type and
a deterministic builder constructs it. A model makes charts *better* and makes
*editing* possible; it is not a precondition for the first chart.

---

## 1. Command line — fastest way to see something

```bash
nexcraftviz chart revenue.csv "revenue by region" --out chart.html
```

CSV or JSON, and numbers are coerced (without that, every CSV column profiles as
text and the whole thing falls apart). Output format comes from the extension —
`.html`, `.png`, `.svg`, or `.json` for the raw spec. Omit `--out` and the spec
goes to stdout.

```bash
nexcraftviz chart revenue.csv --type donut --theme nexcraftviz-dark --out share.png
nexcraftviz table revenue.csv --out table.html      # the table half
nexcraftviz recommend revenue.csv "which region is behind?"
```

`recommend` is worth running when a chart surprises you — it prints the ranking
*and the reason for each*, which usually explains it.

## 2. Python — three lines

```python
from nexcraftviz.recommend import build_best

spec, chart_type = build_best(rows, question="revenue by region")
```

`spec` is a `Spec` — a validated Vega-Lite v5 specification with the rows
embedded. Hand it to any Vega renderer, or:

```python
from nexcraftviz.render import save, to_svg

save(spec, "chart.png")          # or .svg / .html / .json
svg = to_svg(spec)               # for embedding directly
```

Force a type when you know better, and apply a theme:

```python
from nexcraftviz.recommend import build_chart
from nexcraftviz.theme import apply_theme, strip_hardcoded_colours

spec = build_chart(rows, chart_type="donut", title="Revenue share")
spec = apply_theme(strip_hardcoded_colours(spec).spec, "powerbi").spec
```

`strip_hardcoded_colours` first, always. A baked-in `mark.color` overrides the
theme silently, so without it the theme appears to do nothing on exactly the
charts people notice.

### The table, first

The table costs nothing and cannot be wrong about what the data contains, so
render it immediately and let the chart follow:

```python
from nexcraftviz.table import build_table
from nexcraftviz.render.html import render_table

html = render_table(build_table(rows))
```

Renderers are chosen per column from the profile — a share becomes a progress
bar, a bounded score a heatmap cell, a judged status a tone-mapped pill, a
signed movement an arrow, a list of numbers a sparkline.

## 3. A conversation — asking for changes

A `Session` holds the rows, the document as it evolves, and the history.

```python
from nexcraftviz.agent import Session

session = Session(rows=rows)
await session.turn("show me revenue by region")   # no model needed
```

Editing is a judgement, so it needs a model. Two ways to supply one.

**You own the model** — nothing here needs a key:

```python
proposal = session.propose("sort descending and keep the top 3")
# proposal.prompt.system / .user / .output_schema  — no model has been called
output = await my_model(proposal.prompt)
turn = session.commit(proposal, output)

turn.reply       # what the assistant says
turn.changes     # what actually changed
session.undo()   # free — every edit carries its inverse
```

**nexcraftviz owns the model:**

```python
from nexcraftviz.integrations.providers import openai_runner

llm = openai_runner()                      # OPENAI_API_KEY, OPENAI_MODEL
await session.turn("sort descending and keep the top 3", llm=llm)
```

Both paths reach the same state — `commit` *is* the second half of `turn`.

## 4. HTTP — for a UI or another service

```bash
nexcraftviz serve            # :8180, picks up a key if one is set
```

```bash
curl -X POST localhost:8180/v1/sessions \
  -H 'content-type: application/json' \
  -d '{"rows": [{"region":"West","revenue":128}, {"region":"East","revenue":96}]}'
# → {"session_id": "sess_...", "kind": "empty", ...}

curl -X POST localhost:8180/v1/sessions/$ID/turn \
  -H 'content-type: application/json' \
  -d '{"message": "show me revenue by region"}'
# → {"turn": {...}, "state": {"kind": "chart", "document": {...}}}
```

`state.document` is the spec for a chart. For a widget you also get `state.html`
(server-rendered markup) and `state.specs` (mount id → spec), because a client
that injects the markup with `innerHTML` cannot run the inline scripts it
carries.

`/propose` and `/commit` are the same pair as in Python, over HTTP.
`POST /turn` returns **503 with an actionable message** when the skill needs a
model and none is configured — it does not pretend.

### Embedding the conversation

```html
<script type="module" src="http://localhost:8180/embed/nexcraftviz.js"></script>
<nexcraftviz-chat endpoint="http://localhost:8180"></nexcraftviz-chat>
```

```js
const el = document.querySelector('nexcraftviz-chat');
await el.start({ rows, document: spec });     // spec is optional
```

For driven mode — your tool owns the model — set `mode="driven"` and assign
`el.onPropose`. See `playground/embed.html` for a working example that needs no
key at all.

## 5. From another agent

In agent mode **the agent is the model**, so there is no "generate a chart" tool
that would call one behind your back. The sequence:

```
viz_profile      → the columns, and their ROLES
viz_recommend    → chart types ranked, with reasons
viz_build_chart  → a correct chart, no model call
viz_apply_ops    → your operations, applied, validated, repaired
viz_render       → proof it draws
```

```bash
nexcraftviz tools --style openai      # or anthropic, or mcp
nexcraftviz mcp                       # MCP server over stdio
```

`viz_guidance` returns the operating rules and the exact operation vocabulary,
so your operations will be accepted rather than guessed at.

---

## What happens to your data

Profiling infers three things, and the third is the one that matters:

| | |
|---|---|
| **Type** | quantitative / temporal / nominal |
| **Cardinality** | how many distinct values, bucketed |
| **Role** | `measure` · `dimension` · `time` · `identifier` · `series` |

Roles pick charts far better than types. *"One time column, one measure, one
low-cardinality dimension"* is a multi-line chart; *"two quantitative, one
nominal"* tells you nothing.

Four inferences worth knowing about, because each one is a bug if it goes the
other way:

- **A unique column is not automatically an identifier.** Every `GROUP BY
  region` result has one row per region. Treating uniqueness alone as an id
  loses the dimension on the most common chart shape there is.
- **Not every date is a time axis.** `next_audit` on a per-business-unit result
  is an *attribute*. Plotting a line against it produces a chart nobody asked
  for.
- **`"2025-Q1"` is not temporal.** Vega-Lite cannot parse it, and encoding it as
  a date renders an Invalid Date axis.
- **A rate is averaged, not summed.** Adding four regions' completion
  percentages gives a meaningless 340%.

```bash
nexcraftviz profile revenue.csv      # see all of it for your own data
```

## When it goes wrong

**An empty chart, or a wall of `NaN`.** Almost always an encoding referencing a
column that does not exist. It passes the JSON schema and compiles fine, which
is why it is so common.

```bash
nexcraftviz validate chart.json --data revenue.csv --repair
```

Tier 2 catches it and repairs near-miss names deterministically —
`total_revenue` → `revenue_total`.

**A chart that validates but draws nothing.** Compiling is not drawing. Only a
render proves it:

```bash
nexcraftviz render chart.json /tmp/check.png
```

**A theme that appears to do nothing.** A hard-coded `mark.color` is overriding
it. `nexcraftviz theme apply <name> chart.json` strips those first.

**A surprising chart type.** `nexcraftviz recommend` prints the reason for every
candidate; the answer is usually a role that was inferred differently from how
you read the column.

```bash
nexcraftviz config      # what provider, model and .env are in play
```
