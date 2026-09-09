# harness

Setting nexcraftviz up, running it over scenarios, and saying how it did.

```bash
nexcraftviz harness check      # can this environment run anything?
nexcraftviz harness setup      # scaffold .env; print what is left for a human
nexcraftviz harness run        # the scenarios, end to end
nexcraftviz harness report     # the last run, or --compare two of them
```

## check

The one that saves the most time. A missing font makes PNG text render subtly
wrong with **no error at all**; a missing key surfaces three steps later as a
401 from inside a stage. Both are cheap to detect up front.

```
[ok  ] package — nexcraftviz 0.1.0
[ok  ] prompts — 6 prompts
[WARN] themes — 4 themes; below WCAG AA: powerbi
[ok  ] corpus — 200 pairs
[ok  ] render — vl-convert renders (14441 bytes)
[ok  ] fonts — fontconfig available
[MISS] api_key — no OPENAI_API_KEY set
         fix: export OPENAI_API_KEY=sk-...
[ok  ] agents — 8 roles filled

Ready for offline runs. Live runs need: api_key.
```

Three states, and the difference is the useful part. `missing` is genuinely
absent and comes with the command to fix it. `degraded` is present but not fully
working — the `powerbi` theme is below WCAG AA **on purpose**, because matching
PowerBI is the point, so it is a warning forever rather than a bug to fix.

Exit code is 0 when an offline run is possible. A missing key is configuration,
not breakage.

## run

A scenario is a question, some rows, and what a good answer looks like:

```yaml
scenarios:
  ranked_categories:
    question: "Which region brought in the most revenue?"
    rows:
      - {region: "North", revenue: 152.0}
      - {region: "West", revenue: 128.0}
    expect_chart_type: [bar, grouped_bar]
    expect_fields: [region, revenue]
    require_gates: [validates, data_honesty]
```

Loose about the chart, strict about the mistakes. An exact-spec assertion fails
on every harmless rewording; "a rate is never summed" catches a real regression.
`expect_chart_type` takes a list because "revenue over time" is legitimately a
line *or* an area, and failing the second measures conformity rather than
quality.

### offline vs live

`run` with no flags calls **no model**. It uses a mechanical stand-in
(`harness/offline.py`) so the wiring — stage handoff, artifact shapes, gates,
the report itself — is exercised in CI with no key and no cost.

An offline run therefore grades the plumbing and **not any prompt**, and it says
so on every line that reports a number:

```
6/6 scenarios passed  (0 tokens, 1868 ms) [wiring only: the prompts were not graded]
```

The `expect_chart_type` and `expect_fields` checks are skipped offline on
purpose. Passing them would only prove the stand-in agrees with itself, and a
harness that reports a green suite for an untested prompt is worse than one that
reports nothing.

`--live` uses a real model. `--critic` adds the LLM critic and implies `--live`.

The stand-in is a **test double, not a fallback**. Nothing in the package falls
back to rules when a model is absent — a missing key stays an error there.

## report

Per stage, because "the chart was wrong" is not actionable and "the planner
picked bar, the generator built a line, and `matches_plan` caught it" tells you
which prompt to open.

```
per stage
  stage       runs   median ms   tokens  agent
  plan           6           4        0  builtin.planner
  generate       6           9        0  builtin.generator
  evaluate       6          54        0  builtin.evaluator
  deliver        6           0        0  builtin.deliverer

gates
  data_honesty           6/6 passed
  matches_plan           6/6 passed
  ...
```

`--compare BEFORE AFTER` diffs two saved runs, because the question is almost
always "is this better than last time?" rather than "how good is this?".

Runs are saved to `harness/results/` and gitignored — they are local evidence,
not source.

## What this is not

AntV's `harness/` is an iterative skill-optimisation loop: eval, render test,
analyze, let a model rewrite the skill document, rebuild the index. This is
setup + run + report.

Our prompts are data files, so that loop would be straightforward to add. The
reason to hold off is that an automated rewrite with nobody reading the diff is
the fastest way to make prompts quietly worse — and there is nothing here yet
measuring prompt quality well enough to be a safe optimisation target.
