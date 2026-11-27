"""
Local dev server for the public site.

Serves site/ at / and hands everything the site does not own to the app
(./run.sh, http://127.0.0.1:8420), the way the edge will in production:

    MYSIGN_DEV_AUTH=1 ./run.sh          # the app, on :8420
    python site/serve.py                # the site, on http://localhost:8430

Stdlib only. Responses from the app are streamed through chunk by chunk, so
the chat's server-sent events still arrive as they are written.
"""
from __future__ import annotations

import argparse
import contextlib
import http.client
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

SITE = Path(__file__).resolve().parent
# What belongs to the app even if a file of the same name ever appears here.
APP_PREFIXES = ("/api/", "/app", "/static/", "/privacy", "/terms", "/refunds", "/support", "/cookies", "/accuracy", "/couples", "/check", "/gifts", "/e",
                "/ops", "/bt", "/manifest.webmanifest", "/sw.js", "/favicon.ico")
# Never served from site/: tooling, not content.
PRIVATE = {"serve.py", "README.md", "og.html"}
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
       "trailers", "transfer-encoding", "upgrade", "host", "content-length",
       "date", "server"}
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")


def owned_by_app(path: str) -> bool:
    """/api/... and /static/... by prefix; /app, /bt, /privacy... exactly or as a folder."""
    for p in APP_PREFIXES:
        base = p.rstrip("/")
        if path == base or path.startswith(base + "/"):
            return True
    return False


def site_file(path: str) -> Path | None:
    """The file in site/ a request path names, or None. Never escapes site/."""
    rel = "index.html" if path in ("", "/") else path.lstrip("/")
    if not rel or rel.split("/")[0] in PRIVATE or rel.startswith(".") or "/." in rel:
        return None
    f = (SITE / rel).resolve()
    if SITE not in f.parents or not f.is_file() or f.suffix == ".py":
        return None
    return f


class Handler(BaseHTTPRequestHandler):
    upstream = urlsplit("http://127.0.0.1:8420")
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[site] " + (fmt % args) + "\n")

    def _route(self):
        path = urlsplit(self.path).path
        if not owned_by_app(path):
            f = site_file(path)
            if f is not None and self.command in ("GET", "HEAD"):
                return self._file(f)
        return self._proxy()

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _route

    def _file(self, f: Path):
        data = f.read_bytes()
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/xml", "image/svg+xml"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _proxy(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers["Host"] = self.upstream.netloc
        headers["X-Forwarded-Proto"] = "http"
        headers["X-Forwarded-For"] = self.client_address[0]
        conn = http.client.HTTPConnection(self.upstream.hostname, self.upstream.port or 80,
                                          timeout=120)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            r = conn.getresponse()
        except OSError:
            msg = b'{"detail":"The app is not running on ' + self.upstream.netloc.encode() + b'."}'
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return
        self.send_response(r.status, r.reason)
        for k, v in r.getheaders():
            if k.lower() not in HOP:
                self.send_header(k, v)
        if self.command == "HEAD" or r.status in (204, 304) or r.status < 200:
            self.send_header("Content-Length", "0")
            self.end_headers()
            conn.close()
            return
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            while True:
                chunk = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
                if not chunk:
                    break
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8430)
    ap.add_argument("--upstream", default="http://127.0.0.1:8420",
                    help="where the app runs (./run.sh)")
    a = ap.parse_args(argv)
    Handler.upstream = urlsplit(a.upstream)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"[site] http://{a.host if a.host != '127.0.0.1' else 'localhost'}:{a.port}"
          f"  (app: {a.upstream})", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        srv.serve_forever()


if __name__ == "__main__":
    main()
