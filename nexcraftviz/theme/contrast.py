"""WCAG contrast auditing for themes.

A palette that looks fine to whoever picked it can be unreadable for a real
share of the audience. This module makes that measurable rather than a matter
of taste, so ``viz.theme`` can warn instead of silently shipping an unreadable
chart.

Two thresholds from WCAG 2.1 apply here:

* **1.4.3 Contrast (Minimum)** — 4.5:1 for body text, 3:1 for large text
  (≥18.66px, or ≥14px bold).
* **1.4.11 Non-text Contrast** — 3:1 for graphical objects. Chart marks are
  graphical objects, so a categorical colour needs 3:1 against the plot
  background, not 4.5:1.

Categorical palettes get a third check that WCAG does not cover: adjacent
series must be distinguishable *from each other*, or a stacked bar becomes one
smear. That uses CIE76 ΔE, which is crude next to CIEDE2000 but needs no
dependency and is more than good enough to flag "these two are the same blue".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from nexcraftviz.theme.tokens import ThemeTokens

#: WCAG 2.1 minimums.
AA_TEXT = 4.5
AA_LARGE_TEXT = 3.0
AA_NON_TEXT = 3.0

#: Below this CIE76 ΔE two categorical colours read as the same colour.
MIN_DELTA_E = 15.0

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


@dataclass
class ContrastIssue:
    kind: str  # "text" | "non_text" | "similarity"
    message: str
    ratio: float
    required: float
    foreground: str = ""
    background: str = ""

    def __str__(self) -> str:
        return f"[{self.kind}] {self.message} ({self.ratio:.2f} < {self.required:.1f})"


@dataclass
class ContrastReport:
    theme: str
    issues: list[ContrastIssue] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def summary(self) -> str:
        if self.ok:
            unchecked = f", {len(self.unchecked)} colour(s) unchecked" if self.unchecked else ""
            return f"{self.theme}: passes WCAG AA{unchecked}"
        return f"{self.theme}: {len(self.issues)} contrast issue(s)"


# ---------------------------------------------------------------------------
# colour maths
# ---------------------------------------------------------------------------

def parse_hex(colour: str) -> tuple[float, float, float] | None:
    """``#rgb`` / ``#rrggbb`` / ``#rrggbbaa`` → 0-1 RGB. None if unparseable.

    Named colours and ``transparent`` return None rather than raising —
    a theme may legitimately use them, and an unparseable colour is reported as
    unchecked rather than as a failure.
    """
    if not isinstance(colour, str) or not _HEX.match(colour.strip()):
        return None
    value = colour.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def relative_luminance(colour: str) -> float | None:
    """WCAG relative luminance."""
    rgb = parse_hex(colour)
    if rgb is None:
        return None
    channels = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(foreground: str, background: str) -> float | None:
    """WCAG contrast ratio, 1.0–21.0. None if either colour is unparseable."""
    lum_a = relative_luminance(foreground)
    lum_b = relative_luminance(background)
    if lum_a is None or lum_b is None:
        return None
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def delta_e(a: str, b: str) -> float | None:
    """CIE76 colour difference — how distinguishable two colours are."""
    lab_a, lab_b = _to_lab(a), _to_lab(b)
    if lab_a is None or lab_b is None:
        return None
    return sum((x - y) ** 2 for x, y in zip(lab_a, lab_b, strict=True)) ** 0.5


def _to_lab(colour: str) -> tuple[float, float, float] | None:
    rgb = parse_hex(colour)
    if rgb is None:
        return None
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    r, g, b = linear
    # sRGB → XYZ (D65)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t) + (16 / 116)

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

def audit(theme: ThemeTokens, *, check_similarity: bool = True) -> ContrastReport:
    """Check a theme's colours against its own surfaces."""
    report = ContrastReport(theme=theme.name)
    surface = theme.surfaces.surface
    background = theme.surfaces.background

    text_checks = [
        ("text.primary", theme.text.primary, surface, AA_TEXT),
        ("text.secondary", theme.text.secondary, surface, AA_TEXT),
        # Muted text is used for units and axis furniture at display sizes, so
        # the large-text threshold is the honest one to hold it to.
        ("text.muted", theme.text.muted, surface, AA_LARGE_TEXT),
    ]
    for label, colour, against, required in text_checks:
        _check(report, "text", label, colour, against, required)

    # positive / negative / warning colour *text* — a KPI delta reads "+12.4%"
    # in green — so they are held to the text threshold. `accent` is a mark and
    # fill colour (chart marks, progress fills, avatar chips), which WCAG treats
    # as a graphical object at 3:1. Holding it to 4.5:1 would reject perfectly
    # readable brand colours for a rule that does not apply to them.
    for label in ("positive", "negative", "warning"):
        _check(
            report, "text", f"semantic.{label}",
            getattr(theme.semantic, label), surface, AA_TEXT,
        )
    _check(report, "non_text", "semantic.accent", theme.semantic.accent, surface, AA_NON_TEXT)

    for index, colour in enumerate(theme.palette.categorical):
        _check(
            report, "non_text", f"palette.categorical[{index}]", colour, background, AA_NON_TEXT
        )

    if check_similarity:
        report.issues.extend(_similarity_issues(theme.palette.categorical))

    return report


def _check(
    report: ContrastReport,
    kind: str,
    label: str,
    foreground: str,
    background: str,
    required: float,
) -> None:
    ratio = contrast_ratio(foreground, background)
    if ratio is None:
        report.unchecked.append(f"{label}={foreground}")
        return
    if ratio < required:
        report.issues.append(
            ContrastIssue(
                kind=kind,
                message=f"{label} ({foreground}) on {background}",
                ratio=ratio,
                required=required,
                foreground=foreground,
                background=background,
            )
        )


def _similarity_issues(categorical: list[str]) -> list[ContrastIssue]:
    """Flag adjacent categorical colours that are too close to tell apart.

    Only adjacent pairs: a 12-colour palette will always contain some distant
    pair that happens to be similar, and complaining about entry 2 vs entry 9 is
    noise. Neighbours are what land next to each other in a stacked bar.
    """
    issues: list[ContrastIssue] = []
    for index in range(len(categorical) - 1):
        first, second = categorical[index], categorical[index + 1]
        distance = delta_e(first, second)
        if distance is None or distance >= MIN_DELTA_E:
            continue
        issues.append(
            ContrastIssue(
                kind="similarity",
                message=(
                    f"categorical[{index}] ({first}) and [{index + 1}] ({second}) "
                    "are hard to tell apart"
                ),
                ratio=distance,
                required=MIN_DELTA_E,
                foreground=first,
                background=second,
            )
        )
    return issues
