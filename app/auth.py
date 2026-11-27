"""
Email or phone + one-time code. No passwords, no dependencies.

Passwords would mean storage, reset flows, breach exposure, and a signup form
people abandon — for an app whose entire value is that it remembers you. A code
in an inbox proves the same thing and can't leak anything worth stealing.

Sessions are HMAC-signed cookies. Nothing server-side to look up, and a rotated
secret invalidates every session at once, which is the behaviour you want when
something goes wrong.

A phone number is the same idea over SMS, with one difference that shapes
everything below: every text costs money, and a sign-in form that sends texts
to any number typed into it is a well-known way to be billed for someone
else's fraud (SMS pumping). So SMS carries limits email does not — per number,
per caller, and a hard daily ceiling for the whole service.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time

from engine import store
from engine.store import load, save

COOKIE = "ms_session"
SESSION_DAYS = 90
CODE_TTL = 15 * 60
MAX_ATTEMPTS = 5             # guesses at any one code
CODES_KEPT = 2               # a new code doesn't kill the one already in her inbox
# Devices that have signed in to an address before (audit, Nov 2026). Codes and
# limits shared by everyone asking for an address let a stranger lock her out:
# two requests evicted the code in her inbox, thirty spent the day's sends. A
# remembered device keeps its own code and isn't held to the address-wide caps.
TRUSTED_KEPT = 5
TRUSTED_DAYS = 180

# Sign-in limits (security audit M1, M6, L1). Every number is a ceiling a real
# person never meets; each is an atomic counter in the store, so parallel
# requests can't slip past, and a cold start resets nothing. Keys hold only
# keyed hashes of the address, the network address or the email domain.
#
# Sends, per address. The per-(address, network) limit is what keeps a stranger
# from locking her out by requesting codes for her: before, five requests an
# hour from anyone held her at "Too many codes" indefinitely and each one also
# voided the code she was about to type.
SENDS_PER_ID_HOUR = 12
SENDS_PER_ID_DAY = 30
SENDS_PER_ID_IP_HOUR = 5
MAX_SENDS_PER_HOUR = SENDS_PER_ID_HOUR       # older name, kept for callers
#
# Guesses. The old ceiling was five per code with every new code resetting it:
# 25 an hour, 600 a day against one address, forever. Now guesses are counted
# per address over the UTC day and week, and a new code resets nothing. The
# device that asked for a code (a cookie set by /api/auth/request) is "own";
# a guess from anywhere else is "foreign" and has the much smaller budget —
# so someone burning guesses at her address from elsewhere doesn't stop her
# signing in with the code she asked for here, and a signed-in session is
# never touched by any of it.
VERIFY_FOREIGN_PER_ID_DAY = 10
VERIFY_FOREIGN_PER_ID_WEEK = 25
VERIFY_PER_ID_DAY = 30
#
# Per network address and service-wide. The WAF has edge copies (auth-verify-
# rate, auth-request-rate); these are what holds if the WAF is detached or the
# origin is reached some other way. Off under local dev auth only (refused on
# Lambda), where every test signs in from one address.
VERIFY_PER_IP_HOUR = 30
EMAIL_PER_IP_HOUR = 20
EMAIL_PER_DOMAIN_HOUR = 300
REVIEW_PER_HOUR = 10

# SMS only. Tight because a person needs one text, maybe two; anything past
# that is a script.
SMS_PER_NUMBER_HOUR = 3
SMS_PER_NUMBER_DAY = 10
SMS_PER_IP_HOUR = 5
SMS_RESEND_SECONDS = 30
SMS_OFF = "Text sign-in isn't available yet — use your email."
SMS_CAPPED = "Text codes are paused for today — use your email instead."

_secret = None


def secret():
    """From the environment in production. Locally, generated once and kept."""
    global _secret
    if _secret:
        return _secret
    env = os.environ.get("MYSIGN_SECRET")
    if env:
        _secret = env.encode()
        return _secret
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        # Fail closed. The fallback below keeps a secret in the table, which
        # anyone with read access could use to forge a session — and two cold
        # starts racing could each mint a different one, signing people out
        # at random. In production a missing secret is an outage, not a mode.
        raise RuntimeError("MYSIGN_SECRET is not set")
    stored = load("_auth", "dev_secret", None)
    if not stored:
        stored = secrets.token_urlsafe(32)
        save("_auth", "dev_secret", stored)
    _secret = stored.encode()
    return _secret


# ----------------------------------------------------------------- sessions

def _b64(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(uid, ver=0):
    payload = _b64(json.dumps({"u": uid, "v": ver,
                               "e": int(time.time()) + SESSION_DAYS * 86400}).encode())
    sig = _b64(hmac.new(secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def _claims(token):
    try:
        payload, sig = token.split(".", 1)
        expected = _b64(hmac.new(secret(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_unb64(payload))
        return data if data["e"] > time.time() else None
    except Exception:
        return None


def verify(token):
    data = _claims(token)
    return data["u"] if data else None


def session_version(uid):
    return (load(uid, "account", {}) or {}).get("session_ver", 0)


def revoke_sessions(uid):
    """
    Every cookie issued so far stops working. A signed cookie cannot be
    recalled, so the account carries a version the cookie must match; signing
    out bumps it. Deletion needs nothing — no account, no session.
    """
    acct = load(uid, "account", None)
    if acct:
        acct["session_ver"] = acct.get("session_ver", 0) + 1
        save(uid, "account", acct)


# -------------------------------------------------------------------- users

KINDS = ("email", "phone")


def normalize_phone(raw):
    """
    E.164 for a US number, or None.

    US only for now: the texts go out from a US toll-free number, and every
    other country is its own registration, price and fraud profile. Accepts
    what people actually type — (555) 123-4567, 555-123-4567, +1 555 123 4567,
    15551234567 — and refuses anything that can't be a real NANP number, so a
    typo fails here rather than as a billed send to nowhere.
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not re.fullmatch(r"\+?[\d\s().-]{7,24}", s):
        return None
    digits = re.sub(r"\D", "", s)
    if s.startswith("+") and not digits.startswith("1"):
        return None                     # +44, +91 … not yet
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    if len(digits) != 10:
        return None
    # Area code and exchange never start with 0 or 1; N11 codes (411, 911) are
    # services, not area codes.
    if digits[0] in "01" or digits[3] in "01" or digits[1:3] == "11":
        return None
    return "+1" + digits


