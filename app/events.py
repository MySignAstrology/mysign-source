"""
The analytics event: its envelope, and every rule about what it may carry.

One module, two callers, so the privacy rules live in exactly one place:

  - the app (app/activity.py): signed-in events through /api/activity, and the
    server's own events. It knows the uid.
  - the collector Lambda (deploy/collect/): anonymous events from the website
    and from app screens before sign-in. It never knows a uid, and refuses one.

deploy/collect/events.py is a symlink to this file, and the collector's asset
follows it, so the Lambda ships these exact bytes. That is why this module is
stdlib only: the collector has no dependencies but boto3 from the runtime.

What an event can never carry: anything a person typed, an email, a phone
number, a full URL (a referrer is reduced to its domain, a landing page to its
path with every query parameter dropped except utm_*), an IP address, or a
User-Agent string (it is read for a coarse device / OS / browser family and
dropped). Names must look like control ids; property values are short,
enumerable and space-free — a sentence fails the pattern, it isn't trimmed.

The envelope is documented, field by field, in docs/analytics.md.
"""
import json
import math
import re
import time
import uuid
from urllib.parse import parse_qsl, urlsplit

TYPES = frozenset({"page", "screen", "tap", "change", "open", "hide", "heartbeat",
                   "server", "error"})
CLIENT_TYPES = TYPES - {"server"}          # a client can't forge what the server saw
# "backfill" is written only by tools/backfill_events.py, never accepted from a request.
SOURCES = ("site", "app", "server", "native", "backfill")
ANON_SOURCES = ("site", "app", "native")   # what the collector will label a batch

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,59}$")
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{6,40}$")
# Made by the client from a CSPRNG; long enough that it can't be a phone number
# or anything else a person would recognise.
ANON_RE = re.compile(r"^[A-Za-z0-9_-]{16,40}$")
UID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,23}$")

# Properties an event may carry, and how long each may be. Short, enumerable
# things only — nothing a person typed, nothing that names anyone.
PROPS = {"kind": 24, "tier": 8, "mood": 2, "band": 8, "style": 8, "source": 24,
         "precision": 12, "count": 6, "ok": 5, "step": 24, "from": 20, "to": 20,
         "value": 16, "status": 3, "channel": 12, "new": 5,
         "time_skipped": 5, "section": 24}
# No spaces, no "@": an option value or a time, never a sentence or an address.
VALUE_RE = re.compile(r"^[A-Za-z0-9_.:/+-]*$")

UTM = ("utm_source", "utm_medium", "utm_campaign")
CTX = UTM + ("referrer_domain", "landing_path", "country", "device_class",
             "os_family", "browser_family")
DEVICE_CLASSES = ("mobile", "tablet", "desktop", "tv", "other")

MAX_BATCH = 50
MAX_BODY = 64 * 1024
CLOCK_PAST_S = 86400          # an event older than a day is a stuck queue, not history
CLOCK_FUTURE_S = 300


def name_ok(name):
    return isinstance(name, str) and bool(NAME_RE.match(name))


def _value(v, n):
    """A property value that passes, or None."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, str):
        v = v[:n]
        return v if VALUE_RE.match(v) else None
    return None


def clean_props(props):
    out = {}
    if not isinstance(props, dict):
        return out
    for k, v in props.items():
        if k in PROPS:
            v = _value(v, PROPS[k])
            if v is not None:
                out[k] = v
    return out


def clean_event(e, now, types=CLIENT_TYPES):
    """
    One event, normalised to the compact shape the app stores
    ({at, ty, n, sc?, p?}, `at` in seconds), or None if it breaks a rule.
    """
    if not isinstance(e, dict):
        return None
    ty, name = e.get("type"), str(e.get("name") or "").lower()
    if ty not in types or not NAME_RE.match(name):
        return None
    screen = str(e.get("screen") or "").lower()[:20]
    if screen and not NAME_RE.match(screen):
        screen = ""
    try:
        at = float(e.get("at")) / 1000.0
    except (TypeError, ValueError):
        at = now
    # Clocks lie; a phone set to 2031 must not write into the future.
    if not math.isfinite(at) or not now - CLOCK_PAST_S <= at <= now + CLOCK_FUTURE_S:
        at = now
    out = {"at": round(at, 3), "ty": ty, "n": name}
    if screen:
        out["sc"] = screen
    props = clean_props(e.get("props"))
    if props:
        out["p"] = props
    return out


# ------------------------------------------------------------------ context

_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}\.)*[a-z0-9-]{1,63}$")
_PHONEISH = re.compile(r"\d{7,}")


def _token(v, n=40):
    """A UTM value as a lowercase token. Dropped outright if it looks like it
    carries a person (an address, a phone number) — people do paste those into
    campaign links."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    if not v or "@" in v or _PHONEISH.search(re.sub(r"[\s().-]", "", v)):
        return None
    v = re.sub(r"[^a-z0-9_.+-]+", "_", v.lower()).strip("_")[:n]
    return v or None


