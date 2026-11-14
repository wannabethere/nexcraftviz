"""Rows → a board, deterministically.

The planner decides *that* the answer is a board, and may say which field makes
the lanes and the cards. Everything else is read from the rows here, with no
model: a board only regroups the rows it was given, so there is nothing for a
model to add and a great deal it could invent.

Field choice comes from the profile first — roles and cardinality — and names
only break ties, the same ordering the rich table uses: a column called
``status`` holding 400 free-text values is not a lane, and one called ``stage``
holding five values is.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from nexcraftviz.board.schema import DEFAULT_MAX_CARDS, BoardLayout, BoardSpec, Card, Lane
from nexcraftviz.data.profile import ColumnProfile, DataProfile, profile_rows
from nexcraftviz.table.build import _TONE_BY_VALUE

#: A lane per value stops being a board past this many lanes.
MAX_LANES = 12

_LANE_HINTS = ("status", "stage", "state", "phase", "step", "lane", "pipeline", "column")
_TITLE_HINTS = ("name", "title", "project", "deal", "opportunity", "task", "ticket", "subject",
                "summary", "account", "client", "customer", "candidate", "employee", "person")
_DESCRIPTION_HINTS = ("description", "summary", "notes", "note", "comment", "details", "scope",
                      "objective", "purpose")
_SUBTITLE_HINTS = ("client", "customer", "account", "owner", "assignee", "manager", "lead", "company")
_TAG_HINTS = ("region", "segment", "type", "country", "tier", "priority", "rag", "health",
              "category", "industry", "size", "geo", "market")
_BADGE_HINTS = ("assigned", "team", "resources", "people", "members", "headcount", "count",
                "hours", "value", "amount", "budget", "points", "days")

#: Lane values in the order work usually moves through them. A lane takes the
#: position of the first of these its name contains, so "SOW in progress" sits
#: with the SOW stage, not with delivery. Unmatched lanes follow, largest first.
_PIPELINE_ORDER = (
    "backlog", "new", "open", "to do", "todo", "lead", "prospect", "pre-sales", "presales",
    "pre sales", "estimation", "sow", "proposal", "qualified", "negotiation", "kickoff",
    "kick-off", "planned", "planning", "not started", "ready", "in progress", "in-progress",
    "implementation", "active", "review", "testing", "uat", "blocked", "on hold", "paused",
    "done", "complete", "completed", "won", "closed", "lost", "cancelled", "canceled",
)

#: What the planner may call each part of a board. Channels are free text in a
#: plan, so the common spellings all land.
_CHANNELS = {
    "group_by": ("group", "group_by", "lane", "lanes", "column", "columns", "facet", "status", "x"),
    "title_field": ("title", "card", "card_title", "text", "label", "name", "detail", "y"),
    "subtitle_field": ("subtitle", "card_subtitle"),
    "description_field": ("description", "body", "summary"),
    "tag_fields": ("tag", "tags", "chip", "chips", "tooltip", "color"),
    "badge_field": ("badge", "size", "value", "count"),
}


def layout_hints_from_plan(plan: Any, known: set[str]) -> dict[str, Any]:
    """The planner's encodings, read as board parts. Only real columns count."""
    hints: dict[str, Any] = {"tag_fields": []}
    for enc in getattr(plan, "encodings", None) or []:
        channel = str(getattr(enc, "channel", "") or "").strip().lower()
        field = str(getattr(enc, "field", "") or "").strip()
        if not field or field not in known:
            continue
        for part, names in _CHANNELS.items():
            if channel in names:
                if part == "tag_fields":
                    if field not in hints["tag_fields"]:
                        hints["tag_fields"].append(field)
                else:
                    hints.setdefault(part, field)
                break
    return hints


