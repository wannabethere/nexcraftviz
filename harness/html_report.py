"""One page for a live run: every question, every answer, every chart.

A tester wants to see what the agents did, not read JSON. This renders the
latest eval run and harness run together — what needs a look first, then each
harness scenario end to end (question, plan, chart, gates, verdict), then every
eval case grouped by the skill it exercises.

Charts are drawn the way the playground draws them: the theme's stylesheet,
vega-embed in the browser, and the playground's own Vega config read from the
theme's CSS variables — so a tester sees here what the embed and the playground
show. A single number is a KPI card, not a text mark on an empty canvas, and a
widget is the widget, cards and all. The page follows the viewer's light or
dark setting, and the charts follow the page.
"""
from __future__ import annotations

import copy
import html
import json
from pathlib import Path

from harness.run import RESULTS_DIR
from nexcraftviz.render.html import (
    kpi_from_vega,
    render_card,
    render_chart_mount,
    render_kpi,
    render_table,
)
from nexcraftviz.spec.model import Spec

_e = html.escape

#: Skills in pipeline order — the order a request moves through them — with
#: the one thing each is responsible for.
SKILLS: list[tuple[str, str]] = [
    ("viz.manage", "routes an instruction to the actions that carry it out"),
    ("viz.plan", "decides what to draw — chart type, encodings, transforms"),
    ("viz.generate", "draws the plan as a Vega-Lite spec"),
    ("viz.critique", "judges whether a chart answers the question it was made for"),
    ("viz.edit", "changes an existing chart from a plain-language instruction"),
    ("viz.narrate", "says what a chart shows"),
    ("viz.compose", "arranges finished charts into one widget"),
    ("viz.place", "rearranges the tiles of an existing widget"),
]

#: The 12-column grid nexcraftviz widgets and dashboard hosts share.
_SPAN = {"full": 12, "three-quarters": 9, "two-thirds": 8, "half": 6, "third": 4,
         "quarter": 3, "auto": 4}


def latest(prefix: str, directory: Path = RESULTS_DIR) -> Path | None:
    runs = sorted(directory.glob(f"{prefix}-*.json"))
    return runs[-1] if runs else None


def build_report(
    *,
    eval_path: Path | None = None,
    run_path: Path | None = None,
    standalone: bool = True,
) -> str:
    """The page. ``standalone=False`` omits the document shell, for hosts that
    wrap content themselves."""
    evals = json.loads(eval_path.read_text(encoding="utf-8")) if eval_path else None
    runs = json.loads(run_path.read_text(encoding="utf-8")) if run_path else None

    eval_rows = (evals or {}).get("results") or []
    scenario_rows = (runs or {}).get("results") or []
    model = (evals or {}).get("model") or "the configured model"
    when = (evals or runs or {}).get("at", "")

    body = "".join([
        _header(model, when, eval_rows, scenario_rows),
        _attention(eval_rows, scenario_rows),
        _scenarios(scenario_rows),
        _evals(eval_rows),
        _footer(eval_path, run_path),
    ])
    content = (
        f"<title>nexcraftviz Live Run</title><style>{_theme_css()}</style>"
        f"<style>{_CSS}</style><main>{body}</main>{_scripts()}"
    )
    if not standalone:
        return content
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"</head><body>{content}</body></html>"
    )


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------

