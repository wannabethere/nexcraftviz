"""Judging a chart, cheapest check first.

Deterministic gates run before any model, because they are free and they catch
the failures that actually happen. The critic is asked only the question code
cannot answer: does this chart answer the question it was made for?
"""
from nexcraftviz.evaluate.gates import run_gates

__all__ = ["run_gates"]
