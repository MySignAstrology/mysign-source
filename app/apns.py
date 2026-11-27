"""
Native push, for the iPhone app (APNs).

Web Push does not fire inside the App Store shell, so there the same
notifications go out through Apple's push service instead. This is a second
delivery channel and nothing more: app/push.py `send` is still the one door
every notification leaves by, and her settings (time, style, off), the
morning job's stamps and the sky push's opt-in are the ones the web uses.

The app hands over a device token once she turns notifications on
(native/ios/App/App/PushPlugin.swift, through native/bridge.js). It is kept
beside the web-push subscriptions, under her account, a few devices at most.
A token belongs to one account at a time: the phone's next account takes it
with it, so nobody's morning lands on a phone someone else is signed in on.

Off unless switched on: `apns` = "1" in the stack's secret, together with the
key (`apns_key`, the .p8 file's text), `apns_key_id` and `apns_team_id`. With
any of them missing the routes answer "not available", the app shows no
switch, and nothing is ever sent.

No SDK. The provider token is an ES256 JWT signed with `cryptography`, which
already ships for web push, and the request is one HTTP/2 POST (httpx + h2).
Both are imported on first use, to stay off the cold-start path
(tests/test_coldstart.py).
"""
import base64
import datetime as dt
import hashlib
import json
import os
import re
import threading
import time

from engine.store import delete, load, save

BUNDLE_ID = "app.mysign.ios"
HOSTS = {"production": "https://api.push.apple.com",
         "sandbox": "https://api.sandbox.push.apple.com"}
KIND = "apns"                     # her devices, beside "push" (the web's)
INDEX = "_apns"                   # token → account, outside any account
MAX_DEVICES = 5                   # the same cap as the web's (app/push.py)
TTL = 12 * 3600                   # Apple keeps trying this long; a morning note is stale after
TIMEOUT = 10                      # seconds per call; the cron runs on a budget
# Apple refuses a provider token older than an hour, and refuses one that is
# replaced more often than every twenty minutes. Fifty keeps clear of both.
TOKEN_LIFE = 50 * 60
RETRY_WAIT = 0.5                  # before the one retry of a 5xx or a dropped connection

# A device token is opaque bytes, sent by the app as lowercase hex. 32 bytes
# today; Apple says not to assume the length, so longer ones are let through.
_TOKEN = re.compile(r"^[0-9a-f]{64,200}$")

_lock = threading.Lock()          # the cron sends from worker threads
_jwt = {}                         # the cached provider token: {"token", "at", "for"}
_client = None


def _env(name):
    return (os.environ.get(name) or "").strip()


def enabled():
    return (_env("MYSIGN_APNS") == "1" and bool(_env("MYSIGN_APNS_KEY"))
            and bool(_env("MYSIGN_APNS_KEY_ID")) and bool(_env("MYSIGN_APNS_TEAM_ID")))


def host():
    """Apple runs two services, and a token from one means nothing to the other:
    builds run from Xcode register with sandbox, TestFlight and the App Store
    with production. `apns_env` = "sandbox" in the secret picks the first."""
    return HOSTS["sandbox" if _env("MYSIGN_APNS_ENV").lower() in ("sandbox", "development")
                 else "production"]


# ------------------------------------------------------------------ devices

def clean(token):
    """The token as stored, or None if it isn't one."""
    t = token.strip().lower() if isinstance(token, str) else ""
    return t if _TOKEN.match(t) else None


def _slot(token):
    # Hashed: the index is keyed by it, and keys show up in listings and logs.
    return "t-" + hashlib.sha256(token.encode()).hexdigest()


def _held(uid):
    return [d for d in (load(uid, KIND, []) or []) if isinstance(d, dict) and d.get("token")]


def devices(uid):
    return len(_held(uid))


def active(uid):
    """She has a phone to send to, and sending is switched on."""
    return enabled() and bool(_held(uid))


def registered(uid, token):
    token = clean(token)
    return bool(token) and any(d["token"] == token for d in _held(uid))


