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
import os
import sys
from pathlib import Path
from typing import Any

from nexcraftviz import __version__, env
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
    parser.add_argument(
        "--no-dotenv",
        action="store_true",
        help="Do not read a .env file; use the environment as-is.",
    )
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

    p_table = sub.add_parser("table", help="Build a rich table from a data file.")
    p_table.add_argument("data", help="Path to .csv or .json.")
    p_table.add_argument("--out", help="Write HTML here (default: the spec, to stdout).")
    p_table.add_argument("--title", default="", help="Table title.")

    p_recommend = sub.add_parser("recommend", help="Rank chart types for a data file.")
    p_recommend.add_argument("data", help="Path to .csv or .json.")
    p_recommend.add_argument(
        "question", nargs="?", default="", help="Nudge the ranking and narrow the columns."
    )

    p_gallery = sub.add_parser("gallery", help="Generate the playground pages.")
    p_gallery.add_argument("--out", default="playground", help="Output directory.")
    p_gallery.add_argument("--limit", type=int, help="Only this many corpus pairs.")

    p_serve = sub.add_parser("serve", help="Run the HTTP API and serve the embed.")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8180)
    p_serve.add_argument("--model", default="", help="Override OPENAI_MODEL.")
    p_serve.add_argument(
        "--no-model",
        action="store_true",
        help="Start without a provider even if one is configured (propose/commit only).",
    )

    p_eval = sub.add_parser("eval", help="Run the prompt evals against a real model.")
    p_eval.add_argument("--skill", default="", help="Only this skill.")
    p_eval.add_argument("--model", default="", help="Override OPENAI_MODEL.")
    p_eval.add_argument("--json", action="store_true")
    p_eval.add_argument(
        "--no-save", action="store_true",
        help="Do not write eval-*.json to harness/results (written by default).",
    )

    p_harness = sub.add_parser(
        "harness", help="Set up, check, run and report on the chart pipeline."
    )
    harness_sub = p_harness.add_subparsers(dest="harness_command", required=True)
    harness_sub.add_parser(
        "check", help="Report every prerequisite and whether it is satisfied."
    )
    h_setup = harness_sub.add_parser(
        "setup", help="Scaffold .env and directories; print what is left to do."
    )
    h_setup.add_argument("--root", help="Where to write .env (default: the current directory).")
    h_run = harness_sub.add_parser("run", help="Run the pipeline over the scenarios.")
    h_run.add_argument("--scenarios", help="A scenario directory (default: harness/scenarios).")
    h_run.add_argument(
        "--live", action="store_true",
        help="Use a real model. Without this the run is offline and only the "
             "deterministic half is measured.",
    )
    h_run.add_argument(
        "--critic", action="store_true",
        help="Also ask the LLM critic. Implies --live.",
    )
    h_run.add_argument("--only", help="Run one scenario by name.")
    h_run.add_argument("-v", "--verbose", action="store_true", help="Print each run's trace.")
    h_run.add_argument("--json", action="store_true", help="Print the summary as JSON.")
    h_report = harness_sub.add_parser("report", help="Re-print the last run, or compare two.")
    h_report.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    h_report.add_argument("--json", action="store_true")
    h_report.add_argument(
        "--html", metavar="PATH",
        help="Write one page with every question, answer and chart from the latest "
             "eval run and harness run.",
    )

    p_corpus = sub.add_parser(
        "corpus", help="Inspect the corpus, or index it for semantic retrieval."
    )
    corpus_sub = p_corpus.add_subparsers(dest="corpus_command", required=True)
    corpus_sub.add_parser("report", help="Coverage and validation status.")
    c_index = corpus_sub.add_parser(
        "index", help="Embed the corpus into the vector store (needs the retrieval extra)."
    )
    c_index.add_argument(
        "--recreate", action="store_true",
        help="Recreate the collection. Needed the first time and after a corpus change "
             "that alters the embedding dimensions.",
    )
    corpus_sub.add_parser("retrieval", help="What retrieval is configured to do.")

    sub.add_parser("config", help="Show the resolved configuration and where it came from.")

    sub.add_parser("mcp", help="Run the MCP server over stdio.")

    p_tools = sub.add_parser("tools", help="Print the agent tool schemas.")
    p_tools.add_argument("--style", default="openai", choices=("openai", "anthropic", "mcp"))
    p_tools.add_argument("--ops", action="store_true", help="Print the operation schemas too.")

    p_theme = sub.add_parser("theme", help="List, inspect, audit and apply themes.")
    theme_sub = p_theme.add_subparsers(dest="theme_command", required=True)

    theme_sub.add_parser("list", help="List the available themes.")

    t_show = theme_sub.add_parser("show", help="Print a theme's Vega config.")
    t_show.add_argument("name")

    t_css = theme_sub.add_parser("css", help="Print the CSS bundle for a theme pair.")
    t_css.add_argument("--light", default="nexcraftviz-light")
    t_css.add_argument("--dark", default="nexcraftviz-dark")
    t_css.add_argument("--out", help="Write here instead of stdout.")

    t_audit = theme_sub.add_parser("audit", help="Check a theme against WCAG AA.")
    t_audit.add_argument("name", nargs="?", help="Omit to audit every theme.")

    t_apply = theme_sub.add_parser("apply", help="Apply a theme to a spec.")
    t_apply.add_argument("name")
    t_apply.add_argument("spec")
    t_apply.add_argument("--out", help="Write the themed spec here (default: stdout).")
    t_apply.add_argument(
        "--keep-colours",
        action="store_true",
        help="Keep hard-coded mark colours. By default they are stripped, since "
             "they override config and make the theme appear to do nothing.",
    )

    args = parser.parse_args(argv)
    if not args.no_dotenv:
        # A .env in this directory or a parent. Never overrides what is
        # already exported.
        env.load()
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
    if args.command == "theme":
        return _cmd_theme(args)
    if args.command == "table":
        return _cmd_table(args)
    if args.command == "recommend":
        return _cmd_recommend(args)
    if args.command == "gallery":
        return _cmd_gallery(args)
    if args.command == "serve":
        return _cmd_serve(args)
    if args.command == "eval":
        return _cmd_eval(args)
    if args.command == "corpus":
        return _cmd_corpus(args)
    if args.command == "harness":
        return _cmd_harness(args)
    if args.command == "config":
        return _cmd_config(args)
    if args.command == "mcp":
        return _cmd_mcp(args)
    if args.command == "tools":
        return _cmd_tools(args)
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


