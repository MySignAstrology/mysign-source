"""
team2/habit: the named-person morning push, the widget, and "ask your mom".

What these pin is what beats Co-Star on each:
- the push names a person only when the sky really moved them (|delta| >=
  STEADY), says the transit, fits a lock screen, never sells, never repeats
  anyone's mood or words, and goes out once a day;
- the widget is free (no plan gate), small, cached, and answers a timer
  refresh with a 304;
- the birth-time link shows a first name and a birth date and nothing else,
  works once, expires, and writes the time through the exact same code as
  Settings — while "roughly afternoon" changes no chart at all.
"""
import datetime as dt
import re
import time

import pytest
from fastapi.testclient import TestClient

import app.server as server
from app import auth, cron, push
from engine import daily, plan, store
from tests.test_app import last_uid, sign_in

UTC = dt.timezone.utc
BANNED_PUSH = re.compile(r"\$|\bplus\b|upgrade|unlock|subscribe|trial|price", re.I)


def user(email, time_="04:42", name="Maya Rose"):
    c = TestClient(server.app)
    sign_in(c, email)
    r = c.post("/api/onboard", json={"name": name, "date": "1998-03-14", "time": time_,
                                     "city": "Chicago IL"})
    assert r.status_code == 200, r.text
    return c, last_uid()


def add(c, name, date="1996-07-02", time_="21:15", city="London, GB"):
    r = c.post("/api/connections", json={"name": name, "relation": "friend",
                                          "date": date, "time": time_, "city": city})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def fake_brief(moves):
    """_brief replacement: {name: delta}. The reason is a real-shaped day_score
    reason so the composer is exercised, but the numbers are fixed."""
    def _b(uid, row, ctx):
        delta = moves.get(row["name"], 0)
        reason = {"text": "Venus trines the relationship's Moon", "lasts": "lasts a few days",
                  "transit": "Venus", "aspect": "trine", "short": "x", "weight": 1}
        if delta < 0:
            reason = {**reason, "text": "The Moon squares Theo's Venus", "lasts": "passes by tomorrow",
                      "transit": "Moon", "aspect": "square"}
        return {"score": 60 + delta, "usual": 60, "delta": delta, "mood": daily.mood(delta),
                "reasons": [reason], "headline": "h"}
    return _b


# ================================================== named-person morning push

def test_push_names_the_most_moved_person_with_the_mechanism(monkeypatch):
    c, uid = user("hab-name@x.com")
    cid = add(c, "Maya Lin")
    add(c, "Theo")
    monkeypatch.setattr(server, "_brief", fake_brief({"Maya Lin": 9, "Theo": 2}))
    line, who = server.morning_people_line(uid)
    assert who == cid
    assert line == "Easier with Maya today: Venus trines the relationship's Moon, lasts a few days."
    assert len(line) <= server.PUSH_MAX
    assert not BANNED_PUSH.search(line)


def test_push_adds_the_person_moved_the_other_way_if_it_fits(monkeypatch):
    c, uid = user("hab-two@x.com")
    add(c, "Maya")
    add(c, "Mom")
    monkeypatch.setattr(server, "_brief", fake_brief({"Maya": 6, "Mom": -5}))
    line, _ = server.morning_people_line(uid)
    assert line.startswith("Easier with Maya today")
    assert line.endswith("Touchier with Mom today. Go gently.")
    assert len(line) <= server.PUSH_MAX


def test_about_usual_names_nobody(monkeypatch):
    c, uid = user("hab-steady@x.com")
    add(c, "Maya")
    monkeypatch.setattr(server, "_brief", fake_brief({"Maya": daily.STEADY - 1}))
    assert server.morning_people_line(uid) is None


def test_long_mechanism_drops_detail_rather_than_cutting_a_word(monkeypatch):
    c, uid = user("hab-long@x.com")
    add(c, "Maximiliana")

    def _b(uid_, row, ctx):
        return {"score": 70, "usual": 60, "delta": 10, "mood": "a warm day", "headline": "h",
                "reasons": [{"text": "Today's Venus sets off the conjunction between "
                                     "Maximiliana's North Node and your Midheaven, very much so",
                             "lasts": "lasts a few days", "transit": "Venus",
                             "aspect": "conjunction"}]}
    monkeypatch.setattr(server, "_brief", _b)
    line, _ = server.morning_people_line(uid)
    assert len(line) <= server.PUSH_MAX and line.endswith(".")


