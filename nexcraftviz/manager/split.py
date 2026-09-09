"""Split an instruction into clauses and label the ones the rules recognise.

Runs before the model, for the same reason the intent extractor does: most
annotate messages are one plain clause ("make it dark", "sort descending"), and
spending a model call to discover that is both waste and *variance* — the same
phrasing should route the same way every time.

Two jobs, deliberately separate:

* :func:`split_clauses` — cut on conjunctions. Purely mechanical.
* :func:`label` — ask the existing router what each clause is. Free, and it
  reuses the rules that already work rather than growing a second table that
  will drift from the first.

Conservative throughout. A clause the rules cannot label confidently is handed
to the model rather than guessed at: a wrong label sends the whole clause to the
wrong skill, which is worse than paying for one call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from nexcraftviz.manager.decision import Action

#: Conjunctions that join two instructions. Only split on these when both sides
#: still look like instructions — "sort by region and revenue" is one clause
#: naming two columns, not two clauses.
_SPLIT = re.compile(
    r"\s*(?:,\s*(?:and|then|also)\s+|\s+and\s+then\s+|\s+then\s+|\s+also\s+|;\s*|\s+and\s+)",
    re.IGNORECASE,
)

#: A clause shorter than this is a fragment ("and blue"), not an instruction.
_MIN_CLAUSE_WORDS = 2

#: Things nexcraftviz cannot do, because they need a query it cannot run. Each
#: is paired with the reason a user should see.
_OUT_OF_SCOPE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdrill\s?down\b|\bbreak (?:it )?down by\b|\bsplit (?:it )?by\b"
                r"|\bexpand\b|\bgo deeper\b", re.I),
     "that needs a different query — this chart only has the rows it was given"),
    (re.compile(r"\badd (?:a )?(?:column|field|measure|metric)\b"
                r"|\bbring in\b|\bjoin\b|\binclude .* from\b", re.I),
     "that needs data this result set does not contain"),
    (re.compile(r"\blast (?:year|quarter|month|week)\b|\bcompare (?:to|with|against) "
                r"(?:last|previous|prior)\b|\byear[- ]over[- ]year\b", re.I),
     "that needs a period this result set does not cover"),
    (re.compile(r"\bexport\b|\bdownload\b|\bsend (?:it )?to\b|\bemail\b|\bschedule\b", re.I),
     "nexcraftviz builds charts; delivery is the host application's job"),
)

#: Router skill → manager action. `viz.generate` means "start over", which at
#: this surface is a full re-plan rather than a bare generate call.
_ACTION_FOR: dict[str, Action] = {
    "viz.edit": "edit",
    "viz.theme": "theme",
    "viz.narrate": "narrate",
    "viz.place": "place",
    "viz.generate": "recreate",
}


@dataclass
class LabelledClause:
    """One clause and what the rules made of it."""

    text: str
    action: Action | None = None
    why: str = ""
    #: False when the rules matched nothing specific and fell through to their
    #: default. The model decides those.
    confident: bool = False

    @property
    def needs_model(self) -> bool:
        return self.action is None or not self.confident


def split_clauses(message: str) -> list[str]:
    """Cut an instruction on conjunctions. No model, no network."""
    text = (message or "").strip()
    if not text:
        return []

    parts = [part.strip(" ,;.") for part in _SPLIT.split(text)]
    clauses = [p for p in parts if len(p.split()) >= _MIN_CLAUSE_WORDS]

    # A split that produced only fragments was a bad split — "sort by region and
    # revenue" is one instruction. Fall back to the whole message rather than
    # acting on half of it.
    if len(clauses) < 2:
        return [text]
    return clauses


def label(
    clause: str, *, has_chart: bool, has_widget: bool
) -> LabelledClause:
    """Ask the existing router what this clause is.

    Reuses `session.route` rather than growing a parallel rules table. Two
    tables of regexes for the same job drift, and then the conversation and the
    annotate box disagree about what "make it wider" means.
    """
    # Imported here, not at module scope: `skills` imports the manager, and
    # `session` imports `skills`, so a module-level import closes the loop.
    from nexcraftviz.session.route import route

    text = (clause or "").strip()
    if not text:
        return LabelledClause(text=text)

    for pattern, reason in _OUT_OF_SCOPE:
        if pattern.search(text):
            return LabelledClause(text=text, action="decline", why=reason, confident=True)

    decision = route(text, has_chart=has_chart, has_widget=has_widget)
    action = _ACTION_FOR.get(decision.skill)
    if action is None:
        return LabelledClause(text=text)

    # The router's "default: change the current chart" is a fallthrough, not a
    # reading of the text. Treat it as unlabelled so the model gets a look.
    fell_through = decision.reason.startswith("default:") or not decision.confident
    return LabelledClause(
        text=text,
        action=action,
        why=decision.reason,
        confident=not fell_through,
    )


def label_all(
    message: str, *, has_chart: bool, has_widget: bool
) -> list[LabelledClause]:
    return [
        label(clause, has_chart=has_chart, has_widget=has_widget)
        for clause in split_clauses(message)
    ]
