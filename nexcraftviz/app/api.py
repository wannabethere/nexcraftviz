"""HTTP surface — hosted mode, and the server half of the embed.

Every route is a thin wrapper over :class:`~nexcraftviz.session.session.Session`,
and both execution modes are exposed rather than one being the "real" one:

* ``POST /v1/sessions/{id}/propose`` + ``/commit`` — the host owns the model.
  Propose returns the prompt and schema; the host runs its own model and posts
  the output back. No provider key needed here at all.
* ``POST /v1/sessions/{id}/turn`` — nexcraftviz owns the model. Needs a
  configured provider, and returns 503 with an actionable message when there
  isn't one, rather than a stack trace.

Sessions live in memory by default. That is honest for an embed and a demo and
wrong for a deployment; :class:`SessionStore` is the seam to replace.
"""
from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field

from nexcraftviz import __version__
from nexcraftviz.compose.widget import Widget
from nexcraftviz.session.session import Proposal, Session
from nexcraftviz.skills import REGISTRY
from nexcraftviz.spec.model import Spec

#: Where the embed is served from, relative to this package.
EMBED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "embed")


class SessionStore:
    """In-memory session storage.

    Deliberately a class rather than a dict so that swapping in Redis or a
    database is one substitution and not a refactor. Sessions are capped so a
    long-lived embed process cannot leak them forever.
    """

    def __init__(self, *, limit: int = 500) -> None:
        self._sessions: dict[str, Session] = {}
        self._limit = limit

    def create(self, **kwargs: Any) -> Session:
        session = Session(**kwargs)
        if len(self._sessions) >= self._limit:
            self._sessions.pop(next(iter(self._sessions)))
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def drop(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def __len__(self) -> int:
        return len(self._sessions)


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------

class CreateSession(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list)
    document: dict[str, Any] | None = None
    kind: str = Field(default="chart", description="chart | widget")
    theme: str = "nexcraftviz-light"
    language: str = "English"


class MessageIn(BaseModel):
    message: str
    skill: str = Field(default="", description="Override the router.")


class CommitIn(BaseModel):
    turn_id: str
    skill: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] | None = Field(
        default=None, description="What the host's model returned."
    )


class CreateChart(BaseModel):
    """A first chart, for a host that owns the data and the widget."""

    question: str = Field(
        description="Composed by the caller from the data. Required — a chart "
                    "built from a question nobody can see is one nobody can check.",
    )
    rows: list[dict[str, Any]] = Field(default_factory=list)
    theme: str = ""
    language: str = "English"


class AnnotateChart(BaseModel):
    """One instruction against an existing chart."""

    instruction: str
    chart_schema: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
    theme: str = ""
    language: str = "English"
    session_id: str = Field(
        default="", description="Optional — supply one to keep undo across calls."
    )


#: Said the same way wherever a route needs a model and the server has none.
_NO_MODEL = (
    "this server has no model configured. Start it with a provider, or use "
    "/v1/sessions with propose/commit and run your own."
)


