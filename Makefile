PY := ./.venv/bin/python

.PHONY: help venv test lint fmt typecheck check corpus serve eval-live skill-reference clean

help:
	@echo "venv       create .venv and install the package with dev+render extras"
	@echo "test       run the full test suite"
	@echo "lint       ruff check"
	@echo "fmt        ruff check --fix"
	@echo "typecheck  mypy over the package"
	@echo "check      lint + test (what CI runs)"
	@echo "corpus     report corpus coverage and validation status"
	@echo "serve      run the HTTP API and serve the embed"
	@echo "eval-live  run the prompt evals against a real model (needs a key)"
	@echo "skill-reference  regenerate the Claude Code skill's operation reference"

venv:
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev,render]"

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check .

fmt:
	$(PY) -m ruff check --fix .

typecheck:
	$(PY) -m mypy nexcraftviz

check: lint test

corpus:
	$(PY) -m nexcraftviz.corpus.report

serve:
	$(PY) -m nexcraftviz.cli serve

# Live prompt evals. Costs money and needs OPENAI_API_KEY; `make check` stays
# offline and free on purpose.
eval-live:
	NEXCRAFTVIZ_LIVE_EVAL=1 $(PY) -m nexcraftviz.cli eval

skill-reference:
	$(PY) scripts/build_skill_reference.py

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache dist build
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
