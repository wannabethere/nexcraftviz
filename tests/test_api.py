"""The HTTP surface — both execution modes, and the embed it serves."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="needs the `app` extra")

from fastapi.testclient import TestClient  # noqa: E402

from nexcraftviz.app.api import SessionStore, create_app  # noqa: E402
from nexcraftviz.examples import talent_acquisition_widget  # noqa: E402

ROWS = [
    {"region": "West", "revenue": 128},
    {"region": "East", "revenue": 96},
    {"region": "North", "revenue": 152},
]
CHART = {
    "data": {"values": ROWS},
    "mark": "bar",
    "width": "container",
    "height": 200,
    "encoding": {
        "x": {"field": "region", "type": "nominal"},
        "y": {"field": "revenue", "type": "quantitative"},
    },
}
SORT_OPS = {
    "ops": [{"op": "sort_by", "channel": "x", "by": "revenue", "order": "descending"}],
    "reasoning": "North leads.",
}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def session(client: TestClient) -> str:
    response = client.post("/v1/sessions", json={"rows": ROWS, "document": CHART})
    return response.json()["session_id"]


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def test_health_states_whether_hosted_mode_is_available(client: TestClient) -> None:
    body = client.get("/v1/health").json()
    assert body["status"] == "ok"
    assert body["hosted_mode"] is False


def test_skills_endpoint_publishes_the_schemas(client: TestClient) -> None:
    skills = client.get("/v1/skills").json()["skills"]
    assert {s["name"] for s in skills} >= {"viz.edit", "viz.place", "viz.theme"}
    assert all("input_schema" in s and "output_schema" in s for s in skills)


@pytest.mark.parametrize("style", ["openai", "anthropic", "mcp"])
def test_tools_endpoint_serves_every_style(client: TestClient, style: str) -> None:
    body = client.get(f"/v1/tools?style={style}").json()
    assert len(body["tools"]) >= 8
    assert "chart_ops" in body["operations"]


def test_theme_css_is_served_for_the_embed(client: TestClient) -> None:
    response = client.get("/v1/theme.css")
    assert response.headers["content-type"].startswith("text/css")
    assert "--nxv-accent" in response.text


def test_the_embed_script_is_served(client: TestClient) -> None:
    response = client.get("/embed/nexcraftviz.js")
    assert "nexcraftviz-chat" in response.text
    assert "customElements.define" in response.text


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------

def test_create_read_and_delete(client: TestClient, session: str) -> None:
    assert client.get(f"/v1/sessions/{session}").json()["kind"] == "chart"
    assert client.delete(f"/v1/sessions/{session}").json()["deleted"] is True
    assert client.get(f"/v1/sessions/{session}").status_code == 404


def test_a_widget_session_serves_html_and_specs(client: TestClient) -> None:
    body = client.post("/v1/sessions", json={
        "kind": "widget", "document": talent_acquisition_widget().to_dict(),
    }).json()
    assert body["kind"] == "widget"
    assert "nxv-group" in body["html"]
    assert body["specs"]


def test_an_unknown_session_is_a_404(client: TestClient) -> None:
    assert client.post("/v1/sessions/nope/propose", json={"message": "hi"}).status_code == 404


# ---------------------------------------------------------------------------
# skill mode
# ---------------------------------------------------------------------------

def test_propose_returns_a_runnable_prompt_and_calls_no_model(
    client: TestClient, session: str
) -> None:
    body = client.post(f"/v1/sessions/{session}/propose",
                       json={"message": "sort descending"}).json()
    assert body["skill"] == "viz.edit"
    assert body["needs_model"] is True
    assert body["prompt"]["system"] and body["prompt"]["output_schema"]
    assert body["route"]["reason"]


def test_propose_inputs_are_json_safe(client: TestClient, session: str) -> None:
    """Inputs hold live Spec objects internally; the wire needs plain data."""
    import json

    body = client.post(f"/v1/sessions/{session}/propose",
                       json={"message": "sort descending"}).json()
    json.dumps(body["inputs"])  # must not raise
    assert isinstance(body["inputs"]["spec"], dict)


def test_propose_then_commit_round_trip(client: TestClient, session: str) -> None:
    proposal = client.post(f"/v1/sessions/{session}/propose",
                           json={"message": "sort descending"}).json()
    body = client.post(f"/v1/sessions/{session}/commit", json={
        "turn_id": proposal["turn_id"],
        "skill": proposal["skill"],
        "inputs": proposal["inputs"],
        "output": SORT_OPS,
    }).json()

    assert body["turn"]["ok"] is True
    assert body["turn"]["reply"] == "North leads."
    assert body["state"]["can_undo"] is True


def test_commit_without_model_output_is_a_400(client: TestClient, session: str) -> None:
    proposal = client.post(f"/v1/sessions/{session}/propose",
                           json={"message": "sort descending"}).json()
    response = client.post(f"/v1/sessions/{session}/commit", json={
        "turn_id": proposal["turn_id"], "skill": proposal["skill"],
        "inputs": proposal["inputs"], "output": None,
    })
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# hosted mode
# ---------------------------------------------------------------------------

def test_a_model_backed_turn_without_a_provider_explains_itself(
    client: TestClient, session: str
) -> None:
    """A 503 that names the alternative, not a stack trace."""
    response = client.post(f"/v1/sessions/{session}/turn", json={"message": "sort descending"})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "propose" in detail and "commit" in detail


def test_a_deterministic_turn_works_with_no_provider(client: TestClient, session: str) -> None:
    body = client.post(f"/v1/sessions/{session}/turn",
                       json={"message": "switch to dark mode"}).json()
    assert body["turn"]["skill"] == "viz.theme"
    assert body["state"]["theme"] == "nexcraftviz-dark"


async def test_hosted_mode_with_a_provider(session: str) -> None:
    async def llm(system: str, user: str, schema: dict):
        return SORT_OPS, {}

    client = TestClient(create_app(llm=llm))
    created = client.post("/v1/sessions", json={"rows": ROWS, "document": CHART}).json()
    body = client.post(f"/v1/sessions/{created['session_id']}/turn",
                       json={"message": "sort descending"}).json()

    assert body["turn"]["ok"] is True
    assert client.get("/v1/health").json()["hosted_mode"] is True


# ---------------------------------------------------------------------------
# history and storage
# ---------------------------------------------------------------------------

def test_undo_and_redo(client: TestClient, session: str) -> None:
    proposal = client.post(f"/v1/sessions/{session}/propose",
                           json={"message": "sort descending"}).json()
    client.post(f"/v1/sessions/{session}/commit", json={
        "turn_id": proposal["turn_id"], "skill": proposal["skill"],
        "inputs": proposal["inputs"], "output": SORT_OPS,
    })

    assert client.post(f"/v1/sessions/{session}/undo").json()["undone"] is True
    assert client.post(f"/v1/sessions/{session}/redo").json()["redone"] is True
    assert client.post(f"/v1/sessions/{session}/redo").json()["redone"] is False


def test_the_store_is_capped_so_an_embed_cannot_leak_sessions() -> None:
    store = SessionStore(limit=3)
    for _ in range(5):
        store.create()
    assert len(store) == 3
