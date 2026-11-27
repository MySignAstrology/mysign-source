"""The origin lock: only requests carrying CloudFront's header reach the app."""
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.edge import NativeCors, OriginGuard


def _client():
    app = FastAPI()
    app.add_middleware(OriginGuard)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    @app.get("/stream")
    def stream():
        def gen():
            for i in range(3):
                yield f"data: {i}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    return TestClient(app)


def test_noop_when_token_unset(monkeypatch):
    monkeypatch.delenv("MYSIGN_ORIGIN_TOKEN", raising=False)
    r = _client().get("/ping")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_missing_header_is_forbidden(monkeypatch):
    monkeypatch.setenv("MYSIGN_ORIGIN_TOKEN", "s3cret")
    r = _client().get("/ping")
    assert r.status_code == 403
    assert r.json() == {"detail": "forbidden"}


def test_wrong_header_is_forbidden(monkeypatch):
    monkeypatch.setenv("MYSIGN_ORIGIN_TOKEN", "s3cret")
    r = _client().get("/ping", headers={"x-origin-auth": "nope"})
    assert r.status_code == 403


def test_right_header_passes(monkeypatch):
    monkeypatch.setenv("MYSIGN_ORIGIN_TOKEN", "s3cret")
    r = _client().get("/ping", headers={"x-origin-auth": "s3cret"})
    assert r.status_code == 200


def test_streaming_survives_the_guard(monkeypatch):
    monkeypatch.setenv("MYSIGN_ORIGIN_TOKEN", "s3cret")
    with _client().stream("GET", "/stream", headers={"x-origin-auth": "s3cret"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        chunks = "".join(r.iter_text())
    assert chunks == "data: 0\n\ndata: 1\n\ndata: 2\n\n"


def test_during_a_rotation_both_tokens_pass(monkeypatch):
    """deploy/rotate-origin-token.sh: CloudFront switches tokens while the app
    accepts the old and the new, so there is no window of 403s."""
    monkeypatch.setenv("MYSIGN_ORIGIN_TOKEN", "new")
    monkeypatch.setenv("MYSIGN_ORIGIN_TOKEN_PREVIOUS", "old")
    c = _client()
    assert c.get("/ping", headers={"x-origin-auth": "new"}).status_code == 200
    assert c.get("/ping", headers={"x-origin-auth": "old"}).status_code == 200
    assert c.get("/ping", headers={"x-origin-auth": "other"}).status_code == 403
    assert c.get("/ping").status_code == 403


def test_after_a_rotation_the_old_token_is_refused(monkeypatch):
    monkeypatch.setenv("MYSIGN_ORIGIN_TOKEN", "new")
    monkeypatch.delenv("MYSIGN_ORIGIN_TOKEN_PREVIOUS", raising=False)
    assert _client().get("/ping", headers={"x-origin-auth": "old"}).status_code == 403


# ------------------------------------------------------- the App Store shell
def _native():
    app = FastAPI()
    app.add_middleware(NativeCors)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    @app.get("/stream")
    def stream():
        return StreamingResponse(iter(["data: 1\n\n"]), media_type="text/event-stream")

    return TestClient(app)


APP = "capacitor://localhost"


def test_app_origin_gets_a_preflight_without_credentials():
    r = _native().options("/ping", headers={"Origin": APP, "Access-Control-Request-Method": "POST"})
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == APP
    assert "authorization" in r.headers["access-control-allow-headers"]
    assert "access-control-allow-credentials" not in r.headers


def test_app_origin_can_read_responses_and_streams():
    c = _native()
    for path in ("/ping", "/stream"):
        r = c.get(path, headers={"Origin": APP})
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == APP
        assert "access-control-allow-credentials" not in r.headers


def test_no_other_origin_gets_cors():
    c = _native()
    for origin in ("https://evil.example", "capacitor://evil", "capacitor://localhost.evil", "null"):
        assert "access-control-allow-origin" not in c.get("/ping", headers={"Origin": origin}).headers
        r = c.options("/ping", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
        assert r.status_code == 405 and "access-control-allow-origin" not in r.headers
    assert "access-control-allow-origin" not in c.get("/ping").headers


def test_the_app_can_read_every_response_the_server_makes(monkeypatch):
    # Without the header a 403 or 500 looks like a dead network to the app.
    from app.server import app
    assert app.user_middleware[0].cls is NativeCors
    monkeypatch.setenv("MYSIGN_ORIGIN_TOKEN", "secret")
    r = TestClient(app).get("/api/state", headers={"Origin": APP})
    assert r.status_code == 403 and r.headers["access-control-allow-origin"] == APP


def test_a_crash_reaches_the_app_as_a_500(monkeypatch):
    from app import server
    monkeypatch.delenv("MYSIGN_ORIGIN_TOKEN", raising=False)
    monkeypatch.setattr(server.auth, "current_user", lambda request: 1 / 0)
    r = TestClient(server.app, raise_server_exceptions=False).get("/api/state", headers={"Origin": APP})
    assert r.status_code == 500 and r.headers["access-control-allow-origin"] == APP


def test_the_guard_still_stops_requests_with_no_token(monkeypatch):
    from app.server import app
    monkeypatch.setenv("MYSIGN_ORIGIN_TOKEN", "secret")
    assert TestClient(app).get("/api/state").status_code == 403
