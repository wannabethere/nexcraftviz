"""Themes: tokens, Vega config, CSS, contrast, and parity with vega-themes."""
from __future__ import annotations

import pytest

from nexcraftviz.render import available as render_available
from nexcraftviz.render import to_png
from nexcraftviz.spec.model import Spec
from nexcraftviz.theme import (
    ThemeNotFound,
    apply_theme,
    audit,
    available_themes,
    contrast_ratio,
    default,
    load,
    strip_hardcoded_colours,
    to_bundle,
    to_css,
    to_variables,
)
from nexcraftviz.theme.contrast import MIN_DELTA_E, delta_e, relative_luminance
from nexcraftviz.theme.css import CELL_RENDERERS

THEMES = available_themes()
#: The presets derived from vega-themes; their Vega output must stay verbatim.
DERIVED = ("powerbi", "carbon-g90")


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def test_the_expected_presets_ship() -> None:
    assert set(THEMES) == {"nexcraftviz-light", "nexcraftviz-dark", "powerbi", "carbon-g90"}


def test_unknown_theme_lists_what_is_available() -> None:
    with pytest.raises(ThemeNotFound, match="nexcraftviz-light"):
        load("chartreuse-nightmare")


def test_default_resolves_by_mode() -> None:
    assert default("light").name == "nexcraftviz-light"
    assert default("dark").is_dark


@pytest.mark.parametrize("name", THEMES)
def test_every_preset_parses_and_has_a_palette(name: str) -> None:
    theme = load(name)
    assert theme.name == name
    assert theme.palette.categorical, f"{name} has no categorical palette"


# ---------------------------------------------------------------------------
# Vega config
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", THEMES)
def test_config_is_a_non_empty_dict(name: str) -> None:
    assert load(name).vega_config()


def test_generated_config_carries_tokens_through() -> None:
    theme = load("nexcraftviz-light")
    config = theme.vega_config()

    assert config["range"]["category"] == theme.palette.categorical
    assert config["axis"]["labelColor"] == theme.text.secondary
    assert config["bar"]["fill"] == theme.palette.categorical[0]
    # A scheme name becomes a scheme reference, not a literal string.
    assert config["range"]["ramp"] == {"scheme": theme.palette.sequential}


def test_generated_config_puts_the_grid_on_quantitative_axes_only() -> None:
    config = load("nexcraftviz-light").vega_config()
    assert config["axisQuantitative"]["grid"] is True
    assert config["axisBand"]["grid"] is False


@pytest.mark.parametrize("name", DERIVED)
def test_derived_presets_emit_their_source_config_verbatim(name: str) -> None:
    """Deriving is pointless if we then layer our own defaults underneath."""
    theme = load(name)
    assert theme.vega_base == "overrides"
    assert theme.vega_config() == theme.vega_overrides


def test_config_is_a_copy_not_a_live_reference() -> None:
    theme = load("powerbi")
    theme.vega_config()["background"] = "#ff0000"
    assert theme.vega_config()["background"] != "#ff0000"


# ---------------------------------------------------------------------------
# applying
# ---------------------------------------------------------------------------

def test_apply_theme_installs_the_config(bar_spec: Spec) -> None:
    result = apply_theme(bar_spec, "nexcraftviz-dark")
    assert result.spec.raw["config"]["background"] == load("nexcraftviz-dark").surfaces.background


def test_apply_theme_is_undoable(bar_spec: Spec) -> None:
    from nexcraftviz.spec.diff import apply_patch

    before = bar_spec.hash
    result = apply_theme(bar_spec, "powerbi")
    assert apply_patch(result.spec, result.inverse).hash == before


def test_switching_themes_leaves_nothing_behind(bar_spec: Spec) -> None:
    light = apply_theme(bar_spec, "nexcraftviz-light").spec
    dark = apply_theme(light, "nexcraftviz-dark").spec
    assert dark.raw["config"] == load("nexcraftviz-dark").vega_config()


def test_apply_theme_accepts_a_tokens_object(bar_spec: Spec) -> None:
    assert apply_theme(bar_spec, load("powerbi")).changed


def test_strip_hardcoded_colours_lets_a_theme_take_effect(bar_spec: Spec) -> None:
    """A baked-in `mark.color` overrides config, so theming appears to do nothing.

    The fixture spec has `"color": "#0C8BA6"` on the mark, exactly as the
    generator emits today.
    """
    assert bar_spec.primary_view.node["mark"]["color"] == "#0C8BA6"

    stripped = strip_hardcoded_colours(bar_spec).spec
    assert "color" not in stripped.primary_view.node["mark"]

    themed = apply_theme(stripped, "powerbi").spec
    assert themed.raw["config"]["bar"]["fill"] == "#118DFF"


