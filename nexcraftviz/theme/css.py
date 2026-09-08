"""Tokens → CSS custom properties and component classes.

This is the half of theming that Vega cannot do. ``kpi_metadata`` tiles and
``table_with_cells`` rich tables are drawn by hand-written frontend components;
today they carry hard-coded colours that drift from whatever theme the chart
beside them is using. Emitting a CSS bundle from the same tokens that produce
the Vega config keeps the two in step, and gives those components stable class
names to target.

The class vocabulary maps onto what the frontend already special-cases:

* ``.nxv-card``            — the chart/KPI/table card chrome
* ``.nxv-kpi``             — a KPI tile, with modifiers per subtype
* ``.nxv-table``           — a rich table
* ``.nxv-cell--*``         — the per-column renderers the corpus emits
                             (avatar_name, progress_bar, sparkline, pill,
                             badge, heatmap_cell, trend_arrow)
* ``.nxv-grid``            — the dashboard grid
"""
from __future__ import annotations

from nexcraftviz.theme.tokens import ThemeTokens

#: Cell renderers the `table_with_cells` chart type emits. Each gets a class so
#: a frontend can style them without inventing its own vocabulary.
CELL_RENDERERS = (
    "avatar_name",
    "progress_bar",
    "sparkline",
    "pill",
    "badge",
    "heatmap_cell",
    "trend_arrow",
)


def to_variables(theme: ThemeTokens) -> dict[str, str]:
    """The custom properties, as a plain mapping.

    Exposed separately from :func:`to_css` so a caller can inject them into an
    element's ``style`` attribute for per-tile theming rather than shipping a
    whole stylesheet.
    """
    palette, semantic = theme.palette, theme.semantic
    surfaces, text = theme.surfaces, theme.text
    typo, geo = theme.typography, theme.geometry

    variables: dict[str, str] = {
        "--nxv-mode": theme.mode,
        # surfaces
        "--nxv-background": surfaces.background,
        "--nxv-surface": surfaces.surface,
        "--nxv-surface-alt": surfaces.surface_alt,
        "--nxv-border": surfaces.border,
        "--nxv-border-strong": surfaces.border_strong,
        "--nxv-grid": surfaces.grid,
        # text
        "--nxv-text": text.primary,
        "--nxv-text-secondary": text.secondary,
        "--nxv-text-muted": text.muted,
        "--nxv-text-inverse": text.inverse,
        # semantic
        "--nxv-positive": semantic.positive,
        "--nxv-negative": semantic.negative,
        "--nxv-warning": semantic.warning,
        "--nxv-neutral": semantic.neutral,
        "--nxv-target": semantic.target,
        "--nxv-accent": semantic.accent,
        # typography
        "--nxv-font": typo.font_family,
        "--nxv-font-mono": typo.mono_family,
        "--nxv-size-xs": f"{typo.size_xs}px",
        "--nxv-size-sm": f"{typo.size_sm}px",
        "--nxv-size-base": f"{typo.size_base}px",
        "--nxv-size-lg": f"{typo.size_lg}px",
        "--nxv-size-xl": f"{typo.size_xl}px",
        "--nxv-size-display": f"{typo.size_display}px",
        "--nxv-weight-normal": str(typo.weight_normal),
        "--nxv-weight-medium": str(typo.weight_medium),
        "--nxv-weight-bold": str(typo.weight_bold),
        # geometry
        "--nxv-radius-sm": f"{geo.radius_sm}px",
        "--nxv-radius-md": f"{geo.radius_md}px",
        "--nxv-radius-lg": f"{geo.radius_lg}px",
        "--nxv-space": f"{geo.spacing}px",
        "--nxv-border-width": f"{geo.border_width}px",
    }

    for index, colour in enumerate(palette.categorical):
        variables[f"--nxv-cat-{index + 1}"] = colour
    variables["--nxv-cat-count"] = str(len(palette.categorical))

    variables.update(theme.css_overrides)
    return variables


def to_css(theme: ThemeTokens, *, selector: str = ":root") -> str:
    """A complete stylesheet: variables plus the component classes."""
    return f"{_variables_block(theme, selector)}\n\n{_components_block()}"


def to_bundle(light: ThemeTokens, dark: ThemeTokens) -> str:
    """A stylesheet carrying both modes.

    Light is the default; dark applies under an explicit ``[data-nxv-theme]``
    attribute *and* under ``prefers-color-scheme`` when nothing is stamped, so
    the page follows the OS unless the app has an opinion.
    """
    parts = [
        f"/* nexcraftviz — {light.name} / {dark.name} */",
        _variables_block(light, ":root"),
        _variables_block(dark, '[data-nxv-theme="dark"]'),
        "@media (prefers-color-scheme: dark) {",
        _indent(_variables_block(dark, ':root:not([data-nxv-theme="light"])')),
        "}",
        _components_block(),
    ]
    return "\n\n".join(parts) + "\n"