def build_board(
    rows: list[dict[str, Any]],
    *,
    title: str = "",
    max_cards: int = DEFAULT_MAX_CARDS,
    **hints: Any,
) -> BoardSpec:
    """Group ``rows`` into lanes. ``hints`` are :class:`BoardLayout` fields to honour."""
    profile = profile_rows(rows)
    layout = choose_layout(profile, rows, **hints)
    layout.max_cards = max_cards
    if not layout.group_by:
        return BoardSpec(board=layout, lanes=[], title=title)

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_text(row.get(layout.group_by)) or "—", []).append(row)

    keys = _ordered(list(groups), layout.order, {k: len(v) for k, v in groups.items()})
    if not layout.order:
        layout.order = keys
    if not layout.tones:
        layout.tones = {k: _TONE_BY_VALUE[k.lower()] for k in keys if k.lower() in _TONE_BY_VALUE}

    lanes = [
        Lane(
            key=key,
            count=len(groups[key]),
            tone=layout.tones.get(key),
            cards=[_card(row, layout) for row in groups[key][:layout.max_cards]],
        )
        for key in keys
    ]
    return BoardSpec(board=layout, lanes=lanes, title=title)


def build_board_spec(rows: list[dict[str, Any]], **kwargs: Any):
    """:func:`build_board`, as a :class:`~nexcraftviz.spec.model.Spec`."""
    return build_board(rows, **kwargs).to_spec()


def choose_layout(profile: DataProfile, rows: list[dict[str, Any]], **hints: Any) -> BoardLayout:
    known = set(profile.column_names)
    given = {k: v for k, v in hints.items() if v}
    for key in ("group_by", "title_field", "subtitle_field", "description_field", "badge_field"):
        if key in given and given[key] not in known:
            given.pop(key)
    if "tag_fields" in given:
        given["tag_fields"] = [f for f in given["tag_fields"] if f in known]

    used: set[str] = set()

    def take(name: str) -> str:
        if name:
            used.add(name)
        return name

    strings = [c for c in profile.columns if c.python_type == "str" and not _is_id(c.name)]
    group_by = take(given.get("group_by") or _pick_lane(profile))
    title_field = take(given.get("title_field") or _pick(strings, used, _TITLE_HINTS, prefer_unique=True))
    description_field = take(given.get("description_field") or _pick_long_text(strings, used, rows))
    subtitle_field = take(given.get("subtitle_field") or _pick(strings, used, _SUBTITLE_HINTS,
                                                                require_hint=True))
    # A field shows once on a card: a tag that is already the lane, the title
    # or the subtitle would only repeat it (seen live: the plan named
    # customer_type as both subtitle and tag).
    tags = [t for t in (given.get("tag_fields") or _pick_tags(strings, used)) if t not in used]
    used.update(tags)
    badge_field = take(given.get("badge_field") or _pick_badge(profile, used))

    return BoardLayout(
        group_by=group_by,
        title_field=title_field,
        subtitle_field=subtitle_field,
        description_field=description_field,
        tag_fields=list(tags)[:3],
        badge_field=badge_field,
        badge_label=given.get("badge_label") or _badge_label(badge_field),
        order=list(given.get("order") or []),
        tones=dict(given.get("tones") or {}),
    )


# ---------------------------------------------------------------------------
# choosing fields
# ---------------------------------------------------------------------------

def _is_id(name: str) -> bool:
    lowered = name.lower()
    return lowered in {"id", "uuid", "guid", "url", "href"} or lowered.endswith(("_id", "_uuid"))


def _hinted(name: str, hints: tuple[str, ...]) -> int:
    """How early a name matches the hints; len(hints) when it does not."""
    lowered = name.lower()
    for i, hint in enumerate(hints):
        if hint in lowered:
            return i
    return len(hints)


def _pick_lane(profile: DataProfile) -> str:
    candidates = [
        c for c in profile.columns
        if c.python_type == "str" and not _is_id(c.name) and 2 <= c.distinct <= MAX_LANES
        # Every value different usually means a name, not a lane -- unless the
        # column says it is a status: six projects in six statuses is a board.
        and not (c.is_unique and profile.row_count > 2 and _hinted(c.name, _LANE_HINTS) == len(_LANE_HINTS))
    ]
    if not candidates:
        return ""
    candidates.sort(key=lambda c: (_hinted(c.name, _LANE_HINTS), c.distinct))
    return candidates[0].name


