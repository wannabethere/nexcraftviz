"""``nexcraftviz`` command line.

Deliberately narrow at this milestone: the commands that need no LLM. They are
what you reach for when debugging a spec that misbehaves in production —
profile the data, validate the spec, apply an edit, render it to a file — and
they double as the manual verification path for the deterministic core.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from nexcraftviz import __version__
from nexcraftviz.data.profile import profile_rows
from nexcraftviz.spec.model import Spec
from nexcraftviz.spec.ops import apply_ops
from nexcraftviz.spec.validate import validate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nexcraftviz",
        description="Deterministic Vega-Lite spec tooling: profile, validate, edit, render.",
    )
    parser.add_argument("--version", action="version", version=f"nexcraftviz {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_profile = sub.add_parser("profile", help="Profile a CSV or JSON data file.")
    p_profile.add_argument("data", help="Path to .csv or .json (array of row objects).")

    p_validate = sub.add_parser("validate", help="Validate a Vega-Lite spec.")
    p_validate.add_argument("spec", help="Path to a spec .json file.")
    p_validate.add_argument(
        "--data", help="Optional .csv/.json to validate field bindings against."
    )
    p_validate.add_argument("--tier", type=int, default=3, choices=(1, 2, 3))
    p_validate.add_argument("--repair", action="store_true", help="Fix what tier 2 can fix.")
    p_validate.add_argument(
        "--strict-schema", action="store_true", help="Treat schema violations as errors."
    )
    p_validate.add_argument("--out", help="Write the (possibly repaired) spec here.")

    p_edit = sub.add_parser("edit", help="Apply operations to a spec.")
    p_edit.add_argument("spec", help="Path to a spec .json file.")
    p_edit.add_argument(
        "ops",
        help="Operations as a JSON array, or @path to read them from a file.",
    )
    p_edit.add_argument("--out", help="Write the edited spec here (default: stdout).")
    p_edit.add_argument("--show-patch", action="store_true", help="Print the change summary.")

    p_render = sub.add_parser("render", help="Render a spec to svg/png/vega/html.")
    p_render.add_argument("spec", help="Path to a spec .json file.")
    p_render.add_argument("out", help="Output path; format inferred from the suffix.")
    p_render.add_argument("--scale", type=float, default=1.0, help="PNG scale factor.")

    p_ops = sub.add_parser("ops", help="List the available operations and their arguments.")
    p_ops.add_argument("--json", action="store_true", help="Emit the JSON schema instead.")

    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "profile":
        return _cmd_profile(args)
    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "edit":
        return _cmd_edit(args)
    if args.command == "render":
        return _cmd_render(args)
    if args.command == "ops":
        return _cmd_ops(args)
    raise ValueError(f"unknown command {args.command!r}")


def _cmd_profile(args: argparse.Namespace) -> int:
    rows = load_rows(args.data)
    profile = profile_rows(rows)
    print(json.dumps(profile.to_prompt_dict(), indent=2, default=str))
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    spec = Spec(load_json(args.spec))
    rows = load_rows(args.data) if args.data else None
    spec, report = validate(
        spec,
        data=rows,
        max_tier=args.tier,
        repair=args.repair,
        strict_schema=args.strict_schema,
    )

    print(report.summary())
    for note in report.repaired:
        print(f"  repaired: {note}")
    for issue in report.errors:
        print(f"  error:   {issue}")
    for issue in report.warnings:
        print(f"  warning: {issue}")

    if args.out:
        Path(args.out).write_text(spec.to_json(indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0 if report.ok else 2


def _cmd_edit(args: argparse.Namespace) -> int:
    spec = Spec(load_json(args.spec))
    raw_ops = args.ops
    ops = load_json(raw_ops[1:]) if raw_ops.startswith("@") else json.loads(raw_ops)

    result = apply_ops(spec, ops)
    for name, reason in result.failed:
        print(f"skipped {name}: {reason}", file=sys.stderr)
    if args.show_patch:
        for line in result.describe():
            print(f"  {line}", file=sys.stderr)
        if not result.changed:
            print("  (no change)", file=sys.stderr)

    payload = result.spec.to_json(indent=2)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(payload)
    return 0 if not result.failed else 2


def _cmd_render(args: argparse.Namespace) -> int:
    from nexcraftviz.render import RenderUnavailable, save

    spec = Spec(load_json(args.spec))
    try:
        target = save(spec, args.out, scale=args.scale)
    except RenderUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    print(f"wrote {target} ({target.stat().st_size} bytes)")
    return 0


def _cmd_ops(args: argparse.Namespace) -> int:
    from nexcraftviz.spec.ops import OP_REGISTRY, OpList

    if args.json:
        print(json.dumps(OpList.model_json_schema(), indent=2))
        return 0

    for name in sorted(OP_REGISTRY):
        cls = OP_REGISTRY[name]
        summary = (cls.__doc__ or "").strip().splitlines()[0] if cls.__doc__ else ""
        print(f"{name}\n    {summary}")
        for field_name, info in cls.model_fields.items():
            if field_name in ("op", "view"):
                continue
            description = info.description or ""
            required = "required" if info.is_required() else "optional"
            print(f"      - {field_name} ({required}): {description}")
    return 0


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_rows(path: str) -> list[dict[str, Any]]:
    """Load row dicts from CSV or JSON.

    CSV values arrive as strings, so numbers are coerced — otherwise every
    column profiles as nominal and the whole point of profiling is lost.
    """
    target = Path(path)
    if target.suffix.lower() in (".csv", ".tsv"):
        delimiter = "\t" if target.suffix.lower() == ".tsv" else ","
        with target.open(newline="", encoding="utf-8") as handle:
            return [
                {key: _coerce(value) for key, value in row.items()}
                for row in csv.DictReader(handle, delimiter=delimiter)
            ]

    data = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        for key in ("rows", "values", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected an array of row objects")
    return [row for row in data if isinstance(row, dict)]


def _coerce(value: str | None) -> Any:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
