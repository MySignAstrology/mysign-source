"""
My Sign.

  uvicorn app.server:app --reload --port 8420

Everything except the model call runs for real: Swiss Ephemeris computes the
chart and today's transits, salience is scored, context is assembled, memory is
written. Without ANTHROPIC_API_KEY the server runs in demo mode and answers from
a small canned set so the whole flow stays exercisable — the header says so.
"""
import datetime as dt
import hashlib
import html
import json
import os
import pathlib
import re
import secrets
import time
import uuid
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.bootstrap import load_secrets
from app.edge import NativeCors, OriginGuard, public_url

load_secrets()          # must run before DEMO reads the environment

from engine import costs, meter  # noqa: E402
from engine import grounding as grounding_mod  # noqa: E402

meter.install_lazy()    # every model call logs its cost (engine/meter.py)

from app import (  # noqa: E402
    activity,
    alerts,
    apns,
    apple,
    auth,
    billing,
    cities,
    health,
    log,
    metrics,
    ops,
    opsindex,
    places,
    psychic,
    push,
)
from engine import about as about_mod  # noqa: E402
from engine import (  # noqa: E402
    accuracy,
    agent,
    birthsky,
    cards,
    compat,
    compat_fun,
    context,
    corpus,
    couples,
    couples_research,
    couples_watch,
    crisis,
    daily,
    episode,
    freeread,
    funnel,
    glossary,
    guard,
    i18n,
    interpret,
    lines,
    localtime,
    models,
    personmem,
    placement,
    plan,
    progressed,
    quota,
    reading,
    rectify,
    redreply,
    safety,
    salience,
    speech,
    store,
    synastry,
    thread,
    tools,
    tradition,
    unlocks,
    voice,
)
from engine import check as checker  # noqa: E402
from engine import chips as chips_mod  # noqa: E402
from engine import memory as M  # noqa: E402
from engine import mood as mood_mod  # noqa: E402
from engine.chart import (  # noqa: E402
    SIGNS,
    aspects,
    day_phase,
    house_of,
    jd_ut,
    natal,
    plain,
    positions,
    with_time,
)
from engine.store import load, log_mention, save  # noqa: E402

STATIC = pathlib.Path(__file__).parent / "static"
SYSTEM = voice.system_prompt()
DEMO = not os.environ.get("ANTHROPIC_API_KEY")
# Plus is buyable on the web only when Stripe is switched on (app/billing.py),
# and in the iPhone app only when Apple is (app/apple.py). Until one of them
# is, every gate that sells stays open: a wall with nothing to buy is just a
# wall (plan.for_sale).
plan.PRICES["available"] = billing.enabled()
plan.APP_STORE["available"] = apple.enabled()
RECENT_MESSAGES = 100
ROLLOVER_MAX_DAYS = 3
MAX_MESSAGE = 4000          # characters. A 500KB message was accepted, and then
                            # re-sent as history for ten turns.
MIN_AGE = int(os.environ.get("MYSIGN_MIN_AGE", "18"))
SUPPORT_EMAIL = os.environ.get("MYSIGN_SUPPORT_EMAIL", "")          # what the client gets; the archive stays whole
AGE_MESSAGE = ("My Sign is for people 18 and over, so chat is paused on this account. "
               "You can still delete it, and everything in it, any time.")

app = FastAPI(title="My Sign")
# Only CloudFront may call the function directly once a domain is live — the
# Function URL is public, and the WAF and rate limits live at the edge.
app.add_middleware(OriginGuard)
log.install(app)


@app.middleware("http")
async def _charge_to(request: Request, call_next):
    """Model spend in this request is charged to whoever's session it carries
    (engine/costs.py). Attribution only, never authorization: the signature is
    checked, the account is not loaded."""
    from engine import costs
    tok = request.cookies.get(auth.COOKIE, "")
    bearer = request.headers.get("authorization", "")
    if not tok and bearer.lower().startswith("bearer "):
        tok = bearer[7:].strip()
    claims = auth._claims(tok) if tok else None
    costs.current_uid.set(claims["u"] if claims else None)
    return await call_next(request)
# Added last, so it is outermost: a 500 from log.py or a 403 from OriginGuard
# without this header reaches the iPhone app as a network error.
app.add_middleware(NativeCors)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ------------------------------------------------------------------- session

def me(request: Request):
    """Signed-in user, or 401. Every route that touches personal data uses this."""
    uid = auth.current_user(request)
    if not uid:
        raise HTTPException(401, "signed out")
    return uid


def consented(request: Request, uid: str = Depends(me)):
    """Signed in AND has agreed to the terms. Nothing is stored about a day
    until they have."""
    acct = getattr(request.state, "account", None) or load(uid, "account", {}) or {}
    if not acct.get("consented_at") or acct.get("consent_version") != CONSENT_VERSION:
        raise HTTPException(403, "consent required")
    # She told us she is under 18: normal service stops. /api/message decides
    # for itself, so a red message still gets an answer.
    if acct.get("age_flag") and request.url.path != "/api/message":
        raise HTTPException(403, {"code": "age", "message": AGE_MESSAGE})
    _note_tz(uid, acct, request.headers.get("x-tz"))
    return uid


def _note_tz(uid, acct, tz):
    """
    Where she is now, from the browser. Her day is her day: Lambda runs in UTC,
    and every "today" used to be the UTC day — an evening check-in in New York
    filed under tomorrow, the free messages reset at 7pm. The birth city's zone
    is only a fallback; it is where she was born, not where she lives.
    """
    if not tz or tz == acct.get("tz") or len(tz) > 64:
        return
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
    except Exception:
        return
    acct["tz"] = tz
    save(uid, "account", acct)


def _tz(uid):
    from zoneinfo import ZoneInfo
    name = ((load(uid, "account", {}) or {}).get("tz")
            or (_profile(uid) or {}).get("tz") or "UTC")
    try:
        return ZoneInfo(name)
    except Exception:
        return dt.timezone.utc


def _now(uid):
    return dt.datetime.now(_tz(uid))


def _today(uid):
    return _now(uid).date()


# ------------------------------------------------------------------- helpers

def _profile(uid):
    return load(uid, "profile", None)


def _tradition(uid=None):
    """
    Which astrology this chart is read in.

    Per-user when set — an astrologer evaluating the app should be able to see
    the same chart both ways — and the app-wide default otherwise.
    """
    prof = _profile(uid) if uid else None
    return ((prof or {}).get("houses") or tradition.house_system(),
            (prof or {}).get("rulerships")
            or os.environ.get("MYSIGN_RULERSHIPS") or tradition.DEFAULT_RULERS)


def _natal(uid):
    p = _profile(uid)
    if not p:
        return None
    # Marked with whether the time is real, so nothing downstream measures a
    # transit against a noon chart's Midheaven (engine/chart.with_time).
    return with_time(natal(*p["birth_ut"], houses=_tradition(uid)[0]),
                     p.get("birth_time_known", True))