def referrer_domain(ref, own=()):
    """Where a visit came from, as a bare domain. Never the path: a referring
    URL can be someone's profile, a search query, a private document. Our own
    domains come back None — moving between our pages isn't a referral."""
    if not isinstance(ref, str) or not ref.strip():
        return None
    ref = ref.strip()[:2048]
    try:
        host = urlsplit(ref if "//" in ref else "//" + ref).hostname or ""
    except ValueError:
        return None
    host = host.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not _HOST_RE.match(host) or len(host) > 100:
        return None
    if any(host == o or host.endswith("." + o) for o in own):
        return None
    return host


def _segment(p):
    # Anything with a digit or long enough to be a token is an id — an invite
    # code, a person — so it becomes "id". Same rule as Track.error in the app.
    if not p:
        return p
    if re.search(r"\d", p) or len(p) > 20 or not re.match(r"^[a-z0-9_.-]+$", p):
        return "id"
    return p


def landing(url):
    """(path, {utm_*}) from a page URL. Every other query parameter, and the
    fragment, is dropped unread."""
    if not isinstance(url, str) or not url.strip():
        return None, {}
    try:
        parts = urlsplit(url.strip()[:2048])
    except ValueError:
        return None, {}
    path = "/".join(_segment(p) for p in parts.path.lower().split("/"))[:100] or "/"
    if not path.startswith("/"):
        path = "/" + path
    utm = {}
    for k, v in parse_qsl(parts.query, keep_blank_values=False):
        k = k.lower()
        if k in UTM and k not in utm:
            t = _token(v)
            if t:
                utm[k] = t
    return path, utm