def test_opt_out_falls_back_to_her_own_sky(monkeypatch):
    c, uid = user("hab-optout@x.com")
    add(c, "Maya")
    monkeypatch.setattr(server, "_brief", fake_brief({"Maya": 9}))
    assert c.get("/api/profile/push-people").json() == {"push_people": True}   # default on
    assert c.post("/api/profile/push-people", json={"on": False}).json() == {"push_people": False}
    assert server.morning_people_line(uid) is None


def test_real_engine_reason_is_a_real_aspect_that_day():
    """No mocks: whatever the line says, day_score said it for that pair that day."""
    c, uid = user("hab-real@x.com")
    for n, d in (("Theo", "1996-07-02"), ("Jordan", "1995-01-30"), ("Ana", "2001-12-12")):
        add(c, n, date=d)
    day = dt.date(2026, 10, 28)
    briefs, _ = server._people_briefs(uid, day)
    assert len(briefs) == 3
    out = server.morning_people_line(uid, day)
    if out is None:
        assert all(abs(b["delta"]) < daily.STEADY for b in briefs)
        return
    line, cid = out
    head = next(b for b in briefs if b["id"] == cid)
    assert abs(head["delta"]) >= daily.STEADY
    if head["reason"]:
        assert server._mechanism(head["reason"]) in line
        assert head["reason"]["aspect"] in daily.VERB


# ---- the cron

@pytest.fixture
def morning(monkeypatch):
    """One user due at 07:40 LA time, a fake sky for her own line, recorded sends."""
    sent = []
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(push, "send", lambda uid, title, body, url="/", **_: (
        sent.append({"uid": uid, "body": body, "url": url}) or {"sent": 1, "pruned": 0}))
    monkeypatch.setattr(server.context, "build", lambda *a, **k: (
        "block", {"band": "high", "score": 70,
                  "headlines": [{"transit": "Mars", "natal": "Venus", "aspect": "trine", "orb": 1.0}]}))
    users = []
    monkeypatch.setattr(auth, "all_users", lambda: list(users))

    def setup(email):
        c, uid = user(email)
        prof = store.load(uid, "profile", None)
        store.save(uid, "profile", {**prof, "tz": "America/Los_Angeles"})
        store.save(uid, "push", [{"endpoint": "https://fcm.googleapis.com/x", "keys": {}}])
        users.append(uid)
        return c, uid
    return setup, sent


LA_MORNING = dt.datetime(2026, 10, 27, 14, 40, tzinfo=UTC)


def sky_style(uid):
    """No person to name falls back to the style she chose; these tests pin the
    "what the sky is doing" style (the default is the line of the day, covered
    in test_push_cron)."""
    store.save(uid, "account", {**(store.load(uid, "account", {}) or {}), "push_style": "sky"})


def test_cron_sends_once_names_the_person_and_deep_links(monkeypatch, morning):
    setup, sent = morning
    c, uid = setup("hab-cron@x.com")
    cid = add(c, "Maya")
    monkeypatch.setattr(server, "_brief", fake_brief({"Maya": 8}))
    cron.morning(now=LA_MORNING)
    cron.morning(now=LA_MORNING + dt.timedelta(minutes=15))     # a second tick
    mine = [s for s in sent if s["uid"] == uid]
    assert len(mine) == 1
    assert mine[0]["url"] == f"/app?p={cid}"
    assert mine[0]["body"].startswith("Easier with Maya today: Venus trines")
    assert len(mine[0]["body"]) <= 140 and not BANNED_PUSH.search(mine[0]["body"])


def test_cron_quiet_people_day_uses_her_own_sky(monkeypatch, morning):
    setup, sent = morning
    c, uid = setup("hab-cron-quiet@x.com")
    sky_style(uid)
    add(c, "Maya")
    monkeypatch.setattr(server, "_brief", fake_brief({"Maya": 1}))
    cron.morning(now=LA_MORNING)
    mine = [s for s in sent if s["uid"] == uid]
    assert mine == [{"uid": uid, "body": "Mars is on your Venus today.", "url": "/app"}]


