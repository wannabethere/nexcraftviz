"""Setting the thing up, running it over scenarios, and saying how it did.

Four verbs, each answering a question you actually ask:

``check``   Can this environment run anything at all?
``setup``   Make it able to, and say what is left for a human.
``run``     Put the pipeline through the scenarios.
``report``  What happened — per stage, not per chart.

Deliberately *not* AntV's harness, which is an automated skill-rewrite loop:
eval, analyze, let a model rewrite the skill document, rebuild the index. Our
prompts are data files, so that loop would be easy to add — the reason to hold
off is that an automated rewrite with nobody reading the diff is the fastest
way to make prompts quietly worse.
"""
from harness.check import Check, check_environment
from harness.report import report_run, summarise
from harness.run import Scenario, ScenarioResult, load_scenarios, run_scenarios
from harness.setup import setup_environment

__all__ = [
    "Check",
    "Scenario",
    "ScenarioResult",
    "check_environment",
    "load_scenarios",
    "report_run",
    "run_scenarios",
    "setup_environment",
    "summarise",
]
