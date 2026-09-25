# Changelog

Notable changes. The format follows [Keep a Changelog](https://keepachangelog.com/);
this project is pre-1.0, so anything may still move.

## Unreleased

### Added
- **The dashboard skill** — workflows declared as data (`nexcraftviz/workflows/`):
  intents, steps, pauses for a person (ask / select / approve) and hand-offs to
  the host for data and publishing. The loader refuses a skill file that would
  publish without an approval. Served at `/v1/workflows`.
- **Widget documents carry a narration and a table** (version 2), and render both.
- **`viz.suggest_questions`** — a dashboard question becomes the chart questions
  that answer it.
- **A capabilities page** — `docs/capabilities.html`, self-contained, generated
  from the corpus: every chart type with the questions it answers, the columns
  it needs and its output.
- `title_placement` on the chart API, so a host whose card header shows the
  title can keep it out of the chart.

### Fixed
- Charts draw the rows they were given: inline values a model wrote are replaced.
- Generated KPIs are KPI cards with their numbers read from the data, not text
  marks on an empty canvas.
- Titles and axis names are filled in when a model leaves them blank.
- A time axis may not run backwards; a plan naming a chart type is obeyed.
- Repairs for unclosed layers, mangled punctuation, aggregate-prefixed field
  names and comma-joined field lists.

## 0.1.0

First cut: the spec algebra, profiling and recommendation, the staged agent
pipeline with deterministic gates, themes, tables, KPI cards, widgets,
narration, the HTTP surface, the MCP server, the corpus and the harness.