def _cmd_table(args: argparse.Namespace) -> int:
    """Build a rich table from rows — the "render the table" half, with no LLM."""
    from nexcraftviz.render.html import render_table
    from nexcraftviz.table import build_table

    table = build_table(load_rows(args.data), title=args.title)
    if not table.columns:
        print("no rows to render", file=sys.stderr)
        return 2

    if args.out:
        Path(args.out).write_text(_table_page(table, render_table(table)), encoding="utf-8")
        print(f"wrote {args.out}")
        return 0

    print(table.to_spec().to_json(indent=2))
    return 0


def _table_page(table: Any, body: str) -> str:
    """A standalone page. The stylesheet is inlined so the file is portable."""
    from nexcraftviz.theme import load as load_theme
    from nexcraftviz.theme import to_bundle

    css = to_bundle(load_theme("nexcraftviz-light"), load_theme("nexcraftviz-dark"))
    title = table.title or "Table"
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{title}</title><style>{css}\n"
        "body{background:var(--nxv-background);color:var(--nxv-text);"
        "font-family:var(--nxv-font);margin:0;padding:24px}</style></head>"
        f'<body><div class="nxv-card"><div class="nxv-card__header">'
        f'<h3 class="nxv-card__title">{title}</h3></div>'
        f'<div class="nxv-card__body">{body}</div></div></body></html>'
    )


