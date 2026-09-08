"""The regression net: every Vega-Lite spec in the corpus must stay renderable.

This is the test that makes the rest of the package safe to change. 148 real,
hand-authored specs covering 20 chart types go through the full pipeline —
parse, validate to tier 3, compile to Vega, rasterise to PNG. A refactor that
breaks view walking, scope resolution or transform handling fails here loudly
rather than quietly degrading charts in production.

Known-bad specs are listed explicitly in :data:`KNOWN_CORPUS_DEFECTS` rather
than being skipped silently, so the list can only shrink.
"""
from __future__ import annotations

import pytest

from nexcraftviz.corpus.loader import seed
from nexcraftviz.render import available as render_available
from nexcraftviz.render import to_png, to_vega
from nexcraftviz.spec.validate import validate

#: Specs in the shipped corpus that do not validate, with the reason. Each is a
#: real defect to fix in the corpus, not a validator false positive.
KNOWN_CORPUS_DEFECTS: dict[str, str] = {
    # Data is "2025-Q1"-style quarter labels, which Vega-Lite cannot parse as a
    # date — it renders an Invalid Date axis. The encoding should be `ordinal`.
    "line_nps_over_time": "type_mismatch",
}

CORPUS = seed()
VEGA_PAIRS = CORPUS.with_vega_specs


def test_corpus_loads() -> None:
    assert len(CORPUS) == 200
    assert CORPUS.tenant == "global"


def test_corpus_families_partition_cleanly() -> None:
    """Every pair belongs to exactly one renderer family."""
    families = {p.family for p in CORPUS}
    assert families == {"vega-lite", "table-with-cells", "kpi-card"}

    # Only vega-lite pairs carry a real spec; the others are rendered by
    # dedicated frontend components and legitimately have none.
    for pair in CORPUS:
        if pair.family == "vega-lite":
            assert pair.has_vega_spec, f"{pair.name} claims vega-lite but has no spec"
        else:
            assert not pair.has_vega_spec, f"{pair.name} is {pair.family} but ships a spec"


@pytest.mark.parametrize("pair", VEGA_PAIRS, ids=lambda p: p.name)
def test_corpus_spec_parses_and_validates(pair) -> None:
    spec = pair.spec()
    assert spec.family == "vega-lite"

    spec, report = validate(spec, max_tier=3)
    if pair.name in KNOWN_CORPUS_DEFECTS:
        assert not report.ok, f"{pair.name} was expected to fail — remove it from the known list"
        assert report.errors[0].code == KNOWN_CORPUS_DEFECTS[pair.name]
        return
    assert report.ok, f"{pair.name}: {report.summary()}"


@pytest.mark.skipif(not render_available(), reason="needs the `render` extra")
@pytest.mark.parametrize("pair", VEGA_PAIRS, ids=lambda p: p.name)
def test_corpus_spec_compiles_and_rasterises(pair) -> None:
    if pair.name in KNOWN_CORPUS_DEFECTS:
        pytest.skip(f"known corpus defect: {KNOWN_CORPUS_DEFECTS[pair.name]}")

    spec = pair.spec()
    compiled = to_vega(spec)
    assert compiled.get("marks") is not None, f"{pair.name} compiled to a spec with no marks"

    png = to_png(spec)
    assert png[:8] == b"\x89PNG\r\n\x1a\n", f"{pair.name} did not produce a PNG"
    assert len(png) > 1000, f"{pair.name} rendered a suspiciously small image"


def test_known_defect_list_has_no_stale_entries() -> None:
    """Guards against the list outliving the bug it documents."""
    names = {p.name for p in CORPUS}
    stale = set(KNOWN_CORPUS_DEFECTS) - names
    assert not stale, f"KNOWN_CORPUS_DEFECTS names pairs that no longer exist: {stale}"


def test_corpus_coverage_is_recorded() -> None:
    """Documents where the corpus is thin, so M4 has a measurable target.

    Not an assertion about quality — the corpus ships with known gaps. It fails
    only if a chart type drops to a single example, at which point retrieval
    for that type stops working in any useful way.
    """
    counts = CORPUS.chart_type_counts()
    assert len(counts) >= 20
    singletons = [t for t, n in counts.items() if n < 2]
    assert not singletons, f"chart types with <2 examples cannot be retrieved: {singletons}"
