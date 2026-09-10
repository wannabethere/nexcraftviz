"""Stage artifacts — the handoff between pipeline stages.

Each stage writes one slot on :class:`ChartStages`; the next stage reads it and
fails loud when it is empty. The artifact *is* the interface, which means a
stage can be replaced, cached, hand-written or reviewed without touching the
ones around it.

Two conventions, both taken from cp2 because they earn their keep there:

* **Status on the artifact, not an exception.** A planner that cannot plan
  returns ``status="insufficient_data"`` with a reason and a confidence, so the
  caller decides what to do rather than catching something.
* **A uniform telemetry block** on every stage result, so cost and latency are
  attributable per stage rather than per turn.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Every schema is strict. A typo in a field name should fail loudly at the
#: boundary rather than be silently dropped and debugged three stages later.
STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True)

Status = Literal["ok", "insufficient_data", "ambiguous", "failed"]


class Telemetry(BaseModel):
    """What every stage reports about its own execution."""

    model_config = STRICT

    wall_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    prompt_version: str = ""
    cache_hit: bool = False
    retry_count: int = 0
    #: Which agent produced this, for attribution when a role is overridden.
    agent: str = ""

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

class EncodingIntent(BaseModel):
    """One channel of the intended chart, and why.

    Deliberately not a Vega-Lite encoding — it is the *decision*, which the
    generator turns into a spec. Keeping them separate is what lets a plan be
    reviewed before anything is drawn.
    """

    model_config = STRICT

    channel: str = Field(description="x | y | color | size | column | row | theta | ...")
    field: str = Field(description="A column name, or '' for an aggregate like count.")
    aggregate: str = Field(default="", description="sum | mean | count | median | ...")
    sort: str = Field(default="", description="ascending | descending | by another field")
    why: str = Field(default="", description="One clause on why this channel.")


class TransformIntent(BaseModel):
    model_config = STRICT

    kind: str = Field(description="filter | top_n | bin | fold | window | calculate")
    detail: str = Field(default="", description="What it does, in words.")
    field: str = ""
    #: Typed, not `Any`. OpenAI's strict mode rejects an untyped field, and one
    #: untyped field anywhere drops the WHOLE schema to non-strict — silently,
    #: since the provider falls back rather than failing. Non-strict only loosely
    #: enforces structure, and a live run came back with a field placed where it
    #: does not exist, which crashed the pipeline.
    value: str | int | float | bool | list[str] | None = None


class StylingIntent(BaseModel):
    """Presentation decisions, kept on the plan rather than in the prompt.

    cp2's precedent: presentation intent lives on the schema as optional hints
    that a deterministic extractor fills first and the model may override. That
    keeps the prompt about the data and makes the common phrasings free.
    """

    model_config = STRICT

    theme: str = Field(default="", description="A preset name, or '' for the default.")
    palette_intent: str = Field(
        default="",
        description="sequential | diverging | categorical | single — what the "
                    "colour is doing, not which hex.",
    )
    emphasis: str = Field(default="", description="A value or category to draw attention to.")
    reference_lines: list[float] = Field(default_factory=list)
    horizontal: bool | None = Field(
        default=None, description="Force bar orientation; None lets the generator decide."
    )


class ChartMetadata(BaseModel):
    model_config = STRICT

    title: str = ""
    subtitle: str = ""
    units: str = ""
    narrative_angle: str = Field(
        default="",
        description="The point the narration should make, so chart and prose agree.",
    )


#: Transform kinds whose `field` names the column they produce, not one they
#: read. `filter`, `top_n` and `bin` read theirs.
_CREATES_FIELD = frozenset({"calculate", "window", "fold"})


class ChartPlan(BaseModel):
    """What to draw, decided before anything is drawn."""

    model_config = STRICT

    status: Status = "ok"
    reason_if_not_ok: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    chart_type: str = ""
    alternatives: list[str] = Field(
        default_factory=list, description="Considered and rejected."
    )
    rationale: str = ""

    encodings: list[EncodingIntent] = Field(default_factory=list)
    transforms: list[TransformIntent] = Field(default_factory=list)
    styling: StylingIntent = Field(default_factory=StylingIntent)
    metadata: ChartMetadata = Field(default_factory=ChartMetadata)
    follow_ups: list[str] = Field(default_factory=list)

    telemetry: Telemetry = Field(default_factory=Telemetry)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def drawable(self) -> bool:
        """``ok``, or ``ambiguous`` with the conservative reading planned.

        viz.plan's rule 6 asks for exactly that plan when a question has two
        readings. Treating it as a stop delivered no chart and no reason — live,
        widget_entity_rows — so a plan with a chart type and encodings is drawn,
        and the run says it was ambiguous.
        """
        return self.status == "ok" or (
            self.status == "ambiguous" and bool(self.chart_type) and bool(self.encodings)
        )

    def derived_fields(self) -> set[str]:
        """Fields the plan's own transforms CREATE — outputs, not columns.

        A live plan for "2025-Q1"-style quarters added a `calculate` producing
        `quarter_sort` so the labels plot in time order — a good plan. Counting
        that field as a column read from the data called it invented, and the
        planner rejected its own correct work.
        """
        return {t.field for t in self.transforms if t.field and t.kind in _CREATES_FIELD}

    def fields_used(self) -> set[str]:
        """Every column the plan reads from the data — what must exist there.

        Fields a transform creates are excluded, even when an encoding uses
        them: those come from the plan, not the rows. A filter or top-N on a
        column the data lacks is still counted, because that one is read.
        """
        read = {e.field for e in self.encodings if e.field}
        read |= {t.field for t in self.transforms if t.field and t.kind not in _CREATES_FIELD}
        return read - self.derived_fields()

    def summary(self) -> str:
        if not self.ok:
            return f"{self.status}: {self.reason_if_not_ok}"
        channels = ", ".join(f"{e.channel}={e.field or e.aggregate}" for e in self.encodings)
        return f"{self.chart_type} ({channels})"


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------

class GenerateArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    status: Status = "ok"
    reason_if_not_ok: str = ""
    spec: Any = Field(default=None, description="A Spec, or None when nothing was produced.")
    chart_type: str = ""
    reasoning: str = ""
    repaired: list[str] = Field(default_factory=list)
    defect: bool = Field(
        default=False,
        description="The attempt produced something unusable — as opposed to the "
                    "generator deciding no chart suits the data. Only a defect is "
                    "worth a regeneration; a decision would just be made again.",
    )
    telemetry: Telemetry = Field(default_factory=Telemetry)

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.spec is not None


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------

class GateResult(BaseModel):
    """One deterministic check."""

    model_config = STRICT

    gate: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        return f"{'PASS' if self.passed else 'FAIL'} {self.gate}" + (
            f" — {self.detail}" if self.detail else ""
        )


class Critique(BaseModel):
    """The model's verdict on whether the chart answers the question."""

    model_config = STRICT

    answers_question: bool = True
    score: float = Field(default=1.0, ge=0.0, le=1.0)
    complaint: str = Field(
        default="", description="One specific thing wrong, phrased so it can be acted on."
    )
    suggestion: str = ""


