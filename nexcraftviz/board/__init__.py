"""Boards — the ``board`` family: records as cards in lanes, one lane per status.

Not a chart. A Kanban board is drawn by the host's own components, like the
rich table and the KPI card; this package decides the layout and groups the
rows, so every host draws the same board from the same answer.
"""

from nexcraftviz.board.build import build_board, build_board_spec
from nexcraftviz.board.schema import BoardLayout, BoardSpec, Card, Lane

__all__ = ["BoardLayout", "BoardSpec", "Card", "Lane", "build_board", "build_board_spec"]