def _header(model: str, when: str, evals: list[dict], scenarios: list[dict]) -> str:
    passed_evals = sum(1 for r in evals if r.get("ok"))
    passed_scenarios = sum(1 for r in scenarios if r.get("passed"))
    bindings = {r.get("binding") for r in evals if r.get("binding")}
    strict = bindings == {"json_schema_strict"}
    tokens = sum((r.get("tokens") or {}).get("tokens_in") or 0 for r in evals) + sum(
        (r.get("tokens") or {}).get("tokens_out") or 0 for r in evals)
    retried = sum(1 for r in scenarios if ((r.get("run") or {}).get("regenerations") or 0))

    def tally(value: str, label: str, tone: str = "") -> str:
        return (f'<div class="tally {tone}"><span class="tally-n">{_e(value)}</span>'
                f'<span class="tally-l">{_e(label)}</span></div>')

    tallies = "".join([
        tally(f"{passed_evals}/{len(evals)}", "eval cases passed",
              "good" if evals and passed_evals == len(evals) else "bad" if evals else ""),
        tally(f"{passed_scenarios}/{len(scenarios)}", "harness scenarios passed",
              "good" if scenarios and passed_scenarios == len(scenarios)
              else "bad" if scenarios else ""),
        tally("strict" if strict else ", ".join(sorted(bindings)) or "—",
              "schema binding on every call", "good" if strict else "warn"),
        tally(str(retried), "scenarios that spent their one regeneration"),
        tally(f"{tokens:,}", "tokens across the eval cases"),
    ])
    return (
        '<header class="masthead">'
        '<div><p class="eyebrow">nexcraftviz · live run</p>'
        "<h1>What the chart agents did, question by question</h1>"
        f'<p class="meta">{_e(model)} · {_e(when.replace("T", " "))}</p></div>'
        f'<div class="tallies">{tallies}</div></header>'
    )


def _attention(evals: list[dict], scenarios: list[dict]) -> str:
    items: list[str] = []
    for r in scenarios:
        if not r.get("passed"):
            reason = r.get("error") or "; ".join(r.get("problems") or []) or "did not pass"
            items.append(_attention_item("fail", f"scenario · {r.get('scenario')}", reason))
        elif (r.get("run") or {}).get("regenerations"):
            items.append(_attention_item(
                "retry", f"scenario · {r.get('scenario')}",
                "passed on its one regeneration — " + _first_retry(r)))
    for r in evals:
        if not r.get("ok"):
            reason = r.get("error") or "; ".join(
                (r.get("missing") or []) + (r.get("notes") or [])) or "did not pass"
            items.append(_attention_item("fail", f"{r.get('skill')} · {r.get('id')}", reason))
        elif any("repair" in n for n in r.get("notes") or []):
            items.append(_attention_item("repair", f"{r.get('skill')} · {r.get('id')}",
                                         "; ".join(r["notes"])))
    if not items:
        body = '<p class="quiet">Nothing. Every case and scenario passed first time.</p>'
    else:
        body = f'<ul class="attention">{"".join(items)}</ul>'
    return f'<section class="block"><h2>Needs a look</h2>{body}</section>'


def _attention_item(tone: str, where: str, detail: str) -> str:
    label = {"fail": "fail", "retry": "retried", "repair": "repaired"}[tone]
    return (f'<li><span class="pill {tone}">{label}</span>'
            f'<span class="where">{_e(where)}</span><span class="why">{_e(detail)}</span></li>')


def _first_retry(row: dict) -> str:
    for step in (row.get("run") or {}).get("trace") or []:
        if step.startswith("retry:"):
            return step[len("retry:"):].strip()
    return "see the trace"


def _scenarios(rows: list[dict]) -> str:
    if not rows:
        return ""
    cards = "".join(_scenario(r) for r in rows)
    return (
        '<section class="block"><h2>Harness scenarios</h2>'
        '<p class="lede">End to end, as a user would meet them: a question and some '
        "rows go in; a plan, a chart, five deterministic gates and the critic's verdict "
        f"come out.</p>{cards}</section>"
    )


def _scenario(r: dict) -> str:
    plan = r.get("plan") or {}
    critique = r.get("critique")
    mount = f"s-{r.get('scenario', '')}"
    gates = "".join(
        f'<span class="gate {"ok" if g.get("passed") else "no"}" '
        f'title="{_e(g.get("detail") or "")}">'
        f'{_e(g.get("gate", ""))}</span>'
        for g in r.get("gates") or []
    )
    verdict = ""
    if critique:
        says = "answers the question" if critique.get("answers_question") else "does not answer it"
        verdict = (f'<p class="verdict"><span class="k">critic</span> {_e(says)}'
                   + (f" — {_e(critique.get('complaint') or '')}"
                      if critique.get("complaint") else "") + "</p>")
    status = "pass" if r.get("passed") else "fail"
    trace = "".join(f"<li>{_e(step)}</li>" for step in (r.get("run") or {}).get("trace") or [])
    problems = "".join(f"<li>{_e(p)}</li>" for p in r.get("problems") or [])
    if r.get("error"):
        problems += f"<li>{_e(r['error'])}</li>"
    return (
        f'<article class="scenario {status}">'
        '<div class="scenario-text">'
        f'<p class="case-id">{_e(r.get("scenario", ""))}'
        f'<span class="pill {status}">{status}</span></p>'
        f'<h3>{_e(r.get("question", ""))}</h3>'
        f"{_plan_line(plan)}"
        f'<div class="gates">{gates}</div>'
        f"{verdict}"
        + (f'<ul class="problems">{problems}</ul>' if problems else "")
        + (f"<details><summary>pipeline trace</summary><ol class=\"trace\">{trace}</ol></details>"
           if trace else "")
        + f'</div><div class="scenario-chart">{_chart(r.get("spec"), mount)}</div></article>'
    )


