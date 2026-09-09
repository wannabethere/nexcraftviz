"""``viz.compose`` — finished charts in, a widget design out.

The distinction that makes this work: this skill sees **visualizations, not
data**. By the time it runs the charts exist and are correct — each has been
planned, generated and gated on its own — so the only open question is layout,
and layout is answered from what the charts *are*, not from the rows behind
them.

Handing the composer rows as well would invite it to second-guess charts that
have already passed their gates, and it has no way to do that better than the
stage that built them.

The design it returns is a plan for a :class:`~nexcraftviz.compose.widget.Widget`,
not the widget itself: ids reference visualizations the caller already holds, so
a design can be reviewed, edited or reapplied without the charts being rebuilt.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from nexcraftviz.agents.artifacts import STRICT, Status, Telemetry
from nexcraftviz.skills.base import Skill, SkillResult, SkillSpec
from nexcraftviz.spec.model import Spec

Span = Literal["third", "half", "two-thirds", "full", "auto"]


class Visualization(BaseModel):
    """One finished chart, described by what it is rather than what it holds."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    id: str
    spec: Any = Field(default=None, description="The Spec. Not sent to the model.")
    chart_type: str = ""
    question: str = Field(default="", description="What this chart answers.")

    def as_spec(self) -> Spec | None:
        if self.spec is None:
            return None
        return self.spec if isinstance(self.spec, Spec) else Spec(self.spec)

    def describe(self) -> dict[str, Any]:
        """What the composer is shown: shape, not values.

        The spec itself is deliberately withheld — a composer given a full
        Vega-Lite document starts editing encodings, which is not its job and
        would undo work that has already been gated.
        """
        spec = self.as_spec()
        return {
            "id": self.id,
            "chart_type": self.chart_type or (spec.mark_summary if spec else ""),
            "family": spec.family if spec else "unknown",
            "fields": sorted({f for _, _, f in spec.field_refs()}) if spec else [],
            "question": self.question,
        }


class TileDesign(BaseModel):
    model_config = STRICT

    id: str = Field(description="The visualization's id, unchanged.")
    title: str = ""
    span: Span = "auto"
    note: str = ""


class GroupDesign(BaseModel):
    model_config = STRICT

    title: str = ""
    tiles: list[str] = Field(default_factory=list, description="Tile ids, in order.")


class WidgetDesign(BaseModel):
    """A layout for charts that already exist."""

    model_config = STRICT

    status: Status = "ok"
    reason_if_not_ok: str = ""
    title: str = ""
    description: str = ""
    layout: str = "grid"
    tiles: list[TileDesign] = Field(default_factory=list)
    groups: list[GroupDesign] = Field(default_factory=list)
    concerns: list[str] = Field(
        default_factory=list,
        description="Anything wrong with a chart that layout cannot fix.",
    )
    telemetry: Telemetry = Field(default_factory=Telemetry)

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.tiles)


class ComposeIn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    ask: str = Field(description="What the widget is for, in the user's words.")
    visualizations: list[Visualization] = Field(default_factory=list)
    existing: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Tiles already on the widget, when adding rather than building.",
    )
    language: str = "English"


class ComposeSkill(Skill[ComposeIn, WidgetDesign]):
    """Charts → a widget design."""

    spec = SkillSpec(
        name="viz.compose",
        summary=(
            "Arrange finished charts into one widget: reading order, spans, "
            "panels and titles."
        ),
        prompt="viz.compose",
    )
    Input = ComposeIn
    Output = WidgetDesign

    def user_payload(self, inputs: ComposeIn) -> str:
        return json.dumps(
            {
                "ask": inputs.ask,
                "language": inputs.language,
                "visualizations": [v.describe() for v in inputs.visualizations],
                "existing": inputs.existing,
            },
            indent=2,
            default=str,
        )

    def apply(self, inputs: ComposeIn, output: WidgetDesign) -> SkillResult[WidgetDesign]:
        result: SkillResult[WidgetDesign] = SkillResult(skill=self.spec.name, output=output)
        design = _repair(output, inputs)
        result.value = design
        result.changes = [
            f"{design.layout} of {len(design.tiles)} tile(s)"
            + (f" in {len(design.groups)} panel(s)" if design.groups else "")
        ]
        result.warnings.extend(design.concerns)
        if not design.ok:
            result.warnings.append(design.reason_if_not_ok or "no tiles were designed")
        return result


def _repair(design: WidgetDesign, inputs: ComposeIn) -> WidgetDesign:
    """Make the design usable, or say why it is not.

    Every fix here is for a design that is well-formed and would still produce a
    broken widget, so nothing downstream would catch it.
    """
    known = {v.id: v for v in inputs.visualizations}
    seen: set[str] = set()
    tiles: list[TileDesign] = []

    for tile in design.tiles:
        # A tile naming a chart that does not exist renders an empty card.
        if tile.id not in known or tile.id in seen:
            design.concerns.append(
                f"dropped tile {tile.id!r}: "
                + ("no such visualization" if tile.id not in known else "listed twice")
            )
            continue
        seen.add(tile.id)
        tiles.append(tile)

    # A chart the composer forgot is a chart the user asked for and does not
    # get. Appending it beats dropping it silently.
    for identifier, visualization in known.items():
        if identifier not in seen:
            tiles.append(TileDesign(
                id=identifier,
                title=visualization.question or identifier,
                span="auto",
            ))
            design.concerns.append(f"appended {identifier!r}: the design omitted it")

    design.tiles = tiles

    # A group referencing tiles that did not survive would render an empty panel.
    groups: list[GroupDesign] = []
    for group in design.groups:
        members = [t for t in group.tiles if t in seen]
        if len(members) >= 2:
            group.tiles = members
            groups.append(group)
        elif group.tiles:
            design.concerns.append(
                f"dropped panel {group.title!r}: fewer than two tiles remained"
            )
    design.groups = groups

    if not design.tiles:
        design.status = "failed"
        design.reason_if_not_ok = "no visualizations to lay out"
    return design


def build_widget(design: WidgetDesign, visualizations: list[Visualization]) -> Any:
    """Turn a design into a real :class:`Widget`.

    Deterministic: the model chose the arrangement, this assembles it. Grouped
    tiles are nested and the rest stay at the top level, in the design's order.
    """
    from nexcraftviz.compose.widget import Widget, group, tile, widget

    by_id = {v.id: v for v in visualizations}
    designed = {t.id: t for t in design.tiles}

    def make(identifier: str) -> Any:
        entry = designed[identifier]
        visualization = by_id[identifier]
        return tile(
            visualization.as_spec(),
            id=identifier,
            title=entry.title or visualization.question,
            span=entry.span,
            note=entry.note,
        )

    grouped: dict[str, str] = {}
    for panel in design.groups:
        for member in panel.tiles:
            grouped[member] = panel.title

    nodes: list[Any] = []
    emitted: set[str] = set()
    for entry in design.tiles:
        if entry.id in emitted:
            continue
        panel_title = grouped.get(entry.id)
        if panel_title is None:
            nodes.append(make(entry.id))
            emitted.add(entry.id)
            continue
        # Emit the whole panel at the position of its first member, so the
        # design's reading order survives grouping.
        panel = next(g for g in design.groups if g.title == panel_title)
        members = [m for m in panel.tiles if m not in emitted]
        nodes.append(group(*(make(m) for m in members), title=panel.title))
        emitted.update(members)

    result: Widget = widget(
        *nodes,
        title=design.title,
        description=design.description,
        layout=design.layout or None,
    )
    return result