def test_cron_survives_a_broken_people_line(monkeypatch, morning):
    setup, sent = morning
    _, uid = setup("hab-cron-broken@x.com")
    sky_style(uid)
    monkeypatch.setattr(server, "_people_briefs", lambda *a, **k: 1 / 0)
    cron.morning(now=LA_MORNING)
    assert [s["body"] for s in sent if s["uid"] == uid] == ["Mars is on your Venus today."]


def test_cron_never_carries_a_linked_persons_mood_or_words(monkeypatch, morning):
    setup, sent = morning
    a, uid_a = setup("hab-link-a@x.com")
    b, _ = user("hab-link-b@x.com", name="Theo")
    tok = a.post("/api/invites").json()["token"]
    assert b.post(f"/api/invites/{tok}/accept", json={"share_chart": True,
                                                      "share_mood": False}).status_code == 200
    b.post("/api/checkin", json={"mood": 9, "text": "secret about Maya"})
    monkeypatch.setattr(server, "_brief", fake_brief({"Theo": -7}))
    cron.morning(now=LA_MORNING)
    body = next(s["body"] for s in sent if s["uid"] == uid_a)
    assert body.startswith("Touchier with Theo today")
    assert "secret" not in body and "9" not in body


# ======================================================================= widget

def test_widget_needs_sign_in():
    assert TestClient(server.app).get("/api/widget").status_code == 401


def test_widget_is_free_small_and_cached(monkeypatch):
    c, uid = user("hab-widget@x.com")
    cid = add(c, "Theo")
    assert plan.get(uid)["tier"] == "free"
    r = c.get("/api/widget")
    assert r.status_code == 200, r.text
    w = r.json()
    assert set(w) >= {"date", "my_score", "line", "moon_phase", "moon_sign", "top", "easiest"}
    assert set(w["top"]) == {"cid", "name", "score", "delta", "reason", "lasts", "url"}
    assert w["top"]["cid"] == cid and w["top"]["url"] == f"/app?p={cid}"
    assert set(w["line"]) == {"text", "why"}
    assert len(r.content) < 1500
    assert "private" in r.headers["cache-control"] and r.headers["etag"]

    # Served from cache: the payload builder must not run again today.
    monkeypatch.setattr(server, "_widget_payload", lambda uid: 1 / 0)
    timings = []
    for _ in range(5):
        t = time.perf_counter()
        again = c.get("/api/widget")
        timings.append(time.perf_counter() - t)
    assert again.json() == w
    assert sorted(timings)[2] < 0.05
    assert c.get("/api/widget", headers={"If-None-Match": r.headers["etag"]}).status_code == 304


def test_widget_never_carries_checkin_text():
    c, _ = user("hab-widget-text@x.com")
    c.post("/api/checkin", json={"mood": 3, "text": "a private sentence"})
    assert "private sentence" not in c.get("/api/widget").text


def test_widget_ignores_plan_entirely(monkeypatch):
    """No gate now, and none can creep in through plan.get later."""
    c, _ = user("hab-widget-plan@x.com")
    monkeypatch.setattr(plan, "get", lambda uid: 1 / 0)
    assert c.get("/api/widget").status_code == 200


# =============================================================== ask your mom

def ask(c):
    r = c.post("/api/birthtime/request")
    assert r.status_code == 200, r.text
    return r.json()


def test_request_needs_an_unknown_time():
    c, _ = user("bt-known@x.com", time_="04:42")
    assert c.post("/api/birthtime/request").status_code == 400


def test_public_page_shows_only_first_name_and_birth_date():
    c, uid = user("bt-page@x.com", time_="", name="Sam Rivera")
    req = ask(c)
    assert req["url"].endswith(f"/bt/{req['token']}") and "what time was I born?" in req["text"]
    rec = store.load("_bt_requests", f"bt-{req['token']}", None)
    assert rec["expires"] - rec["created"] == 14 * 86400

    page = TestClient(server.app).get(f"/bt/{req['token']}")     # no cookie
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert "Sam" in page.text and "March 14, 1998" in page.text
    # The page explains what "within an hour" means for a rising sign, so the
    # word itself appears; what must never appear is a sign — hers or anyone's.
    for leak in ("Rivera", "bt-page@x.com", "Chicago", uid, "Ascendant", *server.SIGNS):
        assert leak not in page.text, leak


