"""Close the brackets a model left open — where the fix is unambiguous.

The generator returns a Vega-Lite document as a JSON *string* inside its JSON
answer, because a Vega-Lite spec is an open-ended object and strict mode only
enforces closed ones. Strict mode then guarantees the outer answer and nothing
inside the string, and a live model writing a nested KPI spec that way dropped
one brace:

    ..."encoding":{"text":{"value":"Total open findings"}}],"title":...
                                                          ^ the layer object
                                                            was never closed

Three shapes are repaired, each with only one possible reading:

* a closer arrives for an OUTER container while an inner one is still open —
  the inner closers go in first;
* a new array item starts where an object key belongs — `...}}},{"mark":` in a
  layer whose first view was never closed, seen live three times in one run —
  so the object is closed before the comma;
* the text ends with containers still open — their closers are appended.

And one character-level repair, :func:`restore_punctuation`: a dash or curly
quote that arrived as a control character, its high byte dropped.

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
            last = _last_significant(out)
            if stack and stack[-1] == "{" and last >= 0 and out[last] == ",":
                # A value where a key belongs. Inside an array that has one
                # reading: the object before the comma ended, and this is the
                # next item. Anywhere else it has none, so refuse.
                if len(stack) < 2 or stack[-2] != "[":
                    return text, []
                out.insert(last, "}")
                stack.pop()
                notes.append(
                    f"inserted '}}' before character {index}: a new array item "
                    f"began before the previous object was closed"
                )
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


def _last_significant(out: list[str]) -> int:
    """Index of the last non-whitespace character written, or -1."""
    for position in range(len(out) - 1, -1, -1):
        if not out[position].isspace():
            return position
    return -1


#: U+20xx punctuation that arrived with its high byte dropped — seen live as
#: "Jan\x13Apr" for "Jan\u2013Apr", in a title the plan had written. Each is a
#: C0 control character, which JSON forbids inside a string, so the text could
#: not have meant it.
_DROPPED_HIGH_BYTE = {
    0x13: "\u2013", 0x14: "\u2014", 0x18: "\u2018",
    0x19: "\u2019", 0x1C: "\u201c", 0x1D: "\u201d",
}


def restore_punctuation(text: str) -> tuple[str, list[str]]:
    """Return ``(text, notes)`` with dropped-high-byte punctuation restored."""
    found = sorted({char for char in text if ord(char) in _DROPPED_HIGH_BYTE})
    if not found:
        return text, []
    notes = [
        f"restored {_DROPPED_HIGH_BYTE[ord(char)]!r} from control character "
        f"U+{ord(char):04X}"
        for char in found
    ]
    return text.translate(_DROPPED_HIGH_BYTE), notes


def repair_json(text: str) -> tuple[str, list[str]]:
    """Every repair here, in order. No notes means nothing was changed."""
    text, notes = restore_punctuation(text)
    balanced, more = balance_brackets(text)
    return balanced, notes + more