def _plan_line(plan: dict) -> str:
    if not plan:
        return ""
    if plan.get("status") and plan["status"] != "ok":
        return (f'<p class="plan"><span class="k">plan</span> {_e(plan["status"])} — '
                f'{_e(plan.get("reason_if_not_ok") or "")}</p>')
    encodings = " ".join(
        f'<span class="chip">{_e(e.get("channel", ""))} '
        f'<b>{_e(e.get("field") or e.get("aggregate") or "")}</b>'
        + (f' <i>{_e(e["aggregate"])}</i>' if e.get("aggregate") and e.get("field") else "")
        + "</span>"
        for e in plan.get("encodings") or []
        # A channel the planner listed and left empty is not an encoding; as a
        # chip it reads as a dangling "color" with nothing after it.
        if e.get("field") or e.get("aggregate")
    )
    rationale = plan.get("rationale") or ""
    return (f'<p class="plan"><span class="k">plan</span> <b>{_e(plan.get("chart_type", ""))}</b> '
            f"{encodings}</p>"
            + (f'<p class="rationale">{_e(rationale)}</p>' if rationale else ""))


def _evals(rows: list[dict]) -> str:
    if not rows:
        return ""
    groups = []
    for skill, role in SKILLS:
        cases = [r for r in rows if r.get("skill") == skill]
        if not cases:
            continue
        passed = sum(1 for r in cases if r.get("ok"))
        tone = "good" if passed == len(cases) else "bad"
        groups.append(
            f'<section class="skill"><header class="skill-head">'
            f'<h3><code>{_e(skill)}</code></h3><p>{_e(role)}</p>'
            f'<span class="count {tone}">{passed}/{len(cases)}</span></header>'
            f'<ol class="cases">{"".join(_case(r) for r in cases)}</ol></section>'
        )
    return (
        '<section class="block"><h2>Eval cases</h2>'
        '<p class="lede">Each case pins one behaviour a prompt must get right. Graded on what '
        "the model actually said, before any repair — the ask, the answer, and what it drew."
        f'</p>{"".join(groups)}</section>'
    )


def _case(r: dict) -> str:
    status = "pass" if r.get("ok") else "fail"
    notes = "".join(f'<li>{_e(n)}</li>' for n in (r.get("missing") or []) + (r.get("notes") or []))
    if r.get("error"):
        notes += f"<li>{_e(r['error'])}</li>"
    return (
        f'<li class="case {status}">'
        '<div class="case-ask">'
        f'<p class="case-id">{_e(r.get("id", ""))}<span class="pill {status}">{status}</span></p>'
        f'<p class="ask">“{_e(r.get("instruction", ""))}”</p>'
        f'<p class="checks">{_e(r.get("checks", ""))}</p>'
        + (f'<ul class="problems">{notes}</ul>' if notes else "")
        + f'</div><div class="case-answer">{_answer(r)}</div></li>'
    )


# ---------------------------------------------------------------------------
# answers, by what each skill says
# ---------------------------------------------------------------------------