def _cmd_recommend(args: argparse.Namespace) -> int:
    from nexcraftviz.recommend.precedent import precedents
    from nexcraftviz.recommend.retrieval import describe as describe_retrieval
    from nexcraftviz.recommend.rules import recommend

    profile = profile_rows(load_rows(args.data))
    result = recommend(profile, question=args.question)
    print(f"shape: {result.shape_signature}")
    axis = profile.time_axis
    print(f"time axis: {axis.name if axis else '(none)'}")

    print("\nfrom the shape alone (rules):")
    for entry in result:
        print(f"  {entry.chart_type:<18} {entry.score:.2f}  {entry.reason}")

    # The rules say what the columns permit. The corpus says what was worth
    # drawing, which is the question the shape cannot answer on its own.
    found = precedents(profile, question=args.question)
    backend = describe_retrieval()
    print(f"\nfrom the corpus ({found.considered} pairs fit this shape, "
          f"matched by {backend['effective']}):")
    for entry in found:
        print(f"  {entry.chart_type:<18} {entry.score:.2f}  {entry.why}")
        if entry.caution:
            print(f"  {'':<18}       caution: {entry.caution}")
    return 0


def _cmd_gallery(args: argparse.Namespace) -> int:
    from nexcraftviz.gallery import write

    for path in write(args.out, limit=args.limit):
        print(f"wrote {path} ({path.stat().st_size} bytes)")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    """Run the API.

    Starts without a model configured: propose/commit work fully, and /turn
    answers 503 with an actionable message rather than pretending.
    """
    try:
        import uvicorn
    except ImportError:
        print("serving needs the `app` extra: pip install 'nexcraftviz[app]'", file=sys.stderr)
        return 3

    from nexcraftviz.app.api import create_app
    from nexcraftviz.integrations.providers import DEFAULT_OPENAI_MODEL, default_runner

    llm = None if args.no_model else default_runner(model=args.model or None)
    model = args.model or os.getenv("OPENAI_MODEL", "") or DEFAULT_OPENAI_MODEL

    from nexcraftviz.app.api import PLAYGROUND_DIR

    print(f"nexcraftviz on http://{args.host}:{args.port}")
    if os.path.isdir(PLAYGROUND_DIR):
        print(f"  demo:   http://{args.host}:{args.port}/playground/")
    print(f"  embed:  http://{args.host}:{args.port}/embed/nexcraftviz.js")
    print(f"  tools:  http://{args.host}:{args.port}/v1/tools")
    token_set = bool(os.getenv("NEXCRAFTVIZ_API_TOKEN", "").strip())
    if token_set:
        print("  auth:   token required on /v1/* (health excepted)")
    else:
        print("  auth:   OPEN — anyone who can reach this port can use it")
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            print(
                f"\nWARNING: listening on {args.host} with no NEXCRAFTVIZ_API_TOKEN set.\n"
                "         Set one before sharing this server:\n"
                "           export NEXCRAFTVIZ_API_TOKEN=$(python3 -c "
                "'import secrets; print(secrets.token_urlsafe(32))')\n",
                file=sys.stderr,
            )
    if llm is None:
        print(f"  model:  none ({env.describe_provider()})")
        print("          /propose and /commit work; /turn returns 503")
    else:
        print(f"  model:  {model}")
    uvicorn.run(create_app(llm=llm), host=args.host, port=args.port, log_level="warning")
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    """Say what is configured, so a missing key is obvious before a 401 is."""
    from nexcraftviz.render import available as render_available
    from nexcraftviz.theme import available_themes

    files = [] if args.no_dotenv else env.find_env_files()
    print(f"provider:   {env.describe_provider()}")
    print(f"model var:  OPENAI_MODEL={os.getenv('OPENAI_MODEL', '') or '(unset)'}")
    print(f".env files: {', '.join(str(f) for f in files) or '(none found)'}")
    try:
        import dotenv  # noqa: F401
    except ImportError:
        print("            python-dotenv not installed — .env files are ignored")
    print(f"rendering:  {'available' if render_available() else 'needs the render extra'}")
    print(f"themes:     {', '.join(available_themes())}")
    print(f"prompts:    {os.getenv('NEXCRAFTVIZ_PROMPT_DIR', '') or '(bundled)'}")
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    from nexcraftviz.evals.runner import main as eval_main

    argv = []
    if args.skill:
        argv += ["--skill", args.skill]
    if args.model:
        argv += ["--model", args.model]
    if args.json:
        argv += ["--json"]
    if not args.no_save:
        try:
            from harness.run import RESULTS_DIR as results_dir
        except ImportError:  # an installed package ships no harness directory
            results_dir = None
        if results_dir is not None:
            argv += ["--save", str(results_dir)]
    return eval_main(argv)


