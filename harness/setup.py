"""Make the environment able to run, and say plainly what is left for a human.

Deliberately narrow about what it will do on its own: it writes a ``.env``
template and creates the directories a run needs. It does **not** install
packages or write a key — installing behind someone's back is how a virtualenv
ends up in a state nobody can explain, and a key is theirs to paste.

So this is: do the safe mechanical part, then print the rest as instructions.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_TEMPLATE = """\
# nexcraftviz — provider configuration.
#
# Matches genieml's names, so one key configures both stacks. The tiers exist so
# planning and critique can use a stronger model than generation without a code
# change; leave them unset and everything uses OPENAI_MODEL.

OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-mini

# NEXCRAFTVIZ_FAST_MODEL=gpt-5-mini
# NEXCRAFTVIZ_SMART_MODEL=gpt-5

# Override the prompt directory to patch a prompt without a release.
# NEXCRAFTVIZ_PROMPT_DIR=
"""


@dataclass
class SetupReport:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    todo: list[str] = field(default_factory=list)
    #: Present but not fully working. Listed separately because some of these
    #: are deliberate — the PowerBI palette is below WCAG AA on purpose — and
    #: putting them under "still to do" tells people to fix what is correct.
    notes: list[str] = field(default_factory=list)

    def text(self) -> str:
        lines: list[str] = []
        for item in self.created:
            lines.append(f"created  {item}")
        for item in self.skipped:
            lines.append(f"kept     {item}")
        if self.todo:
            lines.append("")
            lines.append("Still to do:")
            lines.extend(f"  - {item}" for item in self.todo)
        else:
            lines.append("")
            lines.append("Nothing left to do — try `nexcraftviz harness check`.")
        if self.notes:
            lines.append("")
            lines.append("Worth knowing:")
            lines.extend(f"  - {item}" for item in self.notes)
        return "\n".join(lines) + "\n"


def setup_environment(root: Path | str | None = None) -> SetupReport:
    """Scaffold what is safe to scaffold; report the rest."""
    base = Path(root or Path.cwd())
    base.mkdir(parents=True, exist_ok=True)
    report = SetupReport()

    env_path = base / ".env"
    if env_path.exists():
        # Never overwrite: a .env holds a real key, and losing one to a setup
        # command is a bad afternoon.
        report.skipped.append(f"{env_path} (already exists)")
    else:
        env_path.write_text(ENV_TEMPLATE, encoding="utf-8")
        report.created.append(str(env_path))

    results = Path(__file__).parent / "results"
    if not results.exists():
        results.mkdir(parents=True, exist_ok=True)
        report.created.append(str(results))

    from harness.check import check_environment

    for check in check_environment().checks:
        if check.state == "ok":
            continue
        message = f"{check.name}: {check.fix or check.detail}"
        if check.state == "degraded":
            report.notes.append(message)
        else:
            report.todo.append(message)

    if not os.getenv("OPENAI_API_KEY", "").strip():
        report.todo.append(
            f"paste a key into {env_path} — this command will not write one for you"
        )
    return report
