"""Prompt evals — the one thing a stub model cannot measure."""
from nexcraftviz.evals.cases import ALL_CASES, Case, Expectation, cases_for
from nexcraftviz.evals.runner import CaseResult, Report, run, run_case

__all__ = [
    "ALL_CASES",
    "Case",
    "CaseResult",
    "Expectation",
    "Report",
    "cases_for",
    "run",
    "run_case",
]
