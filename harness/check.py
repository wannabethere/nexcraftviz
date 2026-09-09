"""Is this environment able to run anything?

This matters more than it sounds. Today a missing font makes PNG text render
subtly wrong with no error at all, and a missing key surfaces three steps later
as a 401 from inside a stage. Both are cheap to detect up front and expensive to
diagnose later.

Every check reports one of three states, and the distinction is the useful part:

``ok``       working
``missing``  a prerequisite that is genuinely absent — with the command to fix it
``degraded`` present but not fully working; the thing runs with less than it should

A check never raises. A harness that crashes while telling you what is broken is
the worst possible version of itself.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
from dataclasses import dataclass, field
from typing import Literal

State = Literal["ok", "missing", "degraded"]


@dataclass
class Check:
    name: str
    state: State
    detail: str = ""
    fix: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"

    def line(self) -> str:
        mark = {"ok": "ok  ", "missing": "MISS", "degraded": "WARN"}[self.state]
        text = f"[{mark}] {self.name}"
        if self.detail:
            text += f" — {self.detail}"
        if self.fix and self.state != "ok":
            text += f"\n         fix: {self.fix}"
        return text


@dataclass
class CheckReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def missing(self) -> list[Check]:
        return [c for c in self.checks if c.state == "missing"]

    @property
    def degraded(self) -> list[Check]:
        return [c for c in self.checks if c.state == "degraded"]

    @property
    def can_run_offline(self) -> bool:
        """Everything the deterministic half needs — no model involved."""
        return not [c for c in self.missing if c.name in _OFFLINE_REQUIREMENTS]

    @property
    def can_run_live(self) -> bool:
        return not self.missing

    def to_dict(self) -> dict[str, object]:
        return {
            "can_run_offline": self.can_run_offline,
            "can_run_live": self.can_run_live,
            "checks": [
                {"name": c.name, "state": c.state, "detail": c.detail, "fix": c.fix}
                for c in self.checks
            ],
        }

    def text(self) -> str:
        lines = [c.line() for c in self.checks]
        lines.append("")
        if self.can_run_live:
            lines.append("Ready: offline and live runs will both work.")
        elif self.can_run_offline:
            lines.append(
                f"Ready for offline runs. Live runs need: "
                f"{', '.join(c.name for c in self.missing)}."
            )
        else:
            lines.append(
                f"Not ready: {', '.join(c.name for c in self.missing)} "
                f"{'is' if len(self.missing) == 1 else 'are'} missing."
            )
        return "\n".join(lines)


#: What a no-model run needs. Everything else is required only for a live run.
_OFFLINE_REQUIREMENTS = {"package", "prompts", "themes", "corpus"}


def check_environment() -> CheckReport:
    """Every prerequisite, in the order a newcomer hits them."""
    return CheckReport(checks=[
        _check_package(),
        _check_prompts(),
        _check_themes(),
        _check_corpus(),
        _check_render(),
        _check_fonts(),
        _check_key(),
        _check_agents(),
        _check_retrieval(),
    ])


def _check_package() -> Check:
    try:
        import nexcraftviz

        return Check("package", "ok", f"nexcraftviz {nexcraftviz.__version__}")
    except Exception as exc:  # noqa: BLE001
        return Check("package", "missing", str(exc), fix='pip install -e ".[dev,render]"')


def _check_prompts() -> Check:
    try:
        from nexcraftviz.skills import REGISTRY
        from nexcraftviz.skills.base import PROMPT_DIR, prompt_manifest

        declared = prompt_manifest().get("prompts", {})
        absent = [n for n, e in declared.items() if not (PROMPT_DIR / e["file"]).exists()]
        if absent:
            return Check("prompts", "missing", f"declared but not on disk: {', '.join(absent)}")

        unbacked = [
            s.spec.name for s in REGISTRY.values()
            if s.spec.uses_llm and s.spec.prompt not in declared
        ]
        if unbacked:
            return Check("prompts", "degraded",
                         f"skills with no prompt in the manifest: {', '.join(unbacked)}")

        override = os.getenv("NEXCRAFTVIZ_PROMPT_DIR", "").strip()
        detail = f"{len(declared)} prompts"
        if override:
            detail += f", overridden from {override}"
        return Check("prompts", "ok", detail)
    except Exception as exc:  # noqa: BLE001
        return Check("prompts", "missing", str(exc))


def _check_themes() -> Check:
    try:
        from nexcraftviz.theme.contrast import audit
        from nexcraftviz.theme.tokens import available_themes, load

        names = available_themes()
        if not names:
            return Check("themes", "missing", "no theme presets found")

        failing = [name for name in names if not audit(load(name)).ok]
        if failing:
            # Deliberately `degraded`, not `missing`: the PowerBI palette is
            # below AA on purpose, because matching PowerBI is the point.
            return Check("themes", "degraded",
                         f"{len(names)} themes; below WCAG AA: {', '.join(failing)}")
        return Check("themes", "ok", f"{len(names)} themes, all AA")
    except Exception as exc:  # noqa: BLE001
        return Check("themes", "missing", str(exc))


def _check_corpus() -> Check:
    try:
        from nexcraftviz.corpus.loader import seed

        pairs = seed()
        if not pairs:
            return Check("corpus", "degraded", "no corpus pairs — golden tests will not run")
        return Check("corpus", "ok", f"{len(pairs)} pairs")
    except Exception as exc:  # noqa: BLE001
        return Check("corpus", "degraded", f"not loadable: {exc}")


def _check_render() -> Check:
    if importlib.util.find_spec("vl_convert") is None:
        return Check(
            "render", "missing",
            "vl-convert-python is not installed; PNG/SVG rendering and the "
            "`renders` gate will be skipped",
            fix='pip install -e ".[render]"',
        )
    try:
        from nexcraftviz.render import to_png
        from nexcraftviz.spec.model import Spec

        png = to_png(Spec({
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": [{"a": "x", "b": 1}]},
            "mark": "bar",
            "encoding": {"x": {"field": "a", "type": "nominal"},
                         "y": {"field": "b", "type": "quantitative"}},
        }))
        return Check("render", "ok", f"vl-convert renders ({len(png)} bytes)")
    except Exception as exc:  # noqa: BLE001
        return Check("render", "degraded", f"installed but failed: {exc}")


def _check_fonts() -> Check:
    """A missing font does not error — it silently renders the wrong glyphs.

    vl-convert bundles its own default, so this is a warning about *text
    fidelity*, never a blocker.
    """
    if importlib.util.find_spec("vl_convert") is None:
        return Check("fonts", "degraded", "not checked — vl-convert is not installed")
    if shutil.which("fc-list") is None:
        return Check("fonts", "degraded",
                     "fontconfig not available, so system fonts cannot be enumerated; "
                     "vl-convert's bundled font will be used")
    return Check("fonts", "ok", "fontconfig available")


def _check_key() -> Check:
    from nexcraftviz import env

    loaded = env.load()
    description = env.describe_provider()
    if "no OPENAI_API_KEY" in description:
        return Check(
            "api_key", "missing", description,
            fix="export OPENAI_API_KEY=sk-...  (or put it in a .env — "
                "run `nexcraftviz harness setup`)",
        )
    detail = description
    if loaded:
        detail += f", from {loaded[0]}"
    return Check("api_key", "ok", detail)


def _check_agents() -> Check:
    try:
        from nexcraftviz.agents import AgentRegistry

        registry = AgentRegistry.default()
        mismatched = []
        for agent in registry.describe():
            spec = registry.get(agent["role"]).spec
            declared = spec.declared_tier_in_prompt()
            if declared is not None and declared != spec.model_tier:
                mismatched.append(f"{spec.name} ({declared} vs {spec.model_tier})")
        if mismatched:
            return Check("agents", "degraded",
                         f"prompt tier disagrees with the spec: {', '.join(mismatched)}")
        return Check("agents", "ok", f"{len(registry)} roles filled")
    except Exception as exc:  # noqa: BLE001
        return Check("agents", "missing", str(exc))


def _check_retrieval() -> Check:
    """Which backend picks chart types, and whether it can actually run.

    Never a blocker: lexical matching needs nothing and works. This exists so a
    vector store that is configured but unreachable is reported here rather than
    discovered as quietly worse chart choices.
    """
    try:
        from nexcraftviz.recommend import retrieval

        state = retrieval.describe()
    except Exception as exc:  # noqa: BLE001
        return Check("retrieval", "degraded", f"could not be read: {exc}")

    if state["backend"] == "lexical":
        return Check("retrieval", "ok", "lexical matching over the corpus (no key needed)")
    if state["effective"] == "qdrant":
        return Check("retrieval", "ok", f"qdrant, collection {state['collection']!r}")
    return Check(
        "retrieval", "degraded",
        f"qdrant asked for but unavailable ({'; '.join(state['reasons']) or 'unknown'}) "
        f"— falling back to lexical matching",
        fix="export QDRANT_URL=...  (or unset NEXCRAFTVIZ_RETRIEVAL to use lexical)",
    )
