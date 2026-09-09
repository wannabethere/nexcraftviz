"""Choosing a chart, and building it — both without a model.

``rules`` decides which chart the data shape calls for; ``build`` constructs it.
Together they mean a chart appears with no provider configured, and a model
becomes an improvement on that rather than a precondition for it.
"""
from nexcraftviz.recommend.build import BUILDABLE, BuildError, build_best, build_chart
from nexcraftviz.recommend.rules import Recommendation, RecommendationSet, recommend

__all__ = [
    "BUILDABLE",
    "BuildError",
    "Recommendation",
    "RecommendationSet",
    "build_best",
    "build_chart",
    "recommend",
]
