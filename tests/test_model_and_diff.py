"""Spec model (view walking, hashing, families) and JSON-Patch diffing."""
from __future__ import annotations

import pytest

from nexcraftviz.spec.diff import apply_patch, diff, invert
from nexcraftviz.spec.model import Spec, SpecError

# ---------------------------------------------------------------------------
# parsing and identity
# ---------------------------------------------------------------------------

def test_from_json_strips_markdown_fences() -> None:
    spec = Spec.from_json('```json\n{"mark": "bar"}\n```')
    assert spec.raw == {"mark": "bar"}


def test_from_json_raises_on_garbage() -> None:
    with pytest.raises(SpecError):
        Spec.from_json("not json at all")


def test_from_json_lenient_never_raises() -> None:
    assert Spec.from_json_lenient("nope").raw == {}


def test_from_json_rejects_a_non_object() -> None:
    with pytest.raises(SpecError, match="must decode to an object"):
        Spec.from_json("[1, 2, 3]")


def test_hash_ignores_key_order() -> None:
    a = Spec({"mark": "bar", "width": 100})
    b = Spec({"width": 100, "mark": "bar"})
    assert a.hash == b.hash and a == b


def test_clone_is_deep(bar_spec: Spec) -> None:
    clone = bar_spec.clone()
    clone.primary_view.encoding["x"]["field"] = "changed"
    assert bar_spec.primary_view.encoding["x"]["field"] == "revenue"


# ---------------------------------------------------------------------------
# families
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"$schema": "https://vega.github.io/schema/vega-lite/v5.json"}, "vega-lite"),
        ({"$schema": "https://vega.github.io/schema/vega/v5.json"}, "vega"),
        ({"mark": "bar"}, "vega-lite"),
        ({"marks": [{"type": "rect"}]}, "vega"),
        ({"kpi_metadata": {"chart_subtype": "counter"}}, "kpi"),
        ({"columns": [{"field": "a"}]}, "table"),
        ({"title": "nothing"}, "unknown"),
    ],
)
def test_family_detection(raw, expected) -> None:
    assert Spec(raw).family == expected


# ---------------------------------------------------------------------------
# views
# ---------------------------------------------------------------------------

def test_unit_spec_has_one_view_at_the_root(bar_spec: Spec) -> None:
    views = bar_spec.views()
    assert len(views) == 1 and views[0].path == ()


def test_layered_spec_exposes_each_layer(layered_spec: Spec) -> None:
    views = layered_spec.views()
    assert [v.path for v in views] == [("layer", 0), ("layer", 1)]
    assert [v.mark_type for v in views] == ["bar", "rule"]


def test_nested_concat_is_walked() -> None:
    spec = Spec(
        {
            "vconcat": [
                {"mark": "bar", "encoding": {}},
                {"layer": [{"mark": "line", "encoding": {}}, {"mark": "point", "encoding": {}}]},
            ]
        }
    )
    assert [v.path for v in spec.views()] == [
        ("vconcat", 0),
        ("vconcat", 1, "layer", 0),
        ("vconcat", 1, "layer", 1),
    ]


def test_views_never_returns_empty() -> None:
    """Callers always get something to act on, even for a degenerate spec."""
    assert len(Spec({"title": "x"}).views()) == 1


def test_mark_summary_reports_mixed_layers(layered_spec: Spec) -> None:
    assert layered_spec.mark_summary == "bar+rule"


def test_field_refs_span_every_view(layered_spec: Spec) -> None:
    fields = {name for _, _, name in layered_spec.field_refs()}
    assert fields == {"region", "revenue"}


def test_list_valued_channels_are_expanded(bar_spec: Spec) -> None:
    bar_spec.primary_view.encoding["tooltip"] = [
        {"field": "region", "type": "nominal"},
        {"field": "revenue", "type": "quantitative"},
    ]
    channels = [channel for _, channel, _ in bar_spec.encodings()]
    assert channels.count("tooltip") == 2


def test_datum_channels_are_not_field_refs(layered_spec: Spec) -> None:
    """`{"datum": 100}` is a literal, not a column — validating it would be wrong."""
    assert all(name != "100" for _, _, name in layered_spec.field_refs())


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

def test_data_columns_union_ragged_rows() -> None:
    spec = Spec({"data": {"values": [{"a": 1}, {"b": 2}]}})
    assert set(spec.data_columns) == {"a", "b"}


def test_set_data_values_replaces_a_url_source() -> None:
    spec = Spec({"data": {"url": "https://example.com/data.json"}})
    spec.set_data_values([{"a": 1}])
    assert spec.raw["data"] == {"values": [{"a": 1}]}


def test_ensure_schema_url_pins_the_right_family() -> None:
    assert "vega-lite/v5" in Spec({"mark": "bar"}).ensure_schema_url().raw["$schema"]
    assert Spec({}).ensure_schema_url().raw == {}


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------

def test_diff_of_identical_specs_is_empty(bar_spec: Spec) -> None:
    assert diff(bar_spec, bar_spec.clone()) == []


def test_diff_detects_add_remove_and_replace() -> None:
    before = {"a": 1, "b": 2}
    after = {"a": 9, "c": 3}
    ops = {(op.op, op.path) for op in diff(before, after)}
    assert ops == {("replace", "/a"), ("remove", "/b"), ("add", "/c")}


def test_apply_patch_reproduces_the_target(bar_spec: Spec, line_spec: Spec) -> None:
    patch = diff(bar_spec, line_spec)
    assert apply_patch(bar_spec, patch).hash == line_spec.hash


def test_invert_restores_the_original(bar_spec: Spec, line_spec: Spec) -> None:
    patch = diff(bar_spec, line_spec)
    forward = apply_patch(bar_spec, patch)
    assert apply_patch(forward, invert(patch)).hash == bar_spec.hash


def test_list_growth_and_shrinkage_round_trip() -> None:
    before = Spec({"layer": [{"mark": "bar"}]})
    after = Spec({"layer": [{"mark": "bar"}, {"mark": "line"}, {"mark": "point"}]})

    patch = diff(before, after)
    assert apply_patch(before, patch).hash == after.hash
    assert apply_patch(after, invert(patch)).hash == before.hash


def test_pointer_escaping_survives_awkward_keys() -> None:
    before = Spec({"a/b": 1, "c~d": 2})
    after = Spec({"a/b": 9, "c~d": 2})

    patch = diff(before, after)
    assert patch[0].path == "/a~1b"
    assert apply_patch(before, patch).raw["a/b"] == 9


def test_describe_is_readable() -> None:
    patch = diff({"title": "old"}, {"title": "new"})
    assert patch[0].describe() == "title: 'old' → 'new'"


def test_patch_serialises_to_rfc_6902_shape() -> None:
    patch = diff({"a": 1}, {"a": 2})
    assert patch[0].to_dict() == {"op": "replace", "path": "/a", "value": 2}


def test_apply_patch_does_not_mutate_the_input(bar_spec: Spec) -> None:
    before = bar_spec.hash
    apply_patch(bar_spec, diff(bar_spec, Spec({"mark": "line"})))
    assert bar_spec.hash == before