# One @, no spaces, a dotted domain. Not the whole RFC: just enough to refuse
# what can never receive a code (a doubled paste, "a@b@c.com", "a b@c.com").
EMAIL_RE = re.compile(r"[^@\s]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+")


def valid_email(norm):
    return bool(norm) and len(norm) <= 254 and bool(EMAIL_RE.fullmatch(norm))


def detect(raw):
    """Which kind a single "phone or email" field holds, or None."""
    raw = (raw or "").strip()
    if "@" in raw:
        return "email"
    if re.search(r"\d", raw) and re.fullmatch(r"\+?[\d\s().-]+", raw):
        return "phone"
    return None


def _ikey(kind, value):
    """
    (hash, normalized) for an identifier, or (None, None) if it can't be one.
    Normalized and hashed before it becomes a storage key, so the key
    namespace never contains a plaintext address or number.
    """
    if kind == "email":
        norm = (value or "").strip().lower()
    elif kind == "phone":
        norm = normalize_phone(value)
    else:
        norm = None
    if not norm:
        return None, None
    return hashlib.sha256(norm.encode()).hexdigest()[:24], norm


def _ekey(email):
    return _ikey("email", email)


def _id_kind(kind, k):
    return f"{kind}-{k}"


def _code_kind(kind, k):
    # Email keeps its original name so codes in flight across the deploy still
    # work; phone gets its own prefix so the two can never share a record.
    return f"code-{k}" if kind == "email" else f"code-{kind}-{k}"


def uid_for_id(kind, value, create=True):
    k, norm = _ikey(kind, value)
    if not k:
        return None
    uid = load("_auth", _id_kind(kind, k), None)
    if uid or not create:
        return uid
    uid = "u" + secrets.token_hex(6)
    save("_auth", _id_kind(kind, k), uid)
    # The plaintext lives on the account record and nowhere else, the same as
    # email: it is what she signs in with, and export and deletion find it here.
    created = int(time.time())
    save(uid, "account", {kind: norm, "created_at": created, "consented_at": None})
    # A roster the scheduler can iterate. Listing directories works locally and
    # not at all on DynamoDB, so the index is explicit.
    # One item per member: a single list item was read-modify-write (two
    # signups at once lost one, who then never got a morning push) and would
    # have hit the item size ceiling at around twenty thousand people.
    store.add_member("users", uid)
    # And the operator console's list, newest first (app/opsindex.py).
    from app import opsindex
    opsindex.joined(uid, created)
    return uid


