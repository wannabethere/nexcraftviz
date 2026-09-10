"""The API token.

Open by default — fine on localhost — and required on /v1 once
NEXCRAFTVIZ_API_TOKEN is set. The cases worth guarding are the ones that fail
confusingly in a browser: preflight, CORS on a refusal, and a page whose
`Authorization` header already belongs to someone else.
"""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

from nexcraftviz.app.api import KEY_HEADER, PLAYGROUND_DIR, create_app

TOKEN = "test-token-not-a-secret"
ORIGIN = "http://localhost:3000"


def _client(token: str = TOKEN) -> TestClient:
    return TestClient(create_app(api_token=token))


def test_open_when_no_token_is_set(monkeypatch):
    monkeypatch.delenv("NEXCRAFTVIZ_API_TOKEN", raising=False)
    client = TestClient(create_app())
    assert client.get("/v1/chart/capabilities").status_code == 200
    assert client.get("/v1/health").json()["auth"] == "open"


def test_a_missing_token_is_refused_with_a_reason_you_can_act_on():
    response = _client().get("/v1/chart/capabilities")
    assert response.status_code == 401
    detail = response.json()["detail"]
    assert "Bearer" in detail and KEY_HEADER in detail
    assert response.headers["www-authenticate"] == "Bearer"


def test_a_wrong_token_is_refused():
    response = _client().get(
        "/v1/chart/capabilities", headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code == 401


def test_a_bearer_token_is_accepted():
    response = _client().get(
        "/v1/chart/capabilities", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 200


def test_the_key_header_is_accepted():
    response = _client().get("/v1/chart/capabilities", headers={KEY_HEADER: TOKEN})
    assert response.status_code == 200


def test_the_key_header_works_when_authorization_carries_someone_elses_token():
    """Lexy sends its own session JWT in `Authorization`. One header cannot
    carry two tokens, so a matching key header must be enough on its own."""
    response = _client().get(
        "/v1/chart/capabilities",
        headers={"Authorization": "Bearer lexy-session-jwt", KEY_HEADER: TOKEN},
    )
    assert response.status_code == 200


def test_health_stays_open_and_says_auth_is_on():
    """A load balancer's probe carries no credentials."""
    response = _client().get("/v1/health")
    assert response.status_code == 200
    assert response.json()["auth"] == "token"


def test_cors_preflight_needs_no_token():
    """A browser sends OPTIONS without credentials by design. Requiring a token
    there fails every cross-origin call before the real request is made."""
    response = _client().options(
        "/v1/chart/annotate",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": f"content-type,{KEY_HEADER}",
        },
    )
    assert response.status_code == 200


def test_a_refusal_still_carries_cors_headers():
    """Without them the browser reports an opaque CORS failure and nobody learns
    the real problem was the key. This is what the middleware order is for."""
    response = _client().get("/v1/chart/capabilities", headers={"Origin": ORIGIN})
    assert response.status_code == 401
    assert response.headers.get("access-control-allow-origin")


def test_files_outside_the_api_stay_open():
    client = _client()
    assert client.get("/embed/nexcraftviz.js").status_code == 200
    if os.path.isdir(PLAYGROUND_DIR):
        assert client.get("/playground/").status_code == 200


def test_the_token_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("NEXCRAFTVIZ_API_TOKEN", TOKEN)
    client = TestClient(create_app())
    assert client.get("/v1/chart/capabilities").status_code == 401
    assert client.get(
        "/v1/chart/capabilities", headers={KEY_HEADER: TOKEN}
    ).status_code == 200