def test_strip_hardcoded_colours_collapses_a_bare_mark(line_spec: Spec) -> None:
    spec = line_spec.clone()
    spec.primary_view.node["mark"] = {"type": "line", "color": "#123456"}
    assert strip_hardcoded_colours(spec).spec.primary_view.node["mark"] == "line"


def test_strip_hardcoded_colours_leaves_colour_scales_alone(line_spec: Spec) -> None:
    """A colour *scale* is a data encoding, not styling."""
    stripped = strip_hardcoded_colours(line_spec).spec
    assert stripped.primary_view.encoding["color"]["field"] == "region"


def test_strip_hardcoded_colours_is_a_no_op_when_there_is_nothing_to_strip() -> None:
    assert not strip_hardcoded_colours(Spec({"mark": "bar"})).changed


# ---------------------------------------------------------------------------
# parity — the M2 exit criterion
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not render_available(), reason="needs the `render` extra")
@pytest.mark.parametrize("name,source", [("powerbi", "powerbi"), ("carbon-g90", "carbong90")])
def test_derived_preset_renders_identically_to_the_vega_theme(
    bar_spec: Spec, name: str, source: str
) -> None:
    """Byte-for-byte parity with vega-themes, as a host renders it.

    A host hands vega-themes' config straight to its renderer. Applying our
    derived preset must produce the same pixels, or switching is a visual
    regression rather than an upgrade.
    """
    unstyled = strip_hardcoded_colours(bar_spec).spec

    theirs = to_png(unstyled, theme=source)
    ours = to_png(apply_theme(unstyled, name).spec)
    assert ours == theirs


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

def test_variables_cover_every_token_group() -> None:
    variables = to_variables(load("nexcraftviz-light"))
    for name in (
        "--nxv-background", "--nxv-surface", "--nxv-border", "--nxv-text",
        "--nxv-positive", "--nxv-negative", "--nxv-accent",
        "--nxv-font", "--nxv-size-display", "--nxv-radius-md", "--nxv-space",
    ):
        assert name in variables, f"missing {name}"


def test_categorical_colours_are_exposed_individually() -> None:
    theme = load("nexcraftviz-light")
    variables = to_variables(theme)
    assert variables["--nxv-cat-1"] == theme.palette.categorical[0]
    assert variables["--nxv-cat-count"] == str(len(theme.palette.categorical))


def test_css_overrides_win() -> None:
    theme = load("nexcraftviz-light").model_copy(
        update={"css_overrides": {"--nxv-accent": "#ff00ff"}}
    )
    assert to_variables(theme)["--nxv-accent"] == "#ff00ff"


def test_stylesheet_styles_the_non_vega_families() -> None:
    """The whole point: KPI tiles and rich tables get themed, not just charts."""
    css = to_css(load("nexcraftviz-light"))
    for selector in (
        ".nxv-card", ".nxv-kpi__value", ".nxv-kpi--target-vs-actual",
        ".nxv-kpi--percent-change", ".nxv-table", ".nxv-grid",
    ):
        assert selector in css, f"missing {selector}"


@pytest.mark.parametrize("renderer", CELL_RENDERERS)
def test_every_cell_renderer_has_a_class(renderer: str) -> None:
    css = to_css(load("nexcraftviz-light"))
    assert f".nxv-cell--{renderer.replace('_', '-')}" in css


def test_bundle_supports_both_an_explicit_toggle_and_the_os_preference() -> None:
    css = to_bundle(load("nexcraftviz-light"), load("nexcraftviz-dark"))
    assert ":root {" in css
    assert '[data-nxv-theme="dark"]' in css
    assert "@media (prefers-color-scheme: dark)" in css
    # A stamped light theme must beat the OS preference, not lose to it.
    assert ':root:not([data-nxv-theme="light"])' in css


@pytest.mark.parametrize(
    "rule",
    [
        # Both of these were live bugs found by rendering the playground.
        # A span is inline by default, and an inline box ignores width and
        # height outright — every progress bar and KPI target bar rendered
        # 0x0 and looked empty at any value.
        ".nxv-fill",
        ".nxv-kpi__fill",
    ],
)
def test_progress_fills_are_block_level(rule: str) -> None:
    css = to_css(load("nexcraftviz-light"))
    block = _rule_body(css, rule)
    assert "display: block" in block, f"{rule} must be block-level or it renders 0x0"


def test_card_body_forces_block_layout_for_vega_embed() -> None:
    """vega-embed stamps `display: inline-block` on its mount point, which gives
    a `"width": "container"` spec nothing to measure — the chart renders 0px wide."""
    css = to_css(load("nexcraftviz-light"))
    body = _rule_body(css, ".nxv-card__body")
    assert "display: block" in body and "width: 100%" in body
    assert ".nxv-card__body .vega-embed" in css


