"""The ``board`` contract.

A board payload says how cards are laid out (``board``) and carries the lanes
already grouped (``lanes``). Grouping here rather than in each host is the
point: a pipeline read in two places that disagreed on lane order or counts
would read as two different pipelines.

    {
      "board": {"group_by": "status", "title_field": "project", ...},
      "lanes": [{"key": "Active", "count": 11, "tone": "pass",
                 "cards": [{"title": "...", "description": "...",
                            "tags": ["EMEA", "SMB"], "badge": "2 assigned"}]}],
      "title": "Projects by status"
    }

``lanes`` rather than ``columns``: a top-level ``columns`` list is how a rich
table announces itself, and a board must never be mistaken for one.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nexcraftviz.spec.model import Spec
from nexcraftviz.table.schema import Tone

#: A lane shows this many cards; its header still carries the true count.
DEFAULT_MAX_CARDS = 50


class BoardLayout(BaseModel):
    """Which fields make a lane and a card. Field names are row keys."""

    model_config = ConfigDict(extra="forbid")

    group_by: str = Field(description="The field whose values are the lanes.")
    title_field: str = Field(default="", description="The card's heading.")
    subtitle_field: str = Field(default="", description="A short line under the heading.")
    description_field: str = Field(default="", description="Longer text, clamped by the host.")
    tag_fields: list[str] = Field(default_factory=list, description="Shown as chips.")
    badge_field: str = Field(default="", description="One figure in the card's corner.")
    badge_label: str = Field(default="", description="Words after the badge figure, e.g. 'assigned'.")
    order: list[str] = Field(default_factory=list, description="Lane order; unlisted lanes follow.")
    tones: dict[str, Tone] = Field(default_factory=dict, description="Lane value → tone.")
    max_cards: int = Field(default=DEFAULT_MAX_CARDS, ge=1, le=500)


class Card(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""
    subtitle: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    badge: str = ""
    #: The row the card came from, for a host that links a card to its record.
    row: dict[str, Any] = Field(default_factory=dict)


class Lane(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    count: int = 0
    tone: Tone | None = None
    cards: list[Card] = Field(default_factory=list)


class BoardSpec(BaseModel):
    """A ``board`` payload."""

    model_config = ConfigDict(extra="forbid")

    board: BoardLayout
    lanes: list[Lane] = Field(default_factory=list)
    title: str = ""

    def to_spec(self) -> Spec:
        return Spec(self.model_dump(mode="json", exclude_none=True))

    @classmethod
    def from_spec(cls, spec: Spec | dict[str, Any]) -> BoardSpec:
        raw = spec.raw if isinstance(spec, Spec) else spec
        return cls.model_validate(raw)

    @classmethod
    def from_board_schema(cls, board_schema: str | dict[str, Any]) -> BoardLayout:
        """Parse a corpus pair's ``board_schema`` (the layout only), which may be JSON text."""
        parsed = json.loads(board_schema) if isinstance(board_schema, str) else board_schema
        if not isinstance(parsed, dict):
            raise ValueError("board_schema must be an object")
        return BoardLayout.model_validate(parsed)