def _answer(r: dict) -> str:
    skill = r.get("skill")
    answer = r.get("answer") or {}
    emitted = r.get("emitted") or []
    artifact = r.get("artifact")
    mount = f"e-{r.get('id', '')}"

    if skill == "viz.plan":
        plan = emitted[0] if emitted else answer
        return _plan_line(plan) or '<p class="quiet">no plan</p>'
    if skill == "viz.manage":
        return _decision(answer, emitted)
    if skill == "viz.critique":
        return _verdict(answer or (emitted[0] if emitted else {}))
    if skill == "viz.compose":
        return _design(answer or (emitted[0] if emitted else {}))
    if skill == "viz.narrate":
        return _narration(answer)
    if skill == "viz.place":
        return _ops(emitted) + _widget(artifact, mount)
    if skill == "viz.generate":
        reasoning = answer.get("reasoning") or ""
        return ((f'<p class="rationale">{_e(reasoning)}</p>' if reasoning else "")
                + _chart(artifact, mount))
    return _ops(emitted) + _chart(artifact, mount)


def _ops(ops: list[dict]) -> str:
    if not ops:
        return '<p class="quiet">no operations — the chart was left as it was</p>'
    items = []
    for op in ops:
        args = ", ".join(
            f"{k}={json.dumps(v)}" for k, v in op.items()
            if k not in ("op", "view") and v not in (None, "", [], {})
        )
        items.append(f'<li><code>{_e(op.get("op", "?"))}({_e(args)})</code></li>')
    return f'<ol class="ops">{"".join(items)}</ol>'


def _decision(answer: dict, steps: list[dict]) -> str:
    steps = steps or answer.get("steps") or []
    if answer.get("status") and answer["status"] != "ok":
        head = (f'<p class="plan"><span class="k">status</span> <b>{_e(answer["status"])}</b>'
                + (f' — {_e(answer.get("reason_if_not_ok") or "")}' if
                   answer.get("reason_if_not_ok") else "") + "</p>")
    else:
        head = ""
    items = "".join(
        f'<li><span class="action">{_e(s.get("action", ""))}</span>'
        f'<span>{_e(s.get("instruction", ""))}</span>'
        + (f'<span class="parts">{" · ".join(_e(p) for p in s["parts"])}</span>'
           if s.get("parts") else "") + "</li>"
        for s in steps
    )
    declined = "".join(f"<li>{_e(d)}</li>" for d in answer.get("declined") or [])
    return (head + (f'<ol class="steps">{items}</ol>' if items else "")
            + (f'<ul class="declined">{declined}</ul>' if declined else ""))


def _verdict(v: dict) -> str:
    if not v:
        return '<p class="quiet">no verdict</p>'
    yes = v.get("answers_question")
    tone = "ok" if yes else "no"
    says = "answers the question" if yes else "does not answer the question"
    score = v.get("score")
    return (f'<p class="verdict-big {tone}">{_e(says)}'
            + (f' <span class="score">{score:.2f}</span>' if isinstance(score, (int, float))
               else "") + "</p>"
            + (f'<p class="rationale">{_e(v.get("complaint") or "")}</p>'
               if v.get("complaint") else ""))


def _narration(answer: dict) -> str:
    if not answer:
        return '<p class="quiet">no narration</p>'
    points = "".join(f"<li>{_e(p.get('text', ''))}</li>" for p in answer.get("points") or [])
    return (f'<p class="headline">{_e(answer.get("headline") or "")}</p>'
            f'<p class="prose">{_e(answer.get("summary") or "")}</p>'
            + (f'<ul class="points">{points}</ul>' if points else ""))


def _design(design: dict) -> str:
    tiles = design.get("tiles") or []
    title = design.get("title") or ""
    return (f'<p class="plan"><span class="k">widget</span> <b>{_e(title)}</b> · '
            f'{_e(design.get("layout") or "")}</p>' + _grid(
                [(t.get("id", ""), t.get("title") or t.get("id", ""), t.get("span", "auto"))
                 for t in tiles]))


def _widget(widget: dict | None, mount: str) -> str:
    """The widget itself, as the playground renders it — cards, KPIs, charts.

    Falls back to the span diagram when the document will not rebuild, so a
    malformed widget is still shown for what its tiles were meant to be.
    """
    if not widget:
        return ""
    try:
        from nexcraftviz.compose.widget import Widget

        return Widget.from_dict(widget).to_html(id_prefix=f"{mount}-")
    except Exception:  # noqa: BLE001, S110 — one widget must not sink the page
        pass
    tiles: list[tuple[str, str, str]] = []
    for node in widget.get("nodes") or []:
        members = node.get("tiles") if isinstance(node.get("tiles"), list) else None
        if members is not None:
            for tile in members:
                tiles.append((tile.get("id", ""), tile.get("title") or tile.get("id", ""),
                              tile.get("span", "auto")))
        else:
            tiles.append((node.get("id", ""), node.get("title") or node.get("id", ""),
                          node.get("span", "auto")))
    return _grid(tiles)