def clean_ctx(raw):
    """A ctx dict holding only the known keys, each in its expected shape."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for k in UTM:
        t = _token(raw.get(k))
        if t:
            out[k] = t
    if raw.get("referrer_domain"):
        d = referrer_domain(raw["referrer_domain"])
        if d:
            out["referrer_domain"] = d
    if isinstance(raw.get("landing_path"), str):
        p, _ = landing(raw["landing_path"])
        if p:
            out["landing_path"] = p
    c = raw.get("country")
    if isinstance(c, str) and re.match(r"^[A-Z]{2}$", c):
        out["country"] = c
    if raw.get("device_class") in DEVICE_CLASSES:
        out["device_class"] = raw["device_class"]
    for k in ("os_family", "browser_family"):
        v = raw.get(k)
        if isinstance(v, str) and re.match(r"^[a-z_]{2,16}$", v):
            out[k] = v
    return out


# ------------------------------------------------- the device, coarsely

_BOT = re.compile(r"bot|crawl|spider|slurp|headless|lighthouse|preview|facebookexternalhit|"
                  r"curl|wget|python-|go-http|okhttp|java/|httpclient|monitor|pingdom", re.I)


def is_bot(ua):
    return not ua or bool(_BOT.search(ua))


def os_family(ua):
    ua = ua or ""
    for pat, fam in ((r"iPhone|iPad|iPod", "ios"), (r"Android", "android"),
                     (r"CrOS", "chromeos"), (r"Windows", "windows"),
                     (r"Macintosh|Mac OS X", "macos"), (r"Linux|X11", "linux")):
        if re.search(pat, ua):
            return fam
    return "other"


def browser_family(ua):
    ua = ua or ""
    for pat, fam in ((r"FBAN|FBAV|Instagram|Line/|Snapchat|TikTok", "in_app"),
                     (r"Edg[A-Z]*/", "edge"), (r"OPR/|Opera", "opera"),
                     (r"SamsungBrowser", "samsung"), (r"Firefox/|FxiOS", "firefox"),
                     (r"CriOS|Chrome/|Chromium", "chrome"), (r"Safari/", "safari")):
        if re.search(pat, ua):
            return fam
    return "other"


def device_class(headers):
    """CloudFront's own device headers when present (it parses the UA for us),
    else a coarse guess from the UA."""
    h = headers or {}
    for hdr, cls in (("cloudfront-is-smarttv-viewer", "tv"),
                     ("cloudfront-is-tablet-viewer", "tablet"),
                     ("cloudfront-is-mobile-viewer", "mobile"),
                     ("cloudfront-is-desktop-viewer", "desktop")):
        if str(h.get(hdr, "")).lower() == "true":
            return cls
    ua = h.get("user-agent") or ""
    if re.search(r"iPad|Tablet", ua):
        return "tablet"
    if re.search(r"Mobi|iPhone|Android", ua):
        return "mobile"
    return "desktop" if ua else "other"


def no_ids(headers):
    """Global Privacy Control or Do Not Track: count the visit, keep nothing
    that could link it to another one."""
    h = headers or {}
    return str(h.get("sec-gpc", "")).strip() == "1" or str(h.get("dnt", "")).strip() == "1"


def request_ctx(headers, body_ctx=None, own=()):
    """
    The context of an anonymous request: country and device from CloudFront,
    OS / browser family from the UA (which is then forgotten), and where the
    visit came from. The page sends `referrer` (document.referrer) and
    `landing` (its own URL); without them the Referer header — the page that
    sent the beacon — stands in for `landing`.
    """
    h = headers or {}
    b = body_ctx if isinstance(body_ctx, dict) else {}
    ua = h.get("user-agent") or ""
    ctx = {"device_class": device_class(h), "os_family": os_family(ua),
           "browser_family": browser_family(ua)}
    c = str(h.get("cloudfront-viewer-country", "")).upper()
    if re.match(r"^[A-Z]{2}$", c):
        ctx["country"] = c
    d = referrer_domain(b.get("referrer"), own)
    if d:
        ctx["referrer_domain"] = d
    path, utm = landing(b.get("landing") or h.get("referer"))
    if path:
        ctx["landing_path"] = path
    ctx.update(utm)
    return clean_ctx(ctx)


# ------------------------------------------------------------------ envelope

def _ms(t):
    return round(t * 1000)


def envelope(ev, *, source, received_at=None, uid=None, anon_id=None, session_id=None,
             app_version=None, ctx=None):
    """
    The wire record for a cleaned event (the output of clean_event, or a server
    event in the same shape). Every identifier is re-checked here, whatever the
    caller passed: an id that fails its pattern becomes null, not an error.
    """
    if source not in SOURCES:
        raise ValueError(source)
    received_at = time.time() if received_at is None else received_at
    props = {k: (("true" if v else "false") if isinstance(v, bool) else str(v))
             for k, v in clean_props(ev.get("p")).items()}
    return {
        "event_id": str(uuid.uuid4()),
        "ts": _ms(ev["at"]),
        "received_at": _ms(received_at),
        "source": source,
        "app_version": app_version if isinstance(app_version, str)
        and VERSION_RE.match(app_version) else None,
        "anon_id": anon_id if isinstance(anon_id, str) and ANON_RE.match(anon_id) else None,
        "uid": uid if isinstance(uid, str) and UID_RE.match(uid) else None,
        "session_id": session_id if isinstance(session_id, str)
        and SESSION_RE.match(session_id) else None,
        "type": ev["ty"],
        "name": ev["n"],
        "screen": ev.get("sc") or None,
        # Strings, all of them: the lake's column is map<string,string>, and a
        # schema that changes type per key is one Athena can't read.
        "props": props,
        "ctx": clean_ctx(ctx),
    }


def record(env):
    """One Firehose record. Newline-terminated so anything that lands as raw
    JSON (the error prefix, the backfill) is still one event per line."""
    return (json.dumps(env, separators=(",", ":"), sort_keys=True) + "\n").encode()


def put(client, stream, envs):
    """
    PutRecordBatch, retrying the records Firehose refused once. Returns how
    many were still refused. Raises only what the client raises.
    """
    if not envs:
        return 0
    recs = [{"Data": record(e)} for e in envs]
    for _ in range(2):
        r = client.put_record_batch(DeliveryStreamName=stream, Records=recs)
        if not r.get("FailedPutCount"):
            return 0
        recs = [rec for rec, res in zip(recs, r.get("RequestResponses") or [])
                if res.get("ErrorCode")]
    return len(recs)
