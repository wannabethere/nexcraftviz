"""The live-run report: one page, every question, answer and chart."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.html_report import build_report

SPEC = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "data": {"values": [{"region": "North", "revenue": 152}, {"region": "West", "revenue": 128}]},
    "width": "container",
    "mark": "bar",
    "encoding": {"x": {"field": "region", "type": "nominal"},
                 "y": {"field": "revenue", "type": "quantitative"}},
}

EVALS = {
    "at": "2026-09-10T10:35:08", "model": "gpt-5-mini",
    "results": [
        {"id": "sort-descending", "skill": "viz.edit", "checks": "the most basic instruction",
         "instruction": "sort it so the biggest region is first", "ok": True,
         "binding": "json_schema_strict", "notes": [], "missing": [], "error": "",
         "emitted": [{"op": "sort_by", "view": None, "channel": "x", "by": "revenue",
                      "order": "descending"}],
         "artifact": SPEC, "answer": None, "tokens": {"tokens_in": 100, "tokens_out": 20}},
        {"id": "manage-compound", "skill": "viz.manage", "checks": "keeps both halves",
         "instruction": "make it dark and sort descending", "ok": False,
         "binding": "json_schema_strict",
         "notes": ["actions ['theme'], expected ['theme', 'edit']"],
         "missing": [], "error": "",
         "emitted": [{"action": "theme", "instruction": "make it dark", "parts": []}],
         "artifact": None, "answer": {"status": "ok", "steps": [], "declined": []},
         "tokens": {"tokens_in": 50, "tokens_out": 10}},
        {"id": "narrate-bar", "skill": "viz.narrate", "checks": "leads with the answer",
         "instruction": "what does this show?", "ok": True, "binding": "json_schema_strict",
         "notes": [], "missing": [], "error": "", "emitted": [], "artifact": None,
         "answer": {"headline": "North leads <by a lot>", "summary": "North is ahead.",
                    "points": []},
         "tokens": {}},
    ],
}

RUNS = {
    "at": "2026-09-10T10:40:00",
    "results": [
        {"scenario": "ranked_categories", "passed": True, "question": "Which region leads?",
         "plan": {"status": "ok", "chart_type": "bar", "rationale": "one measure",
                  "encodings": [{"channel": "x", "field": "region", "aggregate": ""}]},
         "spec": SPEC, "critique": {"answers_question": True, "complaint": ""},
         "gates": [{"gate": "validates", "passed": True, "detail": ""}],
         "problems": [], "error": "", "run": {"trace": ["plan: bar"], "regenerations": 0}},
        {"scenario": "widget_entity_rows", "passed": True, "question": "Control owners",
         "plan": {}, "spec": None, "critique": None, "gates": [], "problems": [], "error": "",
         "run": {"trace": ["retry: validates: unknown field mean_days_open"],
                 "regenerations": 1}},
    ],
}


@pytest.fixture
def page(tmp_path: Path) -> str:
    eval_path = tmp_path / "eval-1.json"
    run_path = tmp_path / "run-1.json"
    eval_path.write_text(json.dumps(EVALS))
    run_path.write_text(json.dumps(RUNS))
    return build_report(eval_path=eval_path, run_path=run_path)


def test_the_page_is_named_and_tallied(page):
    assert "<title>nexcraftviz Live Run</title>" in page
    assert "2/3" in page and "eval cases passed" in page
    assert "2/2" in page and "harness scenarios passed" in page


def test_failures_and_retries_are_raised_first(page):
    attention = page[page.index("Needs a look"):page.index("Harness scenarios")]
    assert "manage-compound" in attention
    assert "widget_entity_rows" in attention and "retried" in attention


def test_every_skill_answer_is_shown_in_its_own_terms(page):
    # Escaped, as all model text is: quotes become &quot; in the source and
    # render as quotes on the page.
    assert "sort_by(" in page and "order=&quot;descending&quot;" in page
    assert ">theme<" in page
    assert "North is ahead." in page


def test_model_text_is_escaped(page):
    """Model output is data. A headline with markup in it must not become markup."""
    assert "North leads &lt;by a lot&gt;" in page
    assert "<by a lot>" not in page


def test_both_themes_are_defined(page):
    assert '@media (prefers-color-scheme: dark)' in page
    assert ':root[data-theme="dark"]' in page
    assert "body { background: var(--ground)" in page


def test_charts_are_drawn_when_the_renderer_is_available(page):
    from nexcraftviz.render import available

    if available():
        assert page.count('<figure class="plate">') >= 2
        assert "<svg" in page
    else:
        assert "install the render extra" in page


def test_a_page_can_omit_its_document_shell(tmp_path: Path):
    eval_path = tmp_path / "eval-1.json"
    eval_path.write_text(json.dumps(EVALS))
    content = build_report(eval_path=eval_path, standalone=False)
    assert not content.startswith("<!doctype")
    assert content.startswith("<title>")
