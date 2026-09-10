"""nexcraftviz is host-agnostic: no host's names inside the package.

Host-specific shapes — a dashboard's storage rows, whether a card header shows
the title, where a service key lives — belong in that host's bridge. A host's
name creeping back into the package is a coupling creeping back with it.
"""
from __future__ import annotations

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "nexcraftviz"
HOST_NAMES = re.compile(
    r"lexy|asthera|thread_component|genieml|InlineKpiTile|workflowservices|react-vega",
    re.IGNORECASE,
)
SCANNED = {".py", ".txt", ".yaml", ".yml", ".js", ".css", ".md", ".json"}


def test_no_host_names_in_the_package():
    offenders = []
    for path in sorted(PACKAGE.rglob("*")):
        if path.suffix not in SCANNED or "__pycache__" in path.parts or not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if HOST_NAMES.search(line):
                where = path.relative_to(PACKAGE.parent)
                offenders.append(f"{where}:{number}: {line.strip()[:90]}")
    assert not offenders, "\n".join(offenders)
