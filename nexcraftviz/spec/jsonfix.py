"""Close the brackets a model left open — where the fix is unambiguous.

The generator returns a Vega-Lite document as a JSON *string* inside its JSON
answer, because a Vega-Lite spec is an open-ended object and strict mode only
enforces closed ones. Strict mode then guarantees the outer answer and nothing
inside the string, and a live model writing a nested KPI spec that way dropped
one brace:

    ..."encoding":{"text":{"value":"Total open findings"}}],"title":...
                                                          ^ the layer object
                                                            was never closed

Two shapes are repaired, both with only one possible reading:

* a closer arrives for an OUTER container while an inner one is still open —
  the inner closers go in first;
* the text ends with containers still open — their closers are appended.

Anything else is left exactly as it was: a closer with nothing open to match,
or a string never terminated. Guessing there produces a document that parses
and means something else. The caller still runs every gate on whatever comes
back, and records the repair, so it is never silent.
"""
from __future__ import annotations

_OPENS = {"{": "}", "[": "]"}
_CLOSES = {"}": "{", "]": "["}


def balance_brackets(text: str) -> tuple[str, list[str]]:
    """Return ``(text, notes)`` — the repaired text and one note per insertion.

    No notes means nothing was repaired: either the brackets already balanced,
    or the problem was one this refuses to guess at.
    """
    stack: list[str] = []
    out: list[str] = []
    notes: list[str] = []
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in _OPENS:
            stack.append(char)
        elif char in _CLOSES:
            opener = _CLOSES[char]
            if opener not in stack:
                return text, []  # a closer with nothing to match: not guessable
            while stack[-1] != opener:
                missing = _OPENS[stack.pop()]
                out.append(missing)
                notes.append(f"inserted {missing!r} before character {index}")
            stack.pop()
        out.append(char)

    if in_string:
        return text, []  # an unterminated string: not guessable

    while stack:
        missing = _OPENS[stack.pop()]
        out.append(missing)
        notes.append(f"appended {missing!r} at the end")

    return "".join(out), notes