def _pick(columns: list[ColumnProfile], used: set[str], hints: tuple[str, ...], *,
          prefer_unique: bool = False, require_hint: bool = False) -> str:
    free = [c for c in columns if c.name not in used]
    if require_hint:
        free = [c for c in free if _hinted(c.name, hints) < len(hints)]
    if not free:
        return ""
    free.sort(key=lambda c: (_hinted(c.name, hints), -c.distinct if prefer_unique else 0))
    return free[0].name


def _pick_long_text(columns: list[ColumnProfile], used: set[str], rows: list[dict[str, Any]]) -> str:
    best, best_len = "", 0.0
    for col in columns:
        if col.name in used:
            continue
        values = [str(r.get(col.name)) for r in rows if r.get(col.name) not in (None, "")]
        mean = sum(map(len, values)) / len(values) if values else 0
        hinted = _hinted(col.name, _DESCRIPTION_HINTS) < len(_DESCRIPTION_HINTS)
        if (mean >= 40 or (hinted and mean >= 12)) and mean > best_len:
            best, best_len = col.name, mean
    return best


def _pick_tags(columns: list[ColumnProfile], used: set[str]) -> list[str]:
    free = [c for c in columns if c.name not in used and 1 <= c.distinct <= 15 and not c.is_unique]
    free.sort(key=lambda c: (_hinted(c.name, _TAG_HINTS), c.distinct))
    return [c.name for c in free[:2]]


def _pick_badge(profile: DataProfile, used: set[str]) -> str:
    measures = [c for c in profile.measures if c.name not in used and not _is_id(c.name)]
    if not measures:
        return ""
    measures.sort(key=lambda c: _hinted(c.name, _BADGE_HINTS))
    return measures[0].name


#: A figure that is a value in itself: "48,000", not "48000 amount".
_UNLABELLED = ("amount", "value", "revenue", "price", "cost", "budget", "arr", "mrr", "total")


def _badge_label(field: str) -> str:
    if not field or _hinted(field, _UNLABELLED) < len(_UNLABELLED):
        return ""
    words = re.sub(r"[_\-]+", " ", field).strip().lower()
    words = re.sub(r"\b(count|num|number of|total|n)\b", "", words).strip()
    return words or field


# ---------------------------------------------------------------------------
# lanes and cards
# ---------------------------------------------------------------------------

def _ordered(keys: list[str], given: list[str], counts: dict[str, int]) -> list[str]:
    if given:
        listed = [k for k in given if k in counts]
        rest = sorted((k for k in keys if k not in listed), key=lambda k: -counts[k])
        return listed + rest
    ranked = [(k, _hinted(k, _PIPELINE_ORDER)) for k in keys]
    if all(rank == len(_PIPELINE_ORDER) for _, rank in ranked):
        return sorted(keys, key=lambda k: -counts[k])
    return [k for k, _ in sorted(ranked, key=lambda kr: (kr[1], -counts[kr[0]]))]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    if isinstance(value, (list, dict)):
        return ""
    return str(value).strip()


def _card(row: dict[str, Any], layout: BoardLayout) -> Card:
    raw = row.get(layout.badge_field) if layout.badge_field else None
    badge = f"{raw:,.0f}" if isinstance(raw, (int, float)) and abs(raw) >= 1000 else _text(raw)
    if badge and layout.badge_label:
        badge = f"{badge} {layout.badge_label}"
    return Card(
        title=_text(row.get(layout.title_field)) if layout.title_field else "",
        subtitle=_text(row.get(layout.subtitle_field)) if layout.subtitle_field else "",
        description=_text(row.get(layout.description_field)) if layout.description_field else "",
        tags=[t for t in (_text(row.get(f)) for f in layout.tag_fields) if t],
        badge=badge,
        row={k: v for k, v in row.items() if not isinstance(v, (list, dict))},
    )
