"""Optional ``.env`` loading, matching genieml's convention.

genieml loads a ``.env`` in both ``genieml_skills/env.py`` and ``cp2/env.py``,
so a key set there should work here too rather than being a second thing to
remember.

Two deliberate choices:

* **Opt-in, not automatic on import.** A library that reads files off disk the
  moment it is imported is a surprise, and in a server it is a surprise that
  happens once and then never reflects a change. The CLI calls this; importing
  ``nexcraftviz`` does not.
* **Never overrides.** A variable already in the environment wins over the
  file. Otherwise a stale ``.env`` silently beats the key you just exported,
  which is a genuinely horrible half-hour.

``python-dotenv`` is optional; without it this is a no-op and the environment
still works normally.
"""
from __future__ import annotations

import os
from pathlib import Path

#: Searched in order, nearest first. The genieml root is included because that
#: is where the existing `.env.example` lives — one key, both stacks.
CANDIDATE_NAMES = (".env.local", ".env")


def find_env_files(start: Path | None = None) -> list[Path]:
    """Every ``.env`` from ``start`` up to the filesystem root, nearest first."""
    found: list[Path] = []
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        for name in CANDIDATE_NAMES:
            candidate = directory / name
            if candidate.is_file():
                found.append(candidate)
    return found


def load(start: Path | None = None, *, override: bool = False) -> list[Path]:
    """Load any ``.env`` found, returning the files actually read.

    Returns an empty list when ``python-dotenv`` is not installed, so a caller
    can say so rather than silently doing nothing.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return []

    loaded: list[Path] = []
    for path in find_env_files(start):
        load_dotenv(path, override=override)
        loaded.append(path)
    return loaded


def describe_provider() -> str:
    """A one-line summary of what the provider configuration currently is.

    Used by the CLI so "no key" is reported plainly rather than discovered as a
    401 several steps later.
    """
    from nexcraftviz.integrations.providers import DEFAULT_OPENAI_MODEL

    model = os.getenv("OPENAI_MODEL", "").strip() or DEFAULT_OPENAI_MODEL
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return "no OPENAI_API_KEY set"
    return f"{model} (key ...{key[-4:]})"