def _cmd_corpus(args: argparse.Namespace) -> int:
    from nexcraftviz.recommend import retrieval

    if args.corpus_command == "report":
        from nexcraftviz.corpus.report import report

        return report()

    if args.corpus_command == "retrieval":
        state = retrieval.describe()
        print(f"backend:    {state['backend']}")
        print(f"effective:  {state['effective']}")
        print(f"collection: {state['collection']}")
        if state["embed_model"]:
            print(f"embeddings: {state['embed_model']}")
        for reason in state["reasons"]:
            print(f"  - {reason}")
        if state["effective"] == "lexical" and state["backend"] == "lexical":
            print("\nLexical matching is the default and needs nothing. To use "
                  "embeddings:\n  export NEXCRAFTVIZ_RETRIEVAL=qdrant  QDRANT_URL=...")
        return 0

    if args.corpus_command == "index":
        import asyncio

        try:
            result = asyncio.run(retrieval.index_corpus(recreate=args.recreate))
        except retrieval.RetrievalError as exc:
            print(f"cannot index: {exc}", file=sys.stderr)
            return 1
        print(f"indexed {result['indexed']} pairs into {result['collection']!r} "
              f"({result['dimensions']}d, {result['model']})")
        return 0

    raise ValueError(f"unknown corpus command {args.corpus_command!r}")