def _variables_block(theme: ThemeTokens, selector: str) -> str:
    lines = [f"  {name}: {value};" for name, value in to_variables(theme).items()]
    return f"{selector} {{\n" + "\n".join(lines) + "\n}"


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" if line else line for line in text.splitlines())


def _components_block() -> str:
    cells = "\n\n".join(_cell_rule(name) for name in CELL_RENDERERS)
    return f"""/* ---- card chrome ---- */
.nxv-card {{
  background: var(--nxv-surface);
  border: var(--nxv-border-width) solid var(--nxv-border);
  border-radius: var(--nxv-radius-md);
  color: var(--nxv-text);
  font-family: var(--nxv-font);
  font-size: var(--nxv-size-base);
  padding: calc(var(--nxv-space) * 2);
}}

.nxv-card__header {{
  align-items: baseline;
  display: flex;
  gap: var(--nxv-space);
  justify-content: space-between;
  margin-bottom: calc(var(--nxv-space) * 1.5);
}}

.nxv-card__title {{
  font-size: var(--nxv-size-lg);
  font-weight: var(--nxv-weight-bold);
  margin: 0;
}}

.nxv-card__subtitle {{
  color: var(--nxv-text-secondary);
  font-size: var(--nxv-size-sm);
}}

/* Wide content scrolls inside the card; the page must never scroll sideways.
   The width/display pair is load-bearing: vega-embed stamps `display:
   inline-block` on whatever element it mounts into, and an inline-block has no
   width to hand a `"width": "container"` spec — the chart silently renders 0px
   wide. Forcing block layout is what makes container-sized charts work. */
.nxv-card__body {{
  display: block;
  overflow-x: auto;
  width: 100%;
}}

.nxv-card__body.vega-embed {{ display: block; width: 100%; }}
.nxv-card__body .vega-embed {{ display: block; width: 100%; }}

/* ---- KPI tiles ---- */
.nxv-kpi {{
  display: flex;
  flex-direction: column;
  gap: calc(var(--nxv-space) * 0.5);
}}

.nxv-kpi__value {{
  color: var(--nxv-text);
  font-size: var(--nxv-size-display);
  font-variant-numeric: tabular-nums;
  font-weight: var(--nxv-weight-bold);
  line-height: 1.1;
}}

.nxv-kpi__label {{
  color: var(--nxv-text-secondary);
  font-size: var(--nxv-size-sm);
}}

.nxv-kpi__unit {{
  color: var(--nxv-text-muted);
  font-size: var(--nxv-size-lg);
  font-weight: var(--nxv-weight-medium);
}}

.nxv-kpi__delta {{
  align-items: center;
  display: inline-flex;
  font-size: var(--nxv-size-sm);
  font-weight: var(--nxv-weight-medium);
  gap: 4px;
}}

.nxv-kpi__delta--up {{ color: var(--nxv-positive); }}
.nxv-kpi__delta--down {{ color: var(--nxv-negative); }}
.nxv-kpi__delta--flat {{ color: var(--nxv-text-muted); }}

/* target_vs_actual — a big number over a progress track */
.nxv-kpi--target-vs-actual .nxv-kpi__track {{
  background: var(--nxv-surface-alt);
  border-radius: var(--nxv-radius-sm);
  display: block;
  height: 6px;
  overflow: hidden;
  width: 100%;
}}

/* `display: block` is required, not decorative: these are spans, and an inline
   element ignores width and height outright — the fill renders 0x0 and the bar
   silently appears empty. */
.nxv-kpi--target-vs-actual .nxv-kpi__fill {{
  background: var(--nxv-accent);
  display: block;
  height: 100%;
}}

.nxv-kpi--target-vs-actual .nxv-kpi__fill--over {{ background: var(--nxv-positive); }}
.nxv-kpi--target-vs-actual .nxv-kpi__fill--under {{ background: var(--nxv-warning); }}

.nxv-kpi--target-vs-actual .nxv-kpi__target {{
  color: var(--nxv-target);
  font-size: var(--nxv-size-xs);
}}

/* percent_change — value with a direction arrow */
.nxv-kpi--percent-change .nxv-kpi__value {{ font-size: var(--nxv-size-xl); }}

/* ---- rich tables ---- */
.nxv-table {{
  border-collapse: collapse;
  font-size: var(--nxv-size-base);
  width: 100%;
}}

.nxv-table th {{
  background: var(--nxv-surface-alt);
  border-bottom: var(--nxv-border-width) solid var(--nxv-border-strong);
  color: var(--nxv-text-secondary);
  font-size: var(--nxv-size-sm);
  font-weight: var(--nxv-weight-medium);
  padding: var(--nxv-space);
  position: sticky;
  text-align: left;
  top: 0;
}}

.nxv-table td {{
  border-bottom: var(--nxv-border-width) solid var(--nxv-border);
  padding: var(--nxv-space);
  vertical-align: middle;
}}

.nxv-table tbody tr:hover {{ background: var(--nxv-surface-alt); }}
.nxv-table td.nxv-num {{ font-variant-numeric: tabular-nums; text-align: right; }}

/* ---- per-column cell renderers ---- */
{cells}

/* ---- dashboard grid ---- */
.nxv-grid {{
  display: grid;
  gap: calc(var(--nxv-space) * 2);
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
}}

.nxv-grid--kpi-row {{ grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }}
.nxv-grid__item--wide {{ grid-column: span 2; }}

@media (max-width: 640px) {{
  .nxv-grid, .nxv-grid--kpi-row {{ grid-template-columns: 1fr; }}
  .nxv-grid__item--wide {{ grid-column: span 1; }}
}}"""


