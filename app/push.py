"""
Web Push.

The 7:30am notification is the product — everything else in the app is what
happens after it lands. Without it My Sign is a page you have to remember to
visit, which is a different thing entirely.

On iOS, Web Push only works once the site has been added to the Home Screen
(16.4+). That is why there is a manifest and a service worker: not to be a PWA
for its own sake, but because it is the only route to a notification on the
phone the target user actually has.
"""
import base64
import binascii
import importlib
import json
import os
import re
import threading
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from engine.store import load, save

# pywebpush drags in aiohttp, requests and cryptography: a cold start paid for
# them on every request, and only the cron and the key route use them. Loaded
# on first touch; `push.webpush` still exists for tests to patch.
_LAZY = {"requests": ("requests", None),
         "serialization": ("cryptography.hazmat.primitives.serialization", None),
         "Vapid01": ("py_vapid", "Vapid01"),
         "webpush": ("pywebpush", "webpush"),
         "WebPushException": ("pywebpush", "WebPushException")}


def __getattr__(name):
    if name not in _LAZY:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod, attr = _LAZY[name]
    obj = importlib.import_module(mod)
    obj = globals()[name] = getattr(obj, attr) if attr else obj
    return obj


def _lib(name):
    # Module __getattr__ only answers attribute access, not a bare global name.
    return globals()[name] if name in globals() else __getattr__(name)

TTL = 12 * 3600
TIMEOUT = 10            # seconds per push-service call; the cron runs on a budget

# The endpoint is user-supplied and the server POSTs to it, so an unchecked one
# is an SSRF primitive (instance metadata, anything inside the VPC). Only the
# real push services ever get a request. Matched on a dot boundary, so
# "evilfcm.googleapis.com.attacker.net" and "xfcm.googleapis.com" both fail.
# web.push.apple.com is covered by push.apple.com.
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com",
              "push.apple.com", "notify.windows.com")
MAX_SUB_BYTES = 2048    # a real subscription is ~500 bytes
_B64URL = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")

_keys_lock = threading.Lock()


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def keys():
    """
    VAPID keypair. From the environment in production; generated once and kept
    locally otherwise. Rotating it invalidates every existing subscription, so
    it lives with the other secrets rather than being regenerated on deploy.
    """
    priv_pem = os.environ.get("MYSIGN_VAPID_PRIVATE")
    if not priv_pem:
        # Locked because the cron sends from worker threads: two threads both
        # generating on first use would leave one signing with a key never saved.
        with _keys_lock:
            priv_pem = _local_private_key()

    serialization = _lib("serialization")
    priv = serialization.load_pem_private_key(priv_pem.encode(), password=None)
    pub = _b64(priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    return priv_pem, pub


def _local_private_key():
    stored = load("_push", "vapid", None)
    if not stored:
        serialization = _lib("serialization")
        v = _lib("Vapid01")()
        v.generate_keys()
        stored = {"private": v.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()}
        save("_push", "vapid", stored)
    return stored["private"]


def public_key():
    return keys()[1]


def validate_subscription(sub):
    """(ok, reason). Reason is a short code, safe to log — never the endpoint."""
    if not isinstance(sub, dict):
        return False, "not_an_object"
    try:
        if len(json.dumps(sub)) > MAX_SUB_BYTES:
            return False, "too_large"
    except (TypeError, ValueError):
        return False, "not_serialisable"

    endpoint = sub.get("endpoint")
    if not isinstance(endpoint, str):
        return False, "bad_endpoint"
    try:
        u = urlsplit(endpoint)
        host, port = (u.hostname or "").lower(), u.port
    except ValueError:
        return False, "bad_endpoint"
    if u.scheme != "https" or not host or u.username or u.password:
        return False, "bad_endpoint"
    if port not in (None, 443):
        return False, "bad_endpoint"

    k = sub.get("keys")
    if not isinstance(k, dict) or not all(isinstance(k.get(f), str) and k.get(f)
                                          for f in ("p256dh", "auth")):
        return False, "missing_keys"

    if not any(host == h or host.endswith("." + h) for h in PUSH_HOSTS):
        return False, "unknown_push_service"
    # RFC 8291: p256dh is an uncompressed P-256 point (65 bytes), auth is 16.
    for field, size in (("p256dh", 65), ("auth", 16)):
        v = k[field]
        if not _B64URL.match(v):
            return False, f"bad_{field}"
        try:
            if len(_unb64(v)) != size:
                return False, f"bad_{field}"
        except (binascii.Error, ValueError):
            return False, f"bad_{field}"
    return True, "ok"


def subscribe(uid, subscription, tz=None):
    """
    Returns the device count, or False if the subscription is refused. False
    rather than raising so the route stays a 200-with-a-flag until it maps this
    to a 400; it is never stored either way.
    """
    ok, reason = validate_subscription(subscription)
    if not ok:
        from app import log
        log.emit("push.subscribe.rejected", level="warning", uid=uid, reason=reason)
        return False
    # Keep only the fields web push uses, so nothing else rides along in storage.
    clean = {"endpoint": subscription["endpoint"],
             "keys": {"p256dh": subscription["keys"]["p256dh"],
                      "auth": subscription["keys"]["auth"]}}
    subs = load(uid, "push", []) or []
    subs = [s for s in subs if _ep(s) != clean["endpoint"]]
    subs.append({**clean, "tz": tz})
    # Newest wins past MAX_DEVICES (audit M5): every send is one synchronous
    # request per device, so 150 fake devices meant 150 ten-second timeouts
    # per test push and per morning, eating the cron's budget for everyone.
    save(uid, "push", subs[-MAX_DEVICES:])
    return min(len(subs), MAX_DEVICES)


MAX_DEVICES = 5      # a phone, a laptop, a tablet, and a couple of browsers


def _ep(s):
    return s.get("endpoint") if isinstance(s, dict) else None


def unsubscribe(uid, endpoint=None):
    if endpoint is None:
        save(uid, "push", [])
        return 0
    subs = [s for s in (load(uid, "push", []) or []) if _ep(s) != endpoint]
    save(uid, "push", subs)
    return len(subs)


# What a push was about, for push_sent / push_opened. A fixed list: the kind
# rides in the URL the notification opens and lands in analytics as is.
KINDS = ("morning", "person", "sky", "notice", "reveal", "test")
MARK = ("from", "pk")


def mark(url, kind):
    """`url` with from=push&pk=<kind> appended, whatever path it has. The app
    reads the pair on open, counts push_opened and strips it from the address
    bar (the Track block in index.html)."""
    parts = urlsplit(url or "/app")
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in MARK]
    q += [("from", "push"), ("pk", kind)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", urlencode(q),
                       parts.fragment))