def _grid(tiles: list[tuple[str, str, str]]) -> str:
    """Tiles on the 12-column grid, sized by their span — what placement decides."""
    if not tiles:
        return ""
    cells = "".join(
        f'<div class="cell" style="grid-column: span {_SPAN.get(span, 4)}">'
        f'<span class="cell-t">{_e(title)}</span><span class="cell-s">{_e(span)}</span></div>'
        for _, title, span in tiles
    )
    return f'<div class="grid12" role="img" aria-label="tile layout">{cells}</div>'


# ---------------------------------------------------------------------------
# charts
# ---------------------------------------------------------------------------

def _chart(raw: dict | None, mount: str) -> str:
    """A payload drawn as its host draws it: a KPI card, a table, or a chart."""
    if not raw:
        return ""
    spec = Spec(copy.deepcopy(raw))
    family = spec.family
    if family == "kpi":
        return render_card(title="", body=render_kpi(spec), extra_class="kpi-tile")
    if family == "table":
        return render_card(title="", body=render_table(spec))
    if family != "vega-lite":
        return (f'<p class="quiet">a {_e(family)} payload — this page draws Vega-Lite, '
                "tables and KPIs</p>")
    kpi = kpi_from_vega(spec)
    if kpi is not None:
        return (render_card(title="", body=render_kpi(kpi), extra_class="kpi-tile")
                + '<p class="caption">one number, drawn as a KPI card — as the playground '
                "and dashboard hosts draw it</p>")
    return render_card(title="", body=render_chart_mount(spec, mount))


def _footer(eval_path: Path | None, run_path: Path | None) -> str:
    sources = " · ".join(_e(p.name) for p in (eval_path, run_path) if p)
    return (f'<footer class="colophon"><p>Regenerate with <code>nexcraftviz harness report '
            f"--html PATH</code>. Sources: {sources}.</p></footer>")


# ---------------------------------------------------------------------------
# the playground's stylesheet and scripts
# ---------------------------------------------------------------------------

#: The Vega libraries the playground loads, pinned: a page someone was sent
#: must not change under them because a CDN tag moved.
_VEGA = (
    "https://cdn.jsdelivr.net/npm/vega@5.30.0",
    "https://cdn.jsdelivr.net/npm/vega-lite@5.21.0",
    "https://cdn.jsdelivr.net/npm/vega-embed@6.26.0",
)

#: The playground sets `data-nxv-theme` from its own buttons. Here the viewer's
#: setting decides — an explicit `data-theme` stamp, else the OS — and charts
#: re-mount so their Vega config is read from the new variables.
_THEME_SYNC = """
(function () {
  var root = document.documentElement;
  var dark = window.matchMedia ? matchMedia('(prefers-color-scheme: dark)') : null;
  function wanted() {
    var stamped = root.getAttribute('data-theme');
    if (stamped === 'dark' || stamped === 'light') return stamped;
    return dark && dark.matches ? 'dark' : 'light';
  }
  function sync() {
    var mode = wanted();
    if (root.getAttribute('data-nxv-theme') === mode) return;
    root.setAttribute('data-nxv-theme', mode);
    if (typeof mountCharts === 'function') mountCharts();
  }
  sync();
  new MutationObserver(sync).observe(root, {attributes: true, attributeFilter: ['data-theme']});
  if (dark && dark.addEventListener) dark.addEventListener('change', sync);
})();
"""


def _theme_css() -> str:
    from nexcraftviz.theme import load
    from nexcraftviz.theme.css import to_bundle

    return to_bundle(load("nexcraftviz-light"), load("nexcraftviz-dark"))


def _scripts() -> str:
    from nexcraftviz.gallery import _playground_js

    tags = "".join(f'<script src="{src}"></script>' for src in _VEGA)
    return f"{tags}<script>{_playground_js()}</script><script>{_THEME_SYNC}</script>"


