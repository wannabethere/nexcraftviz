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


#: Live, widget_trend: the first view's encoding closed (`}}}`) and its layer
#: object never did; the next view began where a key belonged.
LIVE_UNCLOSED_LAYER = '{"$schema":"https://vega.github.io/schema/vega-lite/v5.json","title":"Monthly completion rate (Jan\u2013Apr 2026)","width":"container","height":240,"layer":[{"mark":{"type":"area","interpolate":"monotone","opacity":0.15},"encoding":{"x":{"field":"month","type":"temporal","axis":{"title":"Month"},"sort":"descending"},"y":{"field":"completion_pct","type":"quantitative","aggregate":"mean","axis":{"title":"Completion (%)"}}},{"mark":{"type":"line","strokeWidth":2.5,"interpolate":"monotone"},"encoding":{"x":{"field":"month","type":"temporal","sort":"descending"},"y":{"field":"completion_pct","type":"quantitative","aggregate":"mean"}}},{"mark":{"type":"point","filled":true,"size":60},"encoding":{"x":{"field":"month","type":"temporal","sort":"descending"},"y":{"field":"completion_pct","type":"quantitative","aggregate":"mean"},"tooltip":[{"field":"month","type":"temporal"},{"field":"completion_pct","type":"quantitative"}]}}],"data":{"values":[]}}'  # noqa: E501
LIVE_UNCLOSED_LAYER_SPACED = '{"$schema":"https://vega.github.io/schema/vega-lite/v5.json","title":"Monthly completion rate (Jan\u2013Apr 2026)","width":"container","height":240,"layer":[{"mark":{"type":"line","interpolate":"monotone","strokeWidth":2.5},"encoding":{"x":{"field":"month","type":"temporal","sort":{"order":"descending"},"axis":{"title":"Month"}},"y":{"aggregate":"mean","field":"completion_pct","type":"quantitative","axis":{"title":"Completion rate (%)"}}} ,{"mark":{"type":"point","filled":true,"size":60},"encoding":{"x":{"field":"month","type":"temporal","sort":{"order":"descending"}},"y":{"aggregate":"mean","field":"completion_pct","type":"quantitative"},"tooltip":[{"field":"month","type":"temporal","title":"Month"},{"field":"completion_pct","aggregate":"mean","title":"Completion rate (%)"}]}}]}'  # noqa: E501
#: Live, widget_trend: "Jan\u2013Apr" arrived as "Jan\x13Apr".
LIVE_DROPPED_DASH = '{"$schema":"https://vega.github.io/schema/vega-lite/v5.json","width":"container","height":240,"title":{"text":"Monthly completion rate (Jan\x13Apr 2026)"},"layer":[{"mark":{"type":"area","opacity":0.12,"interpolate":"monotone"},"encoding":{"x":{"field":"month","type":"temporal","title":"Month","scale":{"reverse":true}},"y":{"field":"completion_pct","type":"quantitative","aggregate":"mean","title":"Completion rate (%)"}}},{"mark":{"type":"line","strokeWidth":2.5,"interpolate":"monotone"},"encoding":{"x":{"field":"month","type":"temporal","title":"Month","scale":{"reverse":true}},"y":{"field":"completion_pct","type":"quantitative","aggregate":"mean","title":"Completion rate (%)"},"tooltip":[{"field":"month","type":"temporal"},{"field":"completion_pct","type":"quantitative","aggregate":"mean","title":"Completion rate (%)","format":".2f"}]}},{"mark":{"type":"point","filled":true,"size":60},"encoding":{"x":{"field":"month","type":"temporal","title":"Month","scale":{"reverse":true}},"y":{"field":"completion_pct","type":"quantitative","aggregate":"mean","title":"Completion rate (%)"},"tooltip":[{"field":"month","type":"temporal"},{"field":"completion_pct","type":"quantitative","aggregate":"mean","title":"Completion rate (%)","format":".2f"}]}}],"data":{"values":[]}}'  # noqa: E501


def test_a_layer_left_open_is_closed_where_its_sibling_begins():
    """Seen three times in one live run — both trend scenarios, first attempt
    and retry — so a regeneration is no answer to it."""
    for text, views in ((LIVE_UNCLOSED_LAYER, 3), (LIVE_UNCLOSED_LAYER_SPACED, 2)):
        repaired, notes = balance_brackets(text)
        assert notes and "new array item" in notes[0]
        doc = json.loads(repaired)
        assert len(doc["layer"]) == views
        assert all({"mark", "encoding"} <= set(view) for view in doc["layer"])


def test_an_object_where_a_key_belongs_outside_an_array_is_refused():
    """No sibling list to belong to, so no single reading."""
    text = '{"a":{"b":1},{"c":2}}'
    assert balance_brackets(text) == (text, [])


def test_a_dash_that_arrived_as_a_control_character_is_restored():
    from nexcraftviz.spec.jsonfix import repair_json

    repaired, notes = repair_json(LIVE_DROPPED_DASH)
    doc = json.loads(repaired)
    assert doc["title"]["text"] == "Monthly completion rate (Jan\u2013Apr 2026)"
    assert notes == ["restored '\u2013' from control character U+0013"]