def create_app(store: SessionStore | None = None, *, llm: Any = None) -> Any:
    """Build the FastAPI app.

    ``llm`` enables hosted mode. Left as None, propose/commit still work
    perfectly — which is the point of splitting them.
    """
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "the API needs the `app` extra: pip install 'nexcraftviz[app]'"
        ) from exc

    sessions = store or SessionStore()
    app = FastAPI(title="nexcraftviz", version=__version__)

    # An embed is cross-origin by definition. Restrict this in a deployment —
    # NEXCRAFTVIZ_ALLOW_ORIGINS is a comma-separated list.
    origins = os.getenv("NEXCRAFTVIZ_ALLOW_ORIGINS", "*").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins if o.strip()],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    def _require(session_id: str) -> Session:
        session = sessions.get(session_id)
        if session is None:
            raise HTTPException(404, f"no session {session_id!r}")
        return session

    # -- discovery ---------------------------------------------------------

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "hosted_mode": llm is not None,
            "sessions": len(sessions),
        }

    @app.get("/v1/skills")
    def skills() -> dict[str, Any]:
        return {
            "skills": [
                {
                    "name": skill.spec.name,
                    "summary": skill.spec.summary,
                    "uses_llm": skill.spec.uses_llm,
                    "input_schema": skill.input_schema(),
                    "output_schema": skill.output_schema(),
                }
                for skill in REGISTRY.values()
            ]
        }

    @app.get("/v1/tools")
    def tools(style: str = "openai") -> dict[str, Any]:
        from nexcraftviz.integrations.tools import op_schemas, tool_schemas

        return {"tools": tool_schemas(style), "operations": op_schemas()}  # type: ignore[arg-type]

    @app.get("/v1/theme.css")
    def theme_css() -> Any:
        from fastapi.responses import Response

        from nexcraftviz.theme import load, to_bundle

        css = to_bundle(load("nexcraftviz-light"), load("nexcraftviz-dark"))
        return Response(css, media_type="text/css")

    # -- sessions ----------------------------------------------------------

    @app.post("/v1/sessions")
    def create(body: CreateSession) -> dict[str, Any]:
        document: Spec | Widget | None = None
        if body.document:
            document = (
                Widget.from_dict(body.document) if body.kind == "widget"
                else Spec(body.document)
            )
        session = sessions.create(
            rows=body.rows, document=document, theme=body.theme, language=body.language
        )
        return session.state()

    @app.get("/v1/sessions/{session_id}")
    def read(session_id: str) -> dict[str, Any]:
        return _require(session_id).state()

    @app.delete("/v1/sessions/{session_id}")
    def delete(session_id: str) -> dict[str, Any]:
        return {"deleted": sessions.drop(session_id)}

    # -- skill mode --------------------------------------------------------

    @app.post("/v1/sessions/{session_id}/propose")
    def propose(session_id: str, body: MessageIn) -> dict[str, Any]:
        """Decide what to do and return the prompt. Runs no model."""
        session = _require(session_id)
        proposal = session.propose(body.message, skill=body.skill)
        payload = proposal.to_dict()
        payload["inputs"] = _jsonable(proposal.inputs)
        return payload

    @app.post("/v1/sessions/{session_id}/commit")
    def commit(session_id: str, body: CommitIn) -> dict[str, Any]:
        """Apply what the host's model returned."""
        session = _require(session_id)
        from nexcraftviz.session.route import Route

        proposal = Proposal(
            turn_id=body.turn_id,
            skill=body.skill,
            route=Route(body.skill, "committed by the host"),
            prompt=None,
            inputs=body.inputs,
        )
        try:
            turn = session.commit(proposal, body.output)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"turn": turn.to_dict(), "state": session.state()}

    # -- hosted mode -------------------------------------------------------

    @app.post("/v1/sessions/{session_id}/turn")
    async def turn(session_id: str, body: MessageIn) -> dict[str, Any]:
        session = _require(session_id)
        proposal = session.propose(body.message, skill=body.skill)

        if proposal.needs_model and llm is None:
            raise HTTPException(
                503,
                f"{proposal.skill} needs a model and this server has none configured. "
                "Use /propose and /commit and run your own, or start the server "
                "with a provider.",
            )
        try:
            result = await session.turn(body.message, llm=llm, skill=body.skill)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"turn": result.to_dict(), "state": session.state()}

    # -- history -----------------------------------------------------------

    @app.post("/v1/sessions/{session_id}/undo")
    def undo(session_id: str) -> dict[str, Any]:
        session = _require(session_id)
        return {"undone": session.undo(), "state": session.state()}

    @app.post("/v1/sessions/{session_id}/redo")
    def redo(session_id: str) -> dict[str, Any]:
        session = _require(session_id)
        return {"redone": session.redo(), "state": session.state()}

    # -- charts ------------------------------------------------------------
    #
    # A separate surface from /v1/sessions: a dashboard tile already has a
    # widget id to key on, so making it mint a session to draw one chart is
    # ceremony. Stateless unless the caller asks for a session.

    @app.get("/v1/chart/capabilities")
    def chart_capabilities() -> dict[str, Any]:
        from nexcraftviz.app.chart_api import capabilities

        return capabilities()

    @app.post("/v1/chart/create")
    async def chart_create(body: CreateChart) -> dict[str, Any]:
        from nexcraftviz.app.chart_api import ChartSurfaceError, create_chart

        if llm is None:
            raise HTTPException(503, _NO_MODEL)
        try:
            return await create_chart(
                question=body.question, rows=body.rows, theme=body.theme,
                language=body.language, llm=llm,
            )
        except ChartSurfaceError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/chart/annotate")
    async def chart_annotate(body: AnnotateChart) -> dict[str, Any]:
        from nexcraftviz.app.chart_api import ChartSurfaceError, annotate_chart

        session = sessions.get(body.session_id) if body.session_id else None
        if body.session_id and session is None:
            raise HTTPException(404, f"no session {body.session_id!r}")

        try:
            result, used = await annotate_chart(
                instruction=body.instruction,
                chart_schema=body.chart_schema,
                rows=body.rows or (session.rows if session else []),
                theme=body.theme,
                language=body.language,
                llm=llm,
                session=session,
            )
        except ChartSurfaceError as exc:
            raise HTTPException(422, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        if session is not None:
            result["session_id"] = used.id
        return result

    # -- the embed ---------------------------------------------------------

    @app.get("/embed/nexcraftviz.js")
    def embed_script() -> Any:
        path = os.path.join(EMBED_DIR, "nexcraftviz.js")
        if not os.path.exists(path):  # pragma: no cover
            raise HTTPException(404, "embed script not found")
        return FileResponse(path, media_type="application/javascript")

    return app


def _jsonable(inputs: dict[str, Any]) -> dict[str, Any]:
    """Make session inputs safe to serialise.

    Inputs hold live ``Spec`` and ``Widget`` objects so the skills can work
    with them directly; the wire needs plain data.
    """
    out: dict[str, Any] = {}
    for key, value in inputs.items():
        if isinstance(value, Spec):
            out[key] = value.raw
        elif isinstance(value, Widget):
            out[key] = value.to_dict()
        else:
            out[key] = value
    return out
