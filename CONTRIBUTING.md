# Contributing

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,render,app,openai]"
pytest -q          # 1250+ tests, all offline
ruff check nexcraftviz harness tests
```

No API key is needed to develop: the whole suite runs offline against stub
models. A key is needed only for the live evals and the harness (below).

## What the tests expect of a change

The conventions here exist because each one caught a real defect:

- **Prompts are files, not string literals** — `nexcraftviz/prompts/*.txt`,
  listed in `manifest.yaml`, each with a `# prompt_version:` header. They can
  be diffed, reviewed and overridden (`NEXCRAFTVIZ_PROMPT_DIR`) without a
  release.
- **Every model-backed skill has eval cases.** A test derives the list from the
  registry, so a new skill without cases fails the suite — the hand-written
  version of that check once passed for four skills that had none.
- **Deterministic work stays deterministic.** Validation, repair, operations,
  layout, theming and rendering are code; the model decides, the code applies.
  A new rule belongs in a gate with a test that feeds it the thing it exists to
  catch.
- **No host's name in the package.** Anything specific to one product — its
  storage shapes, whether its card header shows the title — belongs in that
  host's bridge. `tests/test_host_agnostic.py` fails if a name creeps back in.
- **Regenerate the pages** when you change the corpus, the gallery or the
  theme: `nexcraftviz gallery --out playground --standalone docs/capabilities.html`.

## Measuring a prompt change

```bash
export OPENAI_API_KEY=...
nexcraftviz eval                 # 34 cases, graded on what the model actually said
nexcraftviz harness run --critic -v   # 13 scenarios, end to end
nexcraftviz harness report --html run.html
```

Evals grade the raw answer, before any repair — a case that passes only because
the code fixed the answer is measuring the code, not the prompt. The harness
runs the whole pipeline and reports which gate failed.

## Commits

Say what changed and why it needed changing; if a live run or a test found the
defect, say so. The history is the design record.