def uid_for(email, create=True):
    return uid_for_id("email", email, create)


def forget_identities(uid, acct):
    """
    Every sign-in key that points at her: the email and phone indexes and any
    code in flight. A mapping is only removed if it is still hers, so a number
    that has since moved to another account keeps working there.
    """
    for kind in KINDS:
        k, _ = _ikey(kind, acct.get(kind))
        if not k:
            continue
        if load("_auth", _id_kind(kind, k), None) == uid:
            store.delete("_auth", _id_kind(kind, k))
        for c in (_code_kind(kind, k), _code_kind(kind, k) + "#ctr"):
            store.delete("_auth", c)


def all_users():
    # The legacy single-item roster is read too, so nobody who signed up
    # before the change drops out of the morning push.
    legacy = load("_auth", "users", []) or []
    return sorted(set(store.members("users")) | set(legacy))


def forget(uid):
    store.remove_member("users", uid)
    legacy = load("_auth", "users", []) or []
    if uid in legacy:
        save("_auth", "users", [u for u in legacy if u != uid])


# --------------------------------------------------------------------- codes

def dev_auth_allowed():
    """
    Whether it is safe to hand the login code back in the HTTP response.

    This has to fail closed. The previous version returned the code whenever SES
    was unconfigured, which asks the wrong question: a deploy that forgets one
    environment variable then hands anyone a login code for any address. That is
    a complete authentication bypass, not a degraded mode.

    So: an explicit opt-in, and never on Lambda regardless of what the opt-in
    says.
    """
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return False
    return os.environ.get("MYSIGN_DEV_AUTH") == "1"


def sms_enabled():
    """Texts go out only once an origination number is configured — which
    happens only after the carriers approve its registration."""
    return bool(os.environ.get("MYSIGN_SMS_ORIGIN"))


def sms_daily_cap():
    try:
        return int(os.environ.get("MYSIGN_SMS_DAILY_CAP", "500"))
    except ValueError:
        return 500


def _sms_day():
    return time.strftime("%Y-%m-%d", time.gmtime())


def sms_available():
    """What the sign-in screen asks before offering a phone field: a sender
    (or the local dev echo), and today's ceiling not yet reached."""
    if not (sms_enabled() or dev_auth_allowed()):
        return False
    return store.counter("_auth", f"sms-day-{_sms_day()}", "n") < sms_daily_cap()


def ip_hash(ip):
    """Keyed, so the stored value can't be reversed by hashing all four
    billion IPv4 addresses. Short-lived: it only ever backs an hourly count."""
    if not ip:
        return None
    return hmac.new(secret(), f"ip:{ip}".encode(), hashlib.sha256).hexdigest()[:24]


def _sms_limits(k, ip):
    """
    None if this text may go out, else (message, status). Atomic counters, so
    parallel requests can't slip past a read-then-write check. Ordered so the
    number's own limits are spent first: a script hammering one victim stops
    there, before it touches the service-wide budget.
    """
    from app import log
    now = time.time()
    hour = time.strftime("%Y%m%d%H", time.gmtime(now))
    day = time.strftime("%Y%m%d", time.gmtime(now))
    if store.incr("_auth", f"sms-n-{k}-{hour}", "n", limit=SMS_PER_NUMBER_HOUR,
                  ttl=int(now) + 2 * 3600) is None or \
       store.incr("_auth", f"sms-n-{k}-{day}", "n", limit=SMS_PER_NUMBER_DAY,
                  ttl=int(now) + 2 * 86400) is None:
        log.emit("auth.sms_limited", scope="number", phone_hash=k[:8])
        return "Too many codes sent to that number. Try again later, or use your email.", 429
    h = ip_hash(ip)
    if h and store.incr("_auth", f"sms-ip-{h}-{hour}", "n", limit=SMS_PER_IP_HOUR,
                        ttl=int(now) + 2 * 3600) is None:
        log.emit("auth.sms_limited", scope="ip", ip_hash=h[:8])
        return "Too many codes requested. Try again in an hour, or use your email.", 429
    if store.incr("_auth", f"sms-day-{_sms_day()}", "n", limit=sms_daily_cap(),
                  ttl=int(now) + 3 * 86400) is None:
        # The circuit breaker against SMS pumping. Alarmed: a person can still
        # sign in with email, but someone should look at why.
        log.emit("auth.sms_cap", level="error", cap=sms_daily_cap())
        return SMS_CAPPED, 503
    return None