def _rule_body(css: str, selector: str) -> str:
    """The declaration block of the first rule whose selector ends in ``selector``."""
    for chunk in css.split("}"):
        head, _, body = chunk.partition("{")
        if head.strip().endswith(selector):
            return body
    raise AssertionError(f"no rule for {selector}")


def test_stylesheet_braces_balance() -> None:
    """Cheap proof the f-string templating did not mangle the CSS."""
    css = to_bundle(load("nexcraftviz-light"), load("nexcraftviz-dark"))
    assert css.count("{") == css.count("}")
    assert "{{" not in css and "}}" not in css


# ---------------------------------------------------------------------------
# contrast
# ---------------------------------------------------------------------------

def test_luminance_endpoints() -> None:
    assert relative_luminance("#000000") == pytest.approx(0.0)
    assert relative_luminance("#ffffff") == pytest.approx(1.0)


def test_contrast_ratio_endpoints() -> None:
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    assert contrast_ratio("#777777", "#777777") == pytest.approx(1.0)


def test_shorthand_hex_is_understood() -> None:
    assert contrast_ratio("#000", "#fff") == pytest.approx(21.0, abs=0.01)


def test_unparseable_colours_are_reported_not_failed() -> None:
    assert contrast_ratio("transparent", "#ffffff") is None
    theme = load("nexcraftviz-light").model_copy(
        update={"semantic": load("nexcraftviz-light").semantic.model_copy(
            update={"accent": "rebeccapurple"}
        )}
    )
    report = audit(theme)
    assert any("accent" in entry for entry in report.unchecked)


#: Contrast failures in the derived presets. These are defects in the upstream
#: vega-themes palettes, not ours, and we keep them deliberately: the whole
#: value of deriving `powerbi` is that it renders identically to what a host
#: ships today. Recording them here makes the deviation visible and stops it
#: growing silently. A brand theme built on our own tokens has no such excuse.
KNOWN_THEME_CONTRAST_DEVIATIONS: dict[str, set[str]] = {
    # Microsoft's yellow, 2.02:1 on white — below WCAG 1.4.11's 3:1 for
    # graphical objects. It is the 7th categorical entry, so it only appears on
    # charts with 7+ series.
    "powerbi": {"palette.categorical[6] (#D9B300) on #FFFFFF"},
}


@pytest.mark.parametrize("name", ("nexcraftviz-light", "nexcraftviz-dark"))
def test_our_own_themes_pass_wcag_aa_outright(name: str) -> None:
    report = audit(load(name))
    assert report.ok, report.summary() + "\n" + "\n".join(str(i) for i in report.issues)


@pytest.mark.parametrize("name", DERIVED)
def test_derived_themes_deviate_only_where_recorded(name: str) -> None:
    report = audit(load(name))
    actual = {issue.message for issue in report.issues}
    expected = KNOWN_THEME_CONTRAST_DEVIATIONS.get(name, set())

    assert actual - expected == set(), f"new contrast failure in {name}: {actual - expected}"
    assert expected - actual == set(), f"{name} no longer fails as recorded: {expected - actual}"


def test_the_audit_actually_catches_a_bad_palette() -> None:
    """Guards against the audit passing because it checks nothing."""
    base = load("nexcraftviz-light")
    unreadable = base.model_copy(
        update={"text": base.text.model_copy(update={"primary": "#f5f5f5"})}
    )
    report = audit(unreadable)
    assert not report.ok
    assert any(i.kind == "text" for i in report.issues)


def test_similar_adjacent_categorical_colours_are_flagged() -> None:
    base = load("nexcraftviz-light")
    duplicated = base.model_copy(
        update={"palette": base.palette.model_copy(
            update={"categorical": ["#0C8BA6", "#0C8CA7", "#B8541F"]}
        )}
    )
    report = audit(duplicated)
    assert any(i.kind == "similarity" for i in report.issues)


def test_distant_pairs_are_not_flagged_only_neighbours() -> None:
    """A long palette always has some distant near-match; that is not a bug."""
    base = load("nexcraftviz-light")
    spaced = base.model_copy(
        update={"palette": base.palette.model_copy(
            update={"categorical": ["#0C8BA6", "#B8541F", "#0C8CA7"]}
        )}
    )
    assert not [i for i in audit(spaced).issues if i.kind == "similarity"]


def test_delta_e_separates_distinct_colours() -> None:
    assert delta_e("#000000", "#ffffff") > MIN_DELTA_E
    assert delta_e("#0C8BA6", "#0C8CA7") < MIN_DELTA_E