def _drop(uid, tokens):
    """Take these tokens off one account, and their index lines if they are hers."""
    kept = [d for d in _held(uid) if d["token"] not in tokens]
    save(uid, KIND, kept or None)
    for t in tokens:
        if (load(INDEX, _slot(t), None) or {}).get("uid") == uid:
            delete(INDEX, _slot(t))
    return len(kept)


def register(uid, token, tz=None, replaces=None, now=None):
    """
    Returns the device count, or False if that isn't a device token.

    `replaces` is the token this phone had before iOS gave it a new one, so a
    refresh swaps the line instead of leaving a dead one to be found by a 410.
    """
    token = clean(token)
    if not token:
        return False
    # The phone changed hands: whoever had it registered no longer gets her
    # notifications on it. Signing out does this too, when it reaches us.
    before = (load(INDEX, _slot(token), None) or {}).get("uid")
    if before and before != uid:
        _drop(before, {token})
    gone = {token} | ({clean(replaces)} - {None})
    held = [d for d in _held(uid) if d["token"] not in gone]
    at = (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")
    held.append({"token": token, "tz": tz if isinstance(tz, str) else None, "at": at})
    # Newest wins past the cap, as on the web: every send is a request per device.
    over, held = held[:-MAX_DEVICES], held[-MAX_DEVICES:]
    save(uid, KIND, held)
    for t in (gone - {token}) | {d["token"] for d in over}:
        if (load(INDEX, _slot(t), None) or {}).get("uid") == uid:
            delete(INDEX, _slot(t))
    save(INDEX, _slot(token), {"uid": uid})
    return len(held)


def unregister(uid, token):
    """This phone, off. Only ever her own line: a token held by another account
    is left exactly as it was."""
    token = clean(token)
    return _drop(uid, {token}) if token else devices(uid)


def forget(uid):
    """Signed out, or the account is going: every phone, and the index lines."""
    held = {d["token"] for d in _held(uid)}
    if held:
        _drop(uid, held)
    return len(held)


def summary(uid):
    """For her export: that these devices exist, never the tokens themselves."""
    return [{"registered_at": d.get("at"), "tz": d.get("tz")} for d in _held(uid)]


# ----------------------------------------------------------- provider token

def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _pem():
    """The .p8 key. A JSON secret often holds it with the line breaks written
    out as \\n, or as the bare base64 body; both are put back together here."""
    raw = _env("MYSIGN_APNS_KEY").replace("\\n", "\n")
    if "BEGIN" not in raw:
        raw = f"-----BEGIN PRIVATE KEY-----\n{raw}\n-----END PRIVATE KEY-----"
    return raw.encode()


def provider_token(now=None):
    """
    The JWT that says a request is ours: ES256, key id in the header, team id
    and the time in the claims. Made once and reused for TOKEN_LIFE.
    """
    now = time.time() if now is None else now
    who = (_env("MYSIGN_APNS_KEY_ID"), _env("MYSIGN_APNS_TEAM_ID"))
    with _lock:
        if _jwt.get("for") == who and 0 <= now - _jwt["at"] < TOKEN_LIFE:
            return _jwt["token"]
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        key = serialization.load_pem_private_key(_pem(), password=None)
        head = _b64(json.dumps({"alg": "ES256", "kid": who[0]}, separators=(",", ":")).encode())
        claims = _b64(json.dumps({"iss": who[1], "iat": int(now)}, separators=(",", ":")).encode())
        # JOSE wants the bare r and s, 32 bytes each, not the DER that comes back.
        r, s = decode_dss_signature(key.sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
        token = f"{head}.{claims}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
        _jwt.update({"token": token, "at": now, "for": who})
        return token


def _expire_provider_token():
    with _lock:
        _jwt.clear()


# ------------------------------------------------------------------ sending

def payload(title, body, url="/app", kind=None):
    """
    What the phone gets. The same words the web push shows and nothing more.
    iOS prints the app's name above every notification, so the title "My Sign"
    would only say it twice; a real title (the morning's sky reason) is kept.
    `url` is where a tap lands in the app (native/bridge.js reads it).
    """
    alert = {"title": title, "body": body} if title and title != "My Sign" else {"body": body}
    out = {"aps": {"alert": alert, "sound": "default"}, "url": url}
    if kind:
        out["kind"] = kind
    return out


def _http():
    global _client
    with _lock:
        if _client is None:
            import httpx
            # One connection, kept: Apple treats a new one per notification as abuse.
            _client = httpx.Client(http2=True, timeout=TIMEOUT)
        return _client


def _post(token, data):
    """One request to Apple. Returns (status, reason, gone_since_ms)."""
    r = _http().post(f"{host()}/3/device/{token}", content=data, headers={
        "authorization": f"bearer {provider_token()}",
        "apns-topic": BUNDLE_ID,
        "apns-push-type": "alert",
        "apns-priority": "10",
        "apns-expiration": str(int(time.time()) + TTL),
        "content-type": "application/json"})
    reason, since = None, None
    if r.status_code != 200:
        try:
            body = r.json()
            reason, since = body.get("reason"), body.get("timestamp")
        except Exception:
            pass
    return r.status_code, reason, since


def _deliver(token, data):
    """
    "sent", "gone" or "failed", and what Apple said.

    Retried once, at once: a 5xx, a dropped connection (a Lambda that slept
    wakes up holding a dead one), or a provider token Apple says has expired.
    Not retried: 429, which is Apple asking for fewer requests to this device.
    Everything that fails is kept and tried again with the next notification;
    only 410 means the token is finished.
    """
    status, reason, since, err = None, None, None, None
    for attempt in (1, 2):
        err = None
        try:
            status, reason, since = _post(token, data)
        except Exception as e:
            status, reason, err = None, None, type(e).__name__
        if status == 200:
            return "sent", {"status": 200}
        if status == 410:
            return "gone", {"status": 410, "reason": reason, "since": since}
        again = err or (status or 0) >= 500
        if status == 403 and reason == "ExpiredProviderToken":
            _expire_provider_token()
            again = True
        if not again or attempt == 2:
            break
        time.sleep(RETRY_WAIT)
    return "failed", {"status": status, "reason": reason, "error_type": err}


def _still_gone(device, since):
    """Apple's 410 carries the moment it last knew the token was dead. A phone
    that registered again after that moment is not the one it means."""
    try:
        at = dt.datetime.fromisoformat(device["at"]).timestamp() * 1000
        return since is None or at <= float(since)
    except (KeyError, TypeError, ValueError):
        return True


def send(uid, title, body, url="/app", kind=None):
    """
    To every phone she has registered. Never raises, like push.send: one bad
    device must not cost anyone else their morning. Called by push.send only.
    """
    zero = {"sent": 0, "pruned": 0, "failed": 0}
    if not enabled():
        return zero
    held = _held(uid)
    if not held:
        return zero
    from app import log
    data = json.dumps(payload(title, body, url, kind), separators=(",", ":"),
                      ensure_ascii=False).encode()
    sent, failed, dead = 0, 0, set()
    for d in held:
        try:
            what, said = _deliver(d["token"], data)
        except Exception as e:        # a bad key, say: logged below, nobody's morning lost
            what, said = "failed", {"status": None, "reason": None, "error_type": type(e).__name__}
        if what == "sent":
            sent += 1
            continue
        pruned = what == "gone" and _still_gone(d, said.get("since"))
        # Never the token: it is the address of her phone. Apple's reason is a
        # fixed word ("Unregistered", "BadDeviceToken", "TooManyRequests").
        log.emit("apns.failed", level="error", uid=uid, status=said.get("status"),
                 reason=said.get("reason"), error_type=said.get("error_type"), pruned=pruned)
        if pruned:
            dead.add(d["token"])
        else:
            failed += 1
    if dead:
        _drop(uid, dead)              # re-reads, so a phone added meanwhile is kept
    return {"sent": sent, "pruned": len(dead), "failed": failed}
