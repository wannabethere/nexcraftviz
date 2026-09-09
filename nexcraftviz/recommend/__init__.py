"""Chart-type ranking, and an explicit builder.

``rules`` ranks chart types from the data shape with a reason for each. It
decides nothing on its own — the ranking is *context handed to an agent*, inside
the ``viz.generate`` prompt and through the ``viz_recommend`` tool.

``build`` constructs a spec from the profile without a model. It is deliberately
**not** wired into the session, the agent tools or the CLI: charts are generated
by agents. It stays available for callers who ask for it by name.
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
