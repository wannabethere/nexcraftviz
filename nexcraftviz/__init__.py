"""nexcraftviz — agentic Vega-Lite charting.

The public surface is deliberately small: a ``Spec``, an algebra of operations
over it, validation, profiling, and rendering. Skills, themes and exports build
on those and are imported from their own subpackages so that nothing pulls an
optional dependency at import time.
"""
from __future__ import annotations

__version__ = "0.1.0"

from nexcraftviz.data.profile import ColumnProfile, DataProfile, profile_rows
from nexcraftviz.spec.model import Spec, SpecError, View
from nexcraftviz.spec.ops import OpError, OpList, OpResult, apply_ops, parse_ops
from nexcraftviz.spec.validate import ValidationIssue, ValidationReport, validate

__all__ = [
    "ColumnProfile",
    "DataProfile",
    "OpError",
    "OpList",
    "OpResult",
    "Spec",
    "SpecError",
    "ValidationIssue",
    "ValidationReport",
    "View",
    "__version__",
    "apply_ops",
    "parse_ops",
    "profile_rows",
    "validate",
]