def _sky(uid, day=None):
    day = day or _today(uid)
    # The house system is in the cache key rather than something to remember to
    # invalidate. Change the tradition and every derived artefact simply misses.
    houses, _ = _tradition(uid)
    cached = load(uid, _sky_key(day, houses), None)
    # A sky cached before positions were carried is recomputed rather than
    # served: the briefing would be missing the day's signs.
    if cached and "positions" in cached:
        return cached
    nat = _natal(uid)
    # Her local noon, the same instant the People tab scores against. A fixed
    # 17:00 UT put Today and "Today with {name}" up to sixteen hours apart for
    # someone in Sydney — about 8° of Moon, sometimes a different Moon sign on
    # the same visit.
    j = daily.local_noon_jd(day, _tz(uid))
    tpos = positions(j)
    sky = {
        "aspects_to_natal": aspects(tpos, nat),
        # Named for her calendar day: "full moon" on the date the exact full
        # Moon falls where she is, not on whichever day noon is nearest it.
        "moon": day_phase(day, _tz(uid)),
        "moon_sign": SIGNS[int(tpos["Moon"]["lon"] // 30)],
        # The Moon changes sign about every 2.5 days; when it does so today,
        # noon's sign is only half the story. Today says both.
        "moon_ingress": _moon_ingress(day, _tz(uid)),
        # No birth time, no houses — noon cusps would name an arena that is
        # not hers. Memory triggers keyed on a house simply never fire.
        "active_houses": (sorted({house_of(v["lon"], nat["house_cusps"]) for v in tpos.values()})
                          if nat.get("time_known", True) else []),
        "upcoming": [],
        "positions": {k: {"label": v["label"], "retrograde": v["retrograde"]}
                      for k, v in tpos.items()},
    }
    # Recomputable in milliseconds, so it need not outlive the week.
    save(uid, _sky_key(day, houses), sky, ttl=int(dt.datetime.now().timestamp()) + 3 * 86400)
    return sky


def _moon_ingress(day, tz):
    """{"from", "to", "at"} when the Moon enters a new sign during her local
    day (at = her local clock time, HH:MM), else None. Sydney's 30 October
    showed "Moon in Gemini" all day, though it was in Cancer from about 2pm."""
    import swisseph as swe

    from engine.ephemeris import FLAGS
    start = dt.datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(dt.timezone.utc)
    end = (dt.datetime(day.year, day.month, day.day, tzinfo=tz)
           + dt.timedelta(days=1)).astimezone(dt.timezone.utc)
    j0 = daily.jd_ut(start.year, start.month, start.day, start.hour + start.minute / 60)
    j1 = j0 + (end - start).total_seconds() / 86400

    def sign(j):
        return int(swe.calc_ut(j, swe.MOON, FLAGS)[0][0] // 30)
    a, b = sign(j0), sign(j1)
    if a == b:
        return None
    lo, hi = j0, j1                 # at most one ingress a day: ~13° < 30°
    for _ in range(20):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if sign(mid) == a else (lo, mid)
    when = start + dt.timedelta(days=hi - j0)
    return {"from": SIGNS[a], "to": SIGNS[b], "at": when.astimezone(tz).strftime("%H:%M")}


def _sky_key(day, houses):
    # v2: computed at her local noon. Skies cached at the old 17:00 UT simply
    # miss and are recomputed, rather than mixing the two instants.
    # v3: no angle, house or natal-Moon contacts for an unknown birth time.
    return f"sky-v3-{day}-{houses}"


def _sky_state(sky):
    return {"moon_phase": sky["moon"]["phase"], "moon_sign": sky["moon_sign"],
            "aspect_keys": [f"{a['transit']}-{a['aspect']}-{a['natal']}"
                            for a in sky["aspects_to_natal"]],
            "active_houses": sky["active_houses"]}


def rollover(uid, include_today=False):
    """
    Turn finished days into episodes. Runs on every state read, so memory
    accumulates from ordinary use with no cron and no background worker.

    Idempotent by date. Today is left alone unless forced — the day isn't over,
    and half a day makes a misleading episode.
    """
    if not _profile(uid):
        return []
    today = _today(uid)
    # Once a day is enough. Without this marker every state read walked every
    # thread shard she had ever written, three or four times per app open.
    if not include_today and load(uid, "rolled_through", None) == today.isoformat():
        return []
    done = {e["date"] for e in load(uid, "episodes", [])}
    days = thread.dates(uid)
    pending = [d for d in days if d not in done and (include_today or d < today.isoformat())]

    from engine import spend
    written = []
    # Bounded: each day is a model call on the request path. A user back after
    # a month away catches up over a few opens rather than timing one out.
    for iso in pending[:ROLLOVER_MAX_DAYS]:
        try:
            msgs = thread.for_date(uid, iso)
            transcript = "\n".join(f"{m['role']}: {m['text']}" for m in msgs)
            checkin = load(uid, f"checkin-{iso}", None) or {}
            # Metered (audit H1); spent, the day is still written, by the
            # heuristic pass demo mode uses.
            extract = M.extract_day if not M.has_key() or spend.claim(uid, "memory") \
                else M.extract_day_heuristic
            written.append(extract(uid, iso, checkin, transcript,
                                   _sky_state(_sky(uid, dt.date.fromisoformat(iso)))))
        except Exception as e:
            log.error("rollover.day.failed", exc=e, uid=uid, day=iso)

    if written:
        log.emit("rollover", uid=uid, days=len(written))
        last = load(uid, "patterns_run", None)
        if (not last or (today - dt.date.fromisoformat(last)).days >= 7) \
                and len(load(uid, "episodes", [])) >= 10 \
                and (not M.has_key() or spend.claim(uid, "memory")):
            try:
                M.weekly_pattern_job(uid)
            except Exception as e:
                log.error("patterns.failed", exc=e, uid=uid)
            # Stamped either way: a failing job retried on every open is a
            # slow app and a bill, not a pattern.
            save(uid, "patterns_run", today.isoformat())
    if not include_today and len(pending) <= ROLLOVER_MAX_DAYS:
        save(uid, "rolled_through", today.isoformat())
    return written


# The reading's starter questions are the main way into the chat now, so demo
# mode has to answer them — a generic "say more" there makes the whole reveal
# look broken to anyone trying this without a key.
# The demo voice, in everyday words: what a planet is doing, to which part of her.
# The planet stays named so every line is still checkable against the context.
_DEMO_DOING = {"conjunction": "sitting right on", "square": "poking at",
               "opposition": "pulling against", "trine": "on good terms with",
               "sextile": "giving a friendly nudge to"}
_DEMO_YOUR = {"Sun": "your sense of self", "Moon": "your feelings",
              "Mercury": "how you talk and think", "Venus": "your love life",
              "Mars": "your drive", "Jupiter": "your luck", "Saturn": "your sense of duty",
              "Uranus": "your need for freedom", "Neptune": "your dreamy side",
              "Pluto": "your deepest stuff", "Chiron": "your old sore spot",
              "Ascendant": "how you come across", "Midheaven": "your career",
              "North Node": "where you're headed"}


def _moon_words(phase):
    """'full moon' -> 'full', 'waxing crescent' -> 'a waxing crescent'."""
    return {"new moon": "new", "full moon": "full",
            "first quarter": "at first quarter",
            "last quarter": "at last quarter"}.get(phase, f"a {phase}")


def _demo_mech(planet, aspect, point):
    """'Mars', 'opposition', 'Ascendant' -> 'Mars is pulling against how you come across'."""
    return (f"{daily._the(planet)} is {_DEMO_DOING.get(aspect, 'busy with')} "
            f"{_DEMO_YOUR.get(point, 'your ' + point)}")


def _demo_body(text, verdict, block=""):
    """
    A stand-in reply when there is no API key.

    Built from the day's actual top aspect rather than written in advance. Fixed
    strings cannot be grounded in a context that changes daily — the eval caught
    the old ones claiming a 12th house on a day the context had no 12th house,
    which is the exact failure the whole engine exists to prevent. Demo mode has
    to obey the same rule as the model.
    """
    lr = re.match(r'About today\'s line, ".*": (.+)\.$', text)
    if lr:
        said = lr.group(1)
        if said.startswith("yes"):
            return "Good, I love when it lands.\n\nWhere's it showing up so far today?"
        if said.startswith("sort"):
            return "Fair, half a fit.\n\nWhich half is true and which isn't?"
        return "Then I'll take your word over the sky.\n\nWhat's today actually like?"
    ci = re.match(r"Checking in: (\d+)/10(?: — ([^.]+))?\.", text)
    if ci:
        n, felt = int(ci.group(1)), (ci.group(2) or "").strip()
        named = f"{felt.capitalize()}. " if felt else ""
        if n <= 4:
            return (f"{named}Thank you for saying it plainly. I've got it, and you don't "
                    f"have to make it sound better than it is.\n\nWhat's sitting heaviest?")
        if n >= 7:
            return (f"{named}I love that for you. Days like this are worth studying, "
                    f"not just enjoying.\n\nWhat made it good?")
        return (f"{named}Somewhere in the middle, then, which is where most of life "
                f"happens.\n\nWhat's behind it today?")
    if verdict["band"] == "low":
        return ("Honestly? Nothing much is happening in your sky today.\n\n"
                "Enjoy the quiet; it never lasts.")

    heads = verdict.get("headlines") or []
    if not heads:
        return ("Say more — what's actually going on? I'd rather answer the real "
                "question than the polite one.")

    top = heads[0]
    mech = _demo_mech(top["transit"], top["aspect"], top["natal"]) + " right now"

    # Her own words back, when the context carries any. Same rule as
    # personmem.notes(): a question she asked the app is not something she
    # logged, and the message being answered right now (or a check-in that is
    # itself that question) must never come back as a memory.
    from engine.memory import QUESTION_RE
    asked = lambda t: t.rstrip().endswith("?") and QUESTION_RE.match(t.strip())  # noqa: E731
    now = text.strip().lower()
    quoted = [q.strip() for q in re.findall(r'— "([^"]{6,120})"', block)
              if not asked(q) and q.strip().lower() != now]
    # Her punctuation stands; only close the sentence when she didn't.
    recall = (f' You logged this before: "{quoted[0]}'
              f'{"" if quoted[0][-1] in ".!?…" else "."}"') if quoted else ""

    if re.search(r"text|answer|reply|respond|him|her|them", text, re.I):
        return (f"Not today. {mech}, and it's close enough to count.{recall}\n\n"
                f"Sleep on it and answer tomorrow, short and sweet.")
    if re.search(r"end it|break up|leave him|leave her|dump|call it off", text, re.I):
        # Amber: no verdict, real timing, one question back — inside the cap the
        # day's salience allows, which on a quiet day is tighter.
        if verdict.get("max_sentences", 4) <= 3:
            return (f"That one is yours to call. What I can tell you is {mech}.\n\n"
                    f"What's making you ask today?")
        return (f"I'm not calling that one — it's yours to live inside afterwards.\n\n"
                f"What I can tell you is {mech}. Decide when you can still predict "
                f"yourself.\n\nWhat's making you ask today?")
    if re.search(r"mercury|retrograde|rx", text, re.I):
        return ("I only blame Mercury when the sky actually says so, so check the reading "
                f"above. What's really making noise is that {mech}.")
    # Her first, the sky as the reason: the same rule the model is held to.
    return (f"What you're feeling has a real reason, and an end date: {mech}.{recall}\n\n"
            f"It isn't random, and it won't last forever.")



def _trim_to(text, max_sentences):
    """
    Hold the reply to the cap the day's salience allows.

    Enforced here rather than inside each branch — that was the first attempt,
    and a test immediately found the branches it had missed. A contract checked
    in one place holds; one restated in five does not.
    """
    parts = [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p]
    if len(parts) <= max_sentences:
        return text
    kept = parts[:max_sentences]
    # Rejoin on the paragraph break if one fell inside what we kept.
    return " ".join(kept).replace(" \n\n", "\n\n")


# The v2 voice ends most replies on one question about her. Demo mode has no
# model to write one, so it picks from these by what she asked.
_DEMO_QUESTIONS = [
    # Amber first: a decision that's hard to undo gets the one question the
    # prompt allows there, never a playful one.
    (re.compile(r"end it|break up|leave him|leave her|dump|call it off|quit|move out", re.I),
     "What's making you ask today?"),
    (re.compile(r"text|answer|reply|respond|him|her|them", re.I),
     "What do you want them to feel when they read it?"),
    (re.compile(r"mercury|retrograde|rx", re.I), "What made you ask — is something going sideways?"),
    (re.compile(r"\bwhy\b", re.I), "Short version, or the deep cut?"),
]
_FOLLOW_UP = re.compile(r'FOLLOW UP[^\n]*\n\s+said [^\n]*— e\.g\. "([^"]+)"')


def _demo_reply(text, verdict, block=""):
    body = _trim_to(_demo_body(text, verdict, block), verdict.get("max_sentences", 4))
    if voice.VOICE == "v1":
        return body
    # Something she said was coming has happened: ask about it first, and let
    # that be the one question.
    due = _FOLLOW_UP.search(block)
    if due:
        # The follow-up is the whole reply: one question, and she answers it.
        return due.group(1)
    if body.rstrip().endswith("?"):
        return body
    q = next((q for rx, q in _DEMO_QUESTIONS if rx.search(text)),
             "What's actually going on today?")
    return f"{body}\n\n{q}"


_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday"]


def _demo_lookup_date(text, today=None):
    """
    Does this question name a day other than today?

    Only weekday names and explicit dates — enough to exercise the real tool
    path without a model, and it refuses to guess at anything vaguer.
    """
    low = text.lower()
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if m:
        return m.group(1)
    for i, name in enumerate(_WEEKDAYS):
        if re.search(rf"\b{name}\b", low):
            today = today or dt.date.today()
            ahead = (i - today.weekday()) % 7 or 7
            return (today + dt.timedelta(days=ahead)).isoformat()
    return None


def _demo_sky_reply(sky):
    top = (sky.get("aspects_to_her_chart") or [None])[0]
    when = f"{sky['weekday']} the {int(sky['date'][-2:])}"
    if not top:
        return (f"{when} looks calm for you: nothing much is happening in your sky. "
                f"The Moon is {_moon_words(sky['moon']['phase'])}.")
    return (f"{when}: {_demo_mech(top['transiting'], top['aspect'], top['her'])}. "
            f"The Moon is {_moon_words(sky['moon']['phase'])}.\n\n"
            f"That's the thing to plan around.")


# --------------------------------------------------------------------- pages

SITE = pathlib.Path(__file__).resolve().parent.parent / "site"
SITE_FILES = ("brand.css", "sky.js", "site.js", "track.js", "consent.js", "og.png", "robots.txt", "sitemap.xml",
              "shop-catalog.js")
APP_PARAMS = ("invite", "p", "sky")


@app.get("/")
def index(request: Request):
    """The website for strangers; the app for anyone signed in or arriving on
    an app link (an invite, a person, a sky). The same decision
    deploy/edge/router.js makes at the edge, so local dev and CloudFront with
    staticFromS3 off behave the same as with it on."""
    if auth.COOKIE in request.cookies or any(k in request.query_params for k in APP_PARAMS):
        q = request.url.query
        return RedirectResponse("/app" + (f"?{q}" if q else ""), status_code=302,
                                headers={"Cache-Control": "no-store"})
    page = SITE / "index.html"
    # The answer depends on the cookie; a heuristically cached website would
    # keep a signed-in visitor from ever being sent on to /app.
    return FileResponse(page if page.is_file() else STATIC / "index.html",
                        headers={"Cache-Control": "no-cache"})


def _site_file(name):
    def serve():
        f = SITE / name
        if not f.is_file():
            raise HTTPException(404)
        return FileResponse(f)
    return serve


for _name in SITE_FILES:
    app.add_api_route(f"/{_name}", _site_file(_name), methods=["GET"], include_in_schema=False)
if (SITE / "fonts").is_dir():
    app.mount("/fonts", StaticFiles(directory=SITE / "fonts"), name="fonts")


# The edge sends signed-in visitors and app links to /app (deploy/edge/router.js);
# this makes the same URLs work locally and with CloudFront still all-Lambda.
@app.get("/app", include_in_schema=False)
@app.get("/app/{view:path}", include_in_schema=False)
def app_shell(view: str = ""):
    if "." in view.rsplit("/", 1)[-1]:
        raise HTTPException(404)
    return FileResponse(STATIC / "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest",
                        media_type="application/manifest+json")


# Browsers ask for this on every visit whatever the page declares; without it
# every first load logs a 404.
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "icons" / "icon-192.png", media_type="image/png",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.get("/sw.js")
def service_worker():
    """Served from the root so its scope covers the whole app — a worker under
    /static/ can only control /static/."""
    return FileResponse(STATIC / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})


class ClientError(BaseModel):
    msg: str = Field(default="", max_length=400)
    src: str = Field(default="", max_length=200)
    line: int = 0
    col: int = 0
    view: str = Field(default="", max_length=40)


@app.post("/api/client-error")
def client_error(e: ClientError, request: Request):
    """
    A JavaScript error on her phone, which otherwise nobody would ever see.
    Public on purpose — the crash may be on the sign-in screen — so it keeps
    nothing identifying: the message and the script's path (no query string,
    no host), line, column and which view was open. Logged at warn, not error,
    so it feeds its own ClientErrors alarm rather than the app-error page.
    """
    if not _public_ok(request, "clienterr", 60):
        return Response(status_code=204)        # past the hour's share: not logged
    src = re.sub(r"^https?://[^/]+", "", e.src or "").split("?")[0].split("#")[0][:120]
    msg = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<email>", e.msg or "")[:200]
    log.emit("client.error", level="warn", msg=msg, src=src, line=max(0, e.line),
             col=max(0, e.col), view=(e.view or "")[:40])
    return Response(status_code=204)


@app.get("/api/glossary")
def glossary_terms(response: Response):
    """Every astrology word the app uses, in plain words (engine/glossary).
    Public and the same for everyone, so the edge may cache it."""
    response.headers["Cache-Control"] = "public, max-age=3600"
    return {"terms": glossary.terms()}


@app.get("/api/cities")
def city_lookup(q: str = ""):
    return cities.search(q)


@app.get("/api/places")
def place_lookup(q: str = "", uid: str = Depends(me)):
    """
    The wider search, for towns too small for the bundled list. Signed-in
    only and capped per day — each call is a billed request (app/places.py).
    """
    if not places.available():
        return {"results": [], "available": False}
    if not places.under_cap(uid, load, save):
        raise HTTPException(429, "You've done a lot of searches today. Pick the nearest town on "
                                 "the list — it changes your chart by less than a minute.")
    try:
        results = places.search(q)
    except Exception as e:
        log.error("places.failed", exc=e, uid=uid)
        raise HTTPException(503, "The wider place search isn't working right now. "
                                 "Pick the nearest town on the list instead.") from e
    log.emit("places.searched", uid=uid, found=len(results))
    return {"results": results, "available": True}


# ---------------------------------------------------------------------- auth

class Email(BaseModel):
    # Either field; `id` is a single "phone or email" box, sorted out here.
    email: Optional[str] = Field(default=None, max_length=254)
    phone: Optional[str] = Field(default=None, max_length=32)
    id: Optional[str] = Field(default=None, max_length=254)
    # The device's random analytics id (app/events.py ANON_RE), from the site's
    # sign-in sheet or the app's sign-in screen, so a first visit can be joined
    # to the sign-up. Optional, and anything that isn't one is ignored.
    anon_id: Optional[str] = Field(default=None, max_length=40)


class Code(Email):
    code: str = Field(max_length=12)


def _ident(b):
    """(kind, value) from whichever field was sent, or 400."""
    if b.email:
        return "email", b.email
    if b.phone:
        return "phone", b.phone
    kind = auth.detect(b.id)
    if kind:
        return kind, b.id
    raise HTTPException(400, "Enter your email or phone number.")


def _public_ok(request, name, per_hour):
    """
    A per-network hourly share for public endpoints that only count or log.
    Past it the caller still gets its answer; the count or log line is
    skipped, so nobody can inflate the funnel or trip the error alarm by
    hammering an unauthenticated route.
    """
    h = auth.ip_hash(_client_ip(request))
    if not h:
        return True
    hour = int(time.time() // 3600)
    return store.incr("_ratelimit", f"{name}-{h}-{hour}", "n", limit=per_hour,
                      ttl=int(time.time()) + 2 * 3600) is not None


def _secure_cookie(proto):
    """Always Secure in production. It followed x-forwarded-proto, a request
    header; behind CloudFront that is https anyway, but the cookie that holds a
    session shouldn't depend on a header to stay off plain http."""
    return proto == "https" or bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def _client_ip(request):
    """
    The viewer's address, for the SMS per-caller limit. CloudFront sets
    CloudFront-Viewer-Address itself (ip:port), so it can't be forged through
    the edge; without the edge, the right-most X-Forwarded-For entry is the one
    our own front door added.
    """
    v = request.headers.get("cloudfront-viewer-address")
    if v:
        return v.rsplit(":", 1)[0].strip("[]")
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else None


def _anon_id(b, request):
    """The body's anon_id, else the x-anon-id header; None under GPC / DNT, or
    when it isn't one of ours."""
    from app.events import ANON_RE, no_ids
    if no_ids(request.headers):
        return None
    for v in (b.anon_id, request.headers.get("x-anon-id")):
        if isinstance(v, str) and ANON_RE.match(v):
            return v
    return None


def _refuse(payload):
    if payload.get("log"):
        log.emit("auth.unavailable", level="error", reason=payload["log"])
    raise HTTPException(payload.get("status")
                        or (429 if "Too many" in payload["error"] else 400),
                        payload["error"])


@app.get("/api/auth/channels")
def auth_channels():
    """Whether the sign-in screen should offer a phone number at all."""
    return {"email": True, "sms": auth.sms_available()}


DEVICE_COOKIE = "ms_dev"


def _device_id(request):
    """The iPhone app can't hold this cookie across origins, so it sends its id."""
    if request.headers.get("x-client") == "ios-app":
        return request.headers.get("x-device", "")
    return request.cookies.get(DEVICE_COOKIE, "")


def _device(request, response):
    """
    A random id for the browser asking for a code, so the code can later be
    told apart from guesses made anywhere else (app/auth.py VERIFY_*: a stranger
    burning guesses at her address can't lock out the device she asked from).
    Not a session and not an identity: random, httpOnly, a day long, and only
    ever stored as a keyed hash beside the code.
    """
    dev = _device_id(request)
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,40}", dev):
        dev = secrets.token_urlsafe(16)
    if response is not None:
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        response.set_cookie(DEVICE_COOKIE, dev, httponly=True, secure=_secure_cookie(proto),
                            samesite="lax", max_age=86400)
    return dev


@app.post("/api/auth/request")
def auth_request(b: Email, request: Request, response: Response):
    kind, value = _ident(b)
    ok, payload = auth.request_code_for(kind, value, ip=_client_ip(request),
                                        device=_device(request, response))
    if not ok:
        _refuse(payload)
    # A count, and the device if it may be linked: no uid exists yet.
    activity.track_anon("auth.code_requested", "sign_in",
                        {"channel": "sms" if kind == "phone" else "email"},
                        _anon_id(b, request))
    return payload


@app.post("/api/auth/verify")
def auth_verify(b: Code, response: Response, request: Request):
    kind, value = _ident(b)
    new_user = not auth.uid_for_id(kind, value, create=False)
    uid, err = auth.verify_code_for(kind, value, b.code, ip=_client_ip(request),
                                    device=_device_id(request))
    if err:
        log.emit("auth.verify.rejected", level="error", reason=err)
        raise HTTPException(401, err)
    channel = "sms" if kind == "phone" else "email"
    log.emit("auth.verify.ok", uid=uid, channel=channel, new_user=new_user)
    funnel.hit(uid, "signed_up")
    try:
        activity.track_auth(uid, channel, new_user, _anon_id(b, request))
    except Exception as e:
        log.error("activity.track_auth_failed", exc=e, uid=uid)
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    token = auth.sign(uid, auth.session_version(uid))
    acct = load(uid, "account", {})
    if request.headers.get("x-client") == "ios-app":
        # The native shell keeps this in the Keychain and sends it as a bearer
        # token: WKWebView drops cross-origin cookies. Never returned to the
        # web client, where the httpOnly cookie keeps it out of reach of script.
        return {"uid": uid, "consented": bool(acct.get("consented_at")
                                              and acct.get("consent_version") == CONSENT_VERSION),
                "token": token}
    response.set_cookie(auth.COOKIE, token, httponly=True,
                        secure=_secure_cookie(proto), samesite="lax",
                        max_age=auth.SESSION_DAYS * 86400)
    return {"uid": uid, "consented": bool(acct.get("consented_at")
                                          and acct.get("consent_version") == CONSENT_VERSION)}


# ----------------------------------------------- sign-in identifiers (sms)
# Adding a phone (or an email) to the account she is already signed in to.
# Same codes, same limits as sign-in; only the last step differs.

@app.get("/api/account/identifiers")
def account_identifiers(request: Request, uid: str = Depends(me)):
    acct = getattr(request.state, "account", None) or load(uid, "account", {}) or {}
    return {"email": acct.get("email"), "phone": acct.get("phone"),
            "sms": auth.sms_available()}


@app.post("/api/account/identifier/request")
def identifier_request(b: Email, request: Request, response: Response,
                       uid: str = Depends(me)):
    kind, value = _ident(b)
    ok, payload = auth.request_code_for(kind, value, ip=_client_ip(request),
                                        device=_device(request, response))
    if not ok:
        _refuse(payload)
    return payload


@app.post("/api/account/identifier/verify")
def identifier_verify(b: Code, request: Request, uid: str = Depends(me)):
    kind, value = _ident(b)
    norm, err = auth.link_identifier(uid, kind, value, b.code, ip=_client_ip(request),
                                     device=_device_id(request))
    if err:
        # Not 401: she is signed in, and the client reads 401 as "signed out".
        # A clash with another account is a 409 — the code was right.
        status = 409 if "different account" in err else 400
        log.emit("auth.link.rejected", uid=uid, kind=kind, status=status)
        raise HTTPException(status, err)
    log.emit("auth.link.ok", uid=uid, kind=kind)
    return {"ok": True, kind: norm}
# --------------------------------------------- /sign-in identifiers (sms)


@app.post("/api/auth/signout")
def auth_signout(request: Request, response: Response):
    # Revoke, not just forget: the cookie is a bearer token good for 90 days,
    # and deleting it here does nothing about a copy somewhere else.
    uid = auth.current_user(request)
    if uid:
        # Signing out ends every session, so no phone is left signed in to
        # send her notifications to (app/apns.py).
        apns.forget(uid)
        auth.revoke_sessions(uid)
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


class Consent(BaseModel):
    ai: bool = False


# Bumped whenever the consent text changes materially, so everyone sees the
# new version once. 2026-10: explicit permission to send her words to the AI
# provider, named — Apple 5.1.2(i), and the honest thing regardless.
CONSENT_VERSION = "2026-11-legal"   # adds: who can read a thread; company, Florida law, live billing


@app.post("/api/consent")
def consent(b: Consent = None, uid: str = Depends(me)):
    if not (b and b.ai):
        raise HTTPException(400, "My Sign can't work without the AI. Tick the box "
                                 "to agree, or close the app — nothing has been stored.")
    acct = load(uid, "account", {})
    acct["consented_at"] = dt.datetime.now().isoformat(timespec="seconds")
    acct["consent_version"] = CONSENT_VERSION
    acct["ai_consent"] = True
    save(uid, "account", acct)
    funnel.hit(uid, "consented")
    activity.track(uid, "consented", screen="consent")
    log.emit("consent.given", uid=uid, version=CONSENT_VERSION)
    return {"ok": True}


# ------------------------------------------------------------------ operator
# Reads other people's conversations. Each route names the least role that may
# call it (app/operators.py) and writes the audit trail before answering.
# Absent — 404, not 401 — until an operator, an admin email or the break-glass
# token is configured, so its existence is not advertised.

from app import admin, operators  # noqa: E402  (here, not above: keeps ops edits in one place)

# ---- one sign-in for the admin platform (app/admin.py). The two paths extend
# /api/auth/request and /api/auth/verify so the WAF's rate rules cover them.

class AdminSignIn(BaseModel):
    email: str = Field(max_length=254)
    code: str = Field("", max_length=12)


def _same_site(request):
    if ops._cross_site(request):
        raise HTTPException(403, "Cross-site requests are refused.", headers=ops.NO_STORE)


@app.post("/api/auth/request-admin")
def admin_request(b: AdminSignIn, request: Request, response: Response):
    _same_site(request)
    response.headers.update(ops.NO_STORE)
    payload, status = admin.request_code(b.email, ops._admin_emails(), ip=_client_ip(request),
                                         device=_device(request, response))
    if status != 200:
        raise HTTPException(status, payload["error"], headers=ops.NO_STORE)
    return payload


@app.post("/api/auth/verify-admin")
def admin_verify(b: AdminSignIn, request: Request, response: Response):
    _same_site(request)
    response.headers.update(ops.NO_STORE)
    uid, token = admin.verify(b.email, b.code, ops._admin_emails(), ip=_client_ip(request),
                              device=_device_id(request))
    if not uid:
        raise HTTPException(401, admin.BAD_CODE, headers=ops.NO_STORE)
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(admin.cookie(), token, httponly=True, secure=_secure_cookie(proto),
                        samesite="lax", path="/", max_age=admin.SESSION_DAYS * 86400)
    return {"ok": True}


@app.get("/api/ops/session")
def admin_session(request: Request, response: Response):
    """Who is signed in to the admin platform and what they may open. The social
    media tool's API asks this too, with the visitor's cookie."""
    response.headers.update(ops.NO_STORE)
    uid, rec = admin.current(request)
    if not uid:
        raise HTTPException(401, "Sign in first.", headers=ops.NO_STORE)
    return {"uid": uid, "email": request.state.account.get("email"), "role": rec["role"],
            "areas": admin.areas(rec)}


@app.post("/api/ops/signout")
def admin_signout(request: Request, response: Response):
    _same_site(request)
    response.headers.update(ops.NO_STORE)
    if request.headers.get("x-ops-request") != "1":
        raise HTTPException(403, "Missing x-ops-request header.", headers=ops.NO_STORE)
    uid, _ = admin.current(request)
    if uid:
        operators.end_sessions(uid)
    response.delete_cookie(admin.cookie(), path="/", httponly=True, samesite="lax",
                           secure=_secure_cookie(request.headers.get("x-forwarded-proto",
                                                                     request.url.scheme)))
    return {"ok": True}


@app.get("/api/ops/me")
def ops_me(request: Request, op: dict = Depends(ops.need("analyst"))):
    acct = (getattr(request.state, "account", None) or {}) if op["via"] == "session" else {}
    return {**op, "email_masked": activity.mask_email(acct.get("email")),
            "roles": list(operators.ROLES),
            # The social media tool knows sessions only, so the token opens nothing there.
            "areas": {**admin.areas(operators.get(op["uid"]) or {"role": op["role"]}),
                      **({} if op["uid"] else {"social": None})}}


def _paged(fn):
    """A bad cursor is the caller's mistake, not a 500."""
    try:
        return fn()
    except ValueError:
        raise HTTPException(400, "Bad cursor.", headers=ops.NO_STORE) from None


# Paged: `cursor` from the previous answer's next_cursor (null at the end).
@app.get("/api/ops/roster")
def ops_roster(cursor: Optional[str] = None, limit: int = 50,
               op: dict = Depends(ops.need("safety"))):
    operators.audit(op, "roster", page=bool(cursor))
    rows, nxt = _paged(lambda: ops.roster(cursor, limit))
    return {"participants": rows, "next_cursor": nxt}


@app.get("/api/ops/flagged")
def ops_flagged(days: int = 7, cursor: Optional[str] = None, limit: int = 200,
                op: dict = Depends(ops.need("safety"))):
    days = max(1, min(days, 400))
    operators.audit(op, "flagged", days=days, page=bool(cursor))
    rows, nxt = _paged(lambda: ops.flagged(days, cursor, limit))
    log.emit("ops.flagged", count=len(rows), days=days)
    return {"flagged": rows, "next_cursor": nxt}


class Grant(BaseModel):
    uid: str
    tier: str
    until: Optional[str] = None


@app.post("/api/ops/plan")
def ops_plan(b: Grant, op: dict = Depends(ops.need("admin"))):
    """Operator grants — pilot participants get Plus by hand until billing exists.

    `uid` is an account id or the email she signs in with. An email is looked
    up; anything that is not an existing account is refused. Before this, an
    email typed into the box was saved as if it were an id: the console said
    "Done", the audit log showed her address, and her account stayed on Free.
    """
    subject = (b.uid or "").strip()
    if "@" in subject:
        found = auth.uid_for(subject, create=False)
        if not found:
            raise HTTPException(404, "No account signs in with that email.", headers=ops.NO_STORE)
        subject = found
    if not operators.UID.match(subject) or load(subject, "account", None) is None:
        raise HTTPException(404, "No such account.", headers=ops.NO_STORE)
    b.uid = subject
    operators.audit(op, "plan.grant", subject=b.uid, tier=b.tier, until=b.until)
    try:
        rec = plan.grant(b.uid, b.tier, source="operator", until=b.until,
                         granted_by=op["uid"] or "break-glass")
    except ValueError as e:
        raise HTTPException(400, str(e), headers=ops.NO_STORE) from None
    if b.tier == "plus":
        funnel.hit(b.uid, "paid")
    log.emit("plan.granted", uid=b.uid, tier=b.tier)
    return rec


class AgeClear(BaseModel):
    uid: str = Field(max_length=64)


@app.post("/api/ops/age-flag/clear")
def ops_age_clear(b: AgeClear, op: dict = Depends(ops.need("admin"))):
    """
    A person reviewed an age flag and it was wrong — a typo, a joke she
    explained to support. Audited; the flag's history stays on the account.
    """
    acct = load(b.uid, "account", None)
    if not acct or not acct.get("age_flag"):
        raise HTTPException(404, "No age flag on that account.", headers=ops.NO_STORE)
    operators.audit(op, "age_flag.clear", subject=b.uid)
    acct["age_flag_cleared"] = {**acct.pop("age_flag"), "by": op["uid"] or "break-glass"}
    save(b.uid, "account", acct)
    return {"uid": b.uid, "age_flag": None}


@app.get("/api/ops/funnel")
def ops_funnel(days: int = 30, op: dict = Depends(ops.need("analyst"))):
    operators.audit(op, "funnel", days=days)
    return funnel.report(days)


# ---- reports (rollups) ------------------------------------------------------
# The analytics rollups (deploy/rollups) read back by app/reports.py, which
# falls back to the DynamoDB counters when there are none. JSON, or a tidy CSV
# with ?format=csv or a ".csv" suffix. Contract: docs/analytics.md, "Rollups".

from fastapi import Query  # noqa: E402

from app import reports  # noqa: E402


def _report_range(frm, to):
    today = dt.datetime.now(dt.timezone.utc).date()
    try:
        b = dt.date.fromisoformat(to) if to else today
        a = dt.date.fromisoformat(frm) if frm else b - dt.timedelta(days=29)
    except ValueError:
        raise HTTPException(400, "Dates are YYYY-MM-DD.", headers=ops.NO_STORE) from None
    if a > b:
        raise HTTPException(400, "`from` is after `to`.", headers=ops.NO_STORE)
    if (b - a).days + 1 > reports.MAX_DAYS:
        raise HTTPException(400, f"At most {reports.MAX_DAYS} days.", headers=ops.NO_STORE)
    return a, b


@app.get("/api/ops/report")
def ops_reports(op: dict = Depends(ops.need("analyst"))):
    return {"reports": reports.available()}


@app.get("/api/ops/report/{name}")
def ops_report(name: str, frm: Optional[str] = Query(None, alias="from"),
               to: Optional[str] = None, compare: bool = False, format: str = "json",
               op: dict = Depends(ops.need("analyst"))):
    if name.endswith(".csv"):
        name, format = name[:-4], "csv"
    if name not in reports.REPORTS:
        raise HTTPException(404, "No such report.", headers=ops.NO_STORE)
    a, b = _report_range(frm, to)
    operators.audit(op, "report", report=name, frm=a.isoformat(), to=b.isoformat(),
                    compare=compare, format=format)
    out = {"name": name, "from": a.isoformat(), "to": b.isoformat(),
           **reports.report(name, a, b)}
    if compare:
        pb = a - dt.timedelta(days=1)
        pa = pb - (b - a)
        prev = reports.report(name, pa, pb)
        out["previous"] = {"from": pa.isoformat(), "to": pb.isoformat(),
                           "source": prev["source"], "totals": prev["totals"]}
    if format == "csv":
        fname = f"mysign-{name}-{out['from']}-{out['to']}.csv"
        return Response(reports.to_csv(out), media_type="text/csv; charset=utf-8",
                        headers={**ops.NO_STORE,
                                 "Content-Disposition": f'attachment; filename="{fname}"'})
    return out


@app.get("/api/ops/health")
def ops_health(frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
               op: dict = Depends(ops.need("analyst"))):
    """Lambda, app metrics, email and cold starts from CloudWatch (app/health.py)."""
    a, b = _report_range(frm, to)
    operators.audit(op, "health", frm=a.isoformat(), to=b.isoformat())
    return health.report(a, b)
# ---- end reports -------------------------------------------------------------


@app.get("/api/ops/audit")
def ops_audit(days: int = 7, op: dict = Depends(ops.need("admin"))):
    days = max(1, min(days, operators.AUDIT_DAYS))
    operators.audit(op, "audit.view", days=days)
    labels = {o["uid"]: o.get("label") for o in operators.all_() if o.get("label")}
    return {**operators.audit_trail(days), "labels": labels}


class OperatorIn(BaseModel):
    email: Optional[str] = Field(None, max_length=254)
    uid: Optional[str] = Field(None, max_length=64)
    role: str
    label: Optional[str] = Field(None, max_length=80)
    social: Optional[bool] = None       # the social media tool as well; None leaves it


def _operator_row(o):
    acct = load(o["uid"], "account", None) or {}
    o = {k: v for k, v in o.items() if k != "sv"}
    return {**o, "email_masked": activity.mask_email(acct.get("email")),
            "account": bool(acct), "social": operators.social(o)}


@app.get("/api/ops/operators")
def ops_operators(op: dict = Depends(ops.need("admin"))):
    operators.audit(op, "operators.list")
    return {"operators": [_operator_row(o) for o in operators.all_()],
            "roles": list(operators.STAFF)}


@app.post("/api/ops/operators")
def ops_operator_set(b: OperatorIn, op: dict = Depends(ops.need("admin"))):
    """Adds or re-roles someone. They must already have an account: the
    console has no sign-in of its own, so nobody can be invited into it."""
    if b.role not in operators.STAFF:
        raise HTTPException(400, f"role must be one of {', '.join(operators.STAFF)}",
                            headers=ops.NO_STORE)
    if b.email:
        uid = auth.uid_for(b.email, create=False)
    elif b.uid and operators.UID.match(b.uid) and load(b.uid, "account", None):
        uid = b.uid
    else:
        uid = None
    if not uid:
        raise HTTPException(404, "No account for that. They need to sign up in the app first.",
                            headers=ops.NO_STORE)
    prev = operators.get(uid)
    if prev and prev["role"] == "admin" and b.role != "admin" and operators.admin_count() <= 1:
        raise HTTPException(409, "That is the last admin.", headers=ops.NO_STORE)
    operators.audit(op, "operator.set", subject=uid, role=b.role,
                    previous=prev["role"] if prev else None)
    rec = operators.put(uid, b.role, added_by=op["uid"] or "break-glass", label=b.label,
                        social=b.social)
    return _operator_row(rec)


@app.delete("/api/ops/operators/{subject}")
def ops_operator_remove(subject: str, op: dict = Depends(ops.need("admin"))):
    prev = operators.get(subject)
    if not prev:
        raise HTTPException(404, "Not an operator.", headers=ops.NO_STORE)
    if prev["role"] == "admin" and operators.admin_count() <= 1:
        raise HTTPException(409, "That is the last admin.", headers=ops.NO_STORE)
    operators.audit(op, "operator.remove", subject=subject, previous=prev["role"])
    operators.remove(subject)
    email = ((load(subject, "account", None) or {}).get("email") or "").lower()
    # Still listed in MYSIGN_OPS_ADMINS means back as admin on their next visit.
    return {"removed": subject, "still_in_ops_admins": bool(email) and email in ops._admin_emails()}


@app.get("/api/card/chart.svg")
def card_chart(uid: str = Depends(consented)):
    r = natal_reading(uid)
    big3 = [(c["label"], c["value"], c.get("sub", "")) for c in r["cards"]]
    _, rulers = _tradition(uid)
    label = r.get("tradition", {}).get("houses_label") or _tradition(uid)[0]
    if r.get("time_known") is False:
        label = "no birth time, no"          # "no birth time, no houses"
    svg = cards.chart_card(r.get("name"), big3, r.get("birth"),
                           tradition=f"{label} houses · {rulers} rulerships")
    return Response(svg, media_type="image/svg+xml",
                    headers={"Content-Disposition": 'inline; filename="my-sign-chart.svg"'})


@app.get("/api/card/today.svg")
def card_today(uid: str = Depends(consented)):
    t = today(uid)
    head = t["headlines"][0] if t.get("headlines") else ""
    meaning = t.get("meanings", [""])[0] if t.get("meanings") else (
        "Nothing in the sky is pressing on your chart today. Quiet days are normal." if t.get("quiet") else "")
    svg = cards.today_card((_profile(uid) or {}).get("name"), t["date"], t["band"], head, meaning)
    return Response(svg, media_type="image/svg+xml",
                    headers={"Content-Disposition": 'inline; filename="my-sign-today.svg"'})


class MoodNote(BaseModel):
    text: str = Field(min_length=1, max_length=mood_mod.NOTE_MAX)


@app.post("/api/mood/note")
def mood_note_write(n: MoodNote, uid: str = Depends(consented)):
    """A note to herself, written on today's good day."""
    d = _today(uid).isoformat()
    ci = load(uid, f"checkin-{d}", None) or {}
    try:
        notes = mood_mod.add_note(load(uid, "mood-notes", []) or [], d, ci.get("mood") or 0, n.text)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    save(uid, "mood-notes", notes)
    activity.track(uid, "mood_note_written", screen="today")
    return {"saved": True, "count": len(notes)}


@app.get("/api/mood/note")
def mood_note_today(uid: str = Depends(consented)):
    """On a low day, one note she wrote on a good one."""
    d = _today(uid).isoformat()
    ci = load(uid, f"checkin-{d}", None) or {}
    notes = load(uid, "mood-notes", []) or []
    note, updated = mood_mod.note_for(notes, d, ci.get("mood"))
    if updated is not notes:
        save(uid, "mood-notes", updated)
    return {"note": note and {"date": note["date"], "mood": note["mood"], "text": note["text"]},
            "written_today": any(x.get("date") == d for x in notes)}


@app.get("/api/card/year.svg")
def card_year(uid: str = Depends(consented)):
    """Her year in colour: one square per day she checked in."""
    m = mood_summary(uid)
    if len(m.get("year") or {}) < 7:
        raise HTTPException(404, "A week of check-ins first.")
    year = _today(uid).year
    svg = cards.year_card(year, m["year"])
    return Response(svg, media_type="image/svg+xml",
                    headers={"Content-Disposition": f'inline; filename="my-sign-{year}.svg"'})


@app.get("/api/card/week.svg")
def card_week(uid: str = Depends(consented)):
    """The weekly recap on the Mood tab, as a story card."""
    w = mood_summary(uid).get("week")
    if not w or w["days"] < 2:
        raise HTTPException(404, "Check in a couple of times this week first.")
    start = dt.date.fromisoformat(w["from"])
    monday = start - dt.timedelta(days=start.weekday())
    got = {x["date"]: x["mood"] for x in w["moods"]}
    days = [("MTWTFSS"[i], got.get((monday + dt.timedelta(days=i)).isoformat())) for i in range(7)]
    end = monday + dt.timedelta(days=6)
    rng = f"{monday.strftime('%d %b').lstrip('0')} – {end.strftime('%d %b').lstrip('0')}"
    best = dt.date.fromisoformat(w["best"]["date"]).strftime("%A")
    m = w["best"]["mood"]
    svg = cards.week_card(rng, days, f"{w['avg']:g}", w["words"],
                          f"best day: {best}, {'an' if m == 8 else 'a'} {m}")
    return Response(svg, media_type="image/svg+xml",
                    headers={"Content-Disposition": 'inline; filename="my-sign-week.svg"'})


@app.get("/api/card/line.svg")
def card_line(uid: str = Depends(consented)):
    t = today(uid)
    ln = t.get("line") or {}
    # The card's receipt line reads "because …", so drop the sentence's own.
    raw = ln.get("why") or ""
    # Only a transit reason reads after "because"; a quiet-sky note doesn't.
    why = lines.sky_of(raw)
    why = why[0].lower() + why[1:] if why and why.startswith("The ") else why
    svg = cards.line_card(dt.date.fromisoformat(t["date"]).strftime("%A %d %B"),
                          ln.get("text", ""), why)
    return Response(svg, media_type="image/svg+xml",
                    headers={"Content-Disposition": 'inline; filename="my-sign-line.svg"'})


@app.get("/api/ops/user/{subject}")
def ops_user(subject: str, op: dict = Depends(ops.need("safety"))):
    """
    Reading someone's conversation — only where the consent text says a person
    might: a safety flag, or a reply she reported. Anything else is refused,
    so the promise "nobody reads your conversations" is enforced, not hoped.
    """
    if not operators.UID.match(subject):
        raise HTTPException(404, "No such person.", headers=ops.NO_STORE)
    flagged = any(m.get("tier") == "red" for m in thread.recent(subject, 0))
    reported = bool(load(subject, "reports", []))
    if not (flagged or reported):
        operators.audit(op, "conversation.refused", subject=subject)
        log.emit("ops.read_thread.refused", subject=subject)
        raise HTTPException(403, "Only conversations with a safety flag or a report can be opened.",
                            headers=ops.NO_STORE)
    operators.audit(op, "conversation.open", subject=subject, flagged=flagged, reported=reported)
    log.emit("ops.read_thread", subject=subject, flagged=flagged, reported=reported)
    return ops.conversation(subject)


@app.get("/api/export")
def export_everything(uid: str = Depends(me)):
    """
    Everything this app holds about you, in one file.

    The consent screen promises deletion; export is the other half of the same
    promise, and the harder one to argue against — someone who trusted this with
    a year of their life should be able to take it with them. Deliberately raw
    rather than prettified: it is her data, not a report about her.

    Driven by the store's own key listing, not a list kept here. The hand-kept
    list had drifted — readings, check-ins without a chat, life events — and an
    export that silently leaves things out is a broken promise.
    """
    acct = load(uid, "account", {}) or {}
    held = {}
    # Account settings belong in her copy too.
    prefs = {k: acct.get(k) for k in ("tz", "push_time", "push_style", "push_off",
                                      "analytics_off", "consent_version", "ai_consent")}
    for kind in sorted(store.keys(uid)):
        if kind.startswith(("sky-", "thread", "quota", "rolled_through")) or "#ctr" in kind:
            continue        # derived from the ephemeris, or bookkeeping
        held[kind] = load(uid, kind, None)
    held.pop("push", None)              # endpoints and keys are device secrets
    held.pop(apns.KIND, None)           # so are the iPhone app's device tokens
    held.pop("account", None)           # summarised below
    take = lambda k, default=None: held.pop(k, default)   # noqa: E731
    readings = {k: held.pop(k) for k in [k for k in held if k.startswith("reading")]}
    usage = {k[len("act-"):]: held.pop(k) for k in sorted(k for k in held if k.startswith("act-"))}
    payload = {
        "exported_at": _now(uid).isoformat(timespec="seconds"),
        "account": {"email": acct.get("email"), "phone": acct.get("phone"),
                    "created_at": acct.get("created_at"),
                    "consented_at": acct.get("consented_at"), **prefs},
        "profile": take("profile"),
        "about": take("about"),
        "reading": next(iter(readings.values()), None),
        "readings": readings,
        "thread": thread.recent(uid, 0),          # 0 = everything, not a window
        "check_ins": {k[len("checkin-"):]: held.pop(k)
                      for k in [k for k in held if k.startswith("checkin-")]},
        "episodes": take("episodes", []),
        "questions": take("questions", []),
        "patterns": take("patterns", []),
        "connections": take("connections", []),
        "life_events": take("life_events", []),
        # The screens she opened and buttons she tapped, by day. Never her words.
        "app_usage": usage,
        "notification_devices": len(load(uid, "push", []) or []) + apns.devices(uid),
        # iPhones with the app's notifications on: when, never the token.
        "iphone_app_devices": apns.summary(uid),
        # Whatever else is held about her, under the name it is stored by.
        "other": held,
    }
    log.emit("account.exported", uid=uid,
             bytes=len(json.dumps(payload, default=str)))
    stamp = _today(uid).isoformat()
    return Response(
        content=json.dumps(payload, indent=1, default=str),
        media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="my-sign-{stamp}.json"'})


@app.delete("/api/account")
def delete_account(uid: str = Depends(me), response: Response = None):
    """
    Actually delete. The consent text promises this, so it has to be real.

    Everything under her id goes, by listing what the store holds rather than
    naming kinds here — the named list missed half of them. Then the email
    and phone indexes and the sign-in codes that point at her, and the roster
    entry.
    """
    acct = load(uid, "account", {}) or {}
    billing.cancel(uid)                   # nobody is billed for an account that's gone
    # An Apple subscription can't be cancelled from here; the client says so
    # before the final confirmation (plan.apple_sub in /api/state).
    had_apple = bool(apple.subscription(uid))
    apple.forget(uid)
    for other in load(uid, "links", []) or []:          # team2/people
        _reveal_forget(uid, other)
    _unlink_everywhere(uid)
    _forget_invites(uid)                  # her links, and her uid in links she joined (M4)
    _bt_forget(uid)                       # team2/habit: her outstanding "ask your mom" links
    opsindex.forget(uid, acct)            # the /ops list and flag queue
    apns.forget(uid)                      # her phones' tokens, and the index that finds them
    n = store.delete_all(uid)
    auth.forget(uid)
    auth.forget_identities(uid, acct)
    log.emit("account.deleted", uid=uid, keys=n)
    if response:
        response.delete_cookie(auth.COOKIE)
    return {"deleted": True, **({"apple_subscription": True} if had_apple else {})}


def _known_dates(uid):
    known = set(thread.dates(uid))
    known |= {e["date"] for e in (load(uid, "episodes", []) or [])}
    return sorted(known)


# --------------------------------------------------------------------- state

@app.get("/api/state")
def state(request: Request):
    uid = auth.current_user(request)
    if not uid:
        # collect: the anonymous collector is behind /e (the stack sets
        # MYSIGN_COLLECTOR when it routes it), so the sign-in screen may count.
        return {"demo": DEMO, "signed_in": False,
                "collect": bool(os.environ.get("MYSIGN_COLLECTOR"))}
    acct = load(uid, "account", {}) or {}
    if not acct.get("consented_at") or acct.get("consent_version") != CONSENT_VERSION:
        return {"demo": DEMO, "signed_in": True,
            "consented": False,
                "email": acct.get("email")}
    # Where she is now, before the day rolls over: after travel the first open
    # used to roll over in the old time zone (audit LOW).
    _note_tz(uid, acct, request.headers.get("x-tz"))
    if acct.get("age_flag"):
        # The 18+ screen: kind, with resources and the delete button. Nothing
        # else about her is served until a person clears the flag.
        p = _profile(uid) or {}
        return {"demo": DEMO, "signed_in": True, "consented": True,
                "age_blocked": True, "min_age": MIN_AGE, "support": SUPPORT_EMAIL,
                "email": acct.get("email"), "age_message": AGE_MESSAGE,
                "apple_sub": apple.subscription(uid),   # the delete button warns first
                "crisis_footer": crisis.footer(p.get("country")),
                "resources": [x for x in (crisis.specific("minor", p.get("country")),
                                          crisis.resource_line(p.get("country")),
                                          f"In an emergency, call {crisis.emergency(p.get('country'))}.")
                              if x]}
    rollover(uid)
    p = _profile(uid)
    d = _today(uid)
    today = d.isoformat()
    created = acct.get("created_at")
    if created:
        age = (d - dt.date.fromtimestamp(created)).days
        if age >= 1:
            funnel.hit(uid, "returned_day2")
        if age >= 7:
            funnel.hit(uid, "week_one")
    paying = plan.providers(uid)          # stripe, apple: who is being paid for Plus now
    return {"demo": DEMO, "signed_in": True, "consented": True,
            "support": SUPPORT_EMAIL, "min_age": MIN_AGE,
            "country": (p or {}).get("country"),
            "crisis_line_known": crisis.known((p or {}).get("country")),
            "crisis_footer": crisis.footer((p or {}).get("country")),
            # Where she lives now, guessed from the browser's zone, for the
            # onboarding form to confirm — never the birth city's country.
            "country_guess": cities.country_for_tz(request.headers.get("x-tz")),
            "birth_country": (p or {}).get("birth_country"),
            # Enough for Settings to draw without waiting on /api/reading,
            # which can be a model call (fifteen seconds on a new account).
            "birth": (p or {}).get("birth_local"),
            "time_known": (p or {}).get("birth_time_known", True) if p else None,
            "push_people": _push_people_on(p) if p else None,
            "email": acct.get("email"), "onboarded": bool(p),
            "analytics_off": bool(acct.get("analytics_off")),
            # Only the iPhone app asks, and only it is told: whether it can
            # offer notifications at all (APNs; off until the key is in).
            **({"native_push": apns.enabled()}
               if request.headers.get("x-client") == "ios-app" else {}),
            "plan": {**plan.get(uid), "used_today": plan.messages_today(uid, d),
                     "free_per_day": plan.FREE_MESSAGES_PER_DAY,
                     "free_people": plan.FREE_PEOPLE, "prices": plan.PRICES,
                     "tiers": plan.TIERS,
                     "manage": bool((load(uid, "billing", None) or {}).get("customer"))
                               and plan.PRICES["available"],
                     "trialing": (load(uid, "billing", None) or {}).get("status") == "trialing",
                     "renews": not (load(uid, "billing", None) or {}).get("cancel_at_period_end"),
                     # An Apple subscription giving her Plus now, and whether a
                     # second provider is being paid for the same thing.
                     "apple_sub": apple.subscription(uid, paying),
                     "double": len(paying) > 1,
                     # Only the iPhone app sells through the App Store.
                     **({"apple": apple.offer(uid)}
                        if request.headers.get("x-client") == "ios-app" else {})},
            "name": (p or {}).get("name"),
            "about_done": about_mod.done(load(uid, "about", None)),
            "share_mood": (p or {}).get("share_mood", True),
            "checkin": load(uid, f"checkin-{today}", None),
            # The client renders a chat, not an archive. Returning every message
            # ever sent grows without bound and is read on every state poll.
            "thread": thread.recent(uid, RECENT_MESSAGES)}


class Onboard(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    date: str = Field(max_length=20)
    time: str = Field(default="", max_length=20)
    city: str = Field(max_length=200)
    # Which occurrence of a repeated hour, on the night the clocks went back.
    # 0 (the first) unless she says otherwise — see engine/localtime.py.
    fold: int = Field(default=0, ge=0, le=1)
    # Where she lives now, as confirmed on the form. Not the birth country.
    country: Optional[str] = Field(default=None, max_length=2)


def _birth_instant(y, m, d, hh, mm, tz, lat, lon, known, fold=0):
    """(utc, note). The note is set only when the wall-clock time was ambiguous
    or impossible that night, and says what we assumed and what the other
    reading would change."""
    utc, issue = localtime.resolve(y, m, d, hh, mm, tz, fold)
    if not (known and issue):
        return utc, None
    other_rising = other_mc = None
    if issue["kind"] == "repeated":
        o = issue["other_utc"]
        ang = natal(o.year, o.month, o.day, o.hour + o.minute / 60, lat, lon)["angles"]
        other_rising, other_mc = ang["Ascendant"]["sign"], ang["Midheaven"]["sign"]
    return utc, localtime.describe(issue, other_rising, other_mc)


def _parse_birth(date, time_):
    """(y, m, d, hh, mm, known) or a 400 — a malformed date used to 500."""
    try:
        y, m, d = (int(x) for x in date.split("-"))
        dt.date(y, m, d)
        known = bool(time_)
        hh, mm = (int(x) for x in time_.split(":")) if known else (12, 0)
        dt.time(hh, mm)
    except (ValueError, TypeError, OverflowError) as err:
        raise HTTPException(400, "That birth date or time didn't parse.") from err
    if not 1900 <= y <= dt.date.today().year:
        raise HTTPException(400, "That birth year doesn't look right.")
    if dt.date(y, m, d) > dt.date.today():
        # Said plainly; it used to fall through to "you must be 18", which is
        # true and baffling.
        raise HTTPException(400, "That date is in the future — check the year?")
    return y, m, d, hh, mm, known


def _current_country(given, tz, birth_country):
    """
    The country her crisis line comes from: what she confirmed, else her
    browser's zone, else — knowing nothing else — the birth country. The birth
    city alone sent "call 14416" to someone born in Mumbai who lives in Ohio.
    """
    if given and cities.is_country(given):
        return given.upper(), "confirmed"
    guess = cities.country_for_tz(tz)
    if guess:
        return guess, "timezone"
    return (birth_country or None), "birth"


@app.post("/api/onboard")
def onboard(o: Onboard, request: Request, uid: str = Depends(consented)):
    c, maybe = cities.lookup(o.city)
    if not c:
        raise HTTPException(400, cities.refusal(
            o.city, maybe, f"I couldn't find {o.city}. Pick one from the list."))
    y, m, d, hh, mm, known = _parse_birth(o.date, o.time)

    # Age gate. Configurable, defaults to 18 — this app holds intimate
    # conversations and pages a human on crisis language, and both of those
    # have different obligations for a minor. Nothing about the date is logged.
    today = dt.date.today()
    age = today.year - y - ((today.month, today.day) < (m, d))
    if age < MIN_AGE:
        log.emit("onboard.underage", level="error", uid=uid)
        raise HTTPException(403, f"My Sign is for people {MIN_AGE} and over.")

    utc, note = _birth_instant(y, m, d, hh, mm, c["tz"], c["lat"], c["lon"],
                               known, o.fold)
    country, source = _current_country(o.country, request.headers.get("x-tz"), c["country"])
    save(uid, "profile", {
        "name": o.name.strip(),
        # Structured, not a sentence. birth_local is for display only — parsing
        # it back to recover the city is how "(time unknown)" ended up inside a
        # place name.
        "birth_date": o.date,
        "birth_time": o.time or None,
        "birth_city": c["label"],
        "birth_local": f"{o.date} {o.time or '(time unknown)'} {c['label']}",
        "birth_time_known": known,
        "birth_ut": [utc.year, utc.month, utc.day,
                     round(utc.hour + utc.minute / 60, 4), c["lat"], c["lon"]],
        "tz": c["tz"], "country": country, "country_source": source,
        "birth_country": c["country"], "people": [],
        "birth_time_note": note,
    })
    nat = _natal(uid)
    # Fit the salience thresholds to this chart once. It walks a year of sky, so
    # it is far too slow to do per request — and it never changes, because the
    # natal chart never changes.
    try:
        bands = salience.calibrate(nat, tz=c["tz"])
        if bands:
            save(uid, "salience_bands", bands)
            log.emit("salience.calibrated", uid=uid,
                     high=bands[0][0], medium=bands[1][0])
    except Exception as e:
        # A miscalibration is not worth failing a signup over; the global
        # thresholds are a serviceable fallback.
        log.error("salience.calibrate.failed", exc=e, uid=uid)

    log.emit("onboarded", uid=uid, time_known=known, country=country, country_source=source)
    funnel.hit(uid, "onboarded")
    activity.track(uid, "chart_built", screen="onboard", precision="exact" if known else "unknown")
    # Without a time the noon chart's houses and angles are fiction, so they
    # are not sent: a client that rendered them would show "Ascendant 14°10'
    # Cancer" to someone with no rising sign.
    return {"ok": True, "time_known": known, "birth_time_note": note,
            # The first screen after this shows the crisis footer; it must be
            # hers already, not the signed-out default from boot.
            "country": country, "country_source": source,
            "crisis_footer": crisis.footer(country),
            "chart": {k: {"label": v["label"], "house": v["house"] if known else None,
                          "retrograde": v["retrograde"]} for k, v in nat["planets"].items()},
            "angles": ({k: v["label"] for k, v in nat["angles"].items()} if known else {}),
            "houses_used": nat["houses_used"] if known else None}


class FreeRead(BaseModel):
    date: str = Field(max_length=20)
    time: str = Field(default="", max_length=20)
    city: str = Field(max_length=200)
    anon_id: Optional[str] = Field(default=None, max_length=40)


@app.post("/api/free-reading")
def free_reading(b: FreeRead, request: Request):
    """
    The website's reading, before any sign-up. Public on purpose. A POST so the
    date and time stay out of URLs, and nothing about them is saved or logged:
    they compute the answer and are gone. No model call.
    """
    c, maybe = cities.lookup(b.city)
    if not c:
        raise HTTPException(400, cities.refusal(
            b.city, maybe, f"I couldn't find {b.city}. Pick one from the list."))
    y, m, d, hh, mm, known = _parse_birth(b.date, b.time)
    today = dt.date.today()
    if today.year - y - ((today.month, today.day) < (m, d)) < MIN_AGE:
        raise HTTPException(403, f"My Sign is for people {MIN_AGE} and over.")
    utc, _ = _birth_instant(y, m, d, hh, mm, c["tz"], c["lat"], c["lon"], known)
    chart = with_time(natal(utc.year, utc.month, utc.day,
                            round(utc.hour + utc.minute / 60, 4), c["lat"], c["lon"]), known)
    activity.track_anon("free_reading.made", "home", None, _anon_id(b, request))
    return JSONResponse(freeread.build(chart, known, (y, m, d)),
                        headers={"Cache-Control": "no-store"})


def _sun_same_everywhere(y, m, d):
    """The Sun's sign at the first and last instant of this calendar date in
    any zone: 00:00 at UTC+14 and 23:59 at UTC-12."""
    from engine.chart import jd_ut
    from engine.chart import positions as _pos
    day = dt.datetime(y, m, d)
    first = day - dt.timedelta(hours=14)
    last = day + dt.timedelta(hours=35, minutes=59)
    def sign(t):
        lon = _pos(jd_ut(t.year, t.month, t.day, t.hour + t.minute / 60))["Sun"]["lon"]
        return int(lon // 30)
    return sign(first) == sign(last)


class FreeGlimpse(BaseModel):
    date: str = Field(max_length=20)
    anon_id: Optional[str] = Field(default=None, max_length=40)


@app.post("/api/free-glimpse")
def free_glimpse(b: FreeGlimpse, request: Request):
    """
    The first taste on the website, from a birthday alone: one line, no city, no
    time. Public, a POST so the date stays out of URLs, nothing stored, no model
    call. `section` is null when the Sun changes sign on that day.
    """
    y, m, d, _, _, _ = _parse_birth(b.date, "")
    today = dt.date.today()
    if today.year - y - ((today.month, today.day) < (m, d)) < MIN_AGE:
        raise HTTPException(403, f"My Sign is for people {MIN_AGE} and over.")
    chart = with_time(natal(y, m, d, 12.0, 0.0, 0.0), False)
    activity.track_anon("free_glimpse.made", "home", None, _anon_id(b, request))
    if not _sun_same_everywhere(y, m, d):
        # No city yet, so no zone: her birthday ran from UTC+14 to UTC-12. A Sun
        # that changes sign anywhere in that span was named here and then taken
        # back by the full reading ("Aquarius Sun", then just "Libra Moon").
        return JSONResponse({"signs": [], "section": None}, headers={"Cache-Control": "no-store"})
    return JSONResponse(freeread.glimpse(chart, (y, m, d)),
                        headers={"Cache-Control": "no-store"})


class VsIn(BaseModel):
    date_a: str = Field(max_length=20)
    date_b: str = Field(max_length=20)
    kind: str = Field(default="romantic", max_length=20)
    name_a: str = Field(default="", max_length=60)
    name_b: str = Field(default="", max_length=60)


def _vs_token(token):
    return checker.verify(token, auth.secret())


@app.post("/api/vs")
def vs_check(b: VsIn, request: Request):
    """
    Two birthdays, one score, no account. Public on purpose, and a POST so the
    dates stay out of URLs. Nothing about either person is saved or logged, and
    the engine is arithmetic: no model call, so traffic can't run up a bill.
    Not /api/check, which is the signed-in quick check in People.
    Both must be adults: it reads a stranger's birthday as a possible romance.
    """
    ya, ma, da, *_ = _parse_birth(b.date_a, "")
    yb, mb, db, *_ = _parse_birth(b.date_b, "")
    if not (checker.old_enough((ya, ma, da), MIN_AGE) and checker.old_enough((yb, mb, db), MIN_AGE)):
        raise HTTPException(403, f"This is only for two people {MIN_AGE} and over.")
    out = checker.read((ya, ma, da), (yb, mb, db), b.kind)
    names = [checker.first_name(b.name_a), checker.first_name(b.name_b)]
    token = checker.sign(out, *names, auth.secret())
    funnel.tally("check_made")
    return JSONResponse({**out, "names": names, "token": token,
                         "url": f"{public_url(request)}/api/vs/r/{token}"},
                        headers={"Cache-Control": "no-store"})


@app.post("/api/vs/shared")
def vs_shared(request: Request):
    """She tapped send or post. A count, with nothing attached."""
    if _public_ok(request, "vsshared", 30):
        funnel.tally("check_shared")
    return {"ok": True}


@app.get("/api/vs/r/{token}", include_in_schema=False)
def vs_link(token: str, request: Request):
    """What a sent result unfurls to, then the page. Under /api so it reaches
    this Lambda without a new CloudFront behaviour. Previews fetch this too, so
    it counts nothing; the page's own request below is the open."""
    base = public_url(request)
    got = _vs_token(token)
    if not got:
        return _unfurl("Are you two compatible?", "Two birthdays. One honest number.",
                       f"{base}/check", base)
    a, b = got["names"]
    who = f"{a} & {b} got" if a and b else "We got"
    return _unfurl(f"{who} {got['score']} on My Sign", f"{got['line']} Check yours.",
                   f"{base}/check?r={token}", base)


@app.get("/api/vs/result/{token}")
def vs_result(token: str, request: Request):
    """The shared result for the page someone landed on."""
    got = _vs_token(token)
    if not got:
        raise HTTPException(404, "That link doesn't work.")
    if _public_ok(request, "vsopened", 30):
        funnel.tally("check_opened")
    return JSONResponse(got, headers={"Cache-Control": "no-store"})


@app.get("/api/vs/card/{token}.svg")
def vs_card(token: str):
    """The card to post. Signed like the link, so it shows only a real result."""
    got = _vs_token(token)
    if not got:
        raise HTTPException(404, "That card doesn't exist.")
    a, b = got["names"]
    svg = cards.compat_card(a or "You", b or "Them", got["score"], got["band"],
                            estimate=True, line=got["line"])
    return Response(svg, media_type="image/svg+xml",
                    headers={"Content-Disposition": 'inline; filename="my-sign-check.svg"',
                             "Cache-Control": "no-store"})


# The parts of a reading that are computed text, not model writing.
READING_FIXED = ("cards", "balance", "standout", "wiring", "personal", "facts",
                 "sky", "old", "sky_facts")


@app.get("/api/starters")
def reading_starters(uid: str = Depends(consented)):
    """
    The questions Today offers before she has said anything. Computed from her
    chart alone, so a new account sees them at once instead of after the model
    has written her reading (/api/reading), which is where they used to come from.
    """
    prof = _profile(uid)
    if not prof:
        raise HTTPException(400, "not onboarded")
    _, rulers = _tradition(uid)
    ut = prof["birth_ut"]
    qs = reading.starters(_natal(uid), time_known=prof.get("birth_time_known", True),
                          birth_ymd=(ut[0], ut[1], ut[2]), rulerships=rulers)
    return {"starters": i18n.starters(qs, i18n.lang_of(uid))}


@app.get("/api/reading")
def natal_reading(uid: str = Depends(consented)):
    """
    The full natal reading. Cached — the chart does not change, and the opening
    paragraph costs a model call.
    """
    if not _profile(uid):
        raise HTTPException(400, "not onboarded")
    houses, rulers = _tradition(uid)
    key = f"reading-{houses}-{rulers}"
    cached = load(uid, key, None)
    # Readings cached before the personality read existed are rebuilt, not served
    # half-empty for the life of the account.
    prof = _profile(uid)
    ut = prof["birth_ut"]
    if cached and "profile" in cached:
        # Said fresh, not from the cache: a reading cached before the polar
        # fallback was named still says which houses it really used.
        cached["tradition"] = tradition.describe(houses, rulers, _natal(uid))
        # Only the model's writing is worth caching. Everything else is fixed
        # text computed from her chart, so it is rebuilt from today's code —
        # otherwise a rewrite of that text (or a fix to it) would never reach
        # anyone who already had a reading.
        try:
            fresh = reading.build(_natal(uid), name=prof.get("name"), want_prose=False,
                                  rulerships=rulers,
                                  time_known=prof.get("birth_time_known", True),
                                  birth_ymd=(ut[0], ut[1], ut[2]),
                                  birth_ut=ut, tz=prof.get("tz"))
            cached.update({k: fresh[k] for k in READING_FIXED if k in fresh})
            # The profile is the model's when it wrote one; when it didn't, it
            # is authored text like the rest, and gets today's wording too.
            if not cached.get("opening_generated"):
                cached["profile"] = fresh["profile"]
        except Exception as e:
            log.error("reading.refresh.failed", exc=e, uid=uid)
        return _with_now(uid, cached)
    from engine import spend
    # The written read is a model call; a failure is not cached, so without a
    # budget every open retried it (audit H1). Spent, the authored read stands.
    capped = bool(os.environ.get("ANTHROPIC_API_KEY")) and not spend.claim(uid, "natal")
    r = reading.build(_natal(uid), name=prof.get("name"), want_prose=not capped,
                      rulerships=rulers,
                      time_known=prof.get("birth_time_known", True),
                      birth_ymd=(ut[0], ut[1], ut[2]),
                      birth_ut=ut, tz=prof.get("tz"))
    r["name"] = prof.get("name")
    r["birth"] = prof.get("birth_local")
    r["time_known"] = prof.get("birth_time_known", True)
    r["tradition"] = tradition.describe(houses, rulers, _natal(uid))
    if capped:
        r["capped"], r["message"] = True, spend.message("natal")
    # Cache only what the model finished, or what was never going to call it.
    # A transient failure cached here would be her reading permanently.
    elif r.get("opening_generated") or not os.environ.get("ANTHROPIC_API_KEY"):
        save(uid, key, r)
    return _with_now(uid, r)


def _with_now(uid, r):
    """
    The 'where you are now' chapter: secondary progressions for today. It moves
    (the progressed Moon changes sign every ~2.5 years), so it is added on every
    request rather than frozen into the cached reading. Pure ephemeris, a few
    milliseconds, and never worth failing the reading over.
    """
    prof = _profile(uid)
    try:
        prec = compat.my_precision(prof)
        cusps = _natal(uid)["house_cusps"] if prec == "exact" else None
        p = progressed.compute(prof["birth_ut"], _today(uid), prec, cusps)
        r = {**r, "starters": i18n.starters(r.get("starters") or [], i18n.lang_of(uid))}
        return {**r, "now": progressed.describe(p), "progressed": p, "wheel": _chapters(uid, _wheel(uid))}
    except Exception as e:
        log.error("reading.progressed.failed", exc=e, uid=uid)
        return {**r, "now": []}


WHEEL_BODIES = ("Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn",
                "Uranus", "Neptune", "Pluto")


def _wheel(uid):
    """
    What the page needs to draw her chart as a wheel: each planet's ecliptic
    longitude, and the lines between them. Every number is the ephemeris's; the
    page only turns degrees into positions on a circle. Never cached with the
    reading, so a corrected birth time redraws it.

    Without a birth time there is no Ascendant and no houses, and the Moon can
    be several degrees off, so its lines are left out rather than drawn wrong.
    """
    try:
        prof, chart = _profile(uid), _natal(uid)
        exact = compat.my_precision(prof) == "exact"
        planets = [{"name": k, "lon": v["lon"], "retro": bool(v.get("retrograde"))}
                   for k, v in chart["planets"].items() if k in WHEEL_BODIES]
        skip = set() if exact else {"Moon"}
        lines = [a for a in interpret.natal_aspects(chart)
                 if a["a"] in WHEEL_BODIES and a["b"] in WHEEL_BODIES
                 and a["a"] not in skip and a["b"] not in skip][:14]
        out = {"planets": planets, "aspects": lines, "time_known": exact}
        if exact:
            out["asc"] = chart["angles"]["Ascendant"]["lon"]
            out["mc"] = chart["angles"]["Midheaven"]["lon"]
            out["cusps"] = chart["house_cusps"]
        return out
    except Exception as e:
        log.error("reading.wheel.failed", exc=e, uid=uid)
        return None


# The planets that move slowly enough for "right now" to mean more than today.
CHAPTER_MOVERS = ("Mars", "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto")


def _chapters(uid, w):
    """
    What the chapter-by-chapter reading adds to the wheel: for each planet its
    house and the engine's one-line reading, a sentence for each line between
    two planets, the lines to the rising sign, and which slow planets are
    touching what today. Sentences come from the corpus and numbers from the
    ephemeris; the page lays them out. Returns the wheel unchanged on failure.
    """
    if not w:
        return w
    try:
        chart, exact = _natal(uid), w["time_known"]

        def say(a):
            m = corpus.aspect_between(a["a"], a["b"], a["aspect"])
            line = m["reading"] if m else corpus.link_for_you(a["a"], a["b"], a["aspect"])
            return {**a, "say": line} if line else a

        planets = []
        for q in w["planets"]:
            p = chart["planets"][q["name"]]
            m = corpus.placement(q["name"], p["sign"]) or {}
            q = {**q, "say": m.get("reading", "")}
            if exact and p.get("house"):
                q["house"], q["arena"] = p["house"], corpus.HOUSES[p["house"]]
            planets.append(q)
        out = {**w, "planets": planets, "aspects": [say(a) for a in w["aspects"]]}
        if exact:
            out["asc_aspects"] = [x for x in (say(a) for a in interpret.natal_aspects(chart)
                                               if "Ascendant" in (a["a"], a["b"])
                                               and (a["a"] in WHEEL_BODIES or a["b"] in WHEEL_BODIES))
                                  if x.get("say")][:4]
        tpos = positions(daily.local_noon_jd(_today(uid), _tz(uid)))
        slow = {k: v for k, v in tpos.items() if k in CHAPTER_MOVERS}
        targets = set(WHEEL_BODIES) | ({"Ascendant"} if exact else set())
        if not exact:
            targets.discard("Moon")
        out["now"] = [{"transit": a["transit"], "natal": a["natal"], "aspect": a["aspect"],
                       "orb": a["orb"], "retro": bool(a["retrograde"]),
                       "verb": corpus.TRANSIT_ASPECTS[a["aspect"]]["verb"]}
                      for a in aspects(slow, chart, exact)
                      if a["natal"] in targets and a["orb"] <= 4
                      and a["aspect"] in corpus.TRANSIT_ASPECTS]
        return out
    except Exception as e:
        log.error("reading.chapters.failed", exc=e, uid=uid)
        return w


# ------------------------------------------------------- placement deep-dives

def _placement_key(uid, point):
    houses, rulers = _tradition(uid)
    return f"{placement.cache_key(point)}-{houses}-{rulers}"


def _placement_inputs(uid):
    prof = _profile(uid)
    if not prof:
        raise HTTPException(400, "not onboarded")
    _, rulers = _tradition(uid)
    ut = prof["birth_ut"]
    known = prof.get("birth_time_known", True)
    chart = _natal(uid)
    analysis = interpret.analyze(chart, known, (ut[0], ut[1], ut[2]), rulers)
    try:
        sky = birthsky.compute(ut, chart, known, prof.get("tz"))
    except Exception as e:
        log.error("placement.sky.failed", exc=e, uid=uid)
        sky = None
    eps = load(uid, "episodes", []) or []
    pats = load(uid, "patterns", []) or []
    fb = (load(uid, "placement-feedback", {}) or {})
    return {"chart": chart, "analysis": analysis, "sky": sky, "time_known": known,
            "episodes": eps, "patterns": pats, "birth_ut": ut, "rulerships": rulers}, fb


def _point(point):
    if point not in placement.POINTS:
        raise HTTPException(404, "Pick sun, moon or rising.")
    return point


@app.get("/api/placement/{point}")
def placement_view(point: str, uid: str = Depends(consented)):
    """
    One placement, every layer. Fast — no model call — so the page opens at
    once; the long read comes from the narrative route and is cached here.
    """
    point = _point(point)
    kw, fb = _placement_inputs(uid)
    out = placement.build(kw["chart"], kw["analysis"], point, episodes=kw["episodes"],
                          patterns=kw["patterns"], sky=kw["sky"], time_known=kw["time_known"],
                          birth_ut=kw["birth_ut"], rulerships=kw["rulerships"])
    cached = load(uid, _placement_key(uid, point), None)
    bucket = placement.data_bucket(out.get("her_data", {}).get("checkins", 0))
    if cached and cached.get("bucket") == bucket:
        out["narrative"], out["generated"] = cached["narrative"], cached.get("generated")
    out["feedback"] = fb.get(point, {})
    return out


@app.post("/api/placement/{point}/narrative")
def placement_narrative(point: str, uid: str = Depends(consented)):
    """
    The long read: one model call, grounding-checked, cached until her data
    grows past the next mark. Demo mode gets the authored version.

    A full read can take longer than CloudFront's 60-second origin timeout, so
    while the model writes, the response streams a space every few seconds and
    ends with the JSON. Leading whitespace is valid JSON, so the client just
    parses the body.
    """
    point = _point(point)
    key = _placement_key(uid, point)
    kw, fb = _placement_inputs(uid)
    her = placement.her_data(kw["episodes"], kw["chart"], point, kw["patterns"]) \
        if point != "rising" or kw["time_known"] else {"checkins": 0}
    bucket = placement.data_bucket(her.get("checkins", 0))
    cached = load(uid, key, None)
    if cached and cached.get("bucket") == bucket:
        return {"narrative": cached["narrative"], "generated": cached.get("generated")}
    rejected = [k for k, v in (fb.get(point) or {}).items() if v == "not_me"]
    from engine import spend
    # Metered per user per UTC day (audit H1). Claimed before the stream starts,
    # so a spent budget is a plain JSON answer, not a held-open connection.
    if os.environ.get("ANTHROPIC_API_KEY") and not spend.claim(uid, "placement"):
        return {"narrative": None, "generated": False, "capped": True,
                "message": spend.message("placement")}

    def work():
        out = placement.build(kw["chart"], kw["analysis"], point, episodes=kw["episodes"],
                              patterns=kw["patterns"], sky=kw["sky"],
                              time_known=kw["time_known"], birth_ut=kw["birth_ut"],
                              rejected=rejected, want_narrative=True,
                              rulerships=kw["rulerships"])
        if out.get("unavailable"):
            return out
        # Cache only what the model finished, or what was never going to call it.
        if out.get("generated") or not os.environ.get("ANTHROPIC_API_KEY"):
            save(uid, key, {"narrative": out["narrative"], "generated": out.get("generated"),
                            "bucket": bucket, "at": dt.datetime.now(dt.timezone.utc).isoformat()})
        log.emit("placement.read", uid=uid, point=point, generated=bool(out.get("generated")))
        return {"narrative": out["narrative"], "generated": out.get("generated")}

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return work()

    def stream():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(work)
            while True:
                try:
                    result = fut.result(timeout=5)
                    break
                except concurrent.futures.TimeoutError:
                    yield b" "
                except Exception as e:
                    log.error("placement.narrative.crashed", exc=e, uid=uid)
                    result = {"narrative": None, "generated": False}
                    break
        yield json.dumps(result).encode()

    return StreamingResponse(stream(), media_type="application/json")


class PlacementFeedback(BaseModel):
    section: str = Field(max_length=40)
    fit: str = Field(max_length=10)


@app.post("/api/placement/{point}/feedback")
def placement_feedback(point: str, b: PlacementFeedback, uid: str = Depends(consented)):
    """'This is me' / 'not me' on one section. The next read is told which
    sections didn't fit, and the counts show where the corpus misses."""
    point = _point(point)
    keys = {k for k, _ in placement.SECTIONS} | {"tagline"}
    if b.section not in keys or b.fit not in ("me", "not_me", "clear"):
        raise HTTPException(400, "Unknown section or fit.")
    fb = load(uid, "placement-feedback", {}) or {}
    mine = fb.setdefault(point, {})
    if b.fit == "clear":
        mine.pop(b.section, None)
    else:
        mine[b.section] = b.fit
    save(uid, "placement-feedback", fb)
    log.emit("placement.feedback", uid=uid, point=point, section=b.section, fit=b.fit)
    return {"feedback": mine}


class BirthTime(BaseModel):
    time: str = Field(max_length=20)         # HH:MM local to the birth city
    fold: int = Field(default=0, ge=0, le=1)


@app.post("/api/birthtime")
def set_birth_time(b: BirthTime, uid: str = Depends(consented)):
    """
    Fill in a birth time that was skipped at signup.

    The reading tells people this is here, so it has to work. Adding a time
    changes the ascendant and therefore every house, so the cached reading and
    the cached sky both have to go.
    """
    return _apply_birth_time(uid, b.time, b.fold)


def _apply_birth_time(uid, time_str, fold=0):
    """The one code path that writes an exact birth time — her own Settings
    and a parent's answer through an "ask your mom" link both land here, so a
    time from either invalidates the same caches and rescores the same rows."""
    prof = _profile(uid)
    if not prof:
        raise HTTPException(400, "not onboarded")
    try:
        hh, mm = (int(x) for x in time_str.split(":"))
        assert 0 <= hh < 24 and 0 <= mm < 60
    except Exception as e:
        raise HTTPException(400, "Time should look like 04:42.") from e

    date_part = prof.get("birth_date") or prof["birth_local"].split(" ")[0]
    y, m, d = (int(x) for x in date_part.split("-"))
    tz = prof.get("tz") or "UTC"
    utc, note = _birth_instant(y, m, d, hh, mm, tz, prof["birth_ut"][4],
                               prof["birth_ut"][5], True, fold)

    city = prof.get("birth_city") or prof["birth_local"].rsplit(") ", 1)[-1]
    prof["birth_ut"] = [utc.year, utc.month, utc.day,
                        round(utc.hour + utc.minute / 60, 4),
                        prof["birth_ut"][4], prof["birth_ut"][5]]
    prof["birth_time_known"] = True
    prof["birth_time"] = time_str
    prof["birth_time_note"] = note
    prof["birth_city"] = city
    prof["birth_local"] = f"{date_part} {time_str} {city}"
    prof.pop("birth_time_approx", None)       # an exact time supersedes "roughly morning"
    save(uid, "profile", prof)

    houses, rulers = _tradition(uid)
    save(uid, f"reading-{houses}-{rulers}", None)        # ascendant changed
    for p in placement.POINTS:                            # every deep-dive changes
        save(uid, _placement_key(uid, p), None)
    for iso in _known_dates(uid) + [_today(uid).isoformat()]:
        save(uid, _sky_key(iso, houses), None)                # houses changed
    rescored = _rescore_connections(uid)             # overlays exist now
    save(uid, "widget", None)                        # team2/habit: her day changed
    # Her thresholds were fitted to a chart with no angles; with a time the
    # angles count, so her ordinary day scores differently.
    try:
        bands = salience.calibrate(_natal(uid), tz=prof.get("tz"))
        if bands:
            save(uid, "salience_bands", bands)
    except Exception as e:
        log.error("salience.calibrate.failed", exc=e, uid=uid)
    return {"ok": True, "birth": prof["birth_local"], "rescored": rescored,
            "birth_time_note": note}


class LifeEvent(BaseModel):
    kind: str = Field(max_length=40)
    date: str = Field(max_length=20)
    note: str = Field(default="", max_length=200)


@app.get("/api/events")
def list_events(uid: str = Depends(consented)):
    return {"events": load(uid, "life_events", []),
            "kinds": [{"id": k, "about": v[1]} for k, v in rectify.EVENT_ANGLES.items()]}


@app.post("/api/events")
def add_event(e: LifeEvent, uid: str = Depends(consented)):
    if e.kind not in rectify.EVENT_ANGLES:
        raise HTTPException(400, f"I don't know what to do with '{e.kind}'.")
    try:
        dt.date.fromisoformat(e.date)
    except ValueError as e:
        raise HTTPException(400, "That date didn't parse. Use YYYY-MM-DD.") from e
    events = load(uid, "life_events", [])
    if len(events) >= 50:
        raise HTTPException(400, "You've added 50 events, which is the most I can use.")
    events.append({"kind": e.kind, "date": e.date, "note": e.note[:200]})
    save(uid, "life_events", events)
    return {"events": events}


@app.post("/api/rectify")
def run_rectify(uid: str = Depends(consented)):
    """
    Narrow an unknown birth time from dated life events.

    Deliberately quick to refuse. Before the permutation null was added, random
    dates produced confident nine-minute windows — so the honest outcome most of
    the time is "this didn't tell me anything", and the flow says so.
    """
    prof = _profile(uid)
    if not prof:
        raise HTTPException(400, "not onboarded")
    events = load(uid, "life_events", [])
    if len(events) < 3:
        return {"ok": False, "reason": "Give me at least three dated events first.",
                "have": len(events)}

    r = rectify.score(prof.get("birth_date") or prof["birth_local"].split(" ")[0],
                      prof["birth_ut"][4], prof["birth_ut"][5],
                      prof.get("tz") or "UTC", events)
    save(uid, "rectify", r)
    return r


class Tradition(BaseModel):
    houses: Optional[str] = None
    rulerships: Optional[str] = None


@app.get("/api/tradition")
def get_tradition(uid: str = Depends(consented)):
    houses, rulers = _tradition(uid)
    return {**tradition.describe(houses, rulers, _natal(uid) if _profile(uid) else None),
            "options": {k: v["note"] for k, v in tradition.HOUSE_SYSTEMS.items()},
            "rulership_options": sorted(tradition.RULERSHIPS)}


@app.post("/api/tradition")
def set_tradition(b: Tradition, uid: str = Depends(consented)):
    """
    Read this chart in a different astrology.

    Every derived artefact is keyed by the tradition, so switching does not need
    an invalidation pass — the caches simply miss and recompute.
    """
    prof = _profile(uid)
    if not prof:
        raise HTTPException(400, "not onboarded")
    try:
        if b.houses:
            prof["houses"] = tradition.house_system(b.houses)
        if b.rulerships:
            tradition.rulerships(b.rulerships)
            prof["rulerships"] = b.rulerships.lower()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    save(uid, "profile", prof)
    houses, rulers = _tradition(uid)
    # People reports are drawn in her house system and cached by person, not
    # by tradition, so they are rebuilt here rather than left in the old one.
    if b.houses:
        _rescore_connections(uid)
    log.emit("tradition.changed", uid=uid, houses=houses, rulerships=rulers)
    return {"ok": True, **tradition.describe(houses, rulers, _natal(uid))}


class Country(BaseModel):
    country: str


@app.post("/api/profile/country")
def set_country(b: Country, uid: str = Depends(consented)):
    """
    Where she is now, which is not where she was born. Crisis resources key
    off this, so it is the one profile field worth asking twice about.
    """
    code = b.country.strip().upper()
    if not cities.is_country(code):
        # "ZZ" used to save, and quietly hand her the directory.
        raise HTTPException(400, "Pick the country you live in from the list.")
    prof = _profile(uid)
    if not prof:
        raise HTTPException(400, "not onboarded")
    prof["country"] = code
    prof["country_source"] = "confirmed"
    save(uid, "profile", prof)
    return {"country": code, "crisis_line_known": crisis.known(code),
            "crisis_footer": crisis.footer(code)}


@app.get("/api/countries")
def country_list(response: Response):
    """The ISO 3166 codes a country can be set to, with English names, for the
    "where do you live now" picker. The same for everyone."""
    response.headers["Cache-Control"] = "public, max-age=86400"
    return {"countries": sorted(({"code": k, "name": v.strip()} for k, v in cities.COUNTRY.items()
                                 if k not in ("AN", "CS")), key=lambda c: c["name"])}


class About(BaseModel):
    hobbies: list[str] = Field(default_factory=list, max_length=40)
    interests: list[str] = Field(default_factory=list, max_length=40)
    skills: list[str] = Field(default_factory=list, max_length=40)
    passions: list[str] = Field(default_factory=list, max_length=40)
    dream: str = Field(default="", max_length=400)
    # Where she answered from, for the usage numbers only.
    source: str = Field(default="settings", max_length=12)


def _about_view(uid, about):
    prof = _profile(uid) or {}
    chart = about_mod.chart_view(_natal(uid), prof.get("birth_time_known", True))
    return {"about": about, "chart": chart, "mix": about_mod.mix(about, chart)}


@app.get("/api/about")
def get_about(uid: str = Depends(consented)):
    """Her hobbies, interests, skills and passions, the options to pick from,
    and where each option's planet sits in her chart."""
    if not _profile(uid):
        raise HTTPException(400, "not onboarded")
    return {**_about_view(uid, load(uid, "about", None)),
            "catalog": about_mod.CATALOG, "limits": about_mod.LIMITS,
            "custom_max": about_mod.CUSTOM_MAX}


@app.put("/api/about")
def put_about(b: About, uid: str = Depends(consented)):
    if not _profile(uid):
        raise HTTPException(400, "not onboarded")
    try:
        clean = about_mod.clean(b.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    prev = load(uid, "about", None) or {}
    about = about_mod.stamp({**clean, "nudge_off": prev.get("nudge_off", False)})
    save(uid, "about", about)
    n = sum(len(about[s]) for s in about_mod.STEPS)
    source = b.source if b.source in ("onboard", "nudge", "settings") else "settings"
    activity.track(uid, "about_saved", screen="about", count=n, source=source)
    return _about_view(uid, about)


@app.post("/api/about/later")
def about_later(uid: str = Depends(consented)):
    """'Not now' on the Home card: stop asking. Settings still has it."""
    about = load(uid, "about", None) or {}
    save(uid, "about", {**about, "nudge_off": True})
    return {"ok": True}


@app.get("/privacy")
def privacy():
    return FileResponse(STATIC / "privacy.html", headers={"Cache-Control": "no-cache"})


@app.get("/accuracy")
def accuracy_page():
    return FileResponse(STATIC / "accuracy.html", headers={"Cache-Control": "no-cache"})


# ------------------------------------------------- celebrity couples (public)
# Famous pairs scored by the same engine as the People tab (engine/couples.py).
# Public and sign-in-free: it is a page people share, and the way in to "now
# check your own". Rendered here rather than shipped as static HTML so every
# couple has its own title and share preview for search and group chats.

def _couples_page(title, desc, path, data, code=200):
    tpl = (STATIC / "couples.html").read_text()
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    for k, v in (("{{TITLE}}", html.escape(title)), ("{{DESC}}", html.escape(desc)),
                 ("{{CANONICAL}}", html.escape(f"https://mysignapp.com{path}")),
                 ("{{DATA}}", blob)):
        tpl = tpl.replace(k, v)
    return HTMLResponse(tpl, status_code=code,
                        headers={"Cache-Control": "public, max-age=300, s-maxage=900"})


@app.get("/api/couples")
def couples_list():
    return couples.listing()


@app.get("/api/couples/{slug}")
def couple_detail(slug: str):
    d = couples.detail(slug)
    if not d:
        raise HTTPException(404, "No such couple.")
    return d


@app.get("/couples")
def couples_index():
    return _couples_page(
        "Celebrity Couple Compatibility — My Sign",
        "The Obamas, Taylor & Travis, Beyoncé & Jay-Z, the Kardashians and history's "
        "great pairs — scored by My Sign's compatibility engine from their real birth dates.",
        "/couples", {"view": "list", **couples.listing()})


@app.get("/couples/{slug}")
def couple_page(slug: str):
    d = couples.detail(slug)
    if not d:
        return _couples_page("Couple not found — My Sign", "Celebrity couple compatibility.",
                             "/couples", {"view": "list", **couples.listing()}, code=404)
    a, b = d["names"]
    return _couples_page(
        f"{a} & {b} compatibility: {d['overall']}/100 — My Sign",
        f"{a} and {b} score {d['overall']}/100 ({d['band']}). See why: their charts, "
        "the aspects between them, and what it all means.",
        f"/couples/{slug}", {"view": "detail", "couple": d})


@app.get("/api/ops/couples")
def ops_couples(op: dict = Depends(ops.need("analyst"))):
    operators.audit(op, "couples.view")
    return couples_watch.ops_view()


@app.post("/api/ops/couples/run")
def ops_couples_run(op: dict = Depends(ops.need("admin"))):
    """Run the weekly check now: status from Wikidata, story research, then reads."""
    operators.audit(op, "couples.run")
    return couples_watch.run()


@app.post("/api/ops/couples/dismiss/{key}")
def ops_couples_dismiss(key: str, op: dict = Depends(ops.need("admin"))):
    operators.audit(op, "couples.dismiss", key=key)
    if not couples_watch.dismiss(key):
        raise HTTPException(404, "No such candidate.")
    return {"ok": True}


@app.post("/api/ops/couples/story/{key}/{decision}")
def ops_couples_story(key: str, decision: str, op: dict = Depends(ops.need("admin"))):
    """A single-source milestone the research found: publish it or drop it."""
    if decision not in ("approve", "drop"):
        raise HTTPException(404, "Approve or drop.")
    operators.audit(op, f"couples.story.{decision}", key=key)
    done = couples_research.approve(key) if decision == "approve" else couples_research.drop(key)
    if not done:
        raise HTTPException(404, "No such milestone.")
    return {"ok": True}


@app.get("/api/accuracy")
def accuracy_report():
    """Public, no sign-in: the evidence behind the accuracy page. The NASA
    comparison is rerun by this server; the reply counts are live."""
    return accuracy.report(places=cities.count())


@app.get("/terms")
def terms():
    return FileResponse(STATIC / "terms.html", headers={"Cache-Control": "no-cache"})


@app.get("/refunds")
def refunds():
    return FileResponse(STATIC / "refunds.html", headers={"Cache-Control": "no-cache"})


@app.get("/support")
def support_page():
    return FileResponse(STATIC / "support.html", headers={"Cache-Control": "no-cache"})


@app.get("/cookies")
def cookies_page():
    return FileResponse(STATIC / "cookies.html", headers={"Cache-Control": "no-cache"})


# The Instagram bio link: a landing page for people arriving from the reels.
# It tags itself utm_source=instagram before the tracker runs, so a bare
# mysignapp.com/ig link is still counted. deploy/edge/router.js does the same.
@app.get("/ig", include_in_schema=False)
def ig_page():
    return FileResponse(SITE / "ig.html", headers={"Cache-Control": "no-cache"})


# The public compatibility check. deploy/edge/router.js does the same.
@app.get("/check", include_in_schema=False)
def check_page():
    return FileResponse(SITE / "check.html", headers={"Cache-Control": "no-cache"})


GIFT_SIGNS = {"aries", "taurus", "gemini", "cancer", "leo", "virgo", "libra", "scorpio",
              "sagittarius", "capricorn", "aquarius", "pisces"}


# The gift guide (tools/gift_guide.py). deploy/edge/router.js does the same.
@app.get("/gifts", include_in_schema=False)
def gifts_page():
    return FileResponse(SITE / "gifts" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/gifts/{name}", include_in_schema=False)
def gifts_sign(name: str):
    if name in GIFT_SIGNS:
        return FileResponse(SITE / "gifts" / f"{name}.html", headers={"Cache-Control": "no-cache"})
    if re.fullmatch(r"pin-[a-z]+\.jpg", name) and (SITE / "gifts" / name).exists():
        return FileResponse(SITE / "gifts" / name)
    raise HTTPException(404)


SHOP = "https://shop.mysignapp.com"


# What deploy/edge/router.js does, for local dev and all-Lambda: /shop is our
# page, anything deeper is Shopify's.
@app.get("/shop", include_in_schema=False)
@app.get("/shop/{rest:path}", include_in_schema=False)
def shop(request: Request, rest: str = ""):
    if not rest:
        return FileResponse(SITE / "shop.html")
    q = request.url.query
    return RedirectResponse(f"{SHOP}/{rest}" + (f"?{q}" if q else ""), status_code=302,
                            headers={"Cache-Control": "no-store"})


def _console(name):
    """Inert pages; every number on them comes from /api/ops/*, which needs a
    signed-in operator (or the break-glass token)."""
    return FileResponse(STATIC / name, headers={"Cache-Control": "no-store",
                                                "X-Robots-Tag": "noindex"})


@app.get("/admin")
def admin_page():
    """admin.mysignapp.com/ — sign in, then pick where to go (deploy/edge/admin.js)."""
    return _console("admin.html")


@app.get("/analytics")
@app.get("/manage")
def console_page():
    return _console("ops.html")


@app.get("/ops")
def ops_page():
    """The console's old address. The browser carries any #/tab across."""
    if admin.url():
        return RedirectResponse(admin.url() + "/analytics", status_code=302,
                                headers=ops.NO_STORE)
    return _console("ops.html")


# ---------------------------------------------------------------- activity
# What screens people open and what they tap — never what they type. See
# app/activity.py for exactly what is and isn't accepted.

class ActivityBatch(BaseModel):
    session: str = Field(max_length=40)
    events: list[dict] = Field(default_factory=list, max_length=50)
    # The device's random pre-sign-in id (app/events.py ANON_RE), so the lake
    # can join a first visit to a sign-up. Optional; anything else is dropped.
    anon_id: Optional[str] = Field(default=None, max_length=40)


@app.post("/api/activity")
def post_activity(b: ActivityBatch, request: Request, uid: str = Depends(consented)):
    # A generous ceiling per minute; past it the app is looping, not a person.
    minute = int(time.time() // 60)
    if store.incr(uid, f"actrate-{minute % 10}", "n", limit=120,
                  ttl=int(time.time()) + 120) is None:
        return {"kept": 0}
    # Global Privacy Control / Do Not Track: her events still count, but the
    # device id that would tie them to her anonymous visits is not kept.
    from app.events import no_ids
    anon = None if no_ids(request.headers) else b.anon_id
    return {"kept": activity.record(uid, b.session, b.events, anon_id=anon)}


class AnalyticsPref(BaseModel):
    off: bool


@app.post("/api/privacy/analytics")
def set_analytics(b: AnalyticsPref, uid: str = Depends(consented)):
    """Turning it off also deletes what's held, so 'off' means none, not 'no more'."""
    acct = load(uid, "account", {}) or {}
    acct["analytics_off"] = b.off
    save(uid, "account", acct)
    if b.off:
        for k in store.keys(uid):
            if k.startswith("act-"):
                store.delete(uid, k)
    log.emit("analytics.pref", uid=uid, off=b.off)
    return {"off": b.off}


@app.get("/api/ops/activity/overview")
def ops_activity_overview(days: int = 14, op: dict = Depends(ops.need("analyst"))):
    days = max(1, min(days, 90))
    operators.audit(op, "activity.overview", days=days)
    return activity.overview(days)


@app.get("/api/ops/activity/users")
def ops_activity_users(days: int = 14, q: Optional[str] = None, stage: Optional[str] = None,
                       cursor: Optional[str] = None, limit: int = activity.PAGE,
                       op: dict = Depends(ops.need("safety"))):
    days = max(1, min(days, 90))
    # Whether a search ran, not what it was: it may be part of an address.
    operators.audit(op, "activity.users", days=days, searched=bool(q), stage=stage,
                    page=bool(cursor))
    return _paged(lambda: activity.users(days, q=(q or "")[:60] or None, limit=limit,
                                         cursor=cursor, stage=stage))


@app.get("/api/ops/activity/user/{subject}")
def ops_activity_user(subject: str, days: int = 14, op: dict = Depends(ops.need("safety"))):
    if not re.match(r"^[A-Za-z0-9_-]{4,64}$", subject) or not load(subject, "account", None):
        raise HTTPException(404, "No such person.", headers=ops.NO_STORE)
    days = max(1, min(days, 90))
    operators.audit(op, "journey.view", subject=subject, days=days)
    log.emit("ops.activity_user", subject=subject)
    return activity.journey(subject, days)


@app.get("/api/today")
def today(uid: str = Depends(consented)):
    if not _profile(uid):
        return {"onboarded": False}
    rollover(uid)
    d = _today(uid)
    sky = _sky(uid, d)
    checkin = load(uid, f"checkin-{d}", None) or {"text": "", "mood": None}
    _, verdict = context.build(uid, _natal(uid), sky, checkin, today=d, surface="push")
    return {"onboarded": True, "date": d.isoformat(),
            "band": verdict["band"], "score": verdict["score"],
            "moon": f"{sky['moon']['phase']}, {sky['moon']['illumination_pct']}% lit",
            "moon_sign": sky["moon_sign"],
            # Set when the Moon changes sign during her day: noon's sign alone
            # was wrong for half of it.
            "moon_ingress": sky.get("moon_ingress"),
            "headlines": [f"{plain(a['transit'])} {a['aspect']} your {plain(a['natal'])} · {a['orb']:.1f}°"
                          for a in verdict["headlines"]],
            # What each one means, in the same order — for the card and the UI.
            "meanings": [(corpus.transit(a["transit"], a["natal"], a["aspect"]) or {})
                         .get("reading", "") for a in verdict["headlines"]],
            "quiet": verdict["band"] == "low",
            # The first thing she reads: one line from the strongest real
            # transit, with the reason in plain words. See engine/lines.py.
            "line": lines.for_user(verdict["headlines"], d.isoformat(), uid,
                               quiet=verdict["band"] == "low",
                               moon_sign=sky.get("moon_sign")),
            "checkin": checkin.get("mood") and checkin,
            # Today drawn over her chart; the line it draws is the first headline.
            "wheel": _today_wheel(uid, d, [] if verdict["band"] == "low" else verdict["headlines"]),
            # The one-tap "does this land?" under the line: offered until used.
            "line_reacted": bool(load(uid, f"linereact-{d}", None)),
            # Asked once on Home of anyone who hasn't told us what they love.
            "about_nudge": _about_nudge(uid)}


def _today_wheel(uid, day, headlines):
    """
    What Today's wheel needs: her chart (see `_wheel`), where each planet is at
    her local noon today, and the two ends of the day's strongest contact so
    the page can draw it as one line. Longitudes only; nothing is interpreted.
    """
    try:
        nat_w = _wheel(uid)
        if not nat_w:
            return None
        tpos = positions(daily.local_noon_jd(day, _tz(uid)))
        out = {"natal": nat_w,
               "now": [{"name": k, "lon": v["lon"], "retro": bool(v.get("retrograde"))}
                       for k, v in tpos.items() if k in WHEEL_BODIES]}
        chart = _natal(uid)
        pts = {k: v["lon"] for k, v in chart["planets"].items()}
        if nat_w["time_known"]:
            pts.update({k: v["lon"] for k, v in chart["angles"].items()})
        for a in headlines or []:
            if a.get("transit") in tpos and a.get("natal") in pts:
                out["hit"] = {"transit": plain(a["transit"]), "natal": plain(a["natal"]),
                              "aspect": a["aspect"], "t_lon": tpos[a["transit"]]["lon"],
                              "n_lon": pts[a["natal"]]}
                break
        return out
    except Exception as e:
        log.error("today.wheel.failed", exc=e, uid=uid)
        return None


def _about_nudge(uid):
    about = load(uid, "about", None) or {}
    return not about_mod.done(about) and not about.get("nudge_off")


class CheckIn(BaseModel):
    mood: int
    text: str = Field(default="", max_length=500)
    # Optional, all of them: the 1-10 alone is still a complete check-in.
    energy: Optional[str] = None
    feelings: List[str] = Field(default_factory=list, max_length=12)
    tags: List[str] = Field(default_factory=list, max_length=24)
    # Asked the day after a low one: "anything help since yesterday?"
    helped: str = Field(default="", max_length=200)


@app.post("/api/checkin")
def checkin(c: CheckIn, uid: str = Depends(consented)):
    if not 1 <= c.mood <= 10:
        raise HTTPException(400, "Mood must be a number from 1 to 10.")
    now = _now(uid)
    energy, feelings, tags = mood_mod.clean(c.energy, c.feelings, c.tags)
    rec = {"mood": c.mood, "text": c.text.strip(), "time": now.strftime("%H:%M")}
    if energy:
        rec["energy"] = energy
    if feelings:
        rec["feelings"] = feelings
    if tags:
        rec["tags"] = tags
    if c.helped.strip():
        rec["helped"] = c.helped.strip()
    key = f"checkin-{now.date().isoformat()}"
    # A second check-in the same day replaces the first; its reply was already
    # given (the app reads this; /api/message enforces it with a counter).
    if (load(uid, key, None) or {}).get("reflected"):
        rec["reflected"] = True
    # Her words are the one part of a check-in that can say she is in danger,
    # and they must be heard whether or not a reply follows (a second
    # check-in gets none, a closed app sends none). Red opens the episode and
    # pages here; the flag makes the app ask for the crisis reply regardless.
    if _checkin_is_red(uid, " ".join(filter(None, [rec["text"], rec.get("helped")]))):
        rec["red"] = True
    save(uid, key, rec)
    picked = len(feelings) + len(tags)
    activity.track(uid, "checked_in", screen="today", mood=c.mood,
                   **({"count": picked} if picked else {}))
    return rec


def _checkin_is_red(uid, said):
    said = (said or "").strip()
    if not said:
        return False
    try:
        ep = episode.active(uid)
        t = safety.classify(said, history=thread.recent(uid, redreply.HISTORY_TURNS),
                            episode=episode.for_classifier(ep))
        if not safety.is_hard_stop(t.tier):
            return False
        kind = t.kind if t.kind in crisis.KINDS else safety.prefilter_kind(said)
        log.emit("safety.red", level="error", uid=uid, chars=len(said), kind=kind, source="checkin")
        log.metric("RedTierEvents", 1)
        _, severity = episode.record_red(uid, kind, bool(t.figurative))
        if severity and kind != "minor":
            alerts.red_tier(uid, said, kind=kind, severity=severity)
        return True
    except Exception as e:
        # Never fail the check-in over this; the floor still decides.
        log.error("safety.checkin.failed", exc=e, uid=uid)
        return safety.prefilter(said) == "red"


@app.get("/api/mood/vocab")
def mood_vocab():
    return mood_mod.vocabulary()


@app.get("/api/mood")
def mood_summary(uid: str = Depends(consented)):
    """The Mood tab: her check-ins over time, described, never diagnosed."""
    today = _today(uid)
    lo = f"checkin-{(today - dt.timedelta(days=400)).isoformat()}"
    hi = f"checkin-{today.isoformat()}"
    rows = [(k[len("checkin-"):], v) for k, v in store.load_range(uid, lo, hi)]
    out = mood_mod.summary(rows, today)
    # New and full moons on the calendar, in her own time zone: context for
    # her days, never a cause. Only the verifier connects the two.
    tz = _tz(uid)                    # where she is now, the zone _today uses
    out["moons"] = {}
    for i in range(35):
        d = today - dt.timedelta(days=i)
        try:
            ph = day_phase(d, tz)["phase"]
        except Exception:
            continue
        if ph in ("full moon", "new moon"):
            out["moons"][d.isoformat()] = ph.split()[0]
    return out


class Msg(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE)
    # The reply to today's check-in. The server writes the message itself from
    # the saved check-in (text is ignored), and it is free once a day: the
    # check-in is never limited, so talking about it shouldn't be either.
    checkin: bool = False
    # A one-tap reaction to the line of the day: "yes", "sort" or "no". Like
    # the check-in reply, the server writes the message and it is free once a day.
    line: Optional[str] = Field(default=None, max_length=4)
    # Said aloud in the psychic's room rather than typed. The turn is the same
    # in every way (safety, limits, memory, voice); only analytics tells them apart.
    voice: bool = False


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


FALLBACK_REPLY = ("I lost my thread there and didn't get an answer out. Ask me "
                  "again? That one didn't count against today.")


RED_MODEL_PER_HOUR = 30


def _crisis_response(uid, text, d, tier=None, history=None, ep_before=None):
    """
    The red path. No astrology, no quota, no plan gate — and first, before
    anything that can refuse. A person in crisis is the likeliest to be sending
    a lot.

    The reply is written by a model with the safety prompt, for this kind of
    red, in her country (engine/redreply.py); the canned text for that kind and
    country covers an API failure. It used to be one canned suicide check for
    every red, repeated word for word.
    """
    kind = (tier.kind if tier and tier.kind in crisis.KINDS else
            safety.prefilter_kind(text))
    figurative = bool(tier and tier.figurative)
    log.emit("safety.red", level="error", uid=uid, chars=len(text), kind=kind)
    log.metric("RedTierEvents", 1)
    log.emit("message", uid=uid, tier="red", chars=len(text), demo=DEMO)
    log.metric("Messages", 1, tier="red", demo=DEMO)
    # Page before replying — once per episode per severity, not per message.
    # Neither the episode store nor paging may stand between her and the reply.
    try:
        _, severity = episode.record_red(uid, kind, figurative)
        if severity and kind != "minor":
            alerts.red_tier(uid, text, kind=kind, severity=severity)
    except Exception as e:
        log.error("safety.page.failed", exc=e, uid=uid)
    country = (_profile(uid) or {}).get("country")
    history = thread.recent(uid, redreply.HISTORY_TURNS) if history is None else history
    last_bot = next((x for x in reversed(history) if x.get("role") == "assistant"), None)
    repeat = bool(last_bot and last_bot.get("red_source") == "fallback")
    now = _now(uid).isoformat(timespec="seconds")
    thread.append(uid, {"id": uuid.uuid4().hex[:8], "role": "user", "text": text, "at": now})

    def finish(reply, source):
        # She may have deleted her account while this streamed.
        if not load(uid, "account", None):
            return
        mid = uuid.uuid4().hex[:8]
        at = _now(uid).isoformat(timespec="seconds")
        thread.append(uid, {"id": mid, "role": "assistant", "text": reply, "band": None,
                            "tier": "red", "red_kind": kind, "red_source": source,
                            "tools": [], "at": at})
        opsindex.flag(uid, at, "red", mid)
        save(uid, "last_outbound", mid)
        metrics.tag_outbound(uid, mid, reply, "", {"band": "n/a", "score": 0}, "red")
        if kind == "minor":
            _flag_age(uid, at)

    def crisis_stream():
        # A plain generator: Starlette runs it in a worker thread, so the model
        # call does not hold the event loop.
        yield _sse("meta", {"band": "n/a", "score": 0, "tier": "red"})
        reply, source = "", "fallback"
        try:
            # Never refused, but past a ceiling no human reaches, the reply is
            # the written one for her kind and country instead of a fresh model
            # call: the red path skips every quota, so a script repeating a red
            # phrase was an unmetered model bill.
            canned = DEMO or store.incr(uid, f"redreplies-{int(time.time() // 3600)}", "n",
                                        limit=RED_MODEL_PER_HOUR,
                                        ttl=int(time.time()) + 2 * 3600) is None
            if canned:
                reply = crisis.fallback(kind, country, repeat=repeat)
                for i in range(0, len(reply), 6):
                    yield _sse("delta", {"t": reply[i:i + 6]})
            else:
                for ev, payload in redreply.stream(history, text, kind, country, figurative,
                                                   ep_before, repeat=repeat):
                    if ev == "delta":
                        reply += payload
                        yield _sse("delta", {"t": payload})
                    else:
                        reply, source = payload["text"], payload["source"]
            yield _sse("done", {"tier": "red", **({"age_locked": True} if kind == "minor" else {})})
        finally:
            # Stored even if she closed the app mid-reply: the thread, the ops
            # flag and the age flag must not depend on the socket staying open.
            finish(reply.strip() or crisis.fallback(kind, country, repeat=repeat), source)
    return StreamingResponse(crisis_stream(), media_type="text/event-stream")


def _flag_age(uid, at):
    """
    She told us she is under 18. The terms say 18+, so normal service stops:
    /api/state shows a kind screen with resources and the delete button, and
    ops gets a notice. Nothing is deleted automatically — that is hers to do,
    or a person's to decide.
    """
    acct = load(uid, "account", None)
    if not acct or acct.get("age_flag"):
        return
    acct["age_flag"] = {"at": at, "source": "chat"}
    save(uid, "account", acct)
    log.emit("safety.age_flag", level="warning", uid=uid)
    alerts.age_notice(uid)


SESSION_GAP = dt.timedelta(hours=3)


def _session(uid, now):
    """Whether this message opens a conversation. Computed before her message
    is stored, so 'first today' means nothing earlier today."""
    last = next((x for x in reversed(thread.recent(uid, 4)) if x.get("role") == "user"), None)
    try:
        at = dt.datetime.fromisoformat(last["at"]) if last and last.get("at") else None
    except ValueError:
        at = None
    if at is None or at.date() != now.date():
        return "first today"
    if at.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    return "back after a break" if now - at >= SESSION_GAP else "continuing"


def _news(uid):
    """What's new today that she didn't do herself: linked friends who chose to
    share how they're feeling. Only the number they shared, nothing else."""
    out = []
    for row in _connections(uid):
        day = _their_day(row)
        if day and isinstance(day.get("mood"), int):
            out.append(f"{row.get('name', 'someone')} (linked) checked in today: {day['mood']}/10")
        if len(out) >= 3:
            break
    return out


# Busy, not wrong: worth one more try on the other model. A 4xx other than 429
# (a bad request, a refusal) would fail the same way on any model.
_BUSY_TYPES = {"overloaded_error", "rate_limit_error", "api_error"}


def _model_busy(e):
    import anthropic
    conn = getattr(anthropic, "APIConnectionError", None)
    status = getattr(anthropic, "APIStatusError", None)
    if conn and isinstance(e, conn):                          # timeouts too
        return True
    if status and isinstance(e, status):
        if e.status_code == 429 or e.status_code >= 500:
            return True
        # An error event mid-stream arrives on a 200 response.
        body = e.body if isinstance(e.body, dict) else {}
        return (body.get("error") or {}).get("type") in _BUSY_TYPES
    return False


class Speak(BaseModel):
    text: str = Field(min_length=1, max_length=psychic.MAX_CHARS)
    voice: str = Field(min_length=1, max_length=20)
    rate: float = Field(default=1.0, ge=0.5, le=1.5)


@app.get("/api/psychic/voices")
def psychic_voices(uid: str = Depends(consented)):
    """The psychic room's studio voices, when ElevenLabs is set up here."""
    on = psychic.enabled()
    return {"enabled": on, "voices": psychic.public_voices() if on else []}


@app.post("/api/psychic/speak")
async def psychic_speak(b: Speak, uid: str = Depends(consented)):
    """
    One line of the reader's reply, as MP3 from ElevenLabs. Only words she was
    actually given (her latest replies, or the room's fixed lines), on a
    daily character budget. 404 when ElevenLabs isn't set up: the room then
    uses the device's own voices.
    """
    if not psychic.enabled():
        raise HTTPException(404, "No studio voices here.")
    v = psychic.voice(b.voice)
    if not v:
        raise HTTPException(400, "Unknown voice.")
    text = b.text.strip()
    replies = [x.get("text") or "" for x in thread.recent(uid, 6) if x.get("role") == "assistant"][-2:]
    if not psychic.allowed(text, replies):
        raise HTTPException(403, "She can only say what she has already answered.")
    day = plan._cap_day(uid, _today(uid)).isoformat()
    if not psychic.claim(uid, plan.get(uid)["tier"], day, len(text)):
        log.emit("psychic.tts_capped", uid=uid)
        raise HTTPException(429, "Her voice is resting for today. She'll still answer in writing.")
    try:
        upstream, close = await psychic.open_stream(v, speech.speakable(text), b.rate)
    except Exception as e:
        log.error("psychic.tts_failed", exc=e, uid=uid)
        raise HTTPException(502, "Her voice didn't come through.") from e
    log.metric("PsychicTtsChars", len(text))

    async def body():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await close()
    return StreamingResponse(body(), media_type="audio/mpeg",
                             headers={"Cache-Control": "no-store"})


@app.post("/api/message")
async def message(m: Msg, uid: str = Depends(consented)):
    if not _profile(uid):
        raise HTTPException(400, "not onboarded")
    d = _today(uid)
    text = m.text.strip()
    reflect, free_key = False, None
    if m.checkin:
        rec = load(uid, f"checkin-{d}", None)
        if not rec:
            raise HTTPException(409, "Check in first.")
        text, reflect = mood_mod.reflect_text(rec), True
        # Once a day, claimed atomically (parallel requests used to all pass the
        # read), on the same day the message cap counts so a zone hop can't buy
        # another. A red check-in always gets its reply: the crisis path is
        # never refused.
        if not rec.get("red"):
            free_key = f"freereply-checkin-{plan._cap_day(uid, d).isoformat()}"
            if store.incr(uid, free_key, "n", limit=1, ttl=int(time.time()) + 3 * 86400) is None:
                raise HTTPException(409, "Today's check-in has already had its reply.")
    react_key = None
    if m.line and not reflect:
        if m.line not in mood_mod.LINE_REACTIONS:
            raise HTTPException(400, "Pick yes, sort of, or no.")
        react_key = f"linereact-{d}"
        shown = ((load(uid, "daily_seen", {}) or {}).get(d.isoformat()) or {}).get("text")
        if not shown:
            raise HTTPException(409, "Today's line has already had its reply.")
        # The same once-a-day claim as the check-in reply.
        free_key = f"freereply-line-{plan._cap_day(uid, d).isoformat()}"
        if store.incr(uid, free_key, "n", limit=1, ttl=int(time.time()) + 3 * 86400) is None:
            raise HTTPException(409, "Today's line has already had its reply.")
        text = mood_mod.line_text(shown, m.line)
    if not text:
        raise HTTPException(400, "Say something first.")

    # Her language, from her own words, before anything that answers in it
    # (the cap message below). Never worth failing a message over.
    try:
        i18n.observe(uid, text)
    except Exception as e:
        log.error("lang.observe.failed", exc=e, uid=uid)

    # Safety first, before anything that can say no. The local pre-filter is
    # instant and never fails. An open crisis episode (a red message in the
    # last day) lifts the burst limiter and the plan cap for her.
    ep = episode.active(uid)
    # Only a real episode lifts her limits. One figurative red ("this exam
    # will kill me") used to lift the burst limiter and the free cap for the
    # day: ~120 messages on the free plan.
    real_ep = episode.for_classifier(ep)
    floor = safety.prefilter(text)

    # Then the ceilings — atomically, so ten parallel requests cannot all pass
    # a limit of eight. Nothing has been stored yet, so a refusal leaves no
    # half-turn behind. A refusal waits for the classifier: a red message the
    # regex missed ("i have my mums pills saved up") must not get "slow down".
    refused = None
    if floor != "red":
        allowed, info = quota.claim(uid, burst=not real_ep, daily_max=costs.daily_message_cap(
            uid, plan.get(uid)["tier"], quota.DAILY_MAX))
        if not allowed:
            refused = info

    # The model classifier, with the thread and the episode. It can only
    # escalate a pattern hit, except to call a quoted joke about someone else
    # what it is. A crisis is a crisis on the free tier, so it runs before the
    # plan gate.
    history = thread.recent(uid, redreply.HISTORY_TURNS)
    told = episode.for_classifier(ep)
    tier = safety.classify(text, history=history, episode=told)
    if safety.is_hard_stop(tier.tier):
        return _crisis_response(uid, text, d, tier, history, told)
    if (refused or (load(uid, "account", {}) or {}).get("age_flag")) and free_key:
        store.incr(uid, free_key, "n", by=-1)        # refused: the free reply stays hers
    if refused:
        log.emit("quota.refused", uid=uid, reason=refused["reason"])
        log.metric("QuotaRefusals", 1, reason=refused["reason"])
        raise HTTPException(429, refused["message"],
                            headers={"Retry-After": str(refused["retry_after"])})
    if (load(uid, "account", {}) or {}).get("age_flag"):
        raise HTTPException(403, {"code": "age", "message": AGE_MESSAGE})
    claimed = False
    if real_ep or reflect or react_key:
        # Never a paywall in the middle of a crisis, nor on the check-in reply
        # or the reaction to the line of the day.
        may, pinfo = True, {}
    else:
        may, pinfo = plan.claim_message(uid, d)
        claimed = may
    if not may:
        log.emit("plan.gated", uid=uid, used=pinfo["used"])
        activity.track(uid, "paywall_shown", screen="chat", source="chat_cap")
        raise HTTPException(402, pinfo["message"])
    if reflect:
        save(uid, f"checkin-{d}", {**rec, "reflected": True})
    if react_key:
        save(uid, react_key, {"r": m.line})
    funnel.hit(uid, "first_message")
    activity.track(uid, "message_sent", screen="chat", tier=tier.tier,
                   source="checkin" if reflect else "line" if react_key
                   else "voice" if m.voice else "typed")

    session = _session(uid, _now(uid))
    thread.append(uid, {"id": uuid.uuid4().hex[:8], "role": "user", "text": text,
                        "at": _now(uid).isoformat(timespec="seconds")})
    last = load(uid, "last_outbound", None)
    if last:
        metrics.tag_reply(uid, last, text)
    # What she goes looking for, separately from what happened to her. Never
    # worth failing a message over: the quota slot is already spent.
    try:
        M.remember_question(uid, text, _sky_state(_sky(uid, d)))
    except Exception as e:
        log.error("questions.failed", exc=e, uid=uid)

    # Shape, not content. Never the message itself — see app/log.py.
    log.emit("message", uid=uid, tier=tier.tier, chars=len(text), demo=DEMO)
    log.metric("Messages", 1, tier=tier.tier, demo=DEMO)

    checkin = load(uid, f"checkin-{d}", None) or {"text": text}
    block, verdict = context.build(uid, _natal(uid), _sky(uid, d), checkin,
                                   today=d, tier=tier.tier, surface="chat",
                                   message=text, session=session,
                                   news=_news(uid), episode=told,
                                   channel="voice" if m.voice else "text")
    # Plus talks to the stronger model until her month's AI cost reaches the
    # premium budget, then carries on, unlimited, on the lighter one.
    my_tier = plan.get(uid)["tier"]
    premium = costs.premium_chat(uid, my_tier)
    if my_tier == "plus" and not premium:
        log.emit("cost.lighter_model", uid=uid)
    chat_model = models.chat_model("plus" if premium else "free")
    # team2/memory: a saved person named in the message brings their computed
    # day, the pair's contacts and her own dated words about them — as the
    # result of a get_person_day lookup, not as more system context, so every
    # aspect the reply cites can be checked against a tool result.
    person_facts, their_day = _person_context(uid, text, d)
    preload = ([{"name": "get_person_day", "input": {"name": person_facts["person"]["name"]},
                 "out": person_facts}] if person_facts else None)

    def persist(full, band, t, used=None, chips=None):
        # She may have deleted her account while this reply was streaming;
        # writing now would recreate items under an id nobody owns.
        if not load(uid, "account", None):
            return None
        mid = uuid.uuid4().hex[:8]
        at = _now(uid).isoformat(timespec="seconds")
        thread.append(uid, {"id": mid, "role": "assistant", "text": full,
                            "band": band, "tier": t,
                            # Kept so reopening the thread still shows the work.
                            "tools": [x["name"] for x in (used or []) if not x.get("quiet")],
                            **({"chips": chips} if chips else {}),
                            "at": at})
        save(uid, "last_outbound", mid)
        if t in ("red", "amber"):
            opsindex.flag(uid, at, t, mid)
        return mid

    async def stream():
        yield _sse("meta", {"band": verdict["band"], "score": verdict["score"],
                            "tier": tier.tier})
        full, grounding, used, failed, raw = "", block, [], False, ""
        if DEMO:
            # Demo mode looks things up for real. The model is absent, not the
            # engine — so a question about another day runs the actual tool and
            # answers from the actual sky, and the lookup is visible in the UI
            # exactly as it will be with a key.
            asked = _demo_lookup_date(text, d)
            if person_facts:
                # team2/memory: the person path, deterministic and grounded in
                # the same get_person_day result the keyed path hands the model.
                p = preload[0]
                yield _sse("tool", {"name": p["name"]})
                yield _sse("tool_result", {"name": p["name"], "ok": True,
                                           "summary": person_facts["person"]["name"]})
                used = [{"name": p["name"], "input": p["input"], "ok": True}]
                grounding = block + f"\n[{p['name']}] " + json.dumps(p["out"], default=str)
                full = personmem.demo_reply(person_facts, their_day)
            elif asked:
                yield _sse("tool", {"name": "get_sky"})
                out = tools.run("get_sky", uid, _natal(uid), {"date": asked})
                ok = not out.get("error")
                used = [{"name": "get_sky", "input": {"date": asked}, "ok": ok}]
                grounding = block + "\n[get_sky] " + json.dumps(out, default=str)
                yield _sse("tool_result", {"name": "get_sky", "ok": ok,
                                           "summary": f"{out.get('weekday','')} {asked}".strip()})
                full = _demo_sky_reply(out) if ok else \
                    "I couldn't look that day up — check the date?"
            else:
                full = _demo_reply(text, verdict, block)
            for i in range(0, len(full), 4):
                yield _sse("delta", {"t": full[i:i + 4]})
        else:
            stopped = None
            try:
                import anthropic
                client = anthropic.Anthropic()
                # Drop the last entry: it is the message being answered, and it
                # is passed separately as the current turn. Empty turns are left
                # out — the API rejects them, and one would break every turn
                # after it until it left the window.
                history = [{"role": x["role"], "content": x["text"]}
                           for x in thread.recent(uid, voice.history_window())[:-1]
                           if x["role"] in ("user", "assistant") and (x.get("text") or "").strip()]
                # LENGTH held in code (audit MEDIUM-4): past the ceiling the
                # middle is dropped and the closing question kept.
                lim = (voice.Limiter(voice.sentence_limit(verdict.get("max_sentences"), text))
                       if tier.tier == "green" else None)
                # The stronger model overloaded or rate-limited after the SDK's
                # own retries: if she has seen no words yet, the lighter model
                # (same prompt, same tools, what free already talks to) answers
                # instead of an error. Once words are on screen, a retry would
                # repeat them, so the plain fallback stands.
                models_to_try = [chat_model] + ([models.CHAT_FREE] if chat_model != models.CHAT_FREE else [])
                spoke, tools_shown = False, set()
                for attempt, model in enumerate(models_to_try):
                    if attempt:                      # the limiter counted the failed try's text
                        lim = (voice.Limiter(voice.sentence_limit(verdict.get("max_sentences"), text))
                               if tier.tier == "green" else None)
                    # Each sentence is checked against the sky before she sees it
                    # (engine/guard.py): an invented aspect is held back, not shown.
                    grd = guard.Guard(block)
                    try:
                        for kind, payload in agent.run(client, SYSTEM, block, text, history,
                                                       uid, _natal(uid), tools, today=d,
                                                       model=model, preload=preload,
                                                       effort=models.chat_effort(tier.tier)):
                            if kind == "delta":
                                ok_text = grd.feed(payload)
                                shown = (lim.feed(ok_text) if lim else ok_text) if ok_text else ""
                                if shown:
                                    spoke = True
                                    yield _sse("delta", {"t": shown})
                            elif kind == "ground":
                                grd.add_context(payload)
                            elif kind in ("tool", "tool_result"):
                                key = (kind, payload["name"])
                                if key not in tools_shown:      # not twice on a retry
                                    tools_shown.add(key)
                                    yield _sse(kind, {"name": payload["name"]} if kind == "tool" else payload)
                            else:
                                raw = payload["text"]
                                last, full = grd.finish(raw)
                                last = (lim.feed(last) if lim else last) if last else ""
                                if last:
                                    spoke = True
                                    yield _sse("delta", {"t": last})
                                grounding = payload["grounding"]
                                used = payload["tools_used"]
                                stopped = payload["stopped"]
                        break
                    except Exception as e:
                        if spoke or attempt == len(models_to_try) - 1 or not _model_busy(e):
                            raise
                        log.emit("agent.model_fallback", level="warning", uid=uid,
                                 frm=model, to=models_to_try[attempt + 1], exc=type(e).__name__)
            except Exception as e:
                log.error("agent.failed", exc=e, uid=uid)
                failed = True
            log.emit("agent.turn", uid=uid, stopped=stopped, failed=failed,
                     tools=[t["name"] for t in used],
                     tool_failures=sum(1 for t in used if not t["ok"]))
            # A refusal, a turn that ran out of room while thinking, or an
            # exception mid-stream: say so plainly, give the message back, and
            # never store an empty turn.
            if failed or not full.strip() or stopped in ("refusal", "max_tokens"):
                # Give back only what was taken: a crisis or free turn never
                # claimed a message (refunding one left a free account at -1),
                # and a free reply that failed can be asked for again.
                if claimed:
                    plan.refund_message(uid, d)
                if free_key:
                    store.incr(uid, free_key, "n", by=-1)
                yield _sse("error", {"message": i18n.text("fallback", i18n.lang_of(uid))})
                yield _sse("done", {"demo": DEMO, "tier": tier.tier, "failed": True})
                return
            if lim:
                full, tail = lim.finish(full)
                if tail:
                    yield _sse("delta", {"t": tail})

        # Two or three things she might tap next. Never on a crisis turn.
        chips = [] if tier.tier == "red" else chips_mod.suggest(text, full, uid=uid,
                                                                 lang=i18n.lang_of(uid))
        mid = persist(full, verdict["band"], tier.tier, used, chips)
        if mid is None:
            return
        # The follow-ups this reply was briefed with and actually asked about.
        try:
            M.mark_raised(uid, verdict.get("follow_ups"), d, reply=full)
        except Exception as e:
            log.error("loops.mark.failed", exc=e, uid=uid)
        if chips:
            yield _sse("chips", {"chips": chips})
        # Grounding, not the briefing: a fact the agent looked up is legitimately
        # citable even though it was not in the opening block.
        metrics.tag_outbound(uid, mid, full, grounding, verdict, tier.tier)
        # Every real reply is scored for invented placements — the count the
        # accuracy page publishes. Demo replies are canned, not the model, so
        # they would only flatter the number.
        if not DEMO:
            try:
                # The model's own words are scored, not the guarded text, so the
                # published number stays honest about the model.
                withheld = bool(grd.dropped)
                ok, problems = accuracy.record_reply(raw, grounding, withheld=withheld)
                if not ok:
                    log.emit("grounding.failed", level="error", uid=uid, problems=problems[:5],
                             withheld=withheld, shown_ok=grounding_mod.check(full, grounding)[0])
            except Exception as e:
                log.error("grounding.record.failed", exc=e, uid=uid)
        log_mention(uid, d.isoformat(), verdict["mention_keys"])
        yield _sse("done", {"demo": DEMO, "tier": tier.tier, "id": mid})

    return StreamingResponse(stream(), media_type="text/event-stream")


class Report(BaseModel):
    id: str = Field(max_length=16)
    reason: str = Field(default="", max_length=300)


@app.post("/api/report")
def report_reply(b: Report, uid: str = Depends(consented)):
    """
    She flags a reply as wrong, hurtful or unsafe. Apple asks for this on any
    app that shows generated content (1.2, 4.7); it is also the cheapest
    quality signal there is. Kept on her record — so it is in her export and
    goes with her deletion — and raised to the operators as a metric.
    """
    msg = next((m for m in thread.recent(uid, 0)
                if m.get("id") == b.id and m.get("role") == "assistant"), None)
    if not msg:
        raise HTTPException(404, "I couldn't find that reply.")
    reports = load(uid, "reports", []) or []
    if len(reports) >= 200:
        raise HTTPException(429, "You've sent a lot of reports. Please write to support instead.")
    at = _now(uid).isoformat(timespec="seconds")
    reports.append({"id": b.id, "reason": b.reason.strip(), "at": at})
    save(uid, "reports", reports)
    opsindex.flag(uid, at, "report", b.id, kind="report")
    log.emit("reply.reported", level="warning", uid=uid, msg=b.id)
    log.metric("RepliesReported", 1)
    return {"ok": True}


@app.post("/api/rollover")
def force_rollover(include_today: bool = False, uid: str = Depends(consented)):
    return {"written": [e["date"] for e in rollover(uid, include_today=include_today)]}


class Sub(BaseModel):
    subscription: dict
    tz: Optional[str] = None


@app.get("/api/push/key")
def push_key():
    return {"key": push.public_key()}


@app.post("/api/push/subscribe")
def push_subscribe(b: Sub, uid: str = Depends(consented)):
    n = push.subscribe(uid, b.subscription, b.tz)
    if n is False:              # not falsy: zero devices is a count, not a refusal
        raise HTTPException(400, "That notification subscription didn't look right.")
    return {"devices": n}


@app.delete("/api/push/subscribe")
def push_unsubscribe(endpoint: Optional[str] = None, uid: str = Depends(consented)):
    return {"devices": push.unsubscribe(uid, endpoint)}


@app.get("/api/push/status")
def push_status(uid: str = Depends(consented)):
    subs = load(uid, "push", []) or []
    last = load(uid, f"pushed-{_today(uid).isoformat()}", None)
    return {"devices": len(subs), "today": last}


# ---- the iPhone app's notifications (APNs, app/apns.py) --------------------
# The same notifications and the same settings as above; only the delivery
# differs. Every route answers 404 until the key is configured and switched on.

class NativeDevice(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    tz: Optional[str] = Field(default=None, max_length=64)
    replaces: Optional[str] = Field(default=None, max_length=200)   # the token before a refresh


def _apns_or_404():
    if not apns.enabled():
        raise HTTPException(404, "Notifications in the iPhone app aren't available yet.")


@app.post("/api/push/apns")
def push_apns_register(b: NativeDevice, uid: str = Depends(consented)):
    """This phone, on. Safe to repeat: the app sends it again at each launch,
    which is also how a refreshed token replaces the old one."""
    _apns_or_404()
    n = apns.register(uid, b.token, b.tz, b.replaces)
    if n is False:
        raise HTTPException(400, "That device token didn't look right.")
    log.emit("push.apns.registered", uid=uid, devices=n)
    return {"devices": n, "registered": True}


@app.post("/api/push/apns/remove")
def push_apns_remove(b: NativeDevice, uid: str = Depends(consented)):
    """This phone, off. Her other devices keep theirs."""
    _apns_or_404()
    return {"devices": apns.unregister(uid, b.token), "registered": False}


@app.post("/api/push/apns/status")
def push_apns_status(b: NativeDevice, uid: str = Depends(consented)):
    """Whether this phone is on, for the switch in Settings. A POST so the
    token rides in the body, never in a URL."""
    _apns_or_404()
    last = load(uid, f"pushed-{_today(uid).isoformat()}", None)
    return {"registered": apns.registered(uid, b.token), "devices": apns.devices(uid),
            "today": last}


class NotifyPrefs(BaseModel):
    time: Optional[str] = Field(default=None, max_length=5)     # HH:MM, her local time
    style: Optional[str] = Field(default=None, max_length=10)   # "line" or "sky"
    off: Optional[bool] = None


@app.get("/api/notify")
def get_notify(uid: str = Depends(consented)):
    acct = load(uid, "account", {}) or {}
    return {"time": acct.get("push_time") or "07:30",
            "style": acct.get("push_style") or "line",
            "off": bool(acct.get("push_off"))}


@app.post("/api/notify")
def set_notify(b: NotifyPrefs, uid: str = Depends(consented)):
    """When the morning notification comes, and what it says. Quarter-hour
    steps, because the scheduler runs every fifteen minutes."""
    acct = load(uid, "account", {}) or {}
    if b.time is not None:
        try:
            hh, mm = (int(x) for x in b.time.split(":"))
            assert 5 <= hh <= 22 and mm in (0, 15, 30, 45)
        except Exception as e:
            raise HTTPException(400, "Pick a time between 05:00 and 22:45, on the quarter hour.") from e
        acct["push_time"] = f"{hh:02d}:{mm:02d}"
    if b.style is not None:
        if b.style not in ("line", "sky"):
            raise HTTPException(400, "Style must be 'line' or 'sky'.")
        acct["push_style"] = b.style
    if b.off is not None:
        acct["push_off"] = bool(b.off)
    save(uid, "account", acct)
    return get_notify(uid)


PUSH_TESTS_PER_HOUR = 3


@app.post("/api/push/test")
def push_test(uid: str = Depends(consented)):
    """Send one now, so opting in has visible proof it worked. A few an hour:
    each one fans out to every device she has (audit M5)."""
    now = time.time()
    if store.incr(uid, f"pushtest-{int(now // 3600)}", "n", limit=PUSH_TESTS_PER_HOUR,
                  ttl=int(now) + 2 * 3600) is None:
        raise HTTPException(429, "You've sent several test notifications already. Try again in an hour.")
    return push.send(uid, "My Sign", "That worked. This is how your morning notifications will look.",
                     kind="test")


class Connection(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    relation: Optional[str] = Field(default=None, max_length=80)
    date: str = Field(max_length=20)
    time: Optional[str] = Field(default=None, max_length=20)
    # Optional, as in "Before you date": a birthday alone still places every
    # planet; the Moon and houses are what a time and place add.
    city: Optional[str] = Field(default=None, max_length=200)
    # How sure she is of their time, and what they are to her. Both change the
    # reading: an approximate time is checked across its window, and a coworker
    # is not weighed on chemistry.
    time_precision: Optional[str] = Field(default=None, max_length=20)
    kind: Optional[str] = Field(default=None, max_length=20)
    # Which occurrence of a repeated hour, as for her own chart.
    fold: int = Field(default=0, ge=0, le=1)
    # "check" when this person was carried over from the public check.
    source: Optional[str] = Field(default=None, max_length=20)


class ConnectionEdit(BaseModel):
    relation: Optional[str] = Field(default=None, max_length=80)
    kind: Optional[str] = Field(default=None, max_length=20)
    time: Optional[str] = Field(default=None, max_length=20)
    time_precision: Optional[str] = Field(default=None, max_length=20)
    fold: Optional[int] = Field(default=None, ge=0, le=1)

# The shipped ephemeris files cover 1800-2399; before 1800 Chiron silently
# vanished from the chart. A person's birth date has to be a real past date.
CONNECTION_MIN_YEAR = 1800


def _precision(time_, given):
    if given and given not in compat.PRECISIONS:
        raise HTTPException(400, "Birth time should be exact, approximate or unknown.")
    if not time_:
        return "unknown"
    return given if given in ("exact", "approximate") else "exact"


def _kind(given, relation):
    if given and given not in compat.KINDS:
        raise HTTPException(400, f"Pick one of: {', '.join(compat.KINDS)}.")
    return given or compat.infer_kind(relation)


def _apply_report(row, rep):
    """The headline numbers on the row come from the deep report: weighted for
    what the relationship is, and given as a range when a time is uncertain."""
    sc = rep["score"]
    row.update({"report_v": rep.get("version"),
                "overall": sc["overall"], "overall_range": sc["overall_range"],
                "band": sc["band"], "dimensions": sc["dimensions"],
                "dimension_ranges": sc["dimension_ranges"], "kind": sc["kind"],
                "themes": [{"key": t["key"], "title": t["title"],
                            "presence": t["presence"], "tone": t["tone"]}
                           for t in rep["themes"][:6]]})


def _connections(uid):
    return load(uid, "connections", [])


def _fresh_connections(uid):
    """
    The rows, with any score from an older report version brought up to date.

    Lazily, on the first list read after a scoring change, rather than a
    migration: the row's headline number comes from the deep report, and a row
    scored before overlays counted both ways would keep showing her a number
    her linked friend never sees.
    """
    rows = _connections(uid)
    stale = [r for r in rows if r.get("report_v") != compat.REPORT_VERSION]
    if stale and _profile(uid):
        for r in stale:
            try:
                _apply_report(r, compat.report_for(uid, r))
            except Exception as e:          # one bad row never hides the list
                log.error("connections.refresh_failed", exc=e, uid=uid)
        save(uid, "connections", rows)
    return rows


def _their_chart(uid, birth_ut):
    """Someone else's chart, drawn in HER house system: overlays on People
    were always Placidus while her own reading used whatever she chose."""
    return natal(*birth_ut, houses=_tradition(uid)[0])


def _chart_from(date, time_, city, fold=0, with_note=False):
    """Birth data -> chart, using the same tz handling onboarding uses. An
    unknown time falls back to noon and the caller is told, because without it
    there are no houses and half the reading is gone.

    with_note=True adds the repeated/skipped-hour note as a fifth value: it
    used to be computed and thrown away, so a friend born in the hour the
    clocks went back was charted as certain.

    No city: noon UT on the date, read as an unknown time (the same as Before
    you date, app/social.py). A time without a place means nothing — the offset
    could be anything from -12 to +14 hours — so it is set aside, not read as UTC."""
    if not city:
        try:
            y, m, d = (int(x) for x in date.split("-"))
            born = dt.date(y, m, d)
        except (ValueError, TypeError) as err:
            raise HTTPException(400, "That birth date didn't parse.") from err
        if y < CONNECTION_MIN_YEAR:
            raise HTTPException(400, f"I can only chart births from {CONNECTION_MIN_YEAR} on.")
        if born > dt.date.today():
            raise HTTPException(400, "That date is in the future — check the year?")
        birth_ut = [y, m, d, 12.0, 0.0, 0.0]
        out = (natal(*birth_ut), birth_ut, False, None)
        return out + (None,) if with_note else out
    c, maybe = cities.lookup(city)
    if not c:
        raise HTTPException(400, cities.refusal(
            city, maybe, f"I couldn't find {city}. Pick one from the list."))
    try:
        y, m, d = (int(x) for x in date.split("-"))
        born = dt.date(y, m, d)
        known = bool(time_)
        hh, mm = (int(x) for x in time_.split(":")) if known else (12, 0)
        dt.time(hh, mm)
    except (ValueError, TypeError) as err:
        raise HTTPException(400, "That birth date or time didn't parse.") from err
    if y < CONNECTION_MIN_YEAR:
        raise HTTPException(400, f"I can only chart births from {CONNECTION_MIN_YEAR} on.")
    if born > dt.date.today():
        raise HTTPException(400, "That date is in the future — check the year?")
    utc, note = _birth_instant(y, m, d, hh, mm, c["tz"], c["lat"], c["lon"], known, fold)
    birth_ut = [utc.year, utc.month, utc.day,
                round(utc.hour + utc.minute / 60, 4), c["lat"], c["lon"]]
    if with_note:
        return natal(*birth_ut), birth_ut, known, c, note
    return natal(*birth_ut), birth_ut, known, c


def _rescore_connections(uid):
    """
    Recompute every stored score against the current profile.

    Adding a birth time changes the ascendant, and with it the house overlays
    and which reference distribution applies. A score computed blind is not
    slightly off — it was answering a different question — so it gets redone
    rather than left to age quietly.
    """
    rows = _connections(uid)
    if not rows:
        return 0
    prof = _profile(uid) or {}
    mine = _natal(uid)
    known_a = prof.get("birth_time_known", False)
    for r in rows:
        sc = synastry.score(mine, _their_chart(uid, r["birth_ut"]),
                            time_known_a=known_a,
                            time_known_b=r.get("birth_time_known", False))
        r.update({k: sc[k] for k in
                  ("overall", "band", "dimensions", "drivers", "frictions",
                   "overlays", "elements", "contacts", "confidence")})
        r.setdefault("kind", compat.infer_kind(r.get("relation")))
        _apply_report(r, compat.report_for(uid, r, recompute=True))
        r["rescored_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    save(uid, "connections", rows)
    return len(rows)


@app.get("/api/connections")
def connections(uid: str = Depends(consented)):
    """Everyone she has ever checked, newest first. The list is the point —
    one score is a party trick, the history is the product."""
    rows = sorted(_fresh_connections(uid), key=lambda r: r["checked_at"], reverse=True)
    ctx = _day_ctx(uid)
    return {"connections": [{k: r.get(k) for k in
                             ("id", "name", "relation", "born", "checked_at",
                              "overall", "band", "dimensions", "confidence",
                              "kind", "overall_range", "themes", "time_precision")}
                            | {"today": _today_brief(uid, r, ctx),
                               "linked": bool(r.get("linked_uid")),
                               "their_day": _their_day(r),
                               # Where their Sun sits, for the map of her people.
                               "sun_lon": _sun_lon(r.get("birth_ut"))}
                            for r in rows],
            "count": len(rows),
            "me": {"sun_lon": _sun_lon((_profile(uid) or {}).get("birth_ut"))}}


def _sun_lon(birth_ut):
    """The Sun's longitude at a birth. It moves a degree a day, so it holds to
    within a degree even when the birth time is unknown and noon was assumed."""
    try:
        y, m, d, h = birth_ut[:4]
        return positions(jd_ut(y, m, d, h))["Sun"]["lon"]
    except (TypeError, ValueError, IndexError):
        return None        # no birth data on the row; the map just leaves them off


@app.get("/api/people/today")
def people_today(uid: str = Depends(consented)):
    """
    Her people, ranked by how much today's sky moved each of them.

    This is the reason to open the People tab every morning: the order and the
    reasons reshuffle with real transits, and every person she adds makes
    tomorrow's list richer. Every word comes from engine/daily.py — the
    headline, the why and how long it lasts are the day's own reasons, quoted.
    """
    ctx = _day_ctx(uid)
    if not ctx["prof"]:
        raise HTTPException(400, "Finish your own chart first.")
    today_iso = ctx["day"].isoformat()
    rows, briefs, checked = _connections(uid), [], set()
    feel_today = 0
    for r in rows:
        d = _brief(uid, r, ctx)
        pulse = load(uid, f"pulse-{r['id']}", {}) or {}
        checked.update(pulse)
        if d is None:
            continue
        top = (d["reasons"] or [None])[0]
        feel = (pulse.get(today_iso) or {}).get("feel")
        feel_today += bool(feel)
        briefs.append({
            "id": r["id"], "name": r.get("name") or "them",
            "relation": r.get("relation"), "kind": r.get("kind"),
            "score": d["score"], "usual": d["usual"], "delta": d["delta"],
            "mood": d["mood"], "headline": d["headline"],
            "why": top and top["short"], "why_detail": top and top["text"],
            "lasts": top and top["lasts"], "feel_today": feel,
            "linked": bool(r.get("linked_uid")), "their_day": _their_day(r),
        })
    out = daily.rank_people(briefs)
    try:
        moon_sign = _sky(uid, ctx["day"])["moon_sign"]
    except Exception as e:          # the ranking stands without the Moon line
        log.error("people_today.sky_failed", exc=e, uid=uid)
        moon_sign = None
    log.emit("people.today", uid=uid, people=len(rows), quiet=out["quiet"])
    return {"date": today_iso, "moon_sign": moon_sign, "count": len(briefs),
            **out, "checked_in_today": feel_today,
            "streak": daily.streak(checked, ctx["day"])}


@app.post("/api/connections")
def add_connection(c: Connection, uid: str = Depends(consented)):
    prof = _profile(uid)
    if not prof:
        raise HTTPException(400, "Finish your own chart first.")
    name = c.name.strip()
    if not name:
        raise HTTPException(400, "Give them a name.")

    if not plan.may_add_person(uid, len(_connections(uid))):
        # Said before the chart is computed, so the refusal is instant and
        # nothing she typed is half-saved.
        log.emit("plan.gated_people", uid=uid)
        activity.track(uid, "paywall_shown", screen="people", source="people_cap")
        raise HTTPException(402, {"code": "plus_people", "message": plan.PEOPLE_CAP_MESSAGE,
                                  "prices": plan.PRICES, "free_people": plan.FREE_PEOPLE})

    their, birth_ut, known, city, note = _chart_from(c.date, c.time, c.city, c.fold,
                                                     with_note=True)
    # A time with no place was set aside above; the row must not claim it.
    time_ = c.time if city else None
    precision = _precision(time_, c.time_precision)
    kind = _kind(c.kind, c.relation)
    mine = _natal(uid)
    sc = synastry.score(mine, their,
                        time_known_a=prof.get("birth_time_known", False),
                        time_known_b=known)

    row = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "relation": (c.relation or "").strip() or None,
        "born": f"{c.date} {time_ or '(time unknown)'} {city['name'] if city else '(place unknown)'}",
        "birth_ut": birth_ut,
        "birth_time_known": known,
        "birth_date": c.date, "birth_time": time_ or None, "birth_city": c.city or None,
        "place_known": bool(city),
        "birth_time_note": note,
        "time_precision": precision, "kind": kind,
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "overall": sc["overall"],
        "band": sc["band"],
        "dimensions": sc["dimensions"],
        "drivers": sc["drivers"],
        "frictions": sc["frictions"],
        "overlays": sc["overlays"],
        "elements": sc["elements"],
        "contacts": sc["contacts"],
        "confidence": sc["confidence"],
    }
    rows = _connections(uid)
    if len(rows) >= 100:
        raise HTTPException(400, "You've reached the limit of 100 people. Remove someone first.")
    _apply_report(row, compat.report_for(uid, row, recompute=True))
    rows.append(row)
    save(uid, "connections", rows)
    if not (c.source == "check" and name == "Them"):     # a placeholder isn't a person she named
        M.learn_people(uid, [name])
    activity.track(uid, "person_added", screen="people", kind=kind, precision=precision,
                   count=len(rows))
    if c.source == "check":
        funnel.hit(uid, "check_joined")
    return row


@app.get("/api/connections/compare")
def compare_connections(uid: str = Depends(consented)):
    """
    Everyone she has saved, ranked — with ties where the gap is smaller than
    this can actually measure.

    Declared before /api/connections/{cid} on purpose: FastAPI matches in order,
    and "compare" would otherwise be read as a connection id.
    """
    rows = _connections(uid)
    result = synastry.compare(rows)
    log.emit("connections.compared", uid=uid, people=result.get("people", 0),
             ok=result["ok"])
    return result


@app.get("/api/card/pair/{cid}.svg")
def card_pair(cid: str, initials: int = 0, uid: str = Depends(consented)):
    """
    "Us, today" — the People tab as something she can post. First names only,
    or just an initial if she'd rather; never anyone's birth data, because the
    other person on the card never agreed to be on it. Every word on it comes
    from engine/daily.py, the same numbers the tab shows.
    """
    row = _row(uid, cid)
    me = ((_profile(uid) or {}).get("name") or "").split()
    me = me[0] if me else "Me"
    first = (row.get("name") or "").split()
    them = first[0] if first else "Them"
    if initials:
        them = them[0].upper() + "."
    # The engine writes the name into its reasons ("…touching Theo's heart"), so
    # the day is read under the card's name — otherwise "initials only" would
    # still print the full name in the headline.
    d = _daily(uid, {**row, "name": them})
    day = dt.date.fromisoformat(d["date"])
    date_line = f"{day.strftime('%A')} · {day.strftime('%B')} {day.day}"
    svg = cards.pair_card(me, them, date_line, d["score"], d["delta"], d["mood"], d["headline"])
    log.emit("card.pair", uid=uid, initials=bool(initials))
    return Response(svg, media_type="image/svg+xml",
                    headers={"Content-Disposition": 'inline; filename="my-sign-us-today.svg"'})


@app.get("/api/card/compat/{cid}.svg")
def card_compat(cid: str, initials: int = 0, uid: str = Depends(consented)):
    """The overall number, to post. No link and no invite: a post is seen by
    anyone, and an invite is a door to her chart. Names as on the pair card."""
    row = _row(uid, cid)
    me = ((_profile(uid) or {}).get("name") or "").split()
    me = me[0] if me else "Me"
    first = (row.get("name") or "").split()
    them = first[0] if first else "Them"
    if initials:
        them = them[0].upper() + "."
    svg = cards.compat_card(me, them, row["overall"], row["band"],
                            estimate=row.get("time_precision") != "exact")
    log.emit("card.compat", uid=uid, initials=bool(initials))
    return Response(svg, media_type="image/svg+xml",
                    headers={"Content-Disposition": 'inline; filename="my-sign-us.svg"'})


class PaywallIntent(BaseModel):
    plan: str = Field(max_length=12)
    surface: str = Field(max_length=24)


@app.post("/api/plan/intent")
def paywall_intent(b: PaywallIntent, uid: str = Depends(consented)):
    """
    She tapped a price before there is a way to pay. Counted, so demand is known
    before billing ships; the reply says plainly that it isn't for sale yet.
    """
    # "waitlist": the paywall while Plus can't be bought shows no prices, only
    # "tell me when it's ready" — no purchase UI that isn't one (3.1.1).
    if b.plan not in ("annual", "monthly", "waitlist") or not re.fullmatch(r"[a-z_]{1,24}", b.surface):
        raise HTTPException(400, "Unknown plan.")
    log.emit("paywall.intent", uid=uid, plan=b.plan, surface=b.surface)
    funnel.hit(uid, "paywall_intent")
    return {"ok": True, "available": plan.PRICES["available"],
            "message": "Plus isn't available to buy yet. You're on the list — we'll tell you "
                       "the day it is, and nothing is charged before then."}


class Checkout(BaseModel):
    plan: str = Field(max_length=12)
    surface: str = Field(default="", max_length=24)


def _billing_or_409():
    if not plan.PRICES["available"]:
        raise HTTPException(409, "Plus isn't available to buy yet.")


@app.post("/api/billing/checkout")
def billing_checkout(b: Checkout, request: Request, uid: str = Depends(consented)):
    """Where the price buttons go: a Stripe Checkout page for that plan."""
    _billing_or_409()
    if b.plan not in billing.LOOKUP:
        raise HTTPException(400, "Unknown plan.")
    if plan.get(uid)["tier"] == "plus":
        raise HTTPException(409, "You already have Plus.")
    acct = load(uid, "account", {}) or {}
    try:
        url = billing.checkout(uid, b.plan, acct.get("email"), public_url(request))
    except billing.BillingError as e:
        log.error("billing.checkout_failed", exc=e, uid=uid)
        raise HTTPException(502, "Payments are having a moment. Nothing was charged — try again shortly.") from e
    log.emit("billing.checkout", uid=uid, plan=b.plan, surface=b.surface)
    funnel.hit(uid, "paywall_intent")
    return {"url": url}


@app.post("/api/billing/portal")
def billing_portal(request: Request, uid: str = Depends(consented)):
    """Stripe's page for her card, her plan, and cancelling."""
    _billing_or_409()
    try:
        return {"url": billing.portal(uid, public_url(request))}
    except billing.BillingError as e:
        log.error("billing.portal_failed", exc=e, uid=uid)
        raise HTTPException(502, "Couldn't open billing just now. Try again shortly.") from e


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request):
    """
    Stripe tells us a subscription changed. Not signed in and not consented —
    the signature is the authentication. A failure answers 500 so Stripe
    retries; a bad signature answers 400 and is never retried into effect.
    """
    if not billing.enabled():
        raise HTTPException(404, "Not found.")
    body = await request.body()
    if not billing.verify(body, request.headers.get("stripe-signature", "")):
        log.emit("billing.webhook_refused")
        raise HTTPException(400, "Bad signature.")
    try:
        event = json.loads(body)
        result = billing.handle(event)
    except billing.BillingError as e:
        log.error("billing.webhook_failed", exc=e)
        raise HTTPException(500, "Try again.") from e
    log.emit("billing.webhook", type=event.get("type"), result=result)
    return {"ok": True}


class AppleSync(BaseModel):
    signedTransaction: str = Field(min_length=1, max_length=20000)


@app.post("/api/billing/apple/sync")
def billing_apple_sync(b: AppleSync, uid: str = Depends(consented)):
    """
    The iPhone app hands over the signed transaction from a purchase, a
    restore or a renewal, so Plus unlocks before Apple's notification lands.
    The app finishes the transaction with StoreKit only on `finish`.
    """
    if not apple.enabled():
        raise HTTPException(409, "Plus isn't available to buy yet.")
    try:
        r = apple.sync(uid, b.signedTransaction)
    except apple.AppleError as e:
        log.emit("billing.apple_sync_refused", uid=uid, reason=str(e))
        raise HTTPException(400, "That purchase couldn't be verified. Nothing changed.") from e
    log.emit("billing.apple_sync", uid=uid, result=r["code"])
    if not r["ok"]:
        raise HTTPException(409, {"code": r["code"], "finish": r["finish"],
                                  "message": "This Apple subscription belongs to a different "
                                             "My Sign account. Sign in to that account to use it."})
    return r


class AppleNotification(BaseModel):
    signedPayload: str = Field(min_length=1, max_length=100000)


@app.post("/api/billing/apple/notify")
def billing_apple_notify(b: AppleNotification):
    """
    App Store Server Notifications V2. Not signed in: Apple's signature on the
    payload is the authentication. A payload that doesn't verify answers 400
    and changes nothing; anything else that fails answers 500 so Apple retries.
    """
    if not apple.enabled():
        raise HTTPException(404, "Not found.")
    try:
        result = apple.notify(b.signedPayload)
    except apple.AppleError as e:
        log.emit("billing.apple_notify_refused", reason=str(e))
        raise HTTPException(400, "Bad signature.") from e
    log.emit("billing.apple_notify", result=result)
    return {"ok": True}


@app.get("/api/connections/{cid}")
def connection(cid: str, uid: str = Depends(consented)):
    for r in _connections(uid):
        if r["id"] == cid:
            # Another person's account id is an internal key, not hers to hold.
            return {k: v for k, v in r.items() if k != "linked_uid"} | \
                {"linked": bool(r.get("linked_uid"))}
    raise HTTPException(404, "No such connection.")


@app.delete("/api/connections/{cid}")
def drop_connection(cid: str, uid: str = Depends(consented)):
    rows = _connections(uid)
    kept = [r for r in rows if r["id"] != cid]
    if len(kept) == len(rows):
        raise HTTPException(404, "No such connection.")
    save(uid, "connections", kept)
    row = next(r for r in rows if r["id"] == cid)
    # Forgetting someone forgets everything built from them, including their
    # name in the chat's context unless she saved another person by that name.
    _forget_row(uid, row, kept)
    other = row.get("linked_uid")
    _reveal_forget(uid, other)  # team2/people
    # A link sent about this person goes with them; it carries their score.
    for t, rec in _my_invites(uid):
        if (rec.get("pair") or {}).get("cid") != cid:
            continue
        if _invite_open(rec):
            store.delete("_invites", _invite_key(t))
        else:
            rec.pop("pair")
            save("_invites", _invite_key(t), rec, ttl=int(rec["expires"]))
    if other:
        # Mutual (audit H4): she leaves their People too, with her birth data,
        # her mood and everything they held that was built from her chart.
        _drop_link(other, uid)
        _drop_link(uid, other)
        log.emit("link.removed", uid=uid)
    return {"ok": True}


def _row(uid, cid):
    for r in _connections(uid):
        if r["id"] == cid:
            return r
    raise HTTPException(404, "No such connection.")


@app.get("/api/connections/{cid}/deep")
def connection_deep(cid: str, uid: str = Depends(consented)):
    """
    Everything the two charts say, itemised. Fast — no model call — so it can
    render the moment she opens someone; the written reading is fetched
    separately because it takes a model's worth of seconds.
    """
    row = _row(uid, cid)
    rep = i18n.compat_report(compat.report_for(uid, row), i18n.lang_of(uid))
    return {"person": {k: row.get(k) for k in
                       ("id", "name", "relation", "kind", "born", "time_precision",
                        "checked_at")} | {"linked": bool(row.get("linked_uid"))},
            **{k: v for k, v in rep.items() if k != "reading"},
            "reading": rep.get("reading")}


@app.post("/api/connections/{cid}/reading")
def connection_reading(cid: str, uid: str = Depends(consented)):
    """The written reading, generated once and kept with the report. Only a
    finished one is kept — a truncated paragraph cached forever is how the
    natal reading first went wrong."""
    row = _row(uid, cid)
    rep = compat.report_for(uid, row)
    if rep.get("fun"):
        return {"reading": rep.get("reading"), "fun": rep["fun"]}
    if DEMO:
        return {"reading": None, "fun": compat_fun.fallback(rep, row["name"])}
    from engine import spend
    # Metered per user per UTC day (audit H1): a new person, or a changed birth
    # time, is a new read, and nothing else is. Spent, she gets the authored
    # read and a plain line saying the written one comes tomorrow.
    if os.environ.get("ANTHROPIC_API_KEY") and not spend.claim(uid, "compat"):
        return {"reading": None, "fun": compat_fun.fallback(rep, row["name"]),
                "capped": True, "message": spend.message("compat")}
    # One call writes the fun read; its "dynamic" doubles as the old prose
    # paragraph so anything still reading `reading` keeps working.
    prof = _profile(uid)
    theirs = _their_chart(uid, row["birth_ut"])
    fun = compat_fun.generate(rep, row["name"], _natal(uid), theirs,
                              prof.get("birth_time_known", True),
                              row.get("birth_time_known", False),
                              lang=i18n.lang_of(uid))
    if fun:
        rep["fun"], rep["reading"] = fun, fun.get("dynamic")
        save(uid, f"compat-{cid}", rep)
        return {"reading": rep["reading"], "fun": fun}
    return {"reading": None, "fun": compat_fun.fallback(rep, row["name"])}


@app.patch("/api/connections/{cid}")
def edit_connection(cid: str, b: ConnectionEdit, uid: str = Depends(consented)):
    """Correct what she knows about someone. A time or a kind changes the
    reading, so the report is rebuilt rather than patched."""
    rows = _connections(uid)
    row = next((r for r in rows if r["id"] == cid), None)
    if not row:
        raise HTTPException(404, "No such connection.")
    before = compat.inputs_sig(_profile(uid) or {}, row)
    if b.relation is not None:
        row["relation"] = b.relation.strip() or None
    if b.kind is not None:
        row["kind"] = _kind(b.kind, row.get("relation"))
    if b.time is not None or b.time_precision is not None:
        date = row.get("birth_date") or (row.get("born") or "").split(" ")[0]
        city = row.get("birth_city")
        if not city:
            raise HTTPException(400, "To use a birth time, I need their birth city. Remove them "
                                     "and add them again with the city.")
        time_ = row.get("birth_time") if b.time is None else (b.time or None)
        _, birth_ut, known, cinfo, note = _chart_from(date, time_, city, b.fold or 0,
                                                      with_note=True)
        row.update({"birth_ut": birth_ut, "birth_time_known": known, "birth_time": time_,
                    "birth_time_note": note,
                    "time_precision": _precision(time_, b.time_precision
                                                 or row.get("time_precision")),
                    "born": f"{date} {time_ or '(time unknown)'} {cinfo['name']}"})
    prof = _profile(uid) or {}
    if compat.inputs_sig(prof, row) == before:
        # Nothing the reading depends on moved (an empty PATCH, the same kind
        # again, a relation label). Rebuilding used to drop the written read,
        # and the next open paid the model again (audit H1).
        save(uid, "connections", rows)
        return row
    sc = synastry.score(_natal(uid), _their_chart(uid, row["birth_ut"]),
                        time_known_a=prof.get("birth_time_known", False),
                        time_known_b=row.get("birth_time_known", False))
    row.update({k: sc[k] for k in ("drivers", "frictions", "overlays", "elements",
                                   "contacts", "confidence")})
    _apply_report(row, compat.report_for(uid, row, recompute=True))
    save(uid, "connections", rows)
    return row


# ------------------------------------------------------- daily compatibility

def _daily(uid, row):
    prof = _profile(uid) or {}
    rep = compat.report_for(uid, row)
    return daily.for_pair(prof, row, rep, _today(uid), _tz(uid))


# ------------------------------------------------------------------- invites
#
# "Add yourself to my sky." She sends a link; a friend opens it, makes their own
# account, enters their OWN birth data, and agrees to share it with her. Then
# each appears in the other's People, with the daily score that moves with the
# sky. Nothing about the friend exists until the friend has said yes, and only
# the 1-10 of a check-in is ever shared - never what anyone writes.
#
# The rules, after the security audit (H4, M4):
# - A link is single-use, lives a week, and is hers to cancel. One forwarded
#   to a group chat used to admit 25 strangers to her exact birth data and her
#   daily mood, and nothing could stop it.
# - She sees who joined through each link.
# - Mood sharing is per person. Each side says yes for the other: the friend
#   at accept time (unticked by default), the inviter on the friend's card.
#   A link from before this follows the master switch, as it always did.
#   "Share how I'm feeling" in Settings is the master switch over all of it,
#   and accepting an invite never writes it — it used to, silently turning
#   sharing back on for everyone.
# - Unlinking is mutual. Removing a linked person takes you out of their
#   People too, with everything derived from your chart; before, they kept
#   your birth data and your live mood.

INVITE_DAYS = 7
INVITE_MAX_USES = 1      # also what a legacy 25-use link is held to now
INVITE_OPEN_MAX = 10     # open links per person; each is a door to her chart


def _invite_key(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,40}", token or ""):
        raise HTTPException(404, "That invite link isn't valid.")
    return f"inv-{token}"


def _invite(token):
    inv = load("_invites", _invite_key(token), None)
    if not inv or inv.get("expires", 0) < time.time():
        raise HTTPException(404, "That invite has expired. Ask for a new link.")
    return inv


def _invite_open(inv):
    return len(inv.get("used_by") or []) < inv.get("max_uses", INVITE_MAX_USES)


def _my_invites(uid):
    """(token, record) for each of her links that still exists, oldest first.
    The index is what lets her see, cancel and delete them (audit M4: the
    records used to be unfindable from her side, so deletion left them)."""
    out = []
    for t in load(uid, "invites", []) or []:
        rec = load("_invites", f"inv-{t}", None)
        if rec and rec.get("from") == uid and rec.get("expires", 0) >= time.time():
            out.append((t, rec))
    return out


def _forget_invites(uid):
    """Account deletion: her links go, and her uid leaves anyone else's link
    she joined through."""
    for t in load(uid, "invites", []) or []:
        store.delete("_invites", f"inv-{t}")
    for t in load(uid, "joined_invites", []) or []:
        rec = load("_invites", f"inv-{t}", None)
        if rec:
            rec["used_by"] = [u for u in rec.get("used_by") or [] if u != uid]
            rec["accepted"] = [a for a in rec.get("accepted") or [] if a.get("uid") != uid]
            save("_invites", f"inv-{t}", rec, ttl=int(rec.get("expires", time.time())))


def _new_invite(uid, prof, pair=None):
    mine = _my_invites(uid)
    if sum(_invite_open(r) for _, r in mine) >= INVITE_OPEN_MAX:
        raise HTTPException(429, f"You have {INVITE_OPEN_MAX} invite links waiting. "
                                 "Cancel one in People first.")
    token = secrets.token_urlsafe(12)
    first = (prof.get("name") or "Someone").split()[0][:30]
    expires = time.time() + INVITE_DAYS * 86400
    rec = {"from": uid, "name": first, "created": time.time(), "expires": expires,
           "max_uses": INVITE_MAX_USES, "used_by": [], "accepted": []}
    if pair:
        rec["pair"] = pair
    # The ttl means the table forgets an unused link on its own (audit M4).
    save("_invites", _invite_key(token), rec, ttl=int(expires))
    save(uid, "invites", [t for t, _ in mine] + [token])
    log.emit("invite.created", uid=uid)
    funnel.hit(uid, "invite_created")
    return token, first


@app.post("/api/connections/{cid}/send")
def send_compat(cid: str, request: Request, uid: str = Depends(consented)):
    """
    Send this person the two of you: a one-time invite that also carries her
    first name and the score, because she chose to tell them. Tapping again
    hands back the same waiting link, so one person never holds several doors.
    The score is all that travels. Their name stays on her side, and the link
    opens the usual yes-or-no.
    """
    prof = _profile(uid)
    if not prof:
        raise HTTPException(400, "Finish your own chart first.")
    row = _row(uid, cid)
    if row.get("linked_uid"):
        raise HTTPException(409, "You two are already linked.")
    them = ((row.get("name") or "").split() or ["them"])[0][:30]
    pair = {"cid": cid, "them": them, "score": row["overall"]}
    for t, rec in _my_invites(uid):
        if (rec.get("pair") or {}).get("cid") == cid and _invite_open(rec):
            token, first = t, rec["name"]
            if rec["pair"] != pair:     # a birth time was added since
                rec["pair"] = pair
                save("_invites", _invite_key(t), rec, ttl=int(rec["expires"]))
            break
    else:
        token, first = _new_invite(uid, prof, pair)
        funnel.hit(uid, "card_sent")
    base = public_url(request)
    return {"url": f"{base}/api/i/{token}", "from": first, "them": them, "score": row["overall"],
            "expires_in_days": INVITE_DAYS,
            "text": f"I got {row['overall']} with you on My Sign. Add your birth details "
                    f"and see if it holds ✦"}


def _unfurl(title, desc, target, base):
    """A link's preview in a chat, then on to `target`. Previews don't run
    scripts, so these tags are the only thing they see."""
    t, d, u = html.escape(title), html.escape(desc), html.escape(target)
    return HTMLResponse(
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{t}</title>'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="robots" content="noindex">'
        f'<meta property="og:type" content="website"><meta property="og:site_name" content="My Sign">'
        f'<meta property="og:title" content="{t}"><meta property="og:description" content="{d}">'
        f'<meta property="og:image" content="{html.escape(base)}/og.png">'
        f'<meta name="twitter:card" content="summary_large_image">'
        f'<meta http-equiv="refresh" content="0;url={u}"></head>'
        f'<body style="background:#0F0B0D"><a href="{u}" style="color:#E6CFA8">{t}</a></body></html>',
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@app.get("/api/i/{token}", include_in_schema=False)
def invite_page(token: str, request: Request):
    """
    What a sent link unfurls to in a chat, then the app. Message previews don't
    run scripts, so this is the one place the score can show before the tap.
    Under /api so it reaches this Lambda without a new CloudFront behaviour.
    Reading it changes nothing: previews fetch it too, and a preview must never
    count as an open or use up a one-time link.
    """
    base = public_url(request)
    well_formed = re.fullmatch(r"[A-Za-z0-9_-]{10,40}", token)
    rec = load("_invites", f"inv-{token}", None) if well_formed else None
    title, desc, target = "My Sign", "Who you are, in plain words.", f"{base}/app"
    # A dead link still carries its token to the app, which can say what
    # happened (used, expired, or you're already linked). The preview stays generic.
    if well_formed:
        target = f"{base}/app?invite={token}"
    if rec and time.time() <= rec.get("expires", 0) and _invite_open(rec):
        pair = rec.get("pair") or {}
        if pair:
            title = f"{rec['name']} got {pair['score']} with you on My Sign"
            desc = "It's half the story until you add your own birth details."
        else:
            title = f"{rec['name']} invited you to link charts on My Sign"
    return _unfurl(title, desc, target, base)


@app.post("/api/invites")
def create_invite(request: Request, uid: str = Depends(consented)):
    prof = _profile(uid)
    if not prof:
        raise HTTPException(400, "Finish your own chart first.")
    token, first = _new_invite(uid, prof)
    base = public_url(request)          # never the Host header (audit M2)
    return {"token": token, "url": f"{base}/app?invite={token}", "from": first,
            "expires_in_days": INVITE_DAYS, "max_uses": INVITE_MAX_USES}


@app.get("/api/invites")
def list_invites(uid: str = Depends(consented)):
    """Her links: when each expires, and who joined through it. First names
    only, and the People row to open — never anyone's account id."""
    by_link = {r.get("linked_uid"): r for r in _connections(uid) if r.get("linked_uid")}
    out = []
    for t, rec in reversed(_my_invites(uid)):
        joined = [{"name": a.get("name") or "Someone", "at": a.get("at"),
                   "person_id": (by_link.get(a.get("uid")) or {}).get("id")}
                  for a in rec.get("accepted") or []]
        out.append({"token": t, "created": rec.get("created"), "expires": rec.get("expires"),
                    "open": _invite_open(rec), "joined": joined,
                    "for": (rec.get("pair") or {}).get("them")})
    return {"invites": out}


@app.delete("/api/invites/{token}")
def revoke_invite(token: str, uid: str = Depends(consented)):
    """Cancel a link. Whoever already joined stays in People until she removes
    them there (which unlinks both sides); nobody new can use it."""
    rec = load("_invites", _invite_key(token), None)
    if not rec or rec.get("from") != uid:
        raise HTTPException(404, "No such invite.")
    store.delete("_invites", _invite_key(token))
    save(uid, "invites", [t for t in load(uid, "invites", []) or [] if t != token] or None)
    log.emit("invite.revoked", uid=uid)
    return {"ok": True}


@app.get("/api/invites/{token}")
def preview_invite(token: str, request: Request):
    """Public: the friend has no account yet. Only the inviter's first name -
    nothing else about her leaves before the friend has said yes. A signed-in
    viewer is also told whether it's her own link or she's already linked, so
    the app can say so before asking her to join."""
    inv = _invite(token)
    out = {"from": inv["name"], "valid": _invite_open(inv)}
    pair = inv.get("pair") if out["valid"] else None
    if pair:
        out["score"] = pair["score"]    # she chose to send it; their name stays hers
    viewer = auth.current_user(request)
    # The app asks this, a link preview doesn't, so it counts people. Once a
    # link, on a counter of its own: this route is public, and writing the
    # record back here could undo an accept or a cancel that landed meanwhile.
    if pair and viewer != inv["from"] \
            and store.incr("_invites", f"opened-{token}", "opened", limit=1,
                       ttl=int(inv["expires"])) is not None:
        funnel.tally("card_opened")
        log.emit("card.opened")
    if viewer:
        out["own"] = inv["from"] == viewer
        out["linked"] = any(r.get("linked_uid") == inv["from"] for r in _connections(viewer))
    if not out["valid"]:
        out["reason"] = "used"
    return out


class Accept(BaseModel):
    share_chart: bool
    share_mood: bool = False         # opt-in: nobody's mood is shared by default


def _linked_row(owner_uid, other_uid, relation=None, like=None):
    """A People row for `other_uid`, built from their own profile - the data
    they entered and agreed to share - scored against `owner_uid`. `like` is the
    row this takes over: it keeps her id, so her notes stay with the person, and
    her name for them, relation and kind."""
    relation = (like or {}).get("relation", relation)
    mine, theirs = _profile(owner_uid), _profile(other_uid)
    known = bool(theirs.get("birth_time_known"))
    row = {
        "id": (like or {}).get("id") or uuid.uuid4().hex[:12],
        "name": (like or {}).get("name") or theirs.get("name") or "them",
        "relation": relation, "born": theirs.get("birth_local"),
        "birth_ut": theirs["birth_ut"], "birth_time_known": known,
        "birth_date": theirs.get("birth_date"), "birth_time": theirs.get("birth_time"),
        "birth_city": theirs.get("birth_city"),
        "time_precision": "exact" if known else "unknown",
        "kind": (like or {}).get("kind") or _kind(None, relation), "linked_uid": other_uid,
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    sc = synastry.score(_natal(owner_uid), _their_chart(owner_uid, row["birth_ut"]),
                        time_known_a=mine.get("birth_time_known", False), time_known_b=known)
    row.update({k: sc[k] for k in ("overall", "band", "dimensions", "drivers", "frictions",
                                    "overlays", "elements", "contacts", "confidence")})
    _apply_report(row, compat.report_for(owner_uid, row, recompute=True))
    return row


def _link(owner_uid, other_uid, mood_shared=None, replaces=None):
    """Add other to owner's People (once) and remember the link both ways.
    `mood_shared` is OTHER's say on whether owner sees their 1-10, written on
    the row it governs; None leaves other's master switch to decide. `replaces`
    is the id of the row owner saved for them by hand, if it is really them."""
    rows = _connections(owner_uid)
    if any(r.get("linked_uid") == other_uid for r in rows):
        return next(r for r in rows if r.get("linked_uid") == other_uid)
    old = next((r for r in rows if r["id"] == replaces), None) if replaces else None
    row = _linked_row(owner_uid, other_uid, like=old)
    if old:
        row["was"] = old        # hers; it comes back if they ever unlink
    if mood_shared is not None:
        row["shared_mood"] = bool(mood_shared)
    rows = [r for r in rows if r is not old] + [row]
    save(owner_uid, "connections", rows)
    M.learn_people(owner_uid, [row["name"]])
    links = load(owner_uid, "links", []) or []
    if other_uid not in links:
        save(owner_uid, "links", links + [other_uid])
    return row


@app.post("/api/invites/{token}/accept")
def accept_invite(token: str, a: Accept, uid: str = Depends(consented)):
    inv = _invite(token)
    if not a.share_chart:
        raise HTTPException(400, "To link up, you need to share your chart with each other. "
                                 "You can say no - nothing is shared until you agree.")
    if inv["from"] == uid:
        raise HTTPException(400, "That's your own invite. Send it to someone else.")
    if not _profile(uid):
        raise HTTPException(400, "Finish your own chart first.")
    if not _profile(inv["from"]):
        raise HTTPException(404, "That invite is no longer active.")
    already = any(r.get("linked_uid") == inv["from"] for r in _connections(uid))
    used_up = HTTPException(404, f"That invite has already been used. "
                                 f"Ask {inv['name']} for a new link.")
    # Already through this link but no longer linked: they were removed, and an
    # old link must not let them back in to her birth data.
    if uid in inv["used_by"] and not already:
        raise used_up
    if not already:
        if not _invite_open(inv):
            raise used_up
        # One-time even when two people accept at once: both loaded the record
        # with room in it, and only one gets this counter.
        if store.incr("_invites", f"claims-{token}", "claims", limit=INVITE_MAX_USES,
                      ttl=int(inv["expires"])) is None:
            raise used_up
        inv["used_by"].append(uid)
        if inv.get("pair"):
            funnel.hit(uid, "card_joined")
        inv.setdefault("accepted", []).append({
            "uid": uid, "name": ((_profile(uid) or {}).get("name") or "Someone").split()[0][:30],
            "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
        save("_invites", _invite_key(token), inv, ttl=int(inv["expires"]))
        save(uid, "joined_invites", (load(uid, "joined_invites", []) or []) + [token])
    # Her yes or no on mood is for this one person, stored on the row it
    # governs. The global switch is hers to change in Settings and is never
    # written here.
    # A sent card was about a row she saved. It becomes the linked row only if
    # the birth date they gave matches it; a link can be forwarded, and whoever
    # opened it must not overwrite the person she actually saved.
    replaces, pair = None, inv.get("pair")
    if pair:
        saved = next((r for r in _connections(inv["from"]) if r["id"] == pair["cid"]), None)
        date = (_profile(uid) or {}).get("birth_date")     # a row with no date matches nobody
        if saved and not saved.get("linked_uid") and date and saved.get("birth_date") == date:
            replaces = pair["cid"]
    theirs = _link(inv["from"], uid, mood_shared=a.share_mood,
                   replaces=replaces)                             # me, in the inviter's People
    # The inviter hasn't said yes for this person yet; she can on their card.
    mine = _link(uid, inv["from"], mood_shared=False)             # the inviter, in mine
    master = (_profile(uid) or {}).get("share_mood", True) is not False
    log.emit("invite.accepted", uid=uid, inviter=inv["from"], share_mood=a.share_mood)
    funnel.hit(uid, "invite_accepted")
    return {"ok": True, "person": {"id": mine["id"], "name": mine["name"]},
            "they_see_you_as": theirs["name"], "already": already,
            "mood_shared": bool(theirs.get("shared_mood")) and master,
            "mood_master_off": bool(a.share_mood) and not master}


class ShareMood(BaseModel):
    on: bool


@app.post("/api/profile/share-mood")
def set_share_mood(b: ShareMood, uid: str = Depends(consented)):
    """The master switch. Off: nobody linked sees her number, whatever she
    said for each of them. On: each person's own yes or no applies."""
    prof = _profile(uid)
    if not prof:
        raise HTTPException(400, "Finish your own chart first.")
    prof["share_mood"] = bool(b.on)
    save(uid, "profile", prof)
    return {"share_mood": prof["share_mood"]}


def _row_about(owner_uid, other_uid):
    """owner's People row for other, or None."""
    return next((r for r in _connections(owner_uid) if r.get("linked_uid") == other_uid), None)


def _i_share_mood(uid, row):
    """Whether the linked person on this row sees her 1-10: her yes for them
    (a link from before per-person sharing follows the master switch), and the
    master switch itself."""
    back = _row_about(row.get("linked_uid"), uid) if row.get("linked_uid") else None
    master = (_profile(uid) or {}).get("share_mood", True) is not False
    return {"on": bool(back) and back.get("shared_mood", True) is not False, "master": master}


@app.post("/api/connections/{cid}/share-mood")
def share_mood_with(cid: str, b: ShareMood, uid: str = Depends(consented)):
    """Let this one linked person see her 1-10, or stop."""
    row = _row(uid, cid)
    other = row.get("linked_uid")
    if not other:
        raise HTTPException(400, "Only someone linked with you can see how you're feeling.")
    rows = _connections(other)
    back = next((r for r in rows if r.get("linked_uid") == uid), None)
    if not back:
        raise HTTPException(404, "You're no longer linked.")
    back["shared_mood"] = bool(b.on)
    save(other, "connections", rows)
    log.emit("share_mood.person", uid=uid, on=bool(b.on))
    return _i_share_mood(uid, row)


def _their_day(row):
    """What a linked person chose to share about today: the 1-10 only.
    None for people she entered herself; {'private': True} when they keep it,
    from everyone (their master switch) or from her (their say on this row)."""
    other = row.get("linked_uid")
    if not other:
        return None
    prof = _profile(other)
    if not prof:
        return None                      # they deleted their account
    if prof.get("share_mood") is False or row.get("shared_mood") is False:
        return {"private": True}
    rec = load(other, f"checkin-{_today(other).isoformat()}", None) or {}
    mood = rec.get("mood")
    return {"mood": mood, "time": rec.get("time")} if isinstance(mood, int) else {"mood": None}


def _forget_row(owner_uid, row, rows_left, keep_notes=False):
    """Everything owner held that was derived from one People row: the deep
    report (built from that person's chart), her check-ins about them, the
    circle-pair and sky-event caches that folded their chart in, and the name
    in the chat's context unless another saved person has it."""
    cid = row["id"]
    store.delete(owner_uid, f"compat-{cid}")
    if not keep_notes:
        store.delete(owner_uid, f"pulse-{cid}")
    for c in load(owner_uid, "circles", []) or []:
        if cid in (c.get("members") or []):
            store.delete(owner_uid, f"circlepairs-{c['id']}")
    for k in store.keys(owner_uid):
        if k.startswith("skyev-"):
            store.delete(owner_uid, k)
    gone = (row.get("name") or "").lower()
    if gone and not any((r.get("name") or "").lower() == gone for r in rows_left):
        prof = _profile(owner_uid) or {}
        people = [p for p in prof.get("people", []) if p.get("name", "").lower() != gone]
        if prof and len(people) != len(prof.get("people", [])):
            prof["people"] = people
            save(owner_uid, "profile", prof)


def _drop_link(owner_uid, other_uid):
    """Take other out of owner's People: the row, everything derived from it,
    and the link."""
    rows = _connections(owner_uid)
    # A row they took over goes back to the one she saved by hand, with her
    # check-ins: they can take themselves out of her People, not her own notes.
    gone = [r for r in rows if r.get("linked_uid") == other_uid]
    keep = [r for r in rows if r.get("linked_uid") != other_uid] + [r["was"] for r in gone if r.get("was")]
    if gone:
        save(owner_uid, "connections", keep)
        for r in gone:
            _forget_row(owner_uid, r, keep, keep_notes=bool(r.get("was")))
    links = load(owner_uid, "links", []) or []
    if other_uid in links:
        save(owner_uid, "links", [x for x in links if x != other_uid] or None)


def _unlink_everywhere(uid):
    """On account deletion: take her out of everyone she linked with, and
    everything they held that was built from her chart (audit M4)."""
    for other in load(uid, "links", []) or []:
        _drop_link(other, uid)


def _day_ctx(uid):
    """
    What every row of the people list shares: her profile, her zone, her today.

    Looked up once per request, not once per person. Per row it used to cost
    six reads (profile three times, account twice, the report once) — 600
    sequential reads at the hundred-person cap, seconds on Lambda, for a tab
    that has to feel instant to be worth opening every day.
    """
    tz = _tz(uid)
    return {"prof": _profile(uid), "tz": tz, "day": dt.datetime.now(tz).date()}


def _brief(uid, row, ctx):
    """One person's day, or None. Never fatal: a list that fails to render
    because one old row lacks data is worse than a missing chip."""
    try:
        return daily.brief_for_pair(ctx["prof"] or {}, row, compat.report_for(uid, row),
                                    ctx["day"], ctx["tz"])
    except Exception as e:
        log.error("daily.brief_failed", exc=e, uid=uid)
        return None


def _today_brief(uid, row, ctx=None):
    """Today's number for the people list."""
    d = _brief(uid, row, ctx or _day_ctx(uid))
    return d and {"score": d["score"], "delta": d["delta"], "mood": d["mood"]}


@app.get("/api/connections/{cid}/today")
def connection_today(cid: str, uid: str = Depends(consented)):
    """
    How the two of them are doing today: the usual score moved by what the sky
    is touching, the reasons, a fortnight back and three days ahead, and her
    own check-ins laid over it — the part only she can supply.
    """
    row = _row(uid, cid)
    d = _daily(uid, row)
    pulse = load(uid, f"pulse-{cid}", {}) or {}
    for s in d["series"]:
        if s["date"] in pulse:
            s["feel"] = pulse[s["date"]]["feel"]
    d["checkin"] = pulse.get(d["date"])
    d["checkins"] = len(pulse)
    return {"person": {k: row.get(k) for k in ("id", "name", "relation", "kind")}
            | {"linked": bool(row.get("linked_uid")), "their_day": _their_day(row)}
            | ({"i_share_mood": _i_share_mood(uid, row)} if row.get("linked_uid") else {}), **d}


class Checkin(BaseModel):
    feel: str = Field(max_length=12)
    note: str = Field(default="", max_length=280)


@app.post("/api/connections/{cid}/checkin")
def connection_checkin(cid: str, b: Checkin, uid: str = Depends(consented)):
    """How it actually went today, in her words. One per day; a second one
    replaces the first, because feelings about the same day change."""
    row = _row(uid, cid)
    if b.feel not in daily.FEELS:
        raise HTTPException(400, f"feel must be one of {', '.join(daily.FEELS)}")
    key = f"pulse-{cid}"
    pulse = daily.record_checkin(load(uid, key, {}), _today(uid), b.feel, b.note)
    save(uid, key, pulse)
    # The feeling, never the note: notes are her words about another person.
    log.emit("connection.checkin", uid=uid, feel=b.feel, has_note=bool(b.note.strip()))
    return connection_today(row["id"], uid)


@app.get("/api/memory")
def memory(uid: str = Depends(consented)):
    prof = _profile(uid) or {}
    eps = load(uid, "episodes", [])
    return {
        "people": prof.get("people", []),
        "episodes": [{"date": e["date"], "mood": e.get("mood"), "summary": e.get("summary"),
                      "her_words": e.get("her_words", []), "topics": e.get("topics", []),
                      "heuristic": e.get("extraction") == "heuristic"}
                     for e in reversed(eps)],
        "patterns": load(uid, "patterns", []),
        "connections": sorted(
            [{k: c.get(k) for k in ("id", "name", "relation", "overall", "band", "checked_at")}
             for c in load(uid, "connections", [])],
            key=lambda c: -c["overall"]),
        "days": len(eps),
        "days_to_patterns": max(0, 90 - len(eps)),
        "themes": M.recurring_themes(uid),
        # team2/memory: honest progress toward the first pattern — a count of
        # candidates with partial support, never what they are.
        "progress": unlocks.progress(uid),
    }


# ============================================================ team2/habit
#
# Two habit features, kept in one block so they merge cleanly.
#
# 1. The morning push names a person. Co-Star's pushes are about "you" in
#    general and lately carry upsells; ours says who today is easier or touchier
#    with, and the transit that does it, computed by engine/daily.py. It is never
#    gated and never sells — plan.py's rule — and she can turn the naming off.
#    GET /api/widget is the same morning in a few hundred bytes, for a home
#    screen widget that is free forever (Co-Star paywalled theirs in 2025).
#
# 2. "Ask your mom." Co-Star treats an exact birth time as a prerequisite and
#    says nothing about what a missing one costs. Here she sends a single-use,
#    expiring link to whoever was in the room; they answer without an account,
#    and the time goes through the same _apply_birth_time as her own Settings.

PUSH_MAX = 140          # one lock-screen line; longer gets cut mid-word on iOS


def _push_people_on(prof):
    """"Mention my people in the morning notification". On unless she said no."""
    return (prof or {}).get("push_people") is not False


def _mechanism(reason):
    """A day_score reason's mechanism sentence, mid-sentence-cased.
    "The Moon squares Maya's Venus" -> "the Moon squares Maya's Venus";
    "Today's Mars sets off ..." -> "Mars sets off ..." (the push already says today)."""
    t = reason["text"]
    if t.startswith("Today's "):
        return t[len("Today's "):]
    return t[0].lower() + t[1:] if t.startswith("The ") else t


def _person_lines(b):
    """Candidate sentences for one moved person, longest (most informative)
    first, so the composer can drop detail rather than truncate mid-word."""
    name = b["name"]
    lead, tail = (("Easier", "") if b["delta"] > 0 else ("Touchier", " Go gently."))
    r = b.get("reason")
    out = []
    if r:
        out.append(f"{lead} with {name} today: {_mechanism(r)}, {r['lasts']}.{tail}")
        out.append(f"{lead} with {name} today: {_mechanism(r)}.{tail}")
    out.append(f"{lead} with {name} today.{tail}")
    return out


def _people_briefs(uid, day=None):
    """Every person's day, computed the way /api/people/today computes it, but
    for an explicit `day` so the cron can ask about *her* morning rather than
    the server's. Mood and check-in text are deliberately not carried: the push
    and the widget never repeat anything a person wrote or chose to keep."""
    ctx = _day_ctx(uid)
    if day is not None:
        ctx = {**ctx, "day": day}
    if not ctx["prof"]:
        return [], ctx
    out = []
    for r in _connections(uid):
        d = _brief(uid, r, ctx)
        if d is None:
            continue
        top = (d["reasons"] or [None])[0]
        out.append({"id": r["id"], "name": (r.get("name") or "them").split()[0][:24],
                    "score": d["score"], "delta": d["delta"], "usual": d["usual"],
                    "reason": top and {"text": top["text"], "lasts": top["lasts"],
                                       "transit": top["transit"], "aspect": top["aspect"]}})
    return out, ctx


def morning_people_line(uid, day=None):
    """
    (sentence, cid) naming who today moved, or None.

    The person is the head of daily.rank_people — the most moved, a fixed order
    for a fixed day — and is named only when |delta| >= daily.STEADY: a push
    that says "about usual with Maya" is the "nothing is happening" push the
    quiet-day rule exists to avoid. When someone else moved the other way, a
    second short clause is added if it still fits the line.
    """
    prof = _profile(uid)
    if not _push_people_on(prof):
        return None
    briefs, _ = _people_briefs(uid, day)
    if not briefs:
        return None
    ranked = daily.rank_people(briefs)["ranked"]
    head = ranked[0]
    if abs(head["delta"]) < daily.STEADY:
        return None
    first = next((x for x in _person_lines(head) if len(x) <= PUSH_MAX), None)
    if not first:
        return None
    other = next((b for b in ranked[1:] if abs(b["delta"]) >= daily.STEADY
                  and (b["delta"] > 0) != (head["delta"] > 0)), None)
    if other:
        extra = _person_lines({**other, "reason": None})[0]
        if len(first) + 1 + len(extra) <= PUSH_MAX:
            first = f"{first} {extra}"
    return first, head["id"]


# --------------------------------------------------------------- widget

WIDGET_TTL_S = 15 * 60          # the sky moves slowly; people change when she adds one


def _widget_payload(uid):
    d = _today(uid)
    sky = _sky(uid, d)
    checkin = load(uid, f"checkin-{d}", None) or {"text": "", "mood": None}
    _, verdict = context.build(uid, _natal(uid), sky, checkin, today=d, surface="push")
    quiet = verdict["band"] == "low"
    line = lines.for_user(verdict["headlines"], d.isoformat(), uid, quiet=quiet,
                      moon_sign=sky.get("moon_sign"))
    briefs, _ = _people_briefs(uid, d)
    top = easiest = None
    if briefs:
        head = daily.rank_people(briefs)["ranked"][0]
        best = max(briefs, key=lambda b: (b["score"], b["delta"], b["name"].lower()))

        def card(b):
            r = b["reason"] or {}
            return {"cid": b["id"], "name": b["name"], "score": b["score"],
                    "delta": b["delta"], "reason": r.get("text"), "lasts": r.get("lasts"),
                    "url": f"/app?p={b['id']}"}
        top, easiest = card(head), card(best)
    return {"date": d.isoformat(), "my_score": verdict["score"], "band": verdict["band"],
            "line": line, "moon_phase": sky["moon"]["phase"],
            "moon_illumination_pct": sky["moon"].get("illumination_pct"),
            "moon_sign": sky.get("moon_sign"), "top": top, "easiest": easiest,
            "url": "/app"}


@app.get("/api/widget")
def widget(request: Request, uid: str = Depends(consented)):
    """
    Her morning in a few hundred bytes, for a home-screen widget or a PWA
    shortcut. Free forever: no plan check here, on purpose. Cached per user for
    WIDGET_TTL_S and per local day, with an ETag so a widget refreshing on a
    timer costs a 304. See docs/widget.md.
    """
    if not _profile(uid):
        raise HTTPException(400, "Finish your own chart first.")
    today_iso = _today(uid).isoformat()
    cached = load(uid, "widget", None)
    if not (cached and cached.get("date") == today_iso):
        cached = _widget_payload(uid)
        save(uid, "widget", cached, ttl=time.time() + WIDGET_TTL_S)
    body = json.dumps(cached, separators=(",", ":"), default=str)
    etag = '"' + hashlib.sha1(body.encode()).hexdigest()[:20] + '"'
    headers = {"ETag": etag, "Cache-Control": "private, max-age=300",
               "Vary": "Cookie"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


class PushPeople(BaseModel):
    on: bool


@app.get("/api/profile/push-people")
def get_push_people(uid: str = Depends(consented)):
    return {"push_people": _push_people_on(_profile(uid))}


@app.post("/api/profile/push-people")
def set_push_people(b: PushPeople, uid: str = Depends(consented)):
    prof = _profile(uid)
    if not prof:
        raise HTTPException(400, "Finish your own chart first.")
    prof["push_people"] = bool(b.on)
    save(uid, "profile", prof)
    return {"push_people": prof["push_people"]}


# ------------------------------------------------------- ask your mom
#
# A request is a global record keyed by an unguessable token. The public page
# shows the requester's first name and birth date — what the parent needs to be
# sure which child is asking — and nothing else: no chart, no email, no city.

BT_DAYS = 14
BT_APPROX = ("morning", "afternoon", "evening", "night")
BT_SURE = ("certificate", "sure", "roughly")


def _bt_key(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,40}", token or ""):
        raise HTTPException(404, "That link isn't valid.")
    return f"bt-{token}"


def _bt_state(token):
    """(record, status). status is 'ok', 'gone' (used, revoked, expired, or the
    time is already known) or 'missing'. Never raises for a well-formed token."""
    rec = load("_bt_requests", _bt_key(token), None)
    if not rec:
        return None, "missing"
    if rec.get("used") or rec.get("revoked") or rec.get("expires", 0) < time.time():
        return rec, "gone"
    prof = _profile(rec["from"])
    if not prof or prof.get("birth_time_known"):
        return rec, "gone"
    return rec, "ok"


def _bt_revoke_all(uid):
    for t in load(uid, "bt-requests", []) or []:
        rec = load("_bt_requests", f"bt-{t}", None)
        if rec and not rec.get("used"):
            save("_bt_requests", f"bt-{t}", {**rec, "revoked": True})


def _bt_forget(uid):
    """Account deletion: her tokens go entirely, not just revoked."""
    for t in load(uid, "bt-requests", []) or []:
        save("_bt_requests", f"bt-{t}", None)
    save(uid, "bt-requests", None)


def _bt_mine(uid):
    """Her one live link, if any."""
    for t in reversed(load(uid, "bt-requests", []) or []):
        rec, st = _bt_state(t)
        if st == "ok":
            return t, rec
    return None, None


@app.post("/api/birthtime/request")
def create_bt_request(request: Request, uid: str = Depends(consented)):
    """One live link at a time: a new one revokes the old, so a link sent to the
    wrong person can be killed by simply making another."""
    prof = _profile(uid)
    if not prof:
        raise HTTPException(400, "Finish your own chart first.")
    if prof.get("birth_time_known"):
        raise HTTPException(400, "You already have an exact birth time.")
    _bt_revoke_all(uid)
    token = secrets.token_urlsafe(16)
    first = (prof.get("name") or "Your kid").split()[0][:30]
    date_part = prof.get("birth_date") or prof["birth_local"].split(" ")[0]
    now = time.time()
    save("_bt_requests", f"bt-{token}", {
        "from": uid, "name": first, "birth_date": date_part, "created": now,
        "expires": now + BT_DAYS * 86400, "used": False, "revoked": False},
        ttl=now + (BT_DAYS + 1) * 86400)
    mine = [t for t in (load(uid, "bt-requests", []) or [])
            if load("_bt_requests", f"bt-{t}", None)][-9:]
    save(uid, "bt-requests", mine + [token])
    log.emit("bt_request.created", uid=uid)
    base = public_url(request)          # never the Host header (audit M2)
    url = f"{base}/bt/{token}"
    return {"token": token, "url": url, "expires_in_days": BT_DAYS,
            "text": (f"Hi! Random question - what time was I born? Even a rough "
                     f"guess helps. You can tap here to tell me, no app needed: {url}")}


@app.get("/api/birthtime/request")
def get_bt_request(uid: str = Depends(consented)):
    """Her live link (so the UI can offer to resend or cancel it) and, once,
    the news that it was answered."""
    token, rec = _bt_mine(uid)
    notice = load(uid, "bt-filled", None)
    if notice:
        save(uid, "bt-filled", None)          # a toast, shown once
    prof = _profile(uid) or {}
    return {"pending": token and {"token": token,
                                  "expires": dt.datetime.fromtimestamp(
                                      rec["expires"], dt.timezone.utc).date().isoformat()},
            "filled": notice, "approx": prof.get("birth_time_approx"),
            "time_known": bool(prof.get("birth_time_known"))}


@app.delete("/api/birthtime/request/{token}")
def revoke_bt_request(token: str, uid: str = Depends(consented)):
    rec = load("_bt_requests", _bt_key(token), None)
    if not rec or rec.get("from") != uid:
        raise HTTPException(404, "No such link.")
    save("_bt_requests", f"bt-{token}", {**rec, "revoked": True})
    return {"revoked": True}


class BtAnswer(BaseModel):
    time: Optional[str] = Field(default=None, max_length=8)    # "HH:MM", exact
    approx: Optional[str] = Field(default=None, max_length=12)  # BT_APPROX
    sure: str = Field(default="sure", max_length=12)           # BT_SURE


BT_WINDOW_MIN = 60      # "Within an hour" means +/- this many minutes


def _bt_parse(time_str):
    """(hh, mm) or a 400, checked before the link is claimed so a typo doesn't
    spend it."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (time_str or "").strip())
    if not m or not (0 <= int(m[1]) < 24 and 0 <= int(m[2]) < 60):
        raise HTTPException(400, "Time should look like 04:42.")
    return int(m[1]), int(m[2])


def _stable_rising(prof, hh, mm, minutes=BT_WINDOW_MIN):
    """The rising sign if it is the same at every instant from `minutes` before
    to `minutes` after this local time, else None.

    The ascendant only moves forward, so two ephemeris points are enough: same
    sign at both ends AND less than a sign's worth (30 degrees) of travel between
    them means it never left that sign. Near a cusp, or at a latitude where a
    sign rises in forty minutes, this says None — and None means we don't say."""
    date_part = prof.get("birth_date") or prof["birth_local"].split(" ")[0]
    y, m, d = (int(x) for x in date_part.split("-"))
    lat, lon = prof["birth_ut"][4], prof["birth_ut"][5]
    utc, _ = _birth_instant(y, m, d, hh, mm, prof.get("tz") or "UTC", lat, lon, False)
    base = [utc.year, utc.month, utc.day, utc.hour + utc.minute / 60, lat, lon]
    lo, hi = (natal(*compat._shift(base, s * minutes / 60))["angles"]["Ascendant"]
              for s in (-1, 1))
    travel = (hi["lon"] - lo["lon"]) % 360
    return lo["sign"] if lo["sign"] == hi["sign"] and travel < 30 else None


def _bt_claim(token, rec):
    """Single use, atomically. Reading `used` and then writing it lets two
    submissions that arrive together both see an unused link; a counter with a
    limit is one conditional write (a DynamoDB ADD under a condition, a locked
    file locally), so exactly one of them gets 1 and the other gets None."""
    return store.incr("_bt_requests", f"bt-{token}", "uses", limit=1,
                      ttl=rec.get("expires", time.time()) + 86400) is not None


def _bt_release(token):
    """Undo a claim whose write then failed, so the parent can try again."""
    store.incr("_bt_requests", f"bt-{token}", "uses", by=-1)


@app.post("/api/bt/{token}")
def answer_bt_request(token: str, a: BtAnswer):
    """
    Public: the parent has no account. A time they are sure of ("written down",
    "I remember it") goes through _apply_birth_time — the same caches dropped,
    the same rows rescored — and she hears about it with the rising sign the
    ephemeris now gives.

    "Within an hour" is NOT an exact time, and is never written as one: the
    ascendant crosses a whole sign in about two hours, so treating 15:20 +/- 1h
    as 15:20 can hand her the wrong rising sign and every house with it. It is
    kept beside the chart as an approximate time, birth_time_known stays False,
    and the rising sign is mentioned only if it is the same across the whole
    window. "Roughly afternoon" is kept as exactly that and changes no chart
    either: houses from a guessed time would be invented houses.
    """
    rec, st = _bt_state(token)
    if st == "missing":
        raise HTTPException(404, "That link isn't valid.")
    if st == "gone":
        raise HTTPException(410, "This link has already been used or has expired.")
    uid, sure = rec["from"], a.sure if a.sure in BT_SURE else "sure"
    if a.time:
        hh, mm = _bt_parse(a.time)
        kind = "approx_time" if sure == "roughly" else "exact"
    elif a.approx in BT_APPROX:
        kind = "approx"
    else:
        raise HTTPException(400, "Pick a time, or roughly when.")

    if not _bt_claim(token, rec):          # the other tap won
        raise HTTPException(410, "This link has already been used or has expired.")
    try:
        if kind == "exact":
            time_str = f"{hh:02d}:{mm:02d}"
            _apply_birth_time(uid, time_str)
            prof = _profile(uid)
            prof["birth_time_source"] = {"by": "link", "sure": sure}
            save(uid, "profile", prof)
            rising = _natal(uid)["angles"]["Ascendant"]["sign"]
            notice = {"kind": "exact", "rising": rising,
                      "text": f"Your rising sign is {rising}. Your houses (the life areas in your chart) are now updated."}
        elif kind == "approx_time":
            prof = _profile(uid)
            time_str = f"{hh:02d}:{mm:02d}"
            rising = _stable_rising(prof, hh, mm)
            prof["birth_time_approx"] = {"time": time_str, "window_min": BT_WINDOW_MIN,
                                         "sure": sure, "rising": rising}
            save(uid, "profile", prof)      # birth_time_known stays False on purpose
            notice = {"kind": "approx_time", "time": time_str, "rising": rising,
                      "text": (f"The answer is in: you were born at about {time_str}, give or take "
                               "an hour. " + (
                                   f"Your rising sign is {rising} either way - it stays the same across "
                                   "that whole hour. " if rising else
                                   "Your rising sign changes during that hour, so I'm leaving it "
                                   "hidden rather than guessing. ")
                               + "Houses need the exact minute, which a birth certificate will show.")}
        else:
            prof = _profile(uid)
            prof["birth_time_approx"] = {"part": a.approx, "sure": sure}
            save(uid, "profile", prof)      # birth_time_known stays False on purpose
            notice = {"kind": "approx", "part": a.approx,
                      "text": (f"The answer is in: you were born sometime in the {a.approx}. Houses stay "
                               "hidden until there's an exact time, which a birth certificate will show.")}
        save("_bt_requests", f"bt-{token}", {**rec, "used": True, "kind": kind})
    except Exception:
        _bt_release(token)
        raise
    if not notice.get("rising"):
        notice.pop("rising", None)
    save(uid, "bt-filled", notice)
    save(uid, "widget", None)
    log.emit("bt_request.answered", uid=uid, kind=notice["kind"])
    try:
        push.send(uid, "My Sign", notice["text"], url="/app", kind="notice")
    except Exception as e:                  # the answer stands without the ping
        log.error("bt_request.push_failed", exc=e, uid=uid)
    out = {"ok": True, "name": rec["name"]}
    if kind == "approx_time":
        # The parent page says what "within an hour" bought, without the chart:
        # whether her rising sign could be shown, never which one.
        out["rising_shown"] = bool(rising)
    return out


_BT_PAGE = (STATIC / "bt.html")


@app.get("/bt/{token}")
def bt_page(token: str):
    """The parent's page. Server-rendered with only the first name and birth
    date filled in, so nothing else about her is even in the HTML. A bad or
    spent link gets the same warm page with a sentence instead of a form."""
    try:
        rec, st = _bt_state(token)
    except HTTPException:
        rec, st = None, "missing"
    tpl = _BT_PAGE.read_text()
    if st == "ok":
        when = dt.date.fromisoformat(rec["birth_date"]).strftime("%B %-d, %Y")
        fill = {"{{NAME}}": html.escape(rec["name"]), "{{DATE}}": html.escape(when),
                "{{TOKEN}}": html.escape(token), "{{STATE}}": "open"}
        code = 200
    else:
        fill = {"{{NAME}}": "", "{{DATE}}": "", "{{TOKEN}}": "",
                "{{STATE}}": "gone" if st == "gone" else "missing"}
        code = 410 if st == "gone" else 404
    for k, v in fill.items():
        tpl = tpl.replace(k, v)
    return HTMLResponse(tpl, status_code=code,
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                 "X-Robots-Tag": "noindex"})
# ======================================================== end team2/habit
# ================================================================ team2/people
#
# Two People features, kept together so they merge as one block.
#
# 1. "Best day to..." — the next seven days with one person, ranked for what
#    she wants to do (engine/planner.py). Every number and reason is
#    daily.day_score's; the intent only re-weighs them, by weights in code.
#    It's Plus, bundled: no per-report fee. While PRICES["available"] is False
#    nobody is gated; once billing exists the free plan sees the best day and
#    its real score, and Plus adds the why.
#
# 2. "How's it going between us?" — a mutual reveal for LINKED pairs only
#    (engine/reveal.py). Free, always: it is the loop that brings the other
#    person back. Neither sees the other's answer until both answered; the
#    optional note never leaves its author.

from engine import planner  # noqa: E402
from engine import reveal as RV  # noqa: E402


def _plus_view(uid):
    """Whether this person sees Plus depth. Everyone does until billing is live."""
    return plan.full_view(uid)


def _week_days(uid, row):
    prof = _profile(uid) or {}
    rep = compat.report_for(uid, row)
    return planner.week(prof, row, rep, _today(uid), _tz(uid))


@app.get("/api/connections/{cid}/plan")
def connection_plan(cid: str, intent: str = "talk", uid: str = Depends(consented)):
    """The next seven days with one person, ranked for one intent."""
    if intent not in planner.INTENTS:
        raise HTTPException(400, f"intent must be one of {', '.join(planner.INTENTS)}")
    row = _row(uid, cid)
    p = planner.plan(intent, _week_days(uid, row), row.get("name", "them"))
    out = p if _plus_view(uid) else planner.free_view(p)
    return {"person": {"id": row["id"], "name": row.get("name")},
            "plus_required": not _plus_view(uid), **out}


# ------------------------------------------------------------- mutual reveal

class RevealAnswer(BaseModel):
    feel: str = Field(max_length=12)
    note: str = Field(default="", max_length=140)


def _mutual(uid, cid):
    """(row, other_uid, their row for me) — only for a link both sides still hold."""
    row = _row(uid, cid)
    other = row.get("linked_uid")
    if not other:
        raise HTTPException(400, "This is only for people linked with you. Invite them to link up first.")
    back = next((r for r in _connections(other) if r.get("linked_uid") == uid), None)
    if not back or not _profile(other):
        raise HTTPException(400, "You're no longer linked with this person.")
    return row, other, back


def _reveal_sky(uid, row, revealed_at):
    """What the sky did between them over the week that ended in this reveal,
    from her side: the seven skies daily.series(back=6, ahead=0) reads, with
    their reasons kept, folded by RV.sky_ranges into at most three dated
    stretches ("Tue–Thu: ..."). The window ends on the day the second answer
    landed, in her time zone, so it stays the same however late she looks.
    Looking back only — never a forecast."""
    try:
        prof = _profile(uid) or {}
        pm, pt = compat.my_precision(prof), compat.their_precision(row)
        tz = _tz(uid)
        end = dt.datetime.fromtimestamp(revealed_at, tz).date()
        rep = compat.report_for(uid, row)
        mine = daily._natal(tuple(prof["birth_ut"]), pm)
        theirs = daily._natal(tuple(row["birth_ut"]), pt)
        # daily.series drops the reasons; this is its loop, keeping them.
        days = [daily.day_score(rep, mine, theirs, end - dt.timedelta(days=i), tz,
                                row.get("name", "them"),
                                moon_mine=pm != "unknown", moon_theirs=pt != "unknown")
                for i in range(6, -1, -1)]
    except Exception as e:
        log.error("reveal.sky_failed", exc=e, uid=uid)
        return None
    last = days[-1]
    return {"from": days[0]["date"], "to": last["date"], "date": last["date"],
            "score": last["score"], "delta": last["delta"], "label": planner.label(last["delta"]),
            "reasons": RV.sky_ranges(days)}


def _reveal_out(uid, row, other, rec):
    v = RV.view(rec, uid, other, _today(uid))
    name = row.get("name", "them")
    out = {"person": {"id": row["id"], "name": name}, **v,
           "history": RV.history(rec, uid, other)}
    if v["state"] == "revealed":
        out["together"] = RV.agree_line(v["mine"]["feel"], v["theirs"]["feel"], name)
        out["sky"] = _reveal_sky(uid, row, v["revealed_at"])
    return out


@app.get("/api/connections/{cid}/reveal")
def get_reveal(cid: str, uid: str = Depends(consented)):
    row, other, _ = _mutual(uid, cid)
    return _reveal_out(uid, row, other, load(RV.NS, RV.key(uid, other), None) or {})


@app.post("/api/connections/{cid}/reveal")
def post_reveal(cid: str, b: RevealAnswer, uid: str = Depends(consented)):
    if b.feel not in RV.FEELS:
        raise HTTPException(400, f"feel must be one of {', '.join(RV.FEELS)}")
    row, other, back = _mutual(uid, cid)
    k = RV.key(uid, other)
    try:
        rec, wk, first = RV.answer(load(RV.NS, k, None), uid, _today(uid), b.feel, b.note)
    except PermissionError as e:
        raise HTTPException(409, str(e)) from None
    # Tell the other person at most once a week per pair, and only that an
    # answer exists. The text names her and nothing she chose; push.send is a
    # no-op for anyone who never turned notifications on (that is the opt-out).
    if first and RV.should_nudge(rec, wk):
        rec["nudged_week"] = wk
        me_name = ((_profile(uid) or {}).get("name") or "Someone").split()[0][:30]
        try:
            push.send(other, "My Sign", f"{me_name} answered “How’s it going between us?” Now it’s your turn.",
                      url=f"/app?p={back['id']}", kind="reveal")
        except Exception as e:
            log.error("reveal.push_failed", exc=e, uid=uid)
    save(RV.NS, k, rec)
    log.emit("reveal.answered", uid=uid, feel=b.feel, first=first)
    return _reveal_out(uid, row, other, rec)


@app.get("/api/reveals/waiting")
def reveals_waiting(uid: str = Depends(consented)):
    """Linked people who answered and are waiting on her — for the Home nudge.
    Names only: nothing about what they chose."""
    today = _today(uid)
    out = []
    for r in _connections(uid):
        other = r.get("linked_uid")
        if not other:
            continue
        rec = load(RV.NS, RV.key(uid, other), None)
        if rec and RV.view(rec, uid, other, today)["they_answered"]:
            out.append({"id": r["id"], "name": r.get("name")})
    return {"waiting": out}


def _reveal_forget(uid, other_uid):
    """Unlinking or deleting an account takes the pair's reveals with it, both
    pending and past: they were a thing the two of them shared."""
    if other_uid:
        store.delete(RV.NS, RV.key(uid, other_uid))
# ============================================================ /team2/people
# ------------------------------------------------- team2/memory: unlocks + people
#
# Pattern unlock moments and relationship-aware memory. Every number on these
# routes comes from memory.verify() or engine/daily; every quote is her own,
# from her own store. See engine/unlocks.py and engine/personmem.py.

def _person_context(uid, text, d):
    """(facts, their_day) for the first saved person named in `text`, or
    (None, None). Never fatal: chat without the garnish beats no chat."""
    try:
        rows = personmem.mentioned(uid, text)
        if not rows:
            return None, None
        row = rows[0]
        # The get_person_day tool's own code, so the pre-run lookup and one the
        # model makes itself cannot disagree.
        facts = tools.person_day(uid, row, d)
        their = _their_day(row)
        # The number they chose to share, never their words — _their_day only
        # ever returns the 1-10.
        if their and isinstance(their.get("mood"), int):
            facts["their_shared_mood_today"] = their["mood"]
        return facts, their
    except Exception as e:
        log.error("personmem.failed", exc=e, uid=uid)
        return None, None


@app.get("/api/memory/unlocks")
def memory_unlocks(uid: str = Depends(consented)):
    """Verified patterns she has not seen yet, each re-verified on read."""
    return {"unlocks": unlocks.pending(uid)}


@app.post("/api/memory/unlocks/{pid}/seen")
def memory_unlock_seen(pid: str, uid: str = Depends(consented)):
    if not re.fullmatch(r"[0-9a-f]{12}", pid) or not unlocks.mark_seen(uid, pid):
        raise HTTPException(404, "No pattern like that holds right now.")
    return {"ok": True}


@app.get("/api/memory/today")
def memory_today(uid: str = Depends(consented)):
    """The heads-up line for Today, when today's sky matches a verified pattern."""
    if not _profile(uid):
        return {"heads_up": []}
    d = _today(uid)
    return {"heads_up": unlocks.heads_up(uid, _sky_state(_sky(uid, d)), d.isoformat())}


@app.get("/api/card/pattern/{pid}.svg")
def card_pattern(pid: str, uid: str = Depends(consented)):
    c = unlocks.find(uid, pid) if re.fullmatch(r"[0-9a-f]{12}", pid) else None
    if not c:
        raise HTTPException(404, "That pattern doesn't hold right now.")
    return Response(cards.pattern_card(c), media_type="image/svg+xml",
                    headers={"Content-Disposition": 'inline; filename="my-sign-pattern.svg"'})


@app.get("/api/memory/people")
def memory_people(uid: str = Depends(consented)):
    """What she has said about each saved person, newest first, deletable."""
    return {"people": personmem.by_person(uid)}


@app.delete("/api/memory/people/{cid}/notes")
def memory_people_forget(cid: str, key: str, uid: str = Depends(consented)):
    if not personmem.forget(uid, cid, key):
        raise HTTPException(404, "Nothing like that is remembered.")
    return {"ok": True}
# ------------------------------------------------------------ team2/sky: events
#
# The big sky days — retrograde stations, eclipses, new and full moons, the
# Sun's sign changes — computed by engine/skyevents.py and read against her
# chart and her people's. Everything here is a cache read on the day itself:
# the sky part is lru-cached per process, the personal part is stored per user
# per day, so the morning half the internet opens the app to ask about Mercury
# is the morning this does the least work.

SKY_EVENT_DAYS = 60
SKY_EVENT_TTL_S = 2 * 86400     # a day's cache outlives her midnight in any zone, then goes
SKY_PUSH_FLAG = "MYSIGN_SKY_EVENT_PUSH"     # the event push ships dark until this is "1"


def _sky_people(uid):
    """Her people's charts as natal points. Only what she typed or what a
    linked friend consented to share (their birth data, already on the row) —
    never a check-in, never a mood."""
    from engine import skyevents
    out = []
    for r in _connections(uid):
        try:
            ch = natal(*r["birth_ut"])
        except Exception:
            continue
        out.append({"cid": r["id"], "name": r.get("name") or "them",
                    "points": skyevents.natal_points(ch, bool(r.get("birth_time_known")))})
    return out


def _sky_events_for(uid, days=SKY_EVENT_DAYS, now=None):
    """The whole payload, cached per user per local day. The key carries a hash
    of everything personal it depends on, so a new birth time or a new person
    misses instead of serving yesterday's houses."""
    from engine import skyevents
    prof = _profile(uid)
    if not prof:
        raise HTTPException(400, "Finish your own chart first.")
    tz = _tz(uid)
    now = now or dt.datetime.now(dt.timezone.utc)
    day = now.astimezone(tz).date()
    houses, _ = _tradition(uid)
    rows = _connections(uid)
    # Default True like _natal: a profile from before the flag existed was
    # onboarded with a time, and reading it as unknown would hide her houses.
    known = bool(prof.get("birth_time_known", True))
    sig = skyevents.signature(prof.get("birth_ut"), known, houses, str(tz),
                              [(r.get("id"), r.get("name"), r.get("birth_ut"),
                                r.get("birth_time_known")) for r in rows])
    key = f"skyev-v1-{day}-{days}-{sig}"
    cached = load(uid, key, None)
    if cached:
        return cached

    nat = _natal(uid)
    people = _sky_people(uid)
    # From her local midnight's UT date, a day early, so an event earlier today
    # in her zone is still listed (and marked past by the client clock).
    evs, cycles = skyevents.sky_events((day - dt.timedelta(days=1)).isoformat(), days + 2)
    listed = [skyevents.personalise(e, nat, known, people, tz) for e in evs]
    listed = [e for e in listed if day.isoformat() <= e["local_date"]
              <= (day + dt.timedelta(days=days)).isoformat()]

    def people_for(lon_a, lon_b):
        seen = {}
        for p in people:
            hits = skyevents.touches(lon_a, p["points"]) + skyevents.touches(lon_b, p["points"])
            if hits:
                seen[p["cid"]] = {"cid": p["cid"], "name": p["name"],
                                  "points": sorted(hits, key=lambda h: h["orb"])[:3]}
        return sorted(seen.values(), key=lambda w: (-len(w["points"]), w["points"][0]["orb"]))[:3]

    act = skyevents.active(cycles, now, tz, people_for)
    today_big = [e for e in listed if e["local_date"] == day.isoformat()
                 and e["kind"] != "ingress"]
    banner = None
    retro = next((a for a in act if a["phase"] == "retrograde"), None)
    if retro:
        banner = {"text": retro["banner"], "kind": "retrograde", "body": retro["body"]}
    elif today_big:
        e = today_big[0]
        where = f", in your {skyevents._ord(e['her_house'])} house" if e["her_house"] else ""
        banner = {"text": f"{e['title']} today in {e['sign']}{where}", "kind": e["kind"],
                  "body": e["body"]}
    elif act:
        banner = {"text": act[0]["banner"], "kind": act[0]["phase"], "body": act[0]["body"]}
    tint = None
    if any(e["kind"].endswith("eclipse") for e in today_big):
        tint = "eclipse"
    elif retro:
        tint = "retrograde"

    patterns = []
    try:
        # Through unlocks.heads_up, never the stored dict: it re-verifies each
        # pattern against her episodes as they are now and writes the line from
        # that result. A stored claim is whatever the proposer wrote weeks ago —
        # a model's sentence, or numbers the data no longer supports.
        patterns = [{k: h[k] for k in ("id", "text", "support", "lift", "p_value",
                                        "p_text", "confidence")}
                    for h in unlocks.heads_up(uid, _sky_state(_sky(uid, day)), day.isoformat())]
    except Exception as e:
        log.error("sky.patterns_failed", exc=e, uid=uid)

    out = {"date": day.isoformat(), "tz": str(tz), "days": days,
           "time_known": known,
           "houses_note": None if known else
           "Add your birth time to see which house each event falls in.",
           "events": listed, "active": act, "banner": banner, "tint": tint,
           "next_big": next((e for e in listed if e["big"]
                             and e["utc"] > now.isoformat().replace("+00:00", "Z")), None),
           "patterns_today": patterns,
           "source": "Planet positions from the Swiss Ephemeris, with eclipses checked against NASA"}
    # The key carries the day, so yesterday's entry is never read again; the
    # ttl is what stops a year of them piling up in her store.
    save(uid, key, out, ttl=time.time() + SKY_EVENT_TTL_S)
    return out


@app.get("/api/sky/events")
def sky_events(response: Response, days: int = SKY_EVENT_DAYS, uid: str = Depends(consented)):
    """Upcoming sky events for her, and whatever is under way right now."""
    days = max(1, min(int(days), 400))
    out = _sky_events_for(uid, days)
    # Private: it carries her houses and her people's names. Short, because the
    # "day N of 21" changes at her midnight.
    response.headers["Cache-Control"] = "private, max-age=300"
    return out


class SkyPrefs(BaseModel):
    event_push: bool


@app.get("/api/sky/prefs")
def sky_prefs(uid: str = Depends(consented)):
    prefs = load(uid, "sky-prefs", {}) or {}
    return {"event_push": bool(prefs.get("event_push")),
            "available": os.environ.get(SKY_PUSH_FLAG) == "1"}


@app.post("/api/sky/prefs")
def set_sky_prefs(b: SkyPrefs, uid: str = Depends(consented)):
    """Opt in to one heads-up before each station or eclipse. Off by default and
    separate from the morning push, which it never replaces or adds to."""
    save(uid, "sky-prefs", {"event_push": bool(b.event_push)})
    return sky_prefs(uid)
# ------------------------------------------------------------ end team2/sky
# ------------------------------------------- team2/social: circles + before you date
# The endpoints live in app/social.py (GET/POST /api/circles, DELETE
# /api/circles/{id}, GET /api/circles/{id}/today, GET /api/card/circle/{id}.svg,
# POST /api/check, POST /api/card/check.svg). Imported last, because that
# module borrows the helpers above.
from app import social as _social  # noqa: E402

app.include_router(_social.router)
