"""``viz.edit`` and ``viz.place`` — the two skills that make a conversation.

Both follow the same shape: the model reads the current state and emits
operations; code applies them. That is what keeps a chat about a chart cheap
(a couple of hundred output tokens per turn), reproducible, and undoable.

They are separate skills rather than one because they act on different things —
a chart's encoding versus a widget's layout — and a model given both
vocabularies at once reliably reaches for the wrong one.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nexcraftviz.compose.ops import AnyWidgetOp, apply_widget_ops
from nexcraftviz.compose.widget import Widget
from nexcraftviz.data.profile import DataProfile, profile_rows
from nexcraftviz.skills.base import Skill, SkillResult, SkillSpec
from nexcraftviz.spec.model import Spec
from nexcraftviz.spec.ops import ModelOp, apply_ops
from nexcraftviz.spec.validate import validate

# ---------------------------------------------------------------------------
# viz.edit — change a chart
# ---------------------------------------------------------------------------

class EditIn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    instruction: str = Field(description="What the user asked for, in their words.")
    spec: Any = Field(description="The chart as it stands — a Spec or a raw dict.")
    rows: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Result rows. Used to profile columns; not sent verbatim.",
    )
    language: str = "English"

    def as_spec(self) -> Spec:
        return self.spec if isinstance(self.spec, Spec) else Spec(self.spec or {})

    def profile(self) -> DataProfile:
        return profile_rows(self.rows or self.as_spec().data_values)


class EditOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ops: list[ModelOp] = Field(default_factory=list, description="Operations, in order.")
    reasoning: str = Field(default="", description="One sentence about the data.")
    needs_data: str = Field(
        default="",
        description="A column the instruction needs that the data does not have.",
    )


class EditSkill(Skill[EditIn, EditOut]):
    """Natural language → chart operations."""

    spec = SkillSpec(
        name="viz.edit",
        summary=(
            "Change an existing chart from a natural-language instruction — sort, "
            "filter, recolour, break down, add a target line, change the mark."
        ),
        prompt="viz.edit",
    )
    Input = EditIn
    Output = EditOut

    def user_payload(self, inputs: EditIn) -> str:
        import json

        spec = inputs.as_spec()
        profile = inputs.profile()
        return json.dumps(
            {
                "instruction": inputs.instruction,
                "language": inputs.language,
                "spec": spec.raw,
                "profile": profile.to_prompt_dict(),
                # Naming the views lets the model target one layer of a layered
                # chart instead of guessing, which it otherwise does badly.
                "views": [
                    {
                        "index": index,
                        "path": list(view.path),
                        "mark": view.mark_type,
                        "channels": sorted(view.encoding),
                    }
                    for index, view in enumerate(spec.views())
                ],
            },
            indent=2,
            default=str,
        )

    def apply(self, inputs: EditIn, output: EditOut) -> SkillResult[EditOut]:
        result: SkillResult[EditOut] = SkillResult(skill=self.spec.name, output=output)

        if output.needs_data:
            result.warnings.append(
                f"needs a column the data does not contain: {output.needs_data}"
            )
            result.value = inputs.as_spec()
            return result

        applied = apply_ops(inputs.as_spec(), list(output.ops))
        result.value = applied.spec
        result.changes = applied.describe()
        result.inverse = applied.inverse
        result.failed = list(applied.failed)

        # Validate the edit rather than trusting it. An operation can be
        # individually legal and still leave the chart referencing a field that
        # no longer survives a transform.
        _, report = validate(applied.spec, profile=inputs.profile(), max_tier=2)
        result.warnings.extend(str(issue) for issue in report.errors)
        result.meta["valid"] = report.ok
        return result


# ---------------------------------------------------------------------------
# viz.place — rearrange a widget
# ---------------------------------------------------------------------------

class PlaceIn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    instruction: str = Field(description="What the user asked for.")
    widget: Any = Field(description="The Widget to rearrange.")
    language: str = "English"

    def as_widget(self) -> Widget:
        return self.widget if isinstance(self.widget, Widget) else Widget.from_dict(self.widget)


class PlaceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ops: list[AnyWidgetOp] = Field(default_factory=list)
    reasoning: str = ""
    unresolved: str = Field(
        default="", description="A tile the user named that does not exist."
    )


class PlaceSkill(Skill[PlaceIn, PlaceOut]):
    """Natural language → placement operations."""

    spec = SkillSpec(
        name="viz.place",
        summary=(
            "Rearrange a dashboard widget from a natural-language instruction — "
            "resize a tile, move it, group tiles into a panel, change the layout."
        ),
        prompt="viz.place",
    )
    Input = PlaceIn
    Output = PlaceOut

    def user_payload(self, inputs: PlaceIn) -> str:
        import json

        widget = inputs.as_widget()
        return json.dumps(
            {
                "instruction": inputs.instruction,
                "language": inputs.language,
                "widget": _structure(widget),
            },
            indent=2,
            default=str,
        )

    def apply(self, inputs: PlaceIn, output: PlaceOut) -> SkillResult[PlaceOut]:
        result: SkillResult[PlaceOut] = SkillResult(skill=self.spec.name, output=output)
        widget = inputs.as_widget()

        if output.unresolved:
            result.warnings.append(f"no such tile: {output.unresolved}")
            result.value = widget
            return result

        applied = apply_widget_ops(widget, list(output.ops))
        result.value = applied.widget
        result.changes = applied.describe()
        result.inverse = applied.inverse
        result.failed = list(applied.failed)
        result.warnings.extend(_row_overflow(applied.widget))
        return result


def _structure(widget: Widget) -> dict[str, Any]:
    """The widget's shape, without the specs.

    Sending whole chart specifications would blow the context for no gain — a
    placement decision needs to know what the tiles *are*, not how they are
    drawn.
    """
    from nexcraftviz.compose.widget import Group

    nodes = []
    for node in widget.nodes:
        if isinstance(node, Group):
            nodes.append({
                "id": node.id,
                "kind": "group",
                "title": node.title,
                "span": node.span,
                "tiles": [
                    {"id": t.id, "kind": "tile", "title": t.title,
                     "family": t.family, "span": t.span}
                    for t in node.tiles
                ],
            })
        else:
            nodes.append({
                "id": node.id, "kind": "tile", "title": node.title,
                "family": node.family, "span": node.span,
            })
    return {
        "title": widget.title,
        "layout": widget.layout,
        "layout_options": ["single_column", "two_column_grid",
                           "kpi_row_plus_grid", "executive_summary"],
        "nodes": nodes,
    }


def _row_overflow(widget: Widget) -> list[str]:
    """Warn when a row's spans exceed the grid and will wrap.

    Not an error — wrapping is legal and sometimes wanted — but it is almost
    never what someone asking to "make this one wider" had in mind.
    """
    from nexcraftviz.compose.widget import SPAN_COLUMNS, Group

    warnings: list[str] = []
    groups = [n for n in widget.nodes if isinstance(n, Group)]
    for group in groups:
        total = sum(SPAN_COLUMNS.get(t.span, 3) for t in group.tiles)
        if total > 12:
            warnings.append(
                f"group {group.id!r} spans {total} of 12 columns — its tiles will wrap"
            )
    return warnings