class EvaluationArtifact(BaseModel):
    model_config = STRICT

    status: Status = "ok"
    gates: list[GateResult] = Field(default_factory=list)
    critique: Critique | None = None
    regenerated: bool = Field(
        default=False, description="Whether a retry was spent on this chart."
    )
    telemetry: Telemetry = Field(default_factory=Telemetry)

    @property
    def passed(self) -> bool:
        gates_ok = all(g.passed for g in self.gates)
        critic_ok = self.critique is None or self.critique.answers_question
        return gates_ok and critic_ok

    @property
    def failures(self) -> list[GateResult]:
        return [g for g in self.gates if not g.passed]

    def complaint(self) -> str:
        """The single most actionable thing wrong, for a regeneration attempt."""
        if self.failures:
            first = self.failures[0]
            return f"{first.gate}: {first.detail}" if first.detail else first.gate
        if self.critique and not self.critique.answers_question:
            return self.critique.complaint
        return ""


# ---------------------------------------------------------------------------
# deliver
# ---------------------------------------------------------------------------

class ExportResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    target: str
    content: str = ""
    path: str = ""
    supported: bool = True
    degraded: list[str] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)
    notes: str = ""


class DeliveryArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    status: Status = "ok"
    package: dict[str, Any] = Field(
        default_factory=dict,
        description="Spec, table, narration, plan, verdict and provenance in one object.",
    )
    files: dict[str, str] = Field(
        default_factory=dict, description="format → path or data URI."
    )
    exports: dict[str, ExportResult] = Field(default_factory=dict)
    tile: dict[str, Any] | None = Field(
        default=None, description="Ready for the thread_components table."
    )
    telemetry: Telemetry = Field(default_factory=Telemetry)


# ---------------------------------------------------------------------------
# the container
# ---------------------------------------------------------------------------

class ChartStages(BaseModel):
    """One nullable slot per stage. The artifact is the handoff."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    plan: ChartPlan | None = None
    generate: GenerateArtifact | None = None
    evaluate: EvaluationArtifact | None = None
    deliver: DeliveryArtifact | None = None

    def completed(self) -> list[str]:
        return [name for name in ("plan", "generate", "evaluate", "deliver")
                if getattr(self, name) is not None]

    def telemetry(self) -> dict[str, Telemetry]:
        return {
            name: getattr(self, name).telemetry
            for name in self.completed()
            if getattr(getattr(self, name), "telemetry", None)
        }

    def total_tokens(self) -> int:
        return sum(t.tokens for t in self.telemetry().values())

    def total_ms(self) -> int:
        return sum(t.wall_ms for t in self.telemetry().values())
