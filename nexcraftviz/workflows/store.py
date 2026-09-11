"""Where runs wait between calls.

In memory, with a time-to-live: a run paused for an answer nobody gives is not
kept forever. Enough for one instance; more than one needs a shared store or
sticky routing, and this class is the seam to replace.
"""
from __future__ import annotations

import time

from nexcraftviz.workflows.executor import Run


class RunStore:
    def __init__(self, *, ttl_seconds: float = 3600.0, limit: int = 500) -> None:
        self._runs: dict[str, Run] = {}
        self._ttl = ttl_seconds
        self._limit = limit

    def put(self, run: Run) -> Run:
        self._expire()
        if run.run_id not in self._runs and len(self._runs) >= self._limit:
            self._runs.pop(next(iter(self._runs)))
        run.updated = time.time()
        self._runs[run.run_id] = run
        return run

    def get(self, run_id: str) -> Run | None:
        self._expire()
        return self._runs.get(run_id)

    def drop(self, run_id: str) -> bool:
        return self._runs.pop(run_id, None) is not None

    def __len__(self) -> int:
        return len(self._runs)

    def _expire(self) -> None:
        cutoff = time.time() - self._ttl
        for run_id in [rid for rid, run in self._runs.items() if run.updated < cutoff]:
            del self._runs[run_id]