def request_code(email):
    return request_code_for("email", email)


def request_code_for(kind, value, ip=None, device=None):
    """
    Returns (ok, payload). The code is echoed back only in explicitly-enabled
    local development; everywhere else it goes to the inbox or phone, or
    nowhere. `ip` feeds the per-caller limits and is only ever hashed.
    `device` is the asking browser's random cookie (server.py sets it); a
    guess later made with the same cookie is "own" (see VERIFY_* above).
    """
    k, norm = _ikey(kind, value)
    if kind == "phone":
        if not k:
            return False, {"error": "That doesn't look like a US mobile number."}
        # Fail closed before anything is stored or counted.
        if not (sms_enabled() or dev_auth_allowed()):
            return False, {"error": SMS_OFF, "status": 503}
    elif not k or not valid_email(norm):
        return False, {"error": "That doesn't look like an email address."}

    channel = "sms" if kind == "phone" else "email"
    hint = {"phone_hash" if kind == "phone" else "email_hash": k[:8]}
    if kind == "email" and is_review_account(norm):
        # App Review's account: no email is sent, its fixed code works. Logged
        # so its traffic is never mistaken for a person's.
        from app import log
        log.emit("auth.review_account", **hint)
        return True, {"sent": True, "channel": channel}

    ck = _code_kind(kind, k)
    rec = load("_auth", ck, None) or {}
    dh = _device_hash(device)
    trusted = _is_trusted(k, dh)
    window = [t for t in rec.get("sends", []) if t > time.time() - 3600]
    if len(window) >= SENDS_PER_ID_HOUR and not trusted:
        return False, {"error": "Too many codes requested. Try again in an hour."}
    refused = _send_limits(k, ip, trusted=trusted)
    if refused:
        return False, {"error": refused[0], "status": refused[1]}
    if kind == "phone":
        if window and time.time() - window[-1] < SMS_RESEND_SECONDS:
            return False, {"error": "A code is on its way — give it half a minute.",
                           "status": 429}
        refused = _sms_limits(k, ip)
        if refused:
            return False, {"error": refused[0], "status": refused[1]}
    else:
        refused = _email_limits(norm, ip)
        if refused:
            return False, {"error": refused[0], "status": refused[1]}

    code = f"{secrets.randbelow(1000000):06d}"
    # The new code joins the newest one still in flight rather than replacing
    # it, so a request she didn't make can't void the code she's typing. Each
    # code has its own id, for its own five tries; a new one resets nothing.
    entry = {"id": secrets.token_hex(4), "hash": _code_hash(code),
             "expires": time.time() + CODE_TTL, "dev": dh}
    save("_auth", ck, {
        "codes": _keep_codes(k, _live_codes(rec) + [entry]),
        "sends": window + [time.time()],
    }, ttl=int(time.time()) + 3600)          # the table forgets it within the day

    if kind == "phone" and sms_enabled():
        # Same contract as mail below: a refused send is a clean 503, never a
        # 500 and never a leaked code.
        try:
            _send_sms(norm, code)
        except Exception as e:
            # Type and API error code only — the message can quote the number.
            code_ = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            return False, {
                "error": "Text sign-in is unavailable right now — use your email.",
                "status": 503,
                "log": f"SMS rejected the send: {type(e).__name__} {code_}".strip(),
            }
        return True, {"sent": True, "channel": channel}

    if kind == "email" and os.environ.get("MYSIGN_FROM_EMAIL"):
        # A mail failure is a configuration problem, not a bug in the request.
        # Returning a raw 500 tells the person nothing and looks like the app is
        # broken; the first live deploy did exactly that because the SES sender
        # had not been verified yet. Fail the same way as no transport at all —
        # cleanly, and without ever falling back to leaking the code.
        try:
            _send_email(norm, code)
        except Exception as e:
            # The error type and SES's code only — its message quotes the
            # recipient address, and addresses do not go in logs.
            code_ = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            return False, {
                "error": "Sign-in is unavailable right now. Try again shortly.",
                "status": 503,
                "log": f"SES rejected the send: {type(e).__name__} {code_}".strip(),
            }
        return True, {"sent": True, "channel": channel}

    if dev_auth_allowed():
        from app import log
        # The code itself is deliberately not in the structured line — it goes to
        # the plain stdout below, which only ever happens in local development.
        log.emit("auth.code.dev", **hint)
        print(f"[auth] code for {norm}: {code}")
        return True, {"sent": False, "dev_code": code, "channel": channel}

    # No transport and no explicit dev opt-in. Refuse rather than leak — the
    # code has already been stored, so an operator who fixes mail can have the
    # user retry.
    return False, {"error": "Sign-in is unavailable right now. Try again shortly.",
                   "status": 503,
                   "log": "MYSIGN_FROM_EMAIL is unset and dev auth is not enabled"}


