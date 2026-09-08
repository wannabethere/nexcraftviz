"""Rich tables — the ``table_with_cells`` family.

The "render the table" half of table-first output: build a typed table straight
from result rows with no model involved, then let a chart follow.
"""
from nexcraftviz.table.build import build_table, build_table_spec
from nexcraftviz.table.sample import rows_from_data_shape, sample_rows, with_sample_rows
from nexcraftviz.table.schema import RENDERERS, TONES, Column, KpiCard, TableSpec

__all__ = [
    "RENDERERS",
    "TONES",
    "Column",
    "KpiCard",
    "TableSpec",
    "build_table",
    "build_table_spec",
    "rows_from_data_shape",
    "sample_rows",
    "with_sample_rows",
]