def test_exact_time_goes_through_the_settings_path_and_link_dies(monkeypatch):
    c, uid = user("bt-exact@x.com", time_="")
    add(c, "Theo")
    calls, sends = [], []
    real = server._apply_birth_time
    monkeypatch.setattr(server, "_apply_birth_time",
                        lambda u, t, fold=0: calls.append((u, t)) or real(u, t, fold))
    monkeypatch.setattr(push, "send", lambda uid_, title, body, url="/", **_: sends.append(body) or {})
    before = server._natal(uid)["angles"]["Ascendant"]["lon"]
    req = ask(c)
    anon = TestClient(server.app)

    r = anon.post(f"/api/bt/{req['token']}", json={"time": "23:10", "sure": "certificate"})
    assert r.status_code == 200 and r.json() == {"ok": True, "name": "Maya"}
    assert calls == [(uid, "23:10")]
    prof = store.load(uid, "profile", None)
    assert prof["birth_time_known"] and prof["birth_time"] == "23:10"
    rising = server._natal(uid)["angles"]["Ascendant"]
    assert rising["lon"] != before
    assert all(row.get("rescored_at") for row in store.load(uid, "connections", []))
    assert sends == [f"Your rising sign is {rising['sign']}. Your houses (the life areas in your chart) are now updated."]

    # Single use, and the page says so warmly.
    assert anon.post(f"/api/bt/{req['token']}", json={"time": "01:00"}).status_code == 410
    gone = anon.get(f"/bt/{req['token']}")
    assert gone.status_code == 410 and "done its job" in gone.text and "Traceback" not in gone.text
    # The toast, exactly once.
    first = c.get("/api/birthtime/request").json()
    assert first["filled"]["rising"] == rising["sign"] and first["time_known"]
    assert c.get("/api/birthtime/request").json()["filled"] is None


def test_same_result_as_typing_it_in_settings():
    a, ua = user("bt-same-a@x.com", time_="")
    b, ub = user("bt-same-b@x.com", time_="")
    TestClient(server.app).post(f"/api/bt/{ask(a)['token']}", json={"time": "06:05"})
    b.post("/api/birthtime", json={"time": "06:05"})
    pa, pb = store.load(ua, "profile", None), store.load(ub, "profile", None)
    assert pa["birth_ut"] == pb["birth_ut"] and pa["birth_local"] == pb["birth_local"]


def test_roughly_afternoon_changes_no_chart():
    c, uid = user("bt-approx@x.com", time_="")
    before = store.load(uid, "profile", None)["birth_ut"]
    req = ask(c)
    r = TestClient(server.app).post(f"/api/bt/{req['token']}", json={"approx": "afternoon"})
    assert r.status_code == 200
    prof = store.load(uid, "profile", None)
    assert prof["birth_time_known"] is False and prof["birth_ut"] == before
    assert prof["birth_time_approx"] == {"part": "afternoon", "sure": "sure"}
    assert c.get("/api/reading").json()["time_known"] is False
    st = c.get("/api/birthtime/request").json()
    assert st["filled"]["kind"] == "approx" and "hidden until" in st["filled"]["text"]
    assert "rising" not in st["filled"]
    assert TestClient(server.app).post(f"/api/bt/{req['token']}", json={"time": "13:00"}).status_code == 410


def test_bad_answers_are_refused_and_the_link_survives():
    c, _ = user("bt-bad@x.com", time_="")
    tok = ask(c)["token"]
    anon = TestClient(server.app)
    assert anon.post(f"/api/bt/{tok}", json={"time": "25:99"}).status_code == 400
    assert anon.post(f"/api/bt/{tok}", json={"time": "soon"}).status_code == 400
    assert anon.post(f"/api/bt/{tok}", json={}).status_code == 400
    assert anon.get(f"/bt/{tok}").status_code == 200