def sms_body(code):
    """
    Under 160 GSM characters, so it is one billed part. The last line is the
    WebOTP / iOS domain-bound format: the browser offers the code only on our
    own site, which is also a small defence against a phishing page asking
    for it. Without a configured domain the line is left off.
    """
    body = f"My Sign: your code is {code}. It works for 15 minutes. Reply STOP to opt out."
    domain = os.environ.get("MYSIGN_DOMAIN", "").strip()
    return f"{body}\n\n@{domain} #{code}" if domain else body


def _send_sms(to, code):
    import boto3
    extra = ({"ConfigurationSetName": os.environ["MYSIGN_SMS_CONFIG_SET"]}
             if os.environ.get("MYSIGN_SMS_CONFIG_SET") else {})
    boto3.client("pinpoint-sms-voice-v2").send_text_message(
        **extra,
        DestinationPhoneNumber=to,
        OriginationIdentity=os.environ["MYSIGN_SMS_ORIGIN"],
        MessageBody=sms_body(code),
        MessageType="TRANSACTIONAL",
        # A code that lands after it has expired is a wasted, billed text.
        TimeToLive=CODE_TTL,
    )


EMAIL_HTML = """<!doctype html><html><body style="margin:0;background:#F1F0F4;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#16182A">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:40px 16px">
<table role="presentation" width="100%" style="max-width:420px" cellspacing="0" cellpadding="0">
<tr><td style="font-family:Georgia,'Times New Roman',serif;font-size:22px;padding-bottom:28px">My Sign</td></tr>
<tr><td style="font-size:15px;line-height:1.5;padding-bottom:20px">Your sign-in code:</td></tr>
<tr><td style="background:#FCFBFD;border:1px solid #D5D3DE;border-radius:3px;padding:20px;text-align:center;font-family:Menlo,Consolas,monospace;font-size:34px;letter-spacing:.18em;color:#8E6118">{code}</td></tr>
<tr><td style="font-size:14px;line-height:1.5;color:#4B4D62;padding-top:20px">It works for the next 15 minutes. If you didn't ask for this, you can ignore it — nobody can use it without your inbox.</td></tr>
<tr><td style="font-size:12px;color:#797B8C;padding-top:36px;border-top:1px solid #D5D3DE;margin-top:20px">A daily companion whose lens is astrology.</td></tr>
</table></td></tr></table></body></html>"""


def _send_email(to, code):
    import boto3
    extra = ({"ConfigurationSetName": os.environ["MYSIGN_SES_CONFIG_SET"]}
             if os.environ.get("MYSIGN_SES_CONFIG_SET") else {})
    boto3.client("ses").send_email(
        **extra,
        Source=os.environ["MYSIGN_FROM_EMAIL"],
        Destination={"ToAddresses": [to]},
        Message={
            "Subject": {"Data": f"{code} — your My Sign code"},
            "Body": {
                # Plain text first: it is what every client can render and
                # what the spam filters read. HTML is the same message, dressed.
                "Text": {"Data": f"Your My Sign code is {code}. It works for the next "
                                 f"15 minutes.\n\nIf you didn't ask for this, you can ignore it."},
                "Html": {"Data": EMAIL_HTML.format(code=code)},
            },
        },
    )


def is_review_account(email):
    """One allowlisted address for App Review, with a fixed code, both from the
    secret. Off unless both are set; the code is long, never logged, and the
    account it opens holds only seeded demo data."""
    want = os.environ.get("MYSIGN_REVIEW_EMAIL", "").strip().lower()
    return bool(want) and bool(os.environ.get("MYSIGN_REVIEW_CODE")) and \
        email.strip().lower() == want


def review_uid():
    want = os.environ.get("MYSIGN_REVIEW_EMAIL", "").strip().lower()
    return uid_for(want, create=False) if want else None