# ---------------------------------------------------------------------------
# style — the page's names for the theme's variables
# ---------------------------------------------------------------------------

#: The page's own names for the theme's `--nxv-*` variables, so every colour on
#: it is the playground's and one theme switch restyles page and charts alike.
_CSS = (
    """
:root { --ground: var(--nxv-background); --surface: var(--nxv-surface);
  --sunk: var(--nxv-surface-alt); --ink: var(--nxv-text); --ink-2: var(--nxv-text-secondary);
  --muted: var(--nxv-text-muted); --accent: var(--nxv-accent); --rule: var(--nxv-border);
  --rule-strong: var(--nxv-border-strong); --good: var(--nxv-positive);
  --bad: var(--nxv-negative); --warn: var(--nxv-warning); color-scheme: light; }
:root[data-nxv-theme="dark"] { color-scheme: dark; }
body { background: var(--ground); color: var(--ink); margin: 0;
  font: 15px/1.55 var(--nxv-font); }
main { max-width: 1180px; margin: 0 auto; padding: 32px 28px 48px; display: grid; gap: 40px; }
h1, h2, h3 { text-wrap: balance; margin: 0; }
h1, h2, .tally-n { font-family: var(--nxv-font); }
h1 { font-size: 34px; line-height: 1.1; font-weight: 600; letter-spacing: -0.01em; }
h2 { font-size: 24px; font-weight: 600; }
h3 { font-size: 17px; font-weight: 600; line-height: 1.35; }
code, .case-id, .chip, .gate, .meta, .ops, .cell-s, .trace {
  font-family: var(--nxv-font-mono); }
.eyebrow { margin: 0 0 6px; color: var(--accent); font-size: 12px; font-weight: 600;
  letter-spacing: 0.08em; text-transform: uppercase; }
.meta { margin: 8px 0 0; color: var(--muted); font-size: 13px; }
.masthead { display: grid; gap: 24px; grid-template-columns: minmax(0, 1fr); }
.tallies { display: grid; gap: 1px; background: var(--rule); border: 1px solid var(--rule);
  border-radius: 10px; overflow: hidden;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); }
.tally { background: var(--surface); padding: 14px 16px; display: grid; gap: 2px; }
.tally-n { font-size: 30px; font-weight: 600; font-variant-numeric: tabular-nums;
  line-height: 1.1; }
.tally-l { color: var(--muted); font-size: 12.5px; }
.tally.good .tally-n { color: var(--good); } .tally.bad .tally-n { color: var(--bad); }
.tally.warn .tally-n { color: var(--warn); }
.block { display: grid; gap: 16px; }
.lede, .quiet { margin: 0; color: var(--ink-2); max-width: 70ch; }
.quiet { color: var(--muted); font-size: 14px; }
.attention { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--rule); }
.attention li { display: grid; grid-template-columns: 88px minmax(160px, 260px) minmax(0, 1fr);
  gap: 14px; align-items: baseline; padding: 10px 0; border-bottom: 1px solid var(--rule); }
.where { font-family: var(--nxv-font-mono); font-size: 13px; }
.why { color: var(--ink-2); font-size: 14px; }
.pill { display: inline-block; font-size: 10.5px; font-weight: 700; letter-spacing: 0.07em;
  text-transform: uppercase; padding: 2px 8px; border-radius: 999px; margin-left: 8px;
  vertical-align: 1px; font-family: var(--nxv-font); }
.attention .pill { margin-left: 0; justify-self: start; }
.pill.pass { color: var(--good); background: color-mix(in srgb, var(--good) 13%, transparent); }
.pill.fail { color: var(--bad); background: color-mix(in srgb, var(--bad) 13%, transparent); }
.pill.retry, .pill.repair { color: var(--warn);
  background: color-mix(in srgb, var(--warn) 14%, transparent); }
.scenario { display: grid; gap: 24px; grid-template-columns: minmax(0, 1fr) minmax(0, 1.05fr);
  background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; padding: 20px; }
.scenario.fail { border-color: color-mix(in srgb, var(--bad) 45%, var(--rule)); }
.scenario-text { display: grid; gap: 10px; align-content: start; }
.case-id { margin: 0; color: var(--muted); font-size: 12.5px; }
.plan, .verdict, .rationale { margin: 0; font-size: 14px; }
.rationale { color: var(--ink-2); }
.k { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em;
  margin-right: 6px; }
.chip { display: inline-block; font-size: 12px; padding: 1px 7px; margin: 2px 2px 0 0;
  border: 1px solid var(--rule); border-radius: 6px; background: var(--sunk); color: var(--ink-2); }
.chip b { color: var(--ink); font-weight: 600; }
.chip i { color: var(--muted); font-style: normal; }
.gates { display: flex; flex-wrap: wrap; gap: 6px; }
.gate { font-size: 11.5px; padding: 2px 8px; border-radius: 6px; border: 1px solid var(--rule); }
.gate.ok { color: var(--good); } .gate.ok::before { content: "✓ "; }
.gate.no { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 40%, var(--rule)); }
.gate.no::before { content: "✕ "; }
.problems { margin: 0; padding-left: 18px; color: var(--bad); font-size: 13.5px; }
details summary { cursor: pointer; color: var(--muted); font-size: 13px; }
details summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.trace { margin: 8px 0 0; padding-left: 20px; font-size: 12px; color: var(--ink-2); }
.scenario-chart, .case-answer { min-width: 0; }
.scenario-chart .nxv-card, .case-answer .nxv-card { margin: 0; overflow-x: auto; }
.caption { margin: 6px 0 0; color: var(--muted); font-size: 12.5px; }
.nxv-card.kpi-tile { max-width: 380px; }
.skill { display: grid; gap: 0; border-top: 2px solid var(--ink); }
.skill-head { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 14px;
  align-items: baseline; padding: 12px 0; }
.skill-head h3 code { font-size: 15px; color: var(--accent); }
.skill-head p { margin: 0; color: var(--ink-2); font-size: 14px; }
.count { font-variant-numeric: tabular-nums; font-weight: 700; font-size: 14px; }
.count.good { color: var(--good); } .count.bad { color: var(--bad); }
.cases { list-style: none; margin: 0; padding: 0; }
.case { display: grid; grid-template-columns: minmax(0, 0.85fr) minmax(0, 1.15fr); gap: 24px;
  padding: 16px 0; border-top: 1px solid var(--rule); }
.case-ask { display: grid; gap: 6px; align-content: start; }
.ask { margin: 0; font-size: 15px; font-weight: 500; }
.checks { margin: 0; color: var(--muted); font-size: 13.5px; }
.case-answer { display: grid; gap: 10px; align-content: start; }
.ops, .steps { margin: 0; padding-left: 20px; font-size: 13px; }
.ops code { font-size: 12.5px; }
.steps li { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; }
.action { font-weight: 700; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--accent); }
.parts { color: var(--muted); font-size: 12.5px; }
.declined { margin: 0; padding-left: 18px; color: var(--warn); font-size: 13.5px; }
.headline { margin: 0; font-weight: 600; }
.prose { margin: 0; max-width: 65ch; }
.points { margin: 0; padding-left: 18px; color: var(--ink-2); font-size: 14px; }
.verdict-big { margin: 0; font-weight: 600; }
.verdict-big.ok { color: var(--good); } .verdict-big.no { color: var(--bad); }
.score { color: var(--muted); font-weight: 400; font-variant-numeric: tabular-nums; }
.grid12 { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 6px; }
.cell { background: var(--sunk); border: 1px solid var(--rule-strong); border-radius: 6px;
  padding: 8px; display: grid; gap: 2px; min-height: 44px; }
.cell-t { font-size: 12.5px; font-weight: 600; overflow-wrap: anywhere; }
.cell-s { font-size: 11px; color: var(--muted); }
.colophon { color: var(--muted); font-size: 13px; border-top: 1px solid var(--rule);
  padding-top: 16px; }
.colophon p { margin: 0; }
@media (max-width: 820px) {
  .scenario, .case { grid-template-columns: minmax(0, 1fr); }
  .attention li { grid-template-columns: minmax(0, 1fr); gap: 4px; }
}
"""
)
