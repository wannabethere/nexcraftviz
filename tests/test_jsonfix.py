"""The bracket balancer — including the exact string a live model produced."""
from __future__ import annotations

import json

from nexcraftviz.spec.jsonfix import balance_brackets

#: Verbatim from gpt-5-mini generating a KPI for "Total open findings" on
#: 2026-09-10. The second layer object is never closed: `}}]` where `}}}]`
#: was meant.
LIVE_KPI = "{\"$schema\":\"https://vega.github.io/schema/vega-lite/v5.json\",\"data\":{\"values\":[{\"open_findings\":47}]},\"width\":\"container\",\"height\":120,\"layer\":[{\"mark\":{\"type\":\"text\",\"align\":\"center\",\"baseline\":\"middle\"},\"encoding\":{\"text\":{\"field\":\"open_findings\",\"type\":\"quantitative\",\"format\":\"d\"}}},{\"mark\":{\"type\":\"text\",\"align\":\"center\",\"baseline\":\"middle\",\"dy\":30},\"encoding\":{\"text\":{\"value\":\"Total open findings\"}}],\"title\":{\"text\":\"Total open findings\",\"subtitle\":\"Current count of unresolved findings (snapshot)\"}}"  # noqa: E501 — verbatim model output


def test_the_live_failure_is_repaired_to_the_document_the_model_meant():
    fixed, notes = balance_brackets(LIVE_KPI)
    spec = json.loads(fixed)
    assert len(notes) == 1 and "inserted" in notes[0]
    assert len(spec["layer"]) == 2
    assert spec["layer"][1]["encoding"]["text"]["value"] == "Total open findings"
    # The title stays at the root, where the model put it — not swallowed into
    # the layer, which a naive "append closers at the end" repair would do.
    assert spec["title"]["text"] == "Total open findings"


def test_containers_left_open_at_the_end_are_closed():
    fixed, notes = balance_brackets('{"a": [1, 2, {"b": 3')
    assert json.loads(fixed) == {"a": [1, 2, {"b": 3}]}
    assert notes == [
        "appended '}' at the end", "appended ']' at the end", "appended '}' at the end",
    ]


def test_valid_json_is_returned_untouched():
    text = '{"a": [1, {"b": "}]"}]}'
    assert balance_brackets(text) == (text, [])


def test_brackets_inside_strings_are_not_counted():
    text = '{"label": "a { b [ c"}'
    assert balance_brackets(text) == (text, [])


def test_a_closer_with_nothing_open_is_not_guessed_at():
    """Guessing here produces a document that parses and means something else."""
    text = '{"a": 1}}'
    assert balance_brackets(text) == (text, [])


def test_an_unterminated_string_is_not_guessed_at():
    text = '{"a": "never closed'
    assert balance_brackets(text) == (text, [])