def test_revoke_expire_and_one_live_link():
    c, _ = user("bt-revoke@x.com", time_="")
    other, _ = user("bt-revoke-other@x.com", time_="")
    anon = TestClient(server.app)
    first = ask(c)["token"]
    second = ask(c)["token"]                     # a new link kills the old one
    assert anon.get(f"/bt/{first}").status_code == 410
    assert c.get("/api/birthtime/request").json()["pending"]["token"] == second
    assert other.delete(f"/api/birthtime/request/{second}").status_code == 404   # not hers
    assert c.delete(f"/api/birthtime/request/{second}").json() == {"revoked": True}
    assert anon.post(f"/api/bt/{second}", json={"time": "10:00"}).status_code == 410
    assert c.get("/api/birthtime/request").json()["pending"] is None

    third = ask(c)["token"]
    rec = store.load("_bt_requests", f"bt-{third}", None)
    store.save("_bt_requests", f"bt-{third}", {**rec, "expires": time.time() - 1})
    page = anon.get(f"/bt/{third}")
    assert page.status_code == 410 and "Traceback" not in page.text


def test_unknown_tokens_get_a_friendly_page():
    anon = TestClient(server.app)
    for t in ("nope", "A" * 22, "x.y"):
        r = anon.get(f"/bt/{t}")
        assert r.status_code == 404 and "isn't one of ours" in r.text
    assert anon.post("/api/bt/" + "A" * 22, json={"time": "10:00"}).status_code == 404


def test_deleting_the_account_deletes_outstanding_links():
    c, _ = user("bt-delete@x.com", time_="")
    tok = ask(c)["token"]
    assert store.load("_bt_requests", f"bt-{tok}", None)
    assert c.delete("/api/account").json() == {"deleted": True}
    assert store.load("_bt_requests", f"bt-{tok}", None) is None
    assert TestClient(server.app).get(f"/bt/{tok}").status_code == 404


def test_parent_page_never_runs_unauthenticated_writes_on_a_known_time():
    """She typed it herself in the meantime: the link is spent, not a way to overwrite."""
    c, uid = user("bt-raced@x.com", time_="")
    tok = ask(c)["token"]
    c.post("/api/birthtime", json={"time": "07:00"})
    assert TestClient(server.app).post(f"/api/bt/{tok}", json={"time": "19:00"}).status_code == 410
    assert store.load(uid, "profile", None)["birth_time"] == "07:00"


# ------------------------------------------------- "within an hour" is not exact

def _rising_truth(prof, hh, mm, minutes=60, step=5):
    """Ground truth by brute force: the ascendant's sign every `step` minutes
    across the window, straight from the ephemeris. One sign -> that sign."""
    y, m, d = (int(x) for x in prof["birth_date"].split("-"))
    lat, lon = prof["birth_ut"][4], prof["birth_ut"][5]
    utc, _ = server._birth_instant(y, m, d, hh, mm, prof["tz"], lat, lon, False)
    signs = set()
    for off in range(-minutes, minutes + 1, step):
        t = utc + dt.timedelta(minutes=off)
        signs.add(server.natal(t.year, t.month, t.day, t.hour + t.minute / 60,
                               lat, lon)["angles"]["Ascendant"]["sign"])
    return signs.pop() if len(signs) == 1 else None


def _find_times(prof):
    """One local time whose +/-1h window keeps a single rising sign, one whose
    window crosses a sign. Found from the ephemeris, not hard-coded."""
    stable = crossing = None
    for h in range(24):
        for mm in (0, 30):
            r = _rising_truth(prof, h, mm)
            if r and not stable:
                stable = (h, mm)
            if not r and not crossing:
                crossing = (h, mm)
    assert stable and crossing
    return stable, crossing


