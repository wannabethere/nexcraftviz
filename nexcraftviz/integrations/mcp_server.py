"""MCP server — the same toolkit, over the Model Context Protocol.

A thin adapter, on purpose. Everything it exposes comes from
:mod:`nexcraftviz.integrations.tools`, so an MCP client and a plain
tool-calling loop get identical behaviour and there is no second
implementation to keep in step.

Run it::

    nexcraftviz mcp                      # stdio, the usual transport

Or register it with Claude Code / Claude Desktop::

    {
      "mcpServers": {
        "nexcraftviz": {"command": "nexcraftviz", "args": ["mcp"]}
      }
    }

The ``mcp`` extra is optional; without it this module imports fine and
:func:`available` reports False, so the CLI can say something useful rather
than raising an ImportError from three frames down.
"""
from __future__ import annotations

import json
from typing import Any

from nexcraftviz import __version__
from nexcraftviz.integrations.tools import TOOLS, call

SERVER_NAME = "nexcraftviz"

#: Shown to the client on connect. Worth writing carefully: it is the only
#: instruction most agents will read before choosing a tool.
INSTRUCTIONS = """\
Charting tools that keep the deterministic parts deterministic.

You are the model here — there is no "generate a chart" tool that calls another
model behind your back. You decide; these tools do the mechanics correctly.

A good sequence:

1. `viz_profile` — see the columns and, more usefully, their ROLES (measure,
   dimension, time, identifier, series). Roles decide the chart, not dtypes.
2. `viz_recommend` — deterministic shape rules rank the chart types, with a
   reason for each. Take the top one unless the question clearly asks otherwise.
3. `viz_guidance` — the operation vocabulary and the rules for using it. Read
   this before your first `viz_apply_ops` call.
4. `viz_apply_ops` — apply your operations. It validates the result and repairs
   near-miss field names.
5. `viz_render` — confirm it actually draws. A spec can validate, compile, and
   still render a blank canvas.

Do not hand-write Vega-Lite. Emitting operations is cheaper, reproducible, and
cannot corrupt a chart that was already correct — and `viz_apply_ops` catches
the failure that hurts most: an encoding referencing a column that does not
exist, which renders an empty chart while passing every schema check.

For a mix of charts, KPI cards and rich tables, build a widget and use
`viz_apply_layout`. `viz_compose` is for combining Vega views into one spec.\
"""


def available() -> bool:
    """True when the ``mcp`` extra is installed."""
    try:
        import mcp.server  # noqa: F401
    except ImportError:
        return False
    return True


def build_server() -> Any:
    """Construct the MCP server. Raises a useful error without the extra."""
    try:
        from mcp.server import Server
        from mcp.types import TextContent
        from mcp.types import Tool as McpTool
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "the MCP server needs the `mcp` extra: pip install 'nexcraftviz[mcp]'"
        ) from exc

    server = Server(SERVER_NAME, version=__version__, instructions=INSTRUCTIONS)

    @server.list_tools()
    async def list_tools() -> list[Any]:
        return [
            McpTool(
                name=tool.name,
                description=tool.description,
                inputSchema=tool.parameters,
            )
            for tool in TOOLS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[Any]:
        result = call(name, arguments or {})
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return server


async def serve_stdio() -> None:  # pragma: no cover - transport plumbing
    """Serve over stdio, the transport every MCP client supports."""
    from mcp.server.stdio import stdio_server

    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> int:  # pragma: no cover - entry point
    import asyncio
    import sys

    if not available():
        print(
            "the MCP server needs the `mcp` extra: pip install 'nexcraftviz[mcp]'",
            file=sys.stderr,
        )
        return 3
    asyncio.run(serve_stdio())
    return 0