def _code_hash(code):
    return hashlib.sha256(((code or "").strip() + secret().decode()).encode()).hexdigest()


def _device_hash(device):
    if not device:
        return None
    return hmac.new(secret(), f"dev:{device}".encode(), hashlib.sha256).hexdigest()[:16]


def _live_codes(rec):
    """Unexpired, unused codes on a record. A record from before codes were
    kept two at a time holds one code at the top level; it reads as one."""
    if not rec:
        return []
    codes = rec.get("codes")
    if codes is None and rec.get("hash"):
        codes = [{"id": "attempts", "hash": rec["hash"], "expires": rec.get("expires", 0),
                  "dev": None}]
    return [c for c in codes or [] if c.get("expires", 0) >= time.time()]


def edge_limits_on():
    """Per-network and service-wide limits apply everywhere except local dev
    auth (never on Lambda: dev_auth_allowed() is refused there), unless a test
    turns them on with MYSIGN_AUTH_LIMITS=1."""
    return os.environ.get("MYSIGN_AUTH_LIMITS") == "1" or not dev_auth_allowed()


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _windows(now):
    return (time.strftime("%Y%m%d%H", time.gmtime(now)), time.strftime("%Y%m%d", time.gmtime(now)),
            f"w{int(now // (7 * 86400))}")


def _limited(scope, **hint):
    from app import log
    log.emit("auth.limited", scope=scope, **hint)


def _service_cap(name, limit, ttl_s, now):
    """A service-wide ceiling: refused past `limit`, and alarmed (AuthCap)."""
    hour, day, _ = _windows(now)
    window = hour if ttl_s <= 2 * 3600 else day
    if store.incr("_auth", f"{name}-{window}", "n", limit=limit, ttl=int(now) + ttl_s) is None:
        from app import log
        log.emit("auth.cap", level="error", cap=name, limit=limit)
        return True
    return False


def _is_trusted(k, dh):
    return bool(dh) and dh in (load("_auth", f"trust-{k}", None) or {}).get("devs", [])


def _trust(k, dh):
    """She signed in on this device: remember it for this address."""
    if not dh:
        return
    devs = [d for d in (load("_auth", f"trust-{k}", None) or {}).get("devs", []) if d != dh]
    save("_auth", f"trust-{k}", {"devs": (devs + [dh])[-TRUSTED_KEPT:]},
         ttl=int(time.time()) + TRUSTED_DAYS * 86400)


def _keep_codes(k, codes):
    """The newest code from each remembered device, plus the newest CODES_KEPT
    from anywhere else: requests from elsewhere can't evict hers."""
    devs = set((load("_auth", f"trust-{k}", None) or {}).get("devs", []))
    mine, seen = [], set()
    for c in reversed(codes):
        if c.get("dev") in devs and c["dev"] not in seen:
            seen.add(c["dev"])
            mine.append(c)
    rest = [c for c in codes if c not in mine][-CODES_KEPT:]
    return [c for c in codes if c in mine or c in rest]


def _send_limits(k, ip, trusted=False):
    """Per (address, network) and per address per day, for every channel.
    A remembered device skips the address-wide day cap (strangers can't spend
    it for her). None, or (message, status)."""
    now = time.time()
    hour, day, _ = _windows(now)
    h = ip_hash(ip)
    if h and store.incr("_auth", f"snd-{k}-{h}-{hour}", "n", limit=SENDS_PER_ID_IP_HOUR,
                        ttl=int(now) + 2 * 3600) is None:
        _limited("send_id_ip", ip_hash=h[:8])
        return "Too many codes requested. Try again in an hour.", 429
    if not trusted and store.incr("_auth", f"snd-{k}-{day}", "n", limit=SENDS_PER_ID_DAY,
                                  ttl=int(now) + 2 * 86400) is None:
        _limited("send_id_day")
        return "Too many codes requested today. Try again tomorrow.", 429
    return None


def email_hourly_cap():
    return _env_int("MYSIGN_EMAIL_HOURLY_CAP", 600)


def email_daily_cap():
    return _env_int("MYSIGN_EMAIL_DAILY_CAP", 5000)