def test_within_an_hour_is_kept_approximate_never_exact(monkeypatch):
    """The blocker: "Within an hour" used to go through _apply_birth_time and
    mark the time exact, which can be a whole sign off on the Ascendant."""
    c, uid = user("bt-rough@x.com", time_="")
    before = store.load(uid, "profile", None)
    (h, mm), _ = _find_times(before)
    calls, sends = [], []
    monkeypatch.setattr(server, "_apply_birth_time", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(push, "send", lambda uid_, title, body, url="/", **_: sends.append(body) or {})
    req = ask(c)
    t = f"{h:02d}:{mm:02d}"
    r = TestClient(server.app).post(f"/api/bt/{req['token']}", json={"time": t, "sure": "roughly"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "name": "Maya", "rising_shown": True}   # never which sign
    assert calls == []
    prof = store.load(uid, "profile", None)
    assert prof["birth_time_known"] is False
    assert prof["birth_ut"] == before["birth_ut"] and prof.get("birth_time") == before.get("birth_time")
    rising = _rising_truth(prof, h, mm)
    assert prof["birth_time_approx"] == {"time": t, "window_min": 60, "sure": "roughly",
                                         "rising": rising}
    assert c.get("/api/reading").json()["time_known"] is False
    st = c.get("/api/birthtime/request").json()
    assert st["time_known"] is False and st["approx"]["time"] == t
    assert st["filled"]["kind"] == "approx_time" and st["filled"]["rising"] == rising
    assert f"Your rising sign is {rising} either way" in sends[0] and "give or take an hour" in sends[0]
    assert "exact minute" in sends[0]
    # A later exact time from Settings supersedes the approximate one.
    monkeypatch.undo()
    c.post("/api/birthtime", json={"time": t})
    prof = store.load(uid, "profile", None)
    assert prof["birth_time_known"] and "birth_time_approx" not in prof


def test_within_an_hour_hides_a_rising_sign_that_changes_in_the_window(monkeypatch):
    c, uid = user("bt-rough-cusp@x.com", time_="")
    _, (h, mm) = _find_times(store.load(uid, "profile", None))
    sends = []
    monkeypatch.setattr(push, "send", lambda uid_, title, body, url="/", **_: sends.append(body) or {})
    r = TestClient(server.app).post(f"/api/bt/{ask(c)['token']}",
                                    json={"time": f"{h:02d}:{mm:02d}", "sure": "roughly"})
    assert r.json()["rising_shown"] is False
    prof = store.load(uid, "profile", None)
    assert prof["birth_time_known"] is False and prof["birth_time_approx"]["rising"] is None
    filled = c.get("/api/birthtime/request").json()["filled"]
    assert "rising" not in filled and "hidden rather than guessing" in filled["text"]
    assert not any(f"{s} rising" in sends[0] for s in server.SIGNS)


def test_two_answers_racing_cannot_both_claim_the_link(monkeypatch):
    """Both requests read the record before either writes it — the window a
    read-then-write claim leaves open. Only the atomic counter decides."""
    c, uid = user("bt-race@x.com", time_="")
    tok = ask(c)["token"]
    stale = server._bt_state(tok)
    monkeypatch.setattr(server, "_bt_state", lambda t: stale)
    anon = TestClient(server.app)
    first = anon.post(f"/api/bt/{tok}", json={"time": "08:15", "sure": "certificate"})
    second = anon.post(f"/api/bt/{tok}", json={"time": "20:45", "sure": "certificate"})
    assert first.status_code == 200 and second.status_code == 410
    assert store.load(uid, "profile", None)["birth_time"] == "08:15"


def test_two_answers_in_real_threads_apply_once(monkeypatch):
    import threading
    c, _ = user("bt-race-threads@x.com", time_="")
    tok = ask(c)["token"]
    real, applied = server._bt_state, []
    gate = threading.Barrier(2)

    def both_read_first(t):                 # hold both until each has read "ok"
        out = real(t)
        gate.wait(timeout=5)
        return out
    monkeypatch.setattr(server, "_bt_state", both_read_first)
    orig = server._apply_birth_time
    monkeypatch.setattr(server, "_apply_birth_time",
                        lambda u, t, fold=0: applied.append(t) or orig(u, t, fold))
    codes = []

    def go(t):
        codes.append(TestClient(server.app).post(f"/api/bt/{tok}", json={"time": t}).status_code)
    ts = [threading.Thread(target=go, args=(t,)) for t in ("08:15", "20:45")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(codes) == [200, 410] and len(applied) == 1


def test_a_failed_write_releases_the_claim(monkeypatch):
    c, uid = user("bt-release@x.com", time_="")
    tok = ask(c)["token"]
    orig = server._apply_birth_time

    def boom(*a, **k):
        raise server.HTTPException(400, "Time should look like 04:42.")
    monkeypatch.setattr(server, "_apply_birth_time", boom)
    anon = TestClient(server.app)
    assert anon.post(f"/api/bt/{tok}", json={"time": "09:00"}).status_code == 400
    monkeypatch.setattr(server, "_apply_birth_time", orig)
    assert anon.post(f"/api/bt/{tok}", json={"time": "09:00"}).status_code == 200
    assert store.load(uid, "profile", None)["birth_time"] == "09:00"
