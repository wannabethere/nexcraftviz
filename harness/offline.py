"""A stand-in for a model, so a harness run needs no key.

**This is a test double, not a fallback.** Charts in nexcraftviz are generated
by agents; nothing in the package falls back to rules when a model is absent,
and a missing key stays an error there. This exists so the harness can exercise
the *wiring* — that the stages hand off correctly, the artifacts are shaped
right, the gates run and the report adds up — without spending money.

What an offline run therefore measures is the plumbing, and what it explicitly
does not measure is any prompt. The runner grades accordingly: chart-type and
field expectations are skipped offline, because passing them would only prove
that :func:`~nexcraftviz.recommend.build.build_chart` agrees with itself. A
harness that reported "6/6 passed" for an untested prompt would be worse than
one that reported nothing.
"""
from __future__ import annotations

import json
from typing import Any

from nexcraftviz.agents.intent import extract_intent
from nexcraftviz.data.profile import profile_rows
from nexcraftviz.recommend.build import BuildError, build_chart
from nexcraftviz.recommend.rules import recommend


def offline_runner(rows: list[dict[str, Any]] | None = None):
    """A runner with the same shape as a real one: ``(system, user, schema)``.

    Takes the rows directly, which a real model never does — it is given a
    profile precisely so it cannot embed values. That asymmetry is the clearest
    statement of what this is: not a small model, but a mechanical stand-in that
    is allowed to cheat because it is only holding the wiring up.

    Routes on the schema's own field names rather than the prompt text, so a
    prompt rewrite cannot silently send every stage down the generate branch.
    """
    data = list(rows or [])

    async def run(system: str, user: str, schema: dict[str, Any]):
        payload_in = _load(user)
        payload_in.setdefault("rows", data)
        properties = set((schema or {}).get("properties") or {})

        if "encodings" in properties and "chart_type" in properties:
            payload = _plan(payload_in)
        elif "spec_json" in properties:
            payload = _generate(payload_in)
        elif "answers_question" in properties:
            payload = _critique()
        elif "ops" in properties:
            payload = {"ops": [], "reasoning": "offline: no edit inferred"}
        elif "summary" in properties:
            payload = {"summary": "offline narration", "headline": "", "points": []}
        else:
            payload = {}
        return payload, {"model": "offline", "tokens_in": 0, "tokens_out": 0}

    return run


def _plan(payload: dict[str, Any]) -> dict[str, Any]:
    question = payload.get("question", "")
    rows = _rows_from(payload)
    profile = profile_rows(rows)
    intent = extract_intent(question)

    ranked = recommend(profile, question=question)
    chart_type = intent.chart_type or (ranked.best.chart_type if ranked.best else "")
    if not chart_type:
        return {
            "status": "insufficient_data",
            "reason_if_not_ok": "offline: the profile supports no chart type",
            "confidence": 0.0, "chart_type": "", "alternatives": [], "rationale": "",
            "encodings": [], "transforms": [], "styling": {}, "metadata": {},
            "follow_ups": [],
        }

    encodings = []
    dimension = next(iter(profile.dimensions), None) or next(iter(profile.times), None)
    measure = next(iter(profile.measures), None)
    if dimension is not None:
        encodings.append({"channel": "x", "field": dimension.name,
                          "why": "offline: the first dimension"})
    if measure is not None:
        encodings.append({"channel": "y", "field": measure.name, "aggregate": "sum",
                          "why": "offline: the first measure"})

    return {
        "status": "ok", "confidence": 0.5, "chart_type": chart_type,
        "alternatives": [r.chart_type for r in ranked][1:3],
        "rationale": "offline stand-in: the rules picked this, not a model.",
        "encodings": encodings, "transforms": [],
        "styling": {"theme": intent.theme}, "metadata": {}, "follow_ups": [],
    }


def _generate(payload: dict[str, Any]) -> dict[str, Any]:
    rows = _rows_from(payload)
    plan = payload.get("plan") or {}
    chart_type = plan.get("chart_type", "") if isinstance(plan, dict) else ""
    try:
        spec = build_chart(rows, chart_type=chart_type, question=payload.get("question", ""))
    except BuildError as exc:
        return {"spec_json": "", "chart_type": "", "reasoning": f"offline: {exc}"}
    return {
        "spec_json": json.dumps(spec.raw),
        "chart_type": chart_type or spec.mark_summary,
        "reasoning": "offline stand-in",
    }


def _critique() -> dict[str, Any]:
    """Always approves — and says why that is not a verdict.

    An offline critic that invented complaints would put noise into the report;
    one that approves is at least honestly uninformative, and the report states
    that no model was called.
    """
    return {
        "answers_question": True, "score": 0.0,
        "complaint": "", "suggestion": "offline: no model judged this chart",
    }


def _rows_from(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("rows")
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _load(user: str) -> dict[str, Any]:
    try:
        parsed = json.loads(user)
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