def _cell_rule(renderer: str) -> str:
    """One block per cell renderer.

    Written out rather than generated from a table because each renderer has a
    genuinely different shape, and a reader looking for "why is the progress bar
    that colour" should find it here.
    """
    base = f".nxv-cell--{renderer.replace('_', '-')}"
    rules = {
        "avatar_name": f"""{base} {{
  align-items: center;
  display: flex;
  gap: var(--nxv-space);
}}

{base} .nxv-avatar {{
  align-items: center;
  background: var(--nxv-accent);
  border-radius: 50%;
  color: var(--nxv-text-inverse);
  display: flex;
  flex: 0 0 auto;
  font-size: var(--nxv-size-xs);
  font-weight: var(--nxv-weight-bold);
  height: 28px;
  justify-content: center;
  width: 28px;
}}""",
        "progress_bar": f"""{base} {{
  align-items: center;
  display: flex;
  gap: var(--nxv-space);
  min-width: 120px;
}}

{base} .nxv-track {{
  background: var(--nxv-surface-alt);
  border-radius: var(--nxv-radius-sm);
  display: block;
  flex: 1;
  height: 6px;
  overflow: hidden;
}}

/* Spans are inline by default and inline boxes ignore width/height, which
   renders the fill 0x0 — the bar looks empty at every value. */
{base} .nxv-fill {{ background: var(--nxv-accent); display: block; height: 100%; }}
{base} .nxv-fill--positive {{ background: var(--nxv-positive); }}
{base} .nxv-fill--warning {{ background: var(--nxv-warning); }}
{base} .nxv-fill--negative {{ background: var(--nxv-negative); }}

{base} .nxv-pct {{
  color: var(--nxv-text-secondary);
  font-size: var(--nxv-size-sm);
  font-variant-numeric: tabular-nums;
  min-width: 3ch;
  text-align: right;
}}""",
        "sparkline": f"""{base} {{ display: block; line-height: 0; }}
{base} svg {{ display: block; height: 24px; width: 80px; }}
{base} .nxv-spark-line {{ fill: none; stroke: var(--nxv-accent); stroke-width: 1.5; }}
{base} .nxv-spark-area {{ fill: var(--nxv-accent); opacity: 0.15; }}""",
        "pill": f"""{base} {{
  background: var(--nxv-surface-alt);
  border-radius: 999px;
  color: var(--nxv-text-secondary);
  display: inline-block;
  font-size: var(--nxv-size-xs);
  font-weight: var(--nxv-weight-medium);
  padding: 2px 10px;
  white-space: nowrap;
}}

{base}.is-positive {{
  background: color-mix(in srgb, var(--nxv-positive) 15%, transparent);
  color: var(--nxv-positive);
}}

{base}.is-negative {{
  background: color-mix(in srgb, var(--nxv-negative) 15%, transparent);
  color: var(--nxv-negative);
}}

{base}.is-warning {{
  background: color-mix(in srgb, var(--nxv-warning) 18%, transparent);
  color: var(--nxv-warning);
}}""",
        "badge": f"""{base} {{
  border: var(--nxv-border-width) solid var(--nxv-border-strong);
  border-radius: var(--nxv-radius-sm);
  color: var(--nxv-text-secondary);
  display: inline-block;
  font-size: var(--nxv-size-xs);
  letter-spacing: 0.02em;
  padding: 1px 6px;
  text-transform: uppercase;
}}""",
        "heatmap_cell": f"""{base} {{
  border-radius: var(--nxv-radius-sm);
  display: block;
  font-variant-numeric: tabular-nums;
  padding: 4px 8px;
  text-align: right;
}}

/* Intensity is set inline per cell as --nxv-heat (0-1). */
{base}[style*="--nxv-heat"] {{
  background: color-mix(in srgb, var(--nxv-accent) calc(var(--nxv-heat) * 100%), transparent);
}}""",
        "trend_arrow": f"""{base} {{
  align-items: center;
  display: inline-flex;
  font-variant-numeric: tabular-nums;
  gap: 4px;
}}

{base}.is-up {{ color: var(--nxv-positive); }}
{base}.is-down {{ color: var(--nxv-negative); }}
{base}.is-flat {{ color: var(--nxv-text-muted); }}""",
    }
    return rules[renderer]