def _email_limits(norm, ip):
    """
    The email counterpart of _sms_limits (audit M6). Every code is a message
    from our domain; scripted sends to dead or role addresses raise the bounce
    rate until SES pauses the account, and then nobody can sign in. So: per
    network address, per recipient domain, and a service-wide hourly and daily
    circuit breaker, alarmed. Ordered so the narrow limits are spent first.
    """
    if not edge_limits_on():
        return None
    now = time.time()
    hour, _, _ = _windows(now)
    h = ip_hash(ip)
    if h and store.incr("_auth", f"mail-ip-{h}-{hour}", "n", limit=EMAIL_PER_IP_HOUR,
                        ttl=int(now) + 2 * 3600) is None:
        _limited("email_ip", ip_hash=h[:8])
        return "Too many codes requested. Try again in an hour.", 429
    dom = norm.rsplit("@", 1)[-1]
    dh = hmac.new(secret(), f"dom:{dom}".encode(), hashlib.sha256).hexdigest()[:16]
    if store.incr("_auth", f"mail-dom-{dh}-{hour}", "n", limit=EMAIL_PER_DOMAIN_HOUR,
                  ttl=int(now) + 2 * 3600) is None:
        _limited("email_domain")
        return "Sign-in by email is busy right now. Try again in a few minutes.", 503
    if _service_cap("mail-h", email_hourly_cap(), 2 * 3600, now) or \
       _service_cap("mail-d", email_daily_cap(), 2 * 86400, now):
        return "Sign-in by email is paused for a little while. Try again shortly.", 503
    return None


def verify_hourly_cap():
    return _env_int("MYSIGN_VERIFY_HOURLY_CAP", 3000)


def _network_verify_limits(ip):
    """Per network address and service-wide guesses, before any address's own
    budget is touched, so one source can't spend someone else's."""
    if not edge_limits_on():
        return None
    now = time.time()
    hour, _, _ = _windows(now)
    h = ip_hash(ip)
    if h and store.incr("_auth", f"vip-{h}-{hour}", "n", limit=VERIFY_PER_IP_HOUR,
                        ttl=int(now) + 2 * 3600) is None:
        _limited("verify_ip", ip_hash=h[:8])
        return "Too many tries from this network. Try again in an hour."
    if _service_cap("verify-h", verify_hourly_cap(), 2 * 3600, now):
        return "Sign-in is busy right now. Try again in a few minutes."
    return None


def _id_verify_limits(k, own, dh=None):
    """This address's own guess budget for the day and week. None or a message.
    A remembered device guessing at its own code has a budget of its own, so
    guesses from elsewhere can't use up the day for her."""
    now = time.time()
    _, day, week = _windows(now)
    if own and _is_trusted(k, dh):
        if store.incr("_auth", f"vt-{k}-{dh}-{day}", "n", limit=VERIFY_PER_ID_DAY,
                      ttl=int(now) + 2 * 86400) is None:
            _limited("verify_trusted_day")
            return "Too many tries for this address today. Try again tomorrow."
        return None
    foreign_spent = not own and (
        store.incr("_auth", f"vf-{k}-{day}", "n", limit=VERIFY_FOREIGN_PER_ID_DAY,
                   ttl=int(now) + 2 * 86400) is None
        or store.incr("_auth", f"vf-{k}-{week}", "n", limit=VERIFY_FOREIGN_PER_ID_WEEK,
                      ttl=int(now) + 8 * 86400) is None)
    if foreign_spent:
        _limited("verify_foreign")
        # The way out is the device she's on: a code asked for here is "own".
        return ("Too many tries for this address from other devices. Ask for a "
                "new code here and enter it on this device.")
    if store.incr("_auth", f"va-{k}-{day}", "n", limit=VERIFY_PER_ID_DAY,
                  ttl=int(now) + 2 * 86400) is None:
        _limited("verify_id_day")
        return "Too many tries for this address today. Try again tomorrow."
    return None


