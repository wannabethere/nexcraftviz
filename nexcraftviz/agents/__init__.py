"""The pipeline-level surface: roles, agents, and the artifacts they hand along.

:mod:`nexcraftviz.skills` stays the skill-level surface — one unit of work, three
pure phases, exposed as MCP tools and tool schemas. This package is the layer
above: a role a host can substitute, a typed artifact per stage, and telemetry
attributable to the agent that spent it.
"""
from nexcraftviz.agents.artifacts import (
    ChartMetadata,
    ChartPlan,
    ChartStages,
    Critique,
    DeliveryArtifact,
    EncodingIntent,
    EvaluationArtifact,
    ExportResult,
    GateResult,
    GenerateArtifact,
    Status,
    StylingIntent,
    Telemetry,
    TransformIntent,
)
from nexcraftviz.agents.intent import ChartIntent, apply_as_floor, extract_intent
from nexcraftviz.agents.registry import (
    ROLES,
    Agent,
    AgentRegistry,
    AgentSpec,
    Role,
    StageContext,
    StageError,
    Tier,
    resolve_model,
)

__all__ = [
    "ROLES",
    "Agent",
    "AgentRegistry",
    "AgentSpec",
    "ChartIntent",
    "ChartMetadata",
    "ChartPlan",
    "ChartStages",
    "Critique",
    "DeliveryArtifact",
    "EncodingIntent",
    "EvaluationArtifact",
    "ExportResult",
    "GateResult",
    "GenerateArtifact",
    "Role",
    "StageContext",
    "StageError",
    "Status",
    "StylingIntent",
    "Telemetry",
    "Tier",
    "TransformIntent",
    "apply_as_floor",
    "extract_intent",
    "resolve_model",
]
