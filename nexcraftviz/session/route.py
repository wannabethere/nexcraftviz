"""Which skill should handle this message?

Routing is rules, not a model call. Two reasons: it is free and instant, and it
is the one decision where being *wrong* is cheapest to recover from — the host
can override the route, and a mis-route costs one turn rather than a corrupted
chart.

The rules are ordered by how unambiguous the signal is. "Make it dark" is a
theme instruction whatever else is in the sentence; "what does this show" is a
narration request even though it mentions the chart.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Ordered most-specific first. Each entry is (skill, patterns, reason).
_RULES: list[tuple[str, tuple[str, ...], str]] = [
    (
        "viz.theme",
        (r"\b(dark|light)\s*(mode|theme)\b", r"\btheme\b", r"\bpower\s*bi\b",
         r"\bbrand(ing)?\b", r"\bcolou?r\s*scheme\b",
         # "make it dark" is the phrasing people actually use, and it names no
         # theme word at all. Anchored to a verb so "dark blue bars" — which is
         # an edit to one mark, not a theme — does not match.
         r"\b(make|switch|set|turn|put)\b[^.]{0,14}\b(dark|light)\b",
         r"\bdarker\b", r"\blighter\b"),
        "names a theme",
    ),
    (
        "viz.narrate",
        (r"\b(what|why|how)\b.*\b(show|mean|say|tell)", r"\bexplain\b",
         r"\bdescribe\b", r"\bsummar(ise|ize)\b", r"\binsight", r"\btakeaway"),
        "asks what the chart means",
    ),
    (
        "viz.place",
        (r"\b(wider|narrower|bigger|smaller|resize)\b", r"\bfull[- ]width\b",
         r"\bmove\b", r"\breorder\b", r"\bgroup\b", r"\bungroup\b", r"\bpanel\b",
         r"\blayout\b", r"\bside by side\b", r"\bcolumn\b.*\blayout\b",
         r"\babove\b", r"\bbelow\b", r"\bnext to\b", r"\brearrange\b"),
        "refers to layout or position",
    ),
    (
        "viz.generate",
        (r"^\s*(show|plot|chart|graph|draw|visuali[sz]e)\b", r"\bnew chart\b",
         r"\bstart (over|again)\b"),
        "asks for a new chart",
    ),
    (
        # Last, and deliberately explicit rather than left to the fallthrough
        # below. The default lands on viz.edit either way, but a *recognised*
        # edit and an unrecognised message are different facts — the manager
        # pays a model call for the second and not the first.
        "viz.edit",
        (r"\b(sort|rank|order)\b", r"\btop \d+\b", r"\bbottom \d+\b",
         r"\bfilter\b", r"\bexclude\b", r"\bonly show\b", r"\bremove\b",
         r"\bcolou?r by\b", r"\bbreak ?down by\b", r"\bstack\b",
         r"\bgroup by\b", r"\baxis\b", r"\blog scale\b", r"\blegend\b",
         r"\blabel", r"\btooltip\b", r"\btarget line\b", r"\bthreshold\b",
         r"\baggregate\b", r"\baverage\b", r"\bas a (bar|line|pie|area)\b"),
        "asks for a change to the chart itself",
    ),
]

#: Words that mean the user is editing the chart rather than the layout, even
#: when a layout word appears alongside.
_EDIT_OVERRIDE = re.compile(
    r"\b(sort|rank|filter|top \d+|bottom \d+|colou?r by|break ?down|split by|"
    r"stack|axis|scale|log|target line|threshold|legend|label|tooltip|aggregate)\b",
    re.IGNORECASE,
)


@dataclass
class Route:
    skill: str
    reason: str
    confident: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"skill": self.skill, "reason": self.reason, "confident": self.confident}


def route(message: str, *, has_chart: bool, has_widget: bool) -> Route:
    """Pick a skill for ``message``.

    ``has_chart`` / ``has_widget`` matter more than the wording: an edit
    instruction with nothing to edit is a request to create, and a placement
    instruction with no widget is a chart edit.
    """
    text = f" {message.strip().lower()} "

    if not has_chart and not has_widget:
        return Route("viz.generate", "nothing to edit yet, so this creates a chart")

    for skill, patterns, reason in _RULES:
        if not any(re.search(pattern, text) for pattern in patterns):
            continue
        if skill == "viz.generate" and has_chart and _EDIT_OVERRIDE.search(text):
            # "show the top 10" opens with a create verb but names an edit
            # operation. With a chart already on screen it is a change to that
            # chart, not a request to start again — and starting again would
            # throw away everything the user had already adjusted.
            return Route(
                "viz.edit",
                "opens like a new chart but asks for a change to the current one",
            )
        if skill == "viz.place":
            if not has_widget:
                return Route(
                    "viz.edit",
                    "sounds like layout, but there is no widget — treating it as a chart edit",
                    confident=False,
                )
            if _EDIT_OVERRIDE.search(text):
                return Route(
                    "viz.edit",
                    "mentions layout but asks for a data change",
                    confident=False,
                )
        return Route(skill, reason)

    if has_widget and not has_chart:
        return Route("viz.place", "a widget is in play and nothing else matched")
    return Route("viz.edit", "default: change the current chart")
