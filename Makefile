PY := ./.venv/bin/python

.PHONY: help venv test lint fmt typecheck check corpus clean

help:
	@echo "venv       create .venv and install the package with dev+render extras"
	@echo "test       run the full test suite"
	@echo "lint       ruff check"
	@echo "fmt        ruff check --fix"
	@echo "typecheck  mypy over the package"
	@echo "check      lint + test (what CI runs)"
	@echo "corpus     report corpus coverage and validation status"

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

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache dist build
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
