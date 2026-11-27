"""
Only CloudFront may talk to the app.

The Function URL has to be public (auth NONE) for CloudFront to reach it
without signing every request, which also means anyone who learns the raw
lambda-url.on.aws address can skip the WAF, the HTTPS redirect and every edge
rule. CloudFront adds a secret `x-origin-auth` header on the way through;
anything arriving without it did not come through the front door.

Pure ASGI on purpose. BaseHTTPMiddleware buffers and re-wraps the response
body, and the chat is server-sent events — wrapping it would make every reply
land as one lump, or not at all. This touches only the request headers and
otherwise hands `send` straight through.

Wire it in app/server.py, right after `app = FastAPI(...)`:

    app.add_middleware(OriginGuard)

Off when MYSIGN_ORIGIN_TOKEN is unset, so local dev and the bare-URL deploy
behave exactly as before. The Lambda Web Adapter's readiness probe sees a 403,
which it counts as up (anything under 500).
"""
import hmac
import json
import os

HEADER = b"x-origin-auth"
_BODY = json.dumps({"detail": "forbidden"}).encode()


class OriginGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Read per request, not at import: bootstrap populates the environment
        # from Secrets Manager, and tests flip it with monkeypatch.
        token = os.environ.get("MYSIGN_ORIGIN_TOKEN")
        if not token or scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        # During a rotation (deploy/rotate-origin-token.sh) the previous token
        # is accepted too, so CloudFront can switch over without a window of
        # 403s. Outside a rotation it is unset.
        accepted = [t for t in (token, os.environ.get("MYSIGN_ORIGIN_TOKEN_PREVIOUS")) if t]

        given = b""
        for k, v in scope.get("headers") or ():
            if k.lower() == HEADER:
                given = v
                break
        # Compare against every accepted token, always, so timing says nothing.
        ok = False
        for t in accepted:
            ok |= hmac.compare_digest(given, t.encode())
        if ok:
            return await self.app(scope, receive, send)

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(_BODY)).encode())]})
        await send({"type": "http.response.body", "body": _BODY})


# ----------------------------------------------------- the App Store shell

# Where the iPhone app's bundled page comes from. Exact, and nothing else: the
# web client is same-origin and never needs any of this.
NATIVE_ORIGIN = b"capacitor://localhost"
_NATIVE_CORS = [(b"access-control-allow-origin", NATIVE_ORIGIN), (b"vary", b"Origin")]
_NATIVE_PREFLIGHT = _NATIVE_CORS + [
    (b"access-control-allow-methods", b"GET, POST, PUT, PATCH, DELETE"),
    (b"access-control-allow-headers", b"authorization, content-type, x-tz, x-client, x-device"),
    (b"access-control-max-age", b"600"),
    (b"content-length", b"0"),
]


class NativeCors:
    """
    Lets the iPhone app call the API from its bundled page. It signs in with a
    bearer token (/api/auth/verify), so credentials stay off: no other site
    gains anything, and no cookie is ever readable across origins.

    Pure ASGI for the reason OriginGuard is: the chat stream must not be
    buffered. Outermost, so every response carries the header. A preflight is
    answered ahead of OriginGuard, and all it returns is these fixed headers.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope.get("headers") or ()}
        if headers.get(b"origin") != NATIVE_ORIGIN:
            return await self.app(scope, receive, send)
        if scope["method"] == "OPTIONS" and b"access-control-request-method" in headers:
            await send({"type": "http.response.start", "status": 204, "headers": _NATIVE_PREFLIGHT})
            await send({"type": "http.response.body", "body": b""})
            return

        async def with_cors(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *_NATIVE_CORS]}
            await send(message)
        await self.app(scope, receive, with_cors)


# ------------------------------------------------------- links we hand out

DEFAULT_PUBLIC_URL = "https://mysignapp.com"


def public_url(request=None):
    """
    The address every link we send out is built on: invites, "ask your mom"
    links, anything in an email.

    Never the Host header. Behind CloudFront (ALL_VIEWER_EXCEPT_HOST_HEADER)
    the Host the app sees is the Function URL's, so every shared invite used
    to hand its recipient the raw lambda-url.on.aws address — the one that
    skips the WAF, and that 403s anyway behind OriginGuard (audit M2). A Host
    header is also the caller's to write.

    MYSIGN_PUBLIC_URL wins; then the stack's MYSIGN_DOMAIN (so staging links
    stay on staging); then mysignapp.com. Only local development, where dev
    auth is on (and that is refused on Lambda), uses the request's own host,
    so links work on 127.0.0.1.
    """
    url = os.environ.get("MYSIGN_PUBLIC_URL", "").strip()
    if url:
        return url.rstrip("/")
    domain = os.environ.get("MYSIGN_DOMAIN", "").strip()
    if domain:
        return f"https://{domain}"
    from app.auth import dev_auth_allowed
    if request is not None and dev_auth_allowed():
        return str(request.base_url).rstrip("/")
    return DEFAULT_PUBLIC_URL