def _cmd_harness(args: argparse.Namespace) -> int:
    """check / setup / run / report.

    Imported here rather than at module scope: the harness pulls in the whole
    agent layer, and `nexcraftviz validate` should not pay for that.
    """
    try:
        from harness import check_environment, load_scenarios, report_run, setup_environment
        from harness.report import compare
        from harness.run import RESULTS_DIR, latest_result, run_sync, save_results
    except ImportError as exc:  # pragma: no cover — only when installed without it
        print(f"the harness is not available in this install: {exc}", file=sys.stderr)
        return 1

    if args.harness_command == "check":
        report = check_environment()
        print(report.text())
        # Offline is the bar: a machine that cannot run the deterministic half
        # is broken, whereas a missing key is a configuration step.
        return 0 if report.can_run_offline else 1

    if args.harness_command == "setup":
        print(setup_environment(args.root).text(), end="")
        return 0

    if args.harness_command == "run":
        scenarios = load_scenarios(args.scenarios)
        if args.only:
            scenarios = [s for s in scenarios if s.name == args.only]
            if not scenarios:
                raise ValueError(f"no scenario named {args.only!r}")
        if not scenarios:
            raise ValueError("no scenarios found")

        live = args.live or args.critic
        llm = _harness_runner() if live else None
        results = run_sync(scenarios, llm=llm, evaluate="full" if args.critic else "gates")
        path = save_results(results)

        if args.json:
            from harness.report import summarise

            print(json.dumps(summarise(results), indent=2))
        else:
            if not live:
                print("offline run: no model was called, so only the deterministic "
                      "half is measured.\n")
            print(report_run(results, verbose=args.verbose), end="")
            print(f"saved to {path}")
        return 0 if all(r.passed for r in results) else 1

    if args.harness_command == "report":
        if args.compare:
            print(compare(Path(args.compare[0]), Path(args.compare[1])), end="")
            return 0
        if args.html:
            from harness.html_report import build_report
            from harness.html_report import latest as latest_of

            eval_path, run_path = latest_of("eval"), latest_of("run")
            if eval_path is None and run_path is None:
                print(f"no runs found in {RESULTS_DIR}", file=sys.stderr)
                return 1
            target = Path(args.html)
            target.write_text(
                build_report(eval_path=eval_path, run_path=run_path), encoding="utf-8"
            )
            print(f"wrote {target} from {', '.join(p.name for p in (eval_path, run_path) if p)}")
            return 0
        newest = latest_result()
        if newest is None:
            print(f"no runs found in {RESULTS_DIR}", file=sys.stderr)
            return 1
        payload = json.loads(newest.read_text(encoding="utf-8"))
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            scopes = {r.get("graded", "full") for r in payload["results"]}
            scope = "" if scopes == {"full"} else f"  [{'+'.join(sorted(scopes))}]"
            print(f"{newest.name} — {payload['at']}{scope}")
            for result in payload["results"]:
                mark = "pass" if result["passed"] else "FAIL"
                reason = result["error"] or "; ".join(result["problems"])
                print(f"  [{mark}] {result['scenario']}" + (f" — {reason}" if reason else ""))
        return 0

    raise ValueError(f"unknown harness command {args.harness_command!r}")


def _harness_runner():
    """A model runner for a live harness run."""
    from nexcraftviz.integrations.providers import openai_runner

    return openai_runner()


def _cmd_mcp(args: argparse.Namespace) -> int:
    from nexcraftviz.integrations.mcp_server import main as mcp_main

    return mcp_main()


def _cmd_tools(args: argparse.Namespace) -> int:
    from nexcraftviz.integrations.tools import op_schemas, tool_schemas

    payload: dict[str, Any] = {"tools": tool_schemas(args.style)}
    if args.ops:
        payload["operations"] = op_schemas()
    print(json.dumps(payload, indent=2))
    return 0


def _cmd_theme(args: argparse.Namespace) -> int:
    from nexcraftviz.theme import (
        apply_theme,
        audit,
        available_themes,
        load,
        strip_hardcoded_colours,
        to_bundle,
    )

    if args.theme_command == "list":
        for name in available_themes():
            theme = load(name)
            print(f"{name:<20} {theme.mode:<6} {theme.description.strip().splitlines()[0][:60]}")
        return 0

    if args.theme_command == "show":
        print(json.dumps(load(args.name).vega_config(), indent=2))
        return 0

    if args.theme_command == "css":
        css = to_bundle(load(args.light), load(args.dark))
        if args.out:
            Path(args.out).write_text(css, encoding="utf-8")
            print(f"wrote {args.out} ({len(css)} bytes)")
        else:
            print(css)
        return 0

    if args.theme_command == "audit":
        names = [args.name] if args.name else available_themes()
        failed = False
        for name in names:
            report = audit(load(name))
            print(report.summary())
            for issue in report.issues:
                print(f"  {issue}")
            failed = failed or not report.ok
        return 2 if failed else 0

    if args.theme_command == "apply":
        spec = Spec(load_json(args.spec))
        if not args.keep_colours:
            spec = strip_hardcoded_colours(spec).spec
        result = apply_theme(spec, args.name)
        payload = result.spec.to_json(indent=2)
        if args.out:
            Path(args.out).write_text(payload, encoding="utf-8")
            print(f"wrote {args.out}", file=sys.stderr)
        else:
            print(payload)
        return 0

    raise ValueError(f"unknown theme command {args.theme_command!r}")


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
