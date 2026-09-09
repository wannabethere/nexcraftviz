"""The manager — one instruction, routed to the actions that carry it out.

`nexcraftviz.session.route` answers "which single skill?". That is the right
question for a chat turn and the wrong one for an instruction box, where "make
it dark and sort descending" is two things and dropping either is a bug the user
can see.

So: split on conjunctions, label each clause with the rules that already exist,
ask a model only about what the rules could not read, and run the steps in order
against one session so undo still works change by change.
"""
from nexcraftviz.manager.decision import ACTIONS, Action, ManagerDecision, ManagerStep
from nexcraftviz.manager.run import ManagerRun, StepOutcome, decide, run_instruction
from nexcraftviz.manager.split import LabelledClause, label_all, split_clauses

# ``viz.manage`` itself lives in :mod:`nexcraftviz.skills.manage`, with the
# other skills — importing it here would close a cycle, and a skill belongs in
# the skill registry rather than in the package that consumes it.

__all__ = [
    "ACTIONS",
    "Action",
    "LabelledClause",
    "ManagerDecision",
    "ManagerRun",
    "ManagerStep",
    "StepOutcome",
    "decide",
    "label_all",
    "run_instruction",
    "split_clauses",
]