def check_code(kind, value, code, ip=None, device=None):
    """
    (normalized, None) if the code is right for this identifier, else
    (None, error). Spends the code. Says nothing about accounts — sign-in and
    adding a number to an account both start here.

    Limits, cheapest and widest first: the network's and the service's, then
    this address's day and week, then the code's own five tries. A guess from
    the device that asked is compared only with that device's code; any other
    guess with every code in flight.
    """
    k, norm = _ikey(kind, value)
    ck = _code_kind(kind, k) if k else None
    rec = load("_auth", ck, None) if k else None
    if not rec:
        return None, "Ask for a code first."
    codes = _live_codes(rec)
    if not codes:
        return None, "That code expired. Ask for a new one."
    refused = _network_verify_limits(ip)
    if refused:
        return None, refused
    dh = _device_hash(device)
    mine = [c for c in codes if dh and c.get("dev") == dh]
    refused = _id_verify_limits(k, own=bool(mine), dh=dh)
    if refused:
        return None, refused

    given = _code_hash(code)
    tried = False
    for c in mine or codes:
        # Atomic. A read-modify-write count let forty parallel guesses through
        # a limit of five; now the sixth is refused however they arrive. Own
        # and foreign guesses spend separate fives, so guesses from elsewhere
        # can't use up the tries on the code she is about to type.
        field = c["id"] if mine else c["id"] + "x"
        if store.incr("_auth", ck, field, limit=MAX_ATTEMPTS,
                      ttl=int(time.time()) + 3600) is None:
            continue
        tried = True
        if hmac.compare_digest(given, c["hash"]):
            # Single use, and the other code in flight goes with it: she's in.
            save("_auth", ck, {"codes": [], "sends": rec.get("sends", [])},
                 ttl=int(time.time()) + 3600)
            if mine:
                _trust(k, dh)
            return norm, None
    if not tried:
        return None, "Too many tries. Ask for a new code."
    return None, "That code isn't right."


def verify_code_for(kind, value, code, ip=None, device=None):
    if kind == "email" and is_review_account(value or ""):
        # The App Review account's fixed code has no per-code budget of its
        # own, so it gets the network limits and an hourly one (audit L1).
        refused = _network_verify_limits(ip)
        if refused:
            return None, refused
        now = time.time()
        # Per network: one global counter let ten wrong guesses from anywhere
        # lock App Review out mid-review.
        if store.incr("_auth", f"review-{ip_hash(ip) or 'none'}-{_windows(now)[0]}", "n",
                      limit=REVIEW_PER_HOUR,
                      ttl=int(now) + 2 * 3600) is None:
            _limited("review")
            return None, "Too many tries. Try again in an hour."
        want = os.environ["MYSIGN_REVIEW_CODE"]
        if len(want) < 12:
            from app import log
            log.emit("auth.review_code_short", level="error")   # make it long; never log it
        if hmac.compare_digest((code or "").strip(), want):
            return uid_for(value), None
        return None, "That code isn't right."
    norm, err = check_code(kind, value, code, ip=ip, device=device)
    if err:
        return None, err
    return uid_for_id(kind, norm), None


def verify_code(email, code):
    return verify_code_for("email", email, code)


def link_identifier(uid, kind, value, code, ip=None, device=None):
    """
    Add a phone (or email) to a signed-in account, once its code proves she
    holds it. Ownership by someone else is only revealed after that proof, so
    this can't be used to ask whether a stranger's number has an account.
    One of each per account: a new number replaces the old, which stops
    signing in.
    """
    norm, err = check_code(kind, value, code, ip=ip, device=device)
    if err:
        return None, err
    k, _ = _ikey(kind, norm)
    owner = load("_auth", _id_kind(kind, k), None)
    noun = "number" if kind == "phone" else "email"
    if owner and owner != uid:
        return None, (f"That {noun} already signs in to a different account. Sign in "
                      f"with it there, or delete that account first.")
    acct = load(uid, "account", None)
    if acct is None:
        return None, "signed out"
    old_k, _ = _ikey(kind, acct.get(kind))
    if old_k and old_k != k and load("_auth", _id_kind(kind, old_k), None) == uid:
        store.delete("_auth", _id_kind(kind, old_k))
    save("_auth", _id_kind(kind, k), uid)
    acct[kind] = norm
    save(uid, "account", acct)
    return norm, None


def current_user(request):
    """
    The signed-in uid, or None. The cookie must be valid AND match a live
    account's session version: a deleted account or a signed-out session gets
    nothing, where before a deleted user's old cookie still read as signed in.
    The account is left on request.state so the next dependency need not
    fetch it again.
    """
    token = request.cookies.get(COOKIE, "")
    bearer = request.headers.get("authorization", "")
    if not token and bearer.lower().startswith("bearer "):
        token = bearer[7:].strip()          # the native app; see /api/auth/verify
    data = _claims(token)
    if not data:
        return None
    acct = load(data["u"], "account", None)
    if not acct or data.get("v", 0) != acct.get("session_ver", 0):
        return None
    request.state.account = acct
    return data["u"]
