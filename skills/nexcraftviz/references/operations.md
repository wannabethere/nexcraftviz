# Operations reference

GENERATED from the code by `make skill-reference`. Do not edit by hand.

## Chart operations

Applied with `viz_apply_ops`. `view` selects one leaf view of a layered chart; omit it for the default target.

### `add_annotation`

Drop a text callout at a data position.

- `text` (required)
- `x` (optional) Datum value on x, or None to omit.
- `y` (optional) Datum value on y, or None to omit.
- `color` (optional)

### `add_reference_line`

Add a target/threshold rule — the single most-requested chart annotation.

- `value` (optional) Constant position for the rule.
- `field` (optional) Field whose aggregate positions the rule.
- `aggregate` (optional) Used with `field`, e.g. mean/median.
- `axis` (optional)
- `label` (optional) Optional text label drawn at the rule.
- `color` (optional)
- `stroke_dash` (optional)

### `add_series`

Add a second measure as its own layer — the dual-measure shape.

- `field` (required) Measure to add.
- `mark` (optional)
- `axis` (optional)
- `color` (optional)
- `independent_scale` (optional)

### `aggregate`

Set an aggregate on a channel (sum, mean, count, …).

- `view` (optional) Index into the spec's leaf views. Omit for the default target.
- `channel` (optional)
- `aggregate` (required) sum | mean | median | min | max | count | distinct
- `field` (optional) Field to aggregate. Omit for count.

### `apply_config`

Merge a ``config`` block — how a theme reaches the spec.

- `config` (optional)
- `replace` (optional) Replace the whole config instead of deep-merging.

### `bin_field`

Bin a quantitative field — turns a scatter into a histogram.

- `view` (optional) Index into the spec's leaf views. Omit for the default target.
- `channel` (optional)
- `maxbins` (optional)
- `step` (optional)
- `enabled` (optional)

### `drop_series`

Remove a layer by index, collapsing back to a unit spec when one remains.

- `index` (required) Layer index to remove.

### `facet_by`

Small multiples — one panel per value of ``field``.

- `view` (optional) Index into the spec's leaf views. Omit for the default target.
- `field` (required) Column to facet by. Empty string removes faceting.
- `mode` (optional)
- `type` (optional)
- `columns` (optional) Wrap after this many panels.

### `group_by`

Side-by-side grouping via ``xOffset`` — the grouped-bar shape.

- `view` (optional) Index into the spec's leaf views. Omit for the default target.
- `field` (required) Column to group by. Empty string removes grouping.
- `type` (optional)

### `limit_top_n`

Keep only the top (or bottom) N rows.

- `view` (optional) Index into the spec's leaf views. Omit for the default target.
- `n` (required) How many rows to keep.
- `by` (required) Measure field to rank on.
- `order` (optional)
- `groupby` (optional) Rank within these groups.

### `resolve_scale`

Share or separate scales across layers/facets — the dual-axis switch.

- `channel` (optional)
- `resolution` (optional)

### `set_axis`

Adjust axis presentation. ``None`` leaves a property untouched;

- `view` (optional) Index into the spec's leaf views. Omit for the default target.
- `channel` (optional)
- `title` (optional)
- `format` (optional) d3-format string, e.g. '.1%'.
- `grid` (optional)
- `label_angle` (optional)
- `tick_count` (optional)

### `set_color_field`

Colour marks by a column — the usual way to add a series breakdown.

- `view` (optional) Index into the spec's leaf views. Omit for the default target.
- `field` (required) Column to colour by. Empty string removes colour encoding.
- `type` (optional) quantitative | temporal | nominal | ordinal.
- `legend_title` (optional) Optional legend title.

### `set_mark`

Change the mark type, preserving mark properties where they still apply.

- `view` (optional) Index into the spec's leaf views. Omit for the default target.
- `mark` (required) Vega-Lite mark type, e.g. bar, line, area, point, arc.
- `properties` (optional) Mark properties to merge, e.g. {'point': true, 'cornerRadius': 3}.

### `set_palette`

Recolour the chart.

- `view` (optional) Index into the spec's leaf views. Omit for the default target.
- `scheme` (optional) Named Vega scheme, e.g. 'tealblues'.
- `range` (optional) Explicit hex colours.
- `color` (optional) Single flat colour for the mark.

### `set_scale`

- `view` (optional) Index into the spec's leaf views. Omit for the default target.
- `channel` (optional)
- `type` (optional) linear | log | sqrt | pow | time | band
- `zero` (optional)
- `nice` (optional)
- `domain` (optional)

### `set_size`

Set width/height. Always root-level: sizing a single layer is a bug.

- `width` (optional) Pixels, or 'container'.
- `height` (optional) Pixels, or 'container'.

### `set_title`

- `text` (required) Chart title. Empty string removes the title.
- `subtitle` (optional) Optional subtitle line.

### `set_tooltip`

Set the hover tooltip. An empty ``fields`` list turns tooltips off.

- `view` (optional) Index into the spec's leaf views. Omit for the default target.
- `fields` (optional)
- `types` (optional) Optional per-field measurement types.

### `sort_by`

Sort a categorical axis.

- `view` (optional) Index into the spec's leaf views. Omit for the default target.
- `channel` (optional) Channel whose axis is being sorted.
- `by` (optional) Field to sort by. Empty = the channel's own field.
- `order` (optional)

### `stack_mode`

Set or clear stacking. ``none`` is how you turn a stacked bar into an

- `view` (optional) Index into the spec's leaf views. Omit for the default target.
- `mode` (optional)
- `channel` (optional) The quantitative channel being stacked.
## Layout operations

Applied with `viz_apply_layout`. Spans: quarter (3), third (4), half (6), two-thirds (8), three-quarters (9), full (12) of a 12-column grid.

### `group_tiles`

Wrap tiles in a titled panel — "put those two in a panel together".

- `tiles` (required)
- `title` (optional)
- `subtitle` (optional)
- `span` (optional)
- `id` (optional)

### `move_tile`

Reorder a tile, optionally moving it into or out of a group.

- `tile` (required)
- `before` (optional)
- `after` (optional)
- `into` (optional) Group id to move the tile into.
- `to_root` (optional) Move the tile out of its group.

### `remove_tile`

- `tile` (required)

### `set_layout`

Switch the whole arrangement.

- `layout` (required)

### `set_span`

Resize a tile — "make the funnel wider".

- `tile` (required) Tile id.
- `span` (required) quarter | third | half | two-thirds | full | auto

### `set_tile_title`

- `tile` (required)
- `title` (optional)
- `subtitle` (optional)

### `set_widget_title`

- `title` (optional)
- `description` (optional)

### `ungroup_tiles`

Dissolve a panel, leaving its tiles in place.

- `group` (required)
## Themes

`carbon-g90`, `nexcraftviz-dark`, `nexcraftviz-light`, `powerbi`
