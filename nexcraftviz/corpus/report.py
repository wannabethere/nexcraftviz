"""``python -m nexcraftviz.corpus.report`` — corpus health at a glance.

Answers the three questions that matter when deciding what to add next: how
many examples per chart type, which types are too thin to retrieve reliably,
and which shipped specs no longer validate.
"""
from __future__ import annotations

import sys

from nexcraftviz.corpus.loader import Corpus, seed
from nexcraftviz.spec.validate import validate

MIN_EXAMPLES = 3


def report(corpus: Corpus | None = None) -> int:
    corpus = corpus or seed()

    print(f"corpus: {len(corpus)} pairs (tenant={corpus.tenant}, version={corpus.version})\n")

    families: dict[str, int] = {}
    for pair in corpus:
        families[pair.family] = families.get(pair.family, 0) + 1
    print("families")
    for family, count in sorted(families.items(), key=lambda kv: -kv[1]):
        print(f"  {family:<20} {count:>3}")

    print("\nchart types")
    for chart_type, count in corpus.chart_type_counts().items():
        flag = "  <- thin" if count < MIN_EXAMPLES else ""
        print(f"  {chart_type:<20} {count:>3}{flag}")

    gaps = corpus.coverage_gaps(MIN_EXAMPLES)
    print(f"\n{len(gaps)} type(s) below {MIN_EXAMPLES} examples: {', '.join(gaps) or 'none'}")

    print("\nvalidating specs...")
    failures: list[tuple[str, str]] = []
    warned = 0
    checked = corpus.with_vega_specs
    for pair in checked:
        _, result = validate(pair.spec(), max_tier=3)
        if not result.ok:
            failures.append((pair.name, str(result.errors[0])))
        elif result.warnings:
            warned += 1

    print(f"  {len(checked) - len(failures)}/{len(checked)} valid, {warned} with warnings")
    for name, message in failures:
        print(f"  FAIL {name}: {message}")

    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(report())