def _apple_web(s):
    """A Safari subscription: on an iPhone, My Sign on the Home Screen."""
    try:
        host = (urlsplit(_ep(s) or "").hostname or "").lower()
    except ValueError:
        return False
    return host == "push.apple.com" or host.endswith(".push.apple.com")


def has_device(uid):
    """Somewhere to send to: a browser subscription, or the iPhone app (app/apns.py)."""
    if load(uid, "push", []) or []:
        return True
    from app import apns
    return apns.active(uid)


def send(uid, title, body, url="/app", kind=None):
    """
    Push to every device this user has registered. Never raises for one bad
    device — the cron loops over everyone, and one person's broken subscription
    must not cost the rest of the roster their morning.

    Two channels, one notification per device: the iPhone app gets it through
    APNs (app/apns.py), browsers through Web Push. When the app took it, her
    Safari subscriptions are skipped for this one: on an iPhone that is the
    Home Screen copy of My Sign, and the same phone would buzz twice. Other
    browsers (a laptop) still get theirs, as they always have. If APNs took
    nothing, the web goes out exactly as before.

    `kind` (one of KINDS) marks the URL so an open can be counted, and records
    `push_sent` in the analytics lake once at least one device took it. Lake
    only (activity.track_lake): the cron sends to everyone at once.

    Permanently bad subscriptions are pruned: 404/410 (the browser revoked it)
    or malformed (missing or undecodable keys, which will never start working).
    Anything else — 5xx, 429, timeouts, connection errors — is kept and retried
    tomorrow.
    """
    from app import apns, log
    if kind not in KINDS:
        kind = None
    payload = {"title": title, "body": body, "url": url}
    if kind:
        payload.update(url=mark(url, kind), kind=kind)
    native = apns.send(uid, title, body, url=payload["url"], kind=kind)

    subs = load(uid, "push", []) or []
    if native["sent"]:
        subs = [s for s in subs if not _apple_web(s)]
    if not subs:
        if kind and native["sent"]:
            _count_sent(uid, kind)
        return native

    webpush, WebPushException, requests = (
        _lib("webpush"), _lib("WebPushException"), _lib("requests"))
    priv_pem, _ = keys()
    claims = {"sub": os.environ.get("MYSIGN_VAPID_CONTACT", "mailto:support@mysignapp.com")}
    data = json.dumps(payload)
    sent, failed, dead = 0, 0, set()
    for s in subs:
        status, permanent = None, False
        try:
            ok, reason = validate_subscription(s)
            if not ok:
                raise ValueError(reason)
            webpush(subscription_info={"endpoint": s["endpoint"], "keys": s["keys"]},
                    data=data, vapid_private_key=priv_pem, vapid_claims=dict(claims),
                    ttl=TTL, timeout=TIMEOUT)
            sent += 1
            continue
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            permanent, err = status in (404, 410), e
        except requests.RequestException as e:
            # Before ValueError: some requests errors subclass it but are network.
            err = e
        except (KeyError, TypeError, ValueError) as e:
            permanent, err = True, e
        except Exception as e:
            err = e
        # Error type only: push-service messages and request errors embed the
        # endpoint URL, which is a bearer capability for this person's device.
        log.emit("push.failed", level="error", uid=uid, status=status,
                 error_type=type(err).__name__, pruned=permanent)
        if permanent:
            dead.add(_ep(s))
        else:
            failed += 1

    if dead:
        # Re-read so a device subscribed while we were sending is not lost.
        current = load(uid, "push", []) or []
        save(uid, "push", [s for s in current if isinstance(s, dict) and _ep(s) not in dead])
    if kind and (sent or native["sent"]):
        _count_sent(uid, kind)
    return {"sent": sent + native["sent"], "pruned": len(dead) + native["pruned"],
            "failed": failed + native["failed"]}


def _count_sent(uid, kind):
    from app import log
    try:
        from app import activity
        activity.track_lake(uid, "push_sent", None, kind=kind)
    except Exception as e:           # a push that went out stays sent
        log.emit("activity.track_failed", uid=uid, name="push_sent",
                 error=type(e).__name__)
