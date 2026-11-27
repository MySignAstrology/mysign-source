"""
App-layer tests. Run with: python -m pytest tests/test_app.py -q

Covers the things that would hurt someone if they broke: the safety tiering,
the auth boundary on every route that touches personal data, consent gating,
and that deletion actually deletes.
"""
import json
import os
import re
import tempfile

import pytest

os.environ["MYSIGN_DATA"] = tempfile.mkdtemp(prefix="mysign-test-")
os.environ["MYSIGN_DEV_AUTH"] = "1"          # tests read the code from the response
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("MYSIGN_TABLE", None)
os.environ.pop("MYSIGN_FROM_EMAIL", None)
os.environ.pop("AWS_LAMBDA_FUNCTION_NAME", None)

from fastapi.testclient import TestClient

import app.server as server
from engine import safety


@pytest.fixture
def c():
    return TestClient(server.app)


def sign_in(c, email="a@b.com"):
    code = c.post("/api/auth/request", json={"email": email}).json()["dev_code"]
    r = c.post("/api/auth/verify", json={"email": email, "code": code})
    c.post("/api/consent", json={"ai": True})
    # The roster is one item per member now, unordered; tests that need the
    # newest user read it from here.
    last_uid.value = r.json()["uid"]
    return c


def last_uid():
    return last_uid.value


def onboard(c, time="04:42"):
    """time="" exercises the unknown-birth-time path."""
    return c.post("/api/onboard", json={"name": "Maya", "date": "1999-03-14",
                                        "time": time, "city": "Chicago IL"})


# ------------------------------------------------------------------- safety

@pytest.mark.parametrize("msg,tier", [
    ("should i answer him", "green"),
    ("this commute is killing me", "green"),
    ("why am i so nostalgic", "green"),
    ("should i break up with theo", "amber"),
    ("thinking about quitting my job", "amber"),
    ("i want to kill myself", "red"),
    ("i don't want to be here anymore", "red"),
    ("kms", "red"),
    ("he hits me when he drinks", "red"),
    ("i havent eaten in three days", "red"),
])
def test_prefilter_tiers(msg, tier):
    assert safety.prefilter(msg) == tier


def test_prefilter_never_needs_network(monkeypatch):
    """Crisis detection must survive the classifier being unreachable."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-bogus")
    monkeypatch.setattr(safety, "_client", None)
    assert safety.classify("i want to kill myself").tier == "red"


def _streamed_text(body):
    return "".join(json.loads(m)["t"] for m in re.findall(r'data: (\{"t":.*?\})\r?\n', body))


def test_red_message_never_reaches_the_model(c):
    sign_in(c); onboard(c)
    with c.stream("POST", "/api/message", json={"text": "i want to kill myself"}) as r:
        body = "".join(r.iter_text())

    assert '"tier": "red"' in body
    # The strongest invariant: byte-for-byte the canned reply, nothing generated.
    assert _streamed_text(body) == safety.CRISIS_REPLY
    assert "988" in safety.CRISIS_REPLY
    # And no astrological claim anywhere. (The reply does say "stop the chart
    # stuff" and "No transits" — those words appearing is the point, so assert
    # on the things that would constitute a reading.)
    for token in ("Neptune", "Pluto", "Venus", "Saturn", "retrograde",
                  "your 12th", "orb", "natal"):
        assert token.lower() not in _streamed_text(body).lower(), \
            f"astrology leaked into a red reply: {token}"


# --------------------------------------------------------------------- auth

@pytest.mark.parametrize("method,path", [
    ("get", "/api/today"), ("get", "/api/memory"), ("post", "/api/checkin"),
    ("post", "/api/message"), ("post", "/api/onboard"), ("post", "/api/rollover"),
    ("delete", "/api/account"),
    ("get", "/api/connections"), ("post", "/api/connections"),
])
def test_personal_routes_require_a_session(method, path):
    fresh = TestClient(server.app)
    kwargs = {"json": {}} if method == "post" else {}
    assert getattr(fresh, method)(path, **kwargs).status_code == 401


def test_state_is_safe_when_signed_out():
    fresh = TestClient(server.app)
    body = fresh.get("/api/state").json()
    assert body["signed_in"] is False and "thread" not in body


@pytest.mark.parametrize("bad", [
    "maya@example.commaya@example.com", "maya@x@example.com", "ma ya@example.com",
    "maya@example", "maya@example..com", "@example.com", "maya@-example.com",
])
def test_an_address_that_cannot_get_mail_is_refused(c, bad):
    r = c.post("/api/auth/request", json={"email": bad})
    assert r.status_code == 400 and "email address" in r.text


def test_ordinary_addresses_still_get_a_code(c):
    for ok in ["Maya.K+tag@Mail.Example.co.uk", "m_1@x-y.io"]:
        assert c.post("/api/auth/request", json={"email": ok}).status_code == 200


def test_wrong_code_rejected(c):
    c.post("/api/auth/request", json={"email": "x@y.com"})
    assert c.post("/api/auth/verify", json={"email": "x@y.com", "code": "000000"}).status_code == 401


def test_code_is_single_use(c):
    code = c.post("/api/auth/request", json={"email": "s@t.com"}).json()["dev_code"]
    assert c.post("/api/auth/verify", json={"email": "s@t.com", "code": code}).status_code == 200
    assert c.post("/api/auth/verify", json={"email": "s@t.com", "code": code}).status_code == 401


def test_consent_gates_personal_data(c):
    code = c.post("/api/auth/request", json={"email": "n@c.com"}).json()["dev_code"]
    c.post("/api/auth/verify", json={"email": "n@c.com", "code": code})
    assert c.get("/api/today").status_code == 403
    c.post("/api/consent", json={"ai": True})
    assert c.get("/api/today").status_code in (200, 400)


def test_users_cannot_see_each_other(c):
    sign_in(c, "one@x.com"); onboard(c)
    c.post("/api/checkin", json={"mood": 3, "text": "private thing"})
    other = sign_in(TestClient(server.app), "two@x.com")
    assert other.get("/api/state").json().get("thread") == []
    assert other.get("/api/today").json() == {"onboarded": False}


# ---------------------------------------------------------------- lifecycle

def test_onboard_computes_a_real_chart(c):
    sign_in(c, "chart@x.com")
    j = onboard(c).json()
    assert j["chart"]["Sun"]["label"].endswith("Pisces")
    assert j["chart"]["Sun"]["house"] == 1
    assert j["angles"]["Ascendant"].endswith("Aquarius")


def test_checkin_validates_mood(c):
    sign_in(c, "mood@x.com"); onboard(c)
    assert c.post("/api/checkin", json={"mood": 11}).status_code == 400
    assert c.post("/api/checkin", json={"mood": 4}).status_code == 200


def test_today_reports_a_band(c):
    sign_in(c, "band@x.com"); onboard(c)
    j = c.get("/api/today").json()
    assert j["band"] in ("high", "medium", "low")
    assert isinstance(j["headlines"], list)
    # The first thing she reads: a line and the plain-words reason for it.
    assert j["line"]["text"] and j["line"]["why"]


def test_message_streams_and_persists(c):
    sign_in(c, "msg@x.com"); onboard(c)
    with c.stream("POST", "/api/message", json={"text": "should i answer him"}) as r:
        body = "".join(r.iter_text())
    assert "event: meta" in body and "event: delta" in body and "event: done" in body
    assert len(c.get("/api/state").json()["thread"]) == 2


def test_rollover_is_idempotent(c):
    sign_in(c, "roll@x.com"); onboard(c)
    c.post("/api/checkin", json={"mood": 5, "text": "tired"})
    with c.stream("POST", "/api/message", json={"text": "hi"}) as r:
        "".join(r.iter_text())
    first = c.post("/api/rollover?include_today=true").json()["written"]
    second = c.post("/api/rollover?include_today=true").json()["written"]
    assert len(first) == 1 and second == []


def test_delete_actually_deletes(c):
    sign_in(c, "gone@x.com"); onboard(c)
    c.post("/api/checkin", json={"mood": 7, "text": "something personal"})
    assert c.delete("/api/account").json() == {"deleted": True}
    again = TestClient(server.app)
    code = again.post("/api/auth/request", json={"email": "gone@x.com"}).json()["dev_code"]
    again.post("/api/auth/verify", json={"email": "gone@x.com", "code": code})
    again.post("/api/consent", json={"ai": True})
    assert again.get("/api/today").json() == {"onboarded": False}


def test_patterns_stay_silent_before_ninety_days(c):
    sign_in(c, "pat@x.com"); onboard(c)
    m = c.get("/api/memory").json()
    assert m["patterns"] == [] and m["days_to_patterns"] == 90


# ---------------------------------------------------------------------- push

def test_push_routes_require_consent():
    fresh = TestClient(server.app)
    assert fresh.get("/api/push/status").status_code == 401
    assert fresh.post("/api/push/subscribe", json={"subscription": {}}).status_code == 401


def test_vapid_key_is_stable(c):
    sign_in(c, "vapid@x.com")
    a = c.get("/api/push/key").json()["key"]
    b = c.get("/api/push/key").json()["key"]
    assert a == b and len(a) > 80


def test_subscribe_and_unsubscribe(c):
    sign_in(c, "sub@x.com"); onboard(c)
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "B" + "A" * 86, "auth": "A" * 22}}
    assert c.post("/api/push/subscribe",
                  json={"subscription": sub, "tz": "America/Chicago"}).json()["devices"] == 1
    # same endpoint twice is one device, not two
    assert c.post("/api/push/subscribe",
                  json={"subscription": sub, "tz": "America/Chicago"}).json()["devices"] == 1
    assert c.get("/api/push/status").json()["devices"] == 1
    assert c.request("DELETE", "/api/push/subscribe").json()["devices"] == 0


def test_manifest_and_service_worker_are_served(c):
    assert c.get("/manifest.webmanifest").status_code == 200
    sw = c.get("/sw.js")
    assert sw.status_code == 200 and "showNotification" in sw.text
    assert c.get("/static/icons/icon-192.png").status_code == 200


# ---------------------------------------------------------------------- cron

def _prep_push_user(c, email, tz="America/Chicago"):
    sign_in(c, email); onboard(c)
    c.post("/api/push/subscribe", json={
        "subscription": {"endpoint": f"https://fcm.googleapis.com/fcm/send/{email}",
                         "keys": {"p256dh": "B" + "A" * 86, "auth": "A" * 22}}, "tz": tz})
    from engine.store import load, save
    uid = last_uid()
    prof = load(uid, "profile", {}); prof["tz"] = tz; save(uid, "profile", prof)
    return uid


def test_cron_only_fires_inside_the_local_window(c, monkeypatch):
    import datetime as dt

    from app import cron, push
    uid = _prep_push_user(c, "cron1@x.com")
    sent = []
    monkeypatch.setattr(push, "send", lambda u, t, b, url="/", **_: sent.append((u, b)) or {"sent": 1, "pruned": 0})

    # 03:00 Chicago — nowhere near 07:30
    off = dt.datetime(2026, 8, 24, 8, 0, tzinfo=dt.timezone.utc)
    assert cron.morning(off)["pushed"] == 0 and sent == []

    # 07:35 Chicago (CDT = UTC-5) -> 12:35 UTC
    on = dt.datetime(2026, 8, 24, 12, 35, tzinfo=dt.timezone.utc)
    assert cron.morning(on)["pushed"] == 1
    assert sent and sent[0][0] == uid


def test_cron_is_idempotent_per_day(c, monkeypatch):
    import datetime as dt

    from app import cron, push
    _prep_push_user(c, "cron2@x.com")
    calls = []
    monkeypatch.setattr(push, "send", lambda u, t, b, url="/", **_: calls.append(u) or {"sent": 1, "pruned": 0})
    when = dt.datetime(2026, 8, 25, 12, 35, tzinfo=dt.timezone.utc)
    first = cron.morning(when)["pushed"]
    second = cron.morning(when)["pushed"]
    assert first >= 1 and second == 0


def test_cron_skips_users_without_a_subscription(c, monkeypatch):
    """Assert on this user specifically — the roster is shared, and other test
    users legitimately do get pushed on the same tick."""
    import datetime as dt

    from app import cron, push
    sign_in(c, "nopush@x.com"); onboard(c)
    uid = last_uid()

    pushed = []
    monkeypatch.setattr(push, "send",
                        lambda u, t, b, url="/", **_: pushed.append(u) or {"sent": 1, "pruned": 0})
    cron.morning(dt.datetime(2026, 8, 26, 12, 35, tzinfo=dt.timezone.utc))
    assert uid not in pushed


def test_quiet_day_sends_a_check_in_not_a_reading():
    from app import cron
    _title, body = cron._compose("u", {"band": "low", "headlines": []}, "", "Maya")
    assert "quiet" in body.lower() and "?" in body
    for token in ("conjunction", "square", "orb", "house"):
        assert token not in body.lower()


# ------------------------------------------------------- auth fails closed

def test_no_transport_and_no_dev_flag_refuses(monkeypatch):
    """
    The regression that matters. This used to return the login code to anyone
    who asked whenever SES was unconfigured — a full authentication bypass on
    any deploy that forgot one environment variable.
    """
    from app import auth
    monkeypatch.delenv("MYSIGN_DEV_AUTH", raising=False)
    monkeypatch.delenv("MYSIGN_FROM_EMAIL", raising=False)
    ok, payload = auth.request_code("victim@example.com")
    assert ok is False
    assert "dev_code" not in payload
    assert payload["status"] == 503


def test_dev_flag_is_ignored_on_lambda(monkeypatch):
    from app import auth
    monkeypatch.setenv("MYSIGN_DEV_AUTH", "1")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "mysign-fn")
    monkeypatch.delenv("MYSIGN_FROM_EMAIL", raising=False)
    assert auth.dev_auth_allowed() is False
    ok, payload = auth.request_code("victim@example.com")
    assert ok is False and "dev_code" not in payload


def test_request_endpoint_never_leaks_a_code_without_the_flag(monkeypatch):
    monkeypatch.delenv("MYSIGN_DEV_AUTH", raising=False)
    monkeypatch.delenv("MYSIGN_FROM_EMAIL", raising=False)
    fresh = TestClient(server.app)
    r = fresh.post("/api/auth/request", json={"email": "victim@example.com"})
    assert r.status_code == 503
    assert "dev_code" not in r.text and "code" not in r.json().get("detail", "").lower()


# ------------------------------------------------------------------ alerting

def test_red_tier_pages(c, monkeypatch):
    from app import alerts
    paged = []
    monkeypatch.setattr(alerts, "red_tier", lambda uid, txt, **kw: paged.append((uid, txt)))
    sign_in(c, "page@x.com"); onboard(c)
    with c.stream("POST", "/api/message", json={"text": "i want to kill myself"}) as r:
        "".join(r.iter_text())
    assert len(paged) == 1


def test_paging_failure_never_breaks_the_reply(c, monkeypatch):
    """A crisis reply must reach the user whether or not PagerDuty is up."""
    from app import alerts
    monkeypatch.setattr(alerts, "_send",
                        lambda p: (_ for _ in ()).throw(OSError("network down")))
    sign_in(c, "pagefail@x.com"); onboard(c)
    with c.stream("POST", "/api/message", json={"text": "i want to kill myself"}) as r:
        body = "".join(r.iter_text())
    assert _streamed_text(body) == safety.CRISIS_REPLY


# ------------------------------------------------------------- compatibility

def test_score_is_centred_on_ordinary_pairs():
    """
    The whole point of the reference distribution: a random pair should land
    near 50. Before it existed the median pair scored in the eighties, which
    made the feature a flattery machine.
    """
    import random
    import statistics

    from engine import synastry
    from engine.chart import natal

    rng = random.Random(3)
    charts = [natal(rng.randint(1990, 2005), rng.randint(1, 12), rng.randint(1, 28),
                    rng.random() * 24, 41.88, -87.63) for _ in range(14)]
    scores = [synastry.score(a, b)["overall"]
              for i, a in enumerate(charts) for b in charts[i + 1:]]
    assert 43 <= statistics.median(scores) <= 57, statistics.median(scores)
    assert statistics.pstdev(scores) > 4, "score barely varies — it says nothing"


def test_every_point_is_itemised():
    """A score the code cannot explain is a bug, not a reading."""
    from engine import synastry
    from engine.chart import natal
    a = natal(1999, 3, 14, 10.7, 41.88, -87.63)
    b = natal(1996, 7, 2, 15.25, 40.71, -74.01)
    sc = synastry.score(a, b)
    assert sc["contacts"] > 0
    assert sc["drivers"] or sc["frictions"]
    assert all("°" in d for d in sc["drivers"] + sc["frictions"])


def test_unknown_birth_time_downgrades_confidence():
    from engine import synastry
    from engine.chart import natal
    a = natal(1999, 3, 14, 10.7, 41.88, -87.63)
    b = natal(1996, 7, 2, 12.0, 40.71, -74.01)
    assert synastry.score(a, b)["confidence"] == "full"
    assert synastry.score(a, b, time_known_b=False)["confidence"] == "partial"


def test_score_is_symmetric_enough_to_trust():
    """Overlays are directional, so A->B and B->A differ — but not wildly."""
    from engine import synastry
    from engine.chart import natal
    a = natal(1999, 3, 14, 10.7, 41.88, -87.63)
    b = natal(1996, 7, 2, 15.25, 40.71, -74.01)
    assert abs(synastry.score(a, b)["overall"] - synastry.score(b, a)["overall"]) <= 8


def test_connection_round_trips_and_is_remembered(c):
    sign_in(c, "conn@b.com")
    onboard(c)
    r = c.post("/api/connections", json={"name": "Theo", "relation": "talking",
                                         "date": "1997-11-03", "time": "08:15",
                                         "city": "Chicago IL"})
    assert r.status_code == 200, r.text
    row = r.json()
    assert 1 <= row["overall"] <= 100 and row["band"]
    assert set(row["dimensions"]) == {"spark", "ease", "staying"}

    listed = c.get("/api/connections").json()
    assert listed["count"] == 1 and listed["connections"][0]["name"] == "Theo"
    assert c.get("/api/memory").json()["connections"][0]["name"] == "Theo"

    assert c.get(f"/api/connections/{row['id']}").json()["id"] == row["id"]
    assert c.delete(f"/api/connections/{row['id']}").json()["ok"] is True
    assert c.get("/api/connections").json()["count"] == 0


def test_connection_needs_your_own_chart_first(c):
    sign_in(c, "nochart@b.com")
    r = c.post("/api/connections", json={"name": "Theo", "date": "1997-11-03",
                                         "city": "Chicago IL"})
    assert r.status_code == 400


def test_unknown_city_is_rejected(c):
    """Not "Atlantis" — that is a real town in the Western Cape, and the 34k
    dataset has it. Fictional-sounding is not the same as absent."""
    sign_in(c, "badcity@b.com")
    onboard(c)
    r = c.post("/api/connections", json={"name": "Theo", "date": "1997-11-03",
                                         "city": "Narnia"})
    assert r.status_code == 400


def test_connections_reach_the_chat_context(c):
    """The memory sell only works if the model can actually see them."""
    import datetime as dt

    from engine import context

    sign_in(c, "ctx@b.com")
    onboard(c)
    c.post("/api/connections", json={"name": "Theo", "relation": "talking",
                                     "date": "1997-11-03", "time": "08:15",
                                     "city": "Chicago IL"})
    uid = last_uid()
    block, _ = context.build(uid, server._natal(uid), server._sky(uid),
                             {"text": "", "mood": 5}, today=dt.date.today())
    line = next(line for line in block.splitlines() if "compatibility:" in line)
    assert "Theo" in line and "talking" in line
    assert "/100" in line and "spark" in line


def test_deleting_the_account_deletes_connections(c):
    sign_in(c, "wipe@b.com")
    onboard(c)
    c.post("/api/connections", json={"name": "Theo", "date": "1997-11-03",
                                     "city": "Chicago IL"})
    c.delete("/api/account")
    sign_in(c, "wipe@b.com")
    assert c.get("/api/connections").json()["count"] == 0
# --------------------------------------------------------------- the reading

def test_reading_requires_auth_and_consent(c):
    assert TestClient(server.app).get("/api/reading").status_code == 401
    code = c.post("/api/auth/request", json={"email": "rd@x.com"}).json()["dev_code"]
    c.post("/api/auth/verify", json={"email": "rd@x.com", "code": code})
    assert c.get("/api/reading").status_code == 403          # consent first


def test_reading_is_built_from_the_real_chart(c):
    sign_in(c, "read1@x.com"); onboard(c)
    r = c.get("/api/reading").json()
    labels = {x["label"]: x["value"] for x in r["cards"]}
    assert labels == {"Sun": "Pisces", "Moon": "Aquarius", "Rising": "Aquarius"}
    assert r["balance"]["element"] and r["balance"]["modality"]
    assert r["standout"] and r["wiring"] and r["starters"]
    # every wiring entry must carry a real measured orb
    assert all(isinstance(w["orb"], (int, float)) for w in r["wiring"])


def test_reading_carries_the_wheel_from_the_ephemeris(c):
    """The drawn chart is positions, not prose: degrees straight from the
    engine, with the Ascendant and twelve cusps when the time is known."""
    sign_in(c, "wheel1@x.com"); onboard(c)
    w = c.get("/api/reading").json()["wheel"]
    lon = {p["name"]: p["lon"] for p in w["planets"]}
    assert len(lon) == 10 and "Chiron" not in lon
    assert 330 <= lon["Sun"] < 360                       # Pisces, as the card says
    assert 300 <= lon["Moon"] < 330 and 300 <= w["asc"] < 330
    assert len(w["cusps"]) == 12 and abs(w["cusps"][0] - w["asc"]) < 0.01
    assert w["aspects"] and all(isinstance(a["orb"], (int, float)) for a in w["aspects"])
    # still there on the cached path
    assert c.get("/api/reading").json()["wheel"]["planets"] == w["planets"]


def test_people_list_places_everyone_at_their_sun(c):
    sign_in(c, "wheel4@x.com"); onboard(c)
    c.post("/api/connections", json={"name": "Theo", "date": "1997-11-03", "city": "Chicago IL"})
    m = c.get("/api/connections").json()
    assert 330 <= m["me"]["sun_lon"] < 360                 # her own Sun, Pisces
    assert 210 <= m["connections"][0]["sun_lon"] < 240     # 3 November: Scorpio


def test_today_carries_the_sky_over_her_chart(c):
    sign_in(c, "wheel3@x.com"); onboard(c)
    t = c.get("/api/today").json()
    w = t["wheel"]
    assert len(w["now"]) == 10 and len(w["natal"]["planets"]) == 10
    assert all(0 <= p["lon"] < 360 for p in w["now"])
    if t["quiet"]:
        assert "hit" not in w
    elif "hit" in w:
        assert 0 <= w["hit"]["t_lon"] < 360 and 0 <= w["hit"]["n_lon"] < 360


def test_wheel_without_a_birth_time_draws_no_houses_or_moon_lines(c):
    sign_in(c, "wheel2@x.com"); onboard(c, time="")
    w = c.get("/api/reading").json()["wheel"]
    assert w["time_known"] is False
    assert "asc" not in w and "cusps" not in w and "mc" not in w
    assert all("Moon" not in (a["a"], a["b"]) for a in w["aspects"])


def test_reading_is_cached(c):
    sign_in(c, "read2@x.com"); onboard(c)
    a = c.get("/api/reading").json()
    b = c.get("/api/reading").json()
    assert a["opening"] == b["opening"] and a["starters"] == b["starters"]


def test_standout_never_repeats_the_same_stellium(c):
    """A stellium sits in one sign AND one house; reporting both reads as two
    findings when it is one."""
    sign_in(c, "read3@x.com"); onboard(c)
    r = c.get("/api/reading").json()
    # A finding is identified by the planets behind it (the small print), not
    # by its title, which is now written about her.
    groups = [tuple(sorted((s.get("because") or s["title"]).split(":")[-1].strip(" .").split(", ")))
              for s in r["standout"]]
    assert len(groups) == len(set(groups))
    assert len(r["standout"]) <= 4


def test_starters_differ_by_chart(c):
    sign_in(c, "st1@x.com"); onboard(c)
    a = set(c.get("/api/reading").json()["starters"])
    other = sign_in(TestClient(server.app), "st2@x.com")
    other.post("/api/onboard", json={"name": "B", "date": "1992-07-22",
                                     "time": "03:30", "city": "Los Angeles CA"})
    b = set(other.get("/api/reading").json()["starters"])
    assert a != b



def test_starters_come_at_once_without_the_model(c, monkeypatch):
    """Today's first questions used to wait for /api/reading, which on a new
    account is a model writing her whole reading. /api/starters is the same
    list from her chart alone."""
    from engine import profile
    sign_in(c, "st3@x.com"); onboard(c)

    def no_model(*a, **k):
        raise AssertionError("the starters must not wait on the written reading")
    monkeypatch.setattr(profile, "build", no_model)
    fast = c.get("/api/starters")
    assert fast.status_code == 200
    monkeypatch.undo()

    assert fast.json()["starters"] == c.get("/api/reading").json()["starters"]
    assert 1 <= len(fast.json()["starters"]) <= 4


def test_starters_are_in_her_language(c):
    from engine import i18n
    from engine.store import load, save
    sign_in(c, "st4@x.com"); onboard(c)
    en = c.get("/api/starters").json()["starters"]
    uid = last_uid()
    save(uid, "profile", {**load(uid, "profile", {}), "lang": "es"})
    assert c.get("/api/starters").json()["starters"] == i18n.starters(en, "es")


def test_starters_need_a_signed_in_onboarded_account(c):
    assert c.get("/api/starters").status_code == 401
    sign_in(c, "st5@x.com")
    assert c.get("/api/starters").status_code == 400


# ------------------------------------------------------- question memory

def test_questions_are_remembered(c):
    sign_in(c, "q1@x.com"); onboard(c)
    for q in ("should i text him back", "why do i overexplain"):
        with c.stream("POST", "/api/message", json={"text": q}) as r:
            "".join(r.iter_text())
    assert c.get("/api/memory").json()["themes"]["total"] == 2


def test_recurring_themes_stay_quiet_below_the_threshold(c):
    """Naming a theme back to someone after two mentions is the same
    overclaiming the pattern verifier exists to prevent."""
    from engine import memory as M
    sign_in(c, "q2@x.com"); onboard(c)
    uid = last_uid()
    for _ in range(2):
        M.remember_question(uid, "should i text him")
    assert M.recurring_themes(uid)["topics"] == []
    M.remember_question(uid, "he texted again")
    assert any(t["name"] == "dating" for t in M.recurring_themes(uid)["topics"])


def test_delete_removes_questions_and_reading(c):
    sign_in(c, "qdel@x.com"); onboard(c)
    with c.stream("POST", "/api/message", json={"text": "should i text him"}) as r:
        "".join(r.iter_text())
    c.get("/api/reading")
    c.delete("/api/account")
    again = sign_in(TestClient(server.app), "qdel@x.com")
    assert again.get("/api/memory").json()["themes"]["total"] == 0


# ------------------------------------------------- unknown birth time

def _onboard_no_time(c):
    return c.post("/api/onboard", json={"name": "Nia", "date": "1999-03-14",
                                        "time": "", "city": "Chicago IL"})


def test_no_birth_time_means_no_rising_and_no_houses(c):
    """
    The ascendant moves through all twelve signs in a day — measured, eight
    distinct rising signs across twenty-four hours. Without a time these are not
    approximate, they are invented, so the reading omits them.
    """
    sign_in(c, "nt1@x.com"); _onboard_no_time(c)
    r = c.get("/api/reading").json()
    assert r["time_known"] is False
    rising = next(x for x in r["cards"] if x["label"] == "Rising")
    assert rising["value"] == "Unknown" and rising["missing"] is True
    for card in r["cards"]:
        assert "house" not in card["sub"], card
    assert not any("house" in s["title"] for s in r["standout"])


def test_no_birth_time_drops_chart_ruler_and_angular(c):
    from engine import interpret as I
    from engine.chart import natal
    nat = natal(1999, 3, 14, 18.0, 41.8781, -87.6298)
    a = I.analyze(nat, time_known=False, birth_ymd=(1999, 3, 14))
    assert a["chart_ruler"] is None
    assert a["angular"] == [] and a["loaded_houses"] == []
    assert all(s["kind"] == "sign" for s in a["stelliums"])
    # aspects to the angles are meaningless without a time
    for t in a["tight_aspects"]:
        assert t["a"] not in ("Ascendant", "Midheaven")
        assert t["b"] not in ("Ascendant", "Midheaven")


def test_balance_ignores_the_angles_without_a_time(c):
    """The angles are weighted heavily because they carry a chart, which is
    exactly why a guessed one must not tilt the balance."""
    from engine import interpret as I
    from engine.chart import natal
    nat = natal(1999, 3, 14, 18.0, 41.8781, -87.6298)
    known = I.analyze(nat, time_known=True)
    blind = I.analyze(nat, time_known=False, birth_ymd=(1999, 3, 14))
    assert sum(known["element_counts"].values()) > sum(blind["element_counts"].values())


def test_starters_never_reference_houses_we_do_not_have(c):
    sign_in(c, "nt2@x.com"); _onboard_no_time(c)
    r = c.get("/api/reading").json()
    banned = ("time on my own", "lose myself in relationships",
              "weird about money", "work feel like my whole identity")
    assert not any(b in q for q in r["starters"] for b in banned)


def test_adding_a_birth_time_matches_signing_up_with_it(c):
    sign_in(c, "nt3@x.com"); _onboard_no_time(c)
    assert c.get("/api/reading").json()["time_known"] is False
    assert c.post("/api/birthtime", json={"time": "04:42"}).status_code == 200

    after = c.get("/api/reading").json()
    other = sign_in(TestClient(server.app), "nt4@x.com")
    onboard(other)
    baseline = other.get("/api/reading").json()

    assert after["time_known"] is True
    assert [x["value"] for x in after["cards"]] == [x["value"] for x in baseline["cards"]]
    assert after["birth"] == "1999-03-14 04:42 Chicago, IL, US"   # not a mangled string


def test_birth_time_is_validated(c):
    sign_in(c, "nt5@x.com"); _onboard_no_time(c)
    for bad in ("99:99", "nonsense", "", "25:00"):
        assert c.post("/api/birthtime", json={"time": bad}).status_code == 400


# ------------------------------------------------------------- birthplaces

def test_the_world_is_covered():
    """128 hand-written cities meant most people could not finish onboarding;
    34k (every place over 15,000 people) still lost most small towns."""
    from app import cities
    assert cities.count() > 200000
    assert len(cities.countries()) > 200


@pytest.mark.parametrize("q,expect", [
    ("thibodaux", "Thibodaux, LA, US"),
    ("ellijay", "Ellijay, GA, US"),
    ("winterset iowa", "Winterset, IA, US"),
    ("tok alaska", "Tok, AK, US"),
    ("truth or consequences", "Truth or Consequences, NM, US"),
])
def test_small_towns_are_there(q, expect):
    """Each of these was missing at 34k places."""
    from app import cities
    assert cities.search(q)[0]["label"] == expect


@pytest.mark.parametrize("q,expect", [
    ("chicago, il", "Chicago, IL, US"),
    ("chicago, illinois", "Chicago, IL, US"),      # abbreviation or full name
    ("new york, ny", "New York City, NY, US"),
    ("portland or", "Portland, OR, US"),           # disambiguates from Maine
    ("portland me", "Portland, ME, US"),
    ("sao paulo", "São Paulo, São Paulo, BR"),   # displays the local spelling
    ("lagos", "Lagos, Lagos, NG"),
    ("mumbai", "Mumbai, Maharashtra, IN"),
])
def test_search_finds_the_right_place(q, expect):
    from app import cities
    assert cities.search(q)[0]["label"] == expect


def test_search_is_accent_insensitive():
    """Someone born in Bogotá should not have to find the accent key."""
    from app import cities
    assert cities.search("bogota")[0]["country"] == "CO"
    assert cities.search("zurich")[0]["country"] == "CH"        # stored as Zürich
    assert cities.search("sao paulo")[0]["country"] == "BR"     # stored as São Paulo


@pytest.mark.parametrize("english,country", [
    ("cologne", "DE"), ("seville", "ES"), ("kiev", "UA"),
    ("bombay", "IN"), ("saigon", "VN"),
])
def test_english_exonyms_resolve(english, country):
    """GeoNames stores Köln, Sevilla, Kyiv. People type the English name."""
    from app import cities
    assert cities.search(english)[0]["country"] == country


def test_every_city_carries_a_timezone():
    """Without one, a birth time cannot become UT and every house is wrong."""
    from app import cities
    db = cities._db()
    assert db.execute("SELECT count(*) FROM places WHERE tz = '' OR instr(tz, '/') = 0 "
                      "AND tz != 'UTC'").fetchone()[0] == 0


def test_search_is_fast_enough_to_type_against():
    import time

    from app import cities
    cities.count()
    worst = 0
    for q in ("chicago, il", "new york, ny", "sao paulo", "springfield, il"):
        t = time.perf_counter()
        cities.search(q)
        worst = max(worst, (time.perf_counter() - t) * 1000)
    assert worst < 40, f"{worst:.0f}ms is visible lag in an autocomplete"


def test_onboarding_works_outside_the_united_states(c):
    sign_in(c, "intl@x.com")
    cid = c.get("/api/cities?q=lagos").json()[0]["id"]
    r = c.post("/api/onboard", json={"name": "Ada", "date": "1996-11-02",
                                     "time": "09:15", "city": cid})
    assert r.status_code == 200
    assert c.get("/api/reading").json()["birth"].endswith("Lagos, Lagos, NG")


def test_unknown_city_is_refused(c):
    """A well-formed id that does not exist must fail, not silently resolve to
    somewhere else — a chart for the wrong city is worse than an error."""
    sign_in(c, "nocity@x.com")
    r = c.post("/api/onboard", json={"name": "X", "date": "1990-01-01",
                                     "time": "12:00", "city": "Nowheresville|Nope|ZZ"})
    assert r.status_code == 400
def test_adding_a_birth_time_rescores_connections(c):
    """
    A score computed without houses was answering a different question. When
    the time arrives it gets redone, not left to age quietly.
    """
    sign_in(c, "rescore@x.com")
    _onboard_no_time(c)
    before = c.post("/api/connections", json={"name": "Theo", "date": "1997-11-03",
                                              "time": "08:15", "city": "Chicago IL"}).json()
    # Her houses don't exist yet; his do, so only his side's overlays count.
    assert before["confidence"] == "partial"
    assert not [o for o in before["overlays"] if o.startswith("their ")]

    r = c.post("/api/birthtime", json={"time": "04:42"}).json()
    assert r["rescored"] == 1

    after = c.get(f"/api/connections/{before['id']}").json()
    assert after["confidence"] == "full"
    assert after["overlays"], "house overlays should exist once there is a time"
    assert after["rescored_at"]


# ----------------------------------------------------- the thread, sharded

def test_thread_shards_by_month():
    from engine import thread
    from engine.store import load
    uid = "shard-a"
    thread.clear(uid)
    for month in ("2026-06", "2026-07", "2026-08"):
        thread.append(uid, {"id": month, "role": "user", "text": "x",
                            "at": f"{month}-05T09:00:00"})
    assert load(uid, "thread_shards", []) == ["thread-2026-06", "thread-2026-07", "thread-2026-08"]
    # one item per month, not one growing item
    assert len(load(uid, "thread-2026-06", [])) == 1
    assert load(uid, "thread", None) is None


def test_legacy_thread_is_migrated_not_lost():
    """
    Everything written before sharding is the memory the product is built on.
    Silently orphaning it would be the worst possible regression.
    """
    from engine import thread
    from engine.store import load, save
    uid = "shard-legacy"
    thread.clear(uid)
    save(uid, "thread", [
        {"id": "a", "role": "user", "text": "old", "at": "2026-06-01T09:00:00"},
        {"id": "b", "role": "user", "text": "newer", "at": "2026-08-01T09:00:00"},
    ])
    assert thread.migrate(uid) == 2
    assert load(uid, "thread", None) is None
    assert [m["id"] for m in thread.recent(uid)] == ["a", "b"]
    assert thread.dates(uid) == ["2026-06-01", "2026-08-01"]


def test_recent_reads_only_what_it_needs():
    """A two-year-old account should cost the same read as a two-day-old one."""
    import engine.store as store
    from engine import thread
    uid = "shard-cost"
    thread.clear(uid)
    for i in range(24):                      # two years of history
        month = f"20{25 + i // 12}-{i % 12 + 1:02d}"
        thread.append(uid, {"id": str(i), "role": "user", "text": "x",
                            "at": f"{month}-05T09:00:00"})

    reads = []
    real_load = store.load
    def counting(u, kind, default):
        reads.append(kind)
        return real_load(u, kind, default)
    thread_load = thread.load
    thread.load = counting
    try:
        got = thread.recent(uid, 1)
    finally:
        thread.load = thread_load
    assert len(got) == 1
    shard_reads = [r for r in reads if r.startswith("thread-2")]
    assert len(shard_reads) == 1, f"walked {len(shard_reads)} shards to get one message"


def test_state_does_not_return_the_whole_archive(c):
    from engine import thread
    sign_in(c, "cap@x.com"); onboard(c)
    uid = last_uid()
    for i in range(server.RECENT_MESSAGES + 25):
        thread.append(uid, {"id": str(i), "role": "user", "text": "x",
                            "at": f"2026-08-01T09:00:{i % 60:02d}"})
    body = c.get("/api/state").json()
    assert len(body["thread"]) == server.RECENT_MESSAGES


def test_deleting_an_account_removes_every_shard(c):
    from engine import thread
    from engine.store import load
    sign_in(c, "sharddel@x.com"); onboard(c)
    uid = last_uid()
    for month in ("2026-06", "2026-07"):
        thread.append(uid, {"id": month, "role": "user", "text": "x",
                            "at": f"{month}-05T09:00:00"})
    assert load(uid, "thread-2026-06", None)
    c.delete("/api/account")
    for k in ("thread_shards", "thread-2026-06", "thread-2026-07"):
        assert not load(uid, k, None), f"{k} survived deletion"


def test_rollover_still_finds_days_across_shards(c):
    from engine import thread
    sign_in(c, "shardroll@x.com"); onboard(c)
    uid = last_uid()
    thread.append(uid, {"id": "old", "role": "user", "text": "a while back",
                        "at": "2026-06-05T09:00:00"})
    written = server.rollover(uid)
    assert any(e["date"] == "2026-06-05" for e in written)


# ------------------------------------- ending a relationship is not a crisis

@pytest.mark.parametrize("msg", [
    "should i just end it with theo",
    "thinking about ending things with him",
    "should i call it off",
    "should i dump him",
    "should i walk away from him",
    "should i cut him off",
    "should i stay with him",
])
def test_breakups_are_amber_never_red(msg):
    """
    The red pattern used to match "ending things", so "I want to end things with
    my boyfriend" returned the 988 crisis reply. That is a false positive in the
    worst possible place: it alarms someone who is fine, and it teaches them the
    crisis message is noise for the day they are not.
    """
    assert safety.prefilter(msg) == "amber", f"{msg!r} misrouted"


@pytest.mark.parametrize("msg", [
    "i want to end it all",
    "i want to end my life",
    "i want to end things",          # no partner named — stays red
    "i want to kill myself",
    "i don't want to be here anymore",
])
def test_real_crisis_language_stays_red(msg):
    assert safety.prefilter(msg) == "red", f"{msg!r} lost its red tier"


# ------------------------------------------------------------- the voice eval

def test_the_voice_eval_passes():
    """
    Runs every scenario against the mechanical rubric.

    Without an API key this scores the demo replies, which is still worth
    gating: those are what anyone without a key sees, and the eval caught them
    claiming a 12th house on a day the context had none — the exact failure the
    engine exists to prevent.
    """
    from tools.eval_voice import run
    results = run()
    failed = [r["id"] for r in results if not r["passed"]]
    assert not failed, f"voice eval failures: {failed}"


def test_the_grounding_check_can_actually_fail():
    """A rubric that cannot fail is decoration."""
    from evals import rubric as R
    ctx = "t.Neptune conjunction n.Mercury — orb 0.99"
    ok, detail = R.grounded("Jupiter is squaring your Venus in the 5th house.", ctx)
    # Jupiter, Venus and the 5th house are all absent — and so is the claim
    # that Venus is in the 5th, which the pairing check reports on its own,
    # and the square, which the aspect check does.
    assert not ok and len(detail) == 5
    assert any("pairing" in d for d in detail)
    assert any(d.startswith("aspect not in context: Jupiter square Venus") for d in detail)
    ok, _ = R.grounded("Neptune is sitting on your Mercury.", ctx)
    assert ok


# ------------------------------------------------- secrets must not be fatal

@pytest.mark.parametrize("payload", [None, "", "not json", "[]", '"a string"'])
def test_an_unreadable_secret_does_not_take_the_app_down(monkeypatch, payload):
    """
    load_secrets runs at import, so anything it raises kills every route —
    including whatever would have told you why. The first real deploy died
    exactly here, on a freshly created secret holding a non-JSON placeholder.
    """
    from app import bootstrap

    class FakeClient:
        def get_secret_value(self, SecretId):
            if payload is None:
                raise RuntimeError("no version yet")
            return {"SecretString": payload}

    import sys
    import types
    fake_boto3 = types.SimpleNamespace(client=lambda name: FakeClient())
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setenv("MYSIGN_SECRET_ARN", "arn:aws:secretsmanager:x:1:secret:y")

    assert bootstrap.load_secrets() == []          # degraded, not dead


def test_a_mail_failure_is_not_a_500(monkeypatch):
    """
    The first live deploy returned a raw Internal Server Error on sign-in
    because the SES sender was unverified. That tells the person nothing and
    reads as a broken app rather than unconfigured mail.
    """
    from app import auth
    monkeypatch.setenv("MYSIGN_FROM_EMAIL", "noreply@example.com")
    monkeypatch.setattr(auth, "_send_email",
                        lambda to, code: (_ for _ in ()).throw(
                            RuntimeError("MessageRejected: not verified")))
    ok, payload = auth.request_code("someone@example.com")
    assert ok is False
    assert payload["status"] == 503
    assert "dev_code" not in payload          # still never leaks


# ------------------------------------------------------------------ logging

def test_a_message_body_never_reaches_a_log(c, capsys):
    """
    The commitment behind the consent screen. This application holds people's
    messages about their relationships and their worst days; none of it belongs
    in an operational log, and a convention would not hold — so the redaction is
    enforced in log.emit and asserted here.
    """
    secret_text = "zebra-marmalade-particular-phrase"
    sign_in(c, "logs@x.com"); onboard(c)
    capsys.readouterr()
    with c.stream("POST", "/api/message", json={"text": secret_text}) as r:
        "".join(r.iter_text())
    out = capsys.readouterr().out
    assert secret_text not in out, "the message body was logged"
    assert '"event": "message"' in out, "the message was not logged at all"
    assert '"tier"' in out and '"chars"' in out, "shape was not recorded"


def test_logs_are_one_json_object_per_line(c, capsys):
    import json as _json
    sign_in(c, "logfmt@x.com")
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.startswith("{")]
    assert lines, "nothing structured was logged"
    for line in lines:
        d = _json.loads(line)                      # must parse, every one
        assert {"ts", "level", "event", "service", "request_id"} <= set(d)


@pytest.mark.parametrize("field", ["text", "email", "code", "her_words",
                                   "api_key", "session_token", "summary"])
def test_sensitive_fields_are_redacted(field, capsys):
    from app import log
    log.emit("probe", **{field: "SENSITIVE-VALUE-HERE"})
    out = capsys.readouterr().out
    assert "SENSITIVE-VALUE-HERE" not in out, f"{field} leaked"
    assert "redacted" in out


def test_a_failed_request_returns_an_id_to_quote(c):
    """An error is useless to support without something to correlate on."""
    from app import server as srv
    orig = srv._profile
    srv._profile = lambda uid: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        sign_in(c, "boom@x.com")
        r = c.get("/api/today")
        assert r.status_code == 500
        assert r.json().get("request_id"), "no request id in the error body"
    finally:
        srv._profile = orig


def test_the_voice_eval_does_not_depend_on_todays_sky():
    """
    It used to. The eval scored against dt.date.today(), so the salience band
    drifted overnight and the sentence cap moved with it — a reply that passed
    on a high-salience day failed on a medium one with nothing about the product
    having changed. A test that depends on the date teaches people to ignore red.
    """
    import datetime as dt

    from evals.cases import EVAL_DAY
    from tools import eval_voice

    assert isinstance(EVAL_DAY, dt.date)
    a = eval_voice.run()
    b = eval_voice.run(EVAL_DAY)
    assert [r["band"] for r in a] == [r["band"] for r in b]
    assert all(r["passed"] for r in a), "pinned run is not clean"


def test_a_demo_reply_respects_the_cap_it_was_given():
    """The cap comes from the day's salience, so a reply cannot assume one.
    The v2 voice may add one question on top of it, and no more."""
    from app.server import _demo_reply
    from engine import voice
    from evals import rubric as R
    extra = 0 if voice.VOICE == "v1" else 1
    for cap in (2, 3, 4):
        verdict = {"band": "medium", "max_sentences": cap,
                   "headlines": [{"transit": "Saturn", "aspect": "square",
                                  "natal": "Moon", "orb": 1.2}]}
        for msg in ("should i end it with him", "should i answer him",
                    "why do i feel like this"):
            reply = _demo_reply(msg, verdict, "")
            assert R.within_length(reply, cap + extra), \
                f"cap {cap}, {len(R.sentences(reply))} sentences: {msg!r}"


# ------------------------------------------- rectification endpoints

def test_event_kinds_are_listed(c):
    """
    This endpoint was 500ing for a while because an import got dropped and
    nothing covered it. 175 tests passed over a completely broken route.
    """
    sign_in(c, "ev1@x.com"); onboard(c)
    r = c.get("/api/events")
    assert r.status_code == 200
    body = r.json()
    assert body["kinds"], "no event kinds offered"
    assert all({"id", "about"} <= set(k) for k in body["kinds"])


def test_events_round_trip(c):
    sign_in(c, "ev2@x.com"); onboard(c)
    assert c.post("/api/events", json={"kind": "moved",
                                       "date": "2017-08-20"}).status_code == 200
    assert c.post("/api/events", json={"kind": "not-a-kind",
                                       "date": "2017-08-20"}).status_code == 400
    assert len(c.get("/api/events").json()["events"]) == 1


def test_rectify_needs_enough_events_before_it_guesses(c):
    sign_in(c, "ev3@x.com"); onboard(c, time="")
    r = c.post("/api/rectify", json={}).json()
    assert r["ok"] is False and "three" in r["reason"].lower()


def test_rectify_runs_once_there_are_enough(c):
    sign_in(c, "ev4@x.com"); onboard(c, time="")
    for kind, date in [("moved", "2017-08-20"), ("career", "2023-06-02"),
                       ("relationship", "2021-03-11"), ("breakup", "2024-11-15")]:
        c.post("/api/events", json={"kind": kind, "date": date})
    r = c.post("/api/rectify", json={}).json()
    # It may honestly report it learned nothing — that is a pass. What it must
    # not do is 500, or claim confidence it has not earned.
    assert "ok" in r
    if r["ok"]:
        assert r["confidence"] in ("weak", "rough", "worth trying")
        assert r["z"] >= 3.0


# ------------------------------------------ the endpoints nothing was hitting

def test_signout_clears_the_session(c):
    sign_in(c, "so@x.com"); onboard(c)
    assert c.get("/api/state").json()["signed_in"] is True
    assert c.post("/api/auth/signout").status_code == 200
    body = c.get("/api/state").json()
    assert body["signed_in"] is False and "thread" not in body


def test_the_page_is_served(c):
    r = c.get("/app")
    assert r.status_code == 200
    assert "My Sign" in r.text
    assert "text/html" in r.headers["content-type"]


def test_a_test_push_needs_consent_and_reports_devices(c, monkeypatch):
    from app import push
    sent = []
    monkeypatch.setattr(push, "send",
                        lambda u, t, b, url="/", **_: sent.append(u) or {"sent": 1, "pruned": 0})
    assert TestClient(server.app).post("/api/push/test").status_code == 401
    sign_in(c, "pt@x.com"); onboard(c)
    r = c.post("/api/push/test")
    assert r.status_code == 200 and r.json()["sent"] == 1


# ------------------------------------------------------- crisis, localized

def test_crisis_reply_matches_where_she_lives(c):
    """988 is a US number. A woman in Manchester was being told to text it."""
    from engine import crisis
    assert "988" in crisis.reply_for("US")
    gb = crisis.reply_for("GB")
    assert "116 123" in gb and "988" not in gb
    assert "Samaritans" in gb


def test_unknown_country_gets_a_directory_not_a_wrong_number(c):
    from engine import crisis
    r = crisis.reply_for("XX")
    assert "findahelpline" in r
    assert "directory" in r.lower()


def test_every_listed_line_has_a_number_or_short_code():
    import re

    from engine import crisis
    for cc, (name, how, extra) in crisis.LINES.items():
        assert re.search(r"\d", how), f"{cc}: {how!r} has no number"


def test_the_ai_provider_safety_reads_and_age_are_on_the_consent_screen(c):
    """Apple 5.1.2(i): the AI provider is named and agreed to explicitly."""
    html = c.get("/app").text
    assert "Anthropic" in html and 'id="aiOk"' in html
    assert "a person is alerted" in html
    assert "18 or over" in html
    assert 'href="/privacy"' in html and 'href="/terms"' in html


def test_legal_pages_are_served_and_specific(c):
    pv = c.get("/privacy")
    assert pv.status_code == 200
    for must in ("Anthropic", "35 days", "18 and over", "GeoNames", "Swiss Ephemeris",
                 "never the content"):
        assert must in pv.text, f"privacy page missing: {must}"
    tm = c.get("/terms")
    assert tm.status_code == 200
    for must in ("not prediction", "Not a crisis service", "18 or over", "Refunds"):
        assert must in tm.text, f"terms page missing: {must}"


def test_onboarding_stores_country_and_crisis_uses_it(c, monkeypatch):
    from engine.store import load
    sign_in(c, "gb@x.com")
    r = c.post("/api/onboard", json={"name": "Amara", "date": "1999-03-14",
                                     "time": "04:42", "city": "manchester, gb"})
    assert r.status_code == 200, r.text
    uid = last_uid()
    assert load(uid, "profile", {})["country"] == "GB"
    with c.stream("POST", "/api/message", json={"text": "i want to kill myself"}) as s:
        body = "".join(s.iter_text())
    text = _streamed_text(body)
    assert "116 123" in text and "988" not in text


def test_she_can_say_where_she_is_now(c):
    sign_in(c, "moved@x.com"); onboard(c)          # born in Chicago
    assert c.get("/api/state").json()["country"] == "US"
    r = c.post("/api/profile/country", json={"country": "gb"})
    assert r.json()["country"] == "GB" and r.json()["crisis_line_known"] is True
    assert "crisis_footer" in r.json()        # so the footer can change at once
    assert c.post("/api/profile/country", json={"country": "britain"}).status_code == 400


# ------------------------------------------------------------------ age gate

def test_under_18_cannot_onboard(c):
    import datetime as dt
    sign_in(c, "young@x.com")
    born = (dt.date.today() - dt.timedelta(days=17 * 365 + 200)).isoformat()
    r = c.post("/api/onboard", json={"name": "K", "date": born, "time": "04:42",
                                     "city": "chicago, il"})
    assert r.status_code == 403
    assert "18 and over" in r.json()["detail"]


def test_exactly_18_today_can_onboard(c):
    import datetime as dt
    sign_in(c, "eighteen@x.com")
    t = dt.date.today()
    born = t.replace(year=t.year - 18).isoformat()
    r = c.post("/api/onboard", json={"name": "K", "date": born, "time": "04:42",
                                     "city": "chicago, il"})
    assert r.status_code == 200, r.text


def test_the_age_refusal_logs_no_birth_date(c, capsys):
    import datetime as dt
    sign_in(c, "youngl@x.com"); capsys.readouterr()
    born = (dt.date.today() - dt.timedelta(days=16 * 365)).isoformat()
    c.post("/api/onboard", json={"name": "K", "date": born, "time": "04:42",
                                 "city": "chicago, il"})
    out = capsys.readouterr().out
    assert "onboard.underage" in out and born not in out


# ------------------------------------------------------------ pilot report

def test_pilot_report_recovers_a_planted_signal(tmp_path, monkeypatch):
    """Seed ten users with a real callback effect and 7/10 retention; the
    report must find both and return GO."""
    import datetime as dt
    import random

    from engine.store import save
    from tools.pilot_report import CRITERIA, run
    rng = random.Random(1)
    start = dt.date(2026, 9, 1)
    uids = [f"rep{i}" for i in range(10)]
    for i, uid in enumerate(uids):
        metrics, eps = [], []
        for d in range(14 if i < 7 else 4):
            date = (start + dt.timedelta(days=d)).isoformat()
            eps.append({"date": date, "mood": 5, "summary": "x"})
            cb = d >= 6 and rng.random() < .5
            metrics.append({"msg_id": f"{uid}{d}", "sent_at": f"{date}T09:00:00",
                            "has_callback": cb, "tier": "green",
                            "edit_distance": (0.30 if d < 7 else 0.10) * rng.random() * 1.6,
                            "reply": {"latency_s": rng.randint(60, 300) * (0.6 if cb else 1.0),
                                      "words": int(rng.randint(8, 30) * (1.4 if cb else 1.0))}})
        save(uid, "metrics", metrics); save(uid, "episodes", eps)
    text, verdicts = run(uids)
    assert "GO" in text and "NO-GO" not in text
    assert verdicts["d7_retention"] is True
    assert set(verdicts) >= {"callback_latency_ratio", "callback_length_ratio",
                             "edit_distance_week2", "d7_retention"}
    # the criteria are the contract with docs/pilot.md
    assert CRITERIA["d7_retention"] == 0.60


def test_pilot_report_says_the_transcripts_decide():
    from tools.pilot_report import run
    text, _ = run([])
    assert "transcripts decide" in text.lower() or "transcripts" in text.lower()


def test_signin_email_has_text_and_html():
    from app.auth import EMAIL_HTML
    h = EMAIL_HTML.format(code="483920")
    assert "483920" in h
    assert "{code}" not in h and "{" not in h.replace("{code}", "")


# --------------------------------------------------- plan and the free tier

def _send(c, text):
    with c.stream("POST", "/api/message", json={"text": text}) as r:
        return r.status_code, "".join(r.iter_text())


def test_free_plan_allows_three_then_gates_kindly(c):
    from engine import plan
    sign_in(c, "free3@x.com"); onboard(c)
    for i in range(plan.FREE_MESSAGES_PER_DAY):
        code, _ = _send(c, f"question {i}")
        assert code == 200, f"message {i} refused"
    code, body = _send(c, "one more")
    assert code == 402
    # kind, and leads with what stays free
    assert "reading" in body.lower() and "check-in" in body.lower()
    assert "upgrade" not in body.lower()


def test_the_reading_and_checkin_are_never_gated(c):
    """Paywall the conversation, never the ritual."""
    from engine import plan
    sign_in(c, "ritual@x.com"); onboard(c)
    for i in range(plan.FREE_MESSAGES_PER_DAY + 1):
        _send(c, f"q{i}")
    assert c.get("/api/reading").status_code == 200
    assert c.get("/api/today").status_code == 200
    assert c.post("/api/checkin", json={"mood": 5, "text": ""}).status_code == 200


def test_a_crisis_is_a_crisis_on_the_free_plan(c):
    """Safety runs before the plan gate. The fourth message can still be red."""
    from engine import plan
    sign_in(c, "freecrisis@x.com"); onboard(c)
    for i in range(plan.FREE_MESSAGES_PER_DAY):
        _send(c, f"q{i}")
    code, body = _send(c, "i want to kill myself")
    assert code == 200
    assert "988" in _streamed_text(body)


def test_plus_is_unlimited_and_lapses(c):
    from engine import plan
    sign_in(c, "plus@x.com"); onboard(c)
    uid = last_uid()
    plan.grant(uid, "plus", source="test", until="2099-01-01")
    for i in range(plan.FREE_MESSAGES_PER_DAY + 2):
        assert _send(c, f"q{i}")[0] == 200
    plan.grant(uid, "plus", source="test", until="2000-01-01")     # lapsed
    assert plan.get(uid)["tier"] == "free"


def test_state_exposes_the_plan(c):
    sign_in(c, "planstate@x.com"); onboard(c)
    p = c.get("/api/state").json()["plan"]
    assert p["tier"] == "free" and p["free_per_day"] == 3 and p["used_today"] == 0


def test_operator_can_grant_plus(c, monkeypatch):
    monkeypatch.setenv("MYSIGN_OPS_TOKEN", "t0k")
    sign_in(c, "grantee@x.com"); onboard(c)
    uid = last_uid()
    r = c.post("/api/ops/plan", json={"uid": uid, "tier": "plus"},
               headers={"x-ops-token": "t0k"})
    assert r.status_code == 200, r.text
    assert c.get("/api/state").json()["plan"]["tier"] == "plus"
    assert c.post("/api/ops/plan", json={"uid": uid, "tier": "gold"},
                  headers={"x-ops-token": "t0k"}).status_code == 400


def test_operator_can_grant_plus_by_email_and_never_to_a_made_up_id(c, monkeypatch):
    monkeypatch.setenv("MYSIGN_OPS_TOKEN", "t0k")
    sign_in(c, "Grantee.Email@x.com"); onboard(c)
    h = {"x-ops-token": "t0k"}
    # her sign-in email, any case, finds her account
    r = c.post("/api/ops/plan", json={"uid": " grantee.email@X.com ", "tier": "plus"}, headers=h)
    assert r.status_code == 200, r.text
    assert c.get("/api/state").json()["plan"]["tier"] == "plus"
    # an email nobody signs in with, or an id with no account, is refused and writes nothing
    assert c.post("/api/ops/plan", json={"uid": "nobody@x.com", "tier": "plus"}, headers=h).status_code == 404
    assert c.post("/api/ops/plan", json={"uid": "u000000000000", "tier": "plus"}, headers=h).status_code == 404
    from engine import plan
    assert plan.get("u000000000000")["tier"] == "free"


# --------------------------------------------------------------- the funnel

def test_funnel_counts_each_step_once_per_person(c, monkeypatch):
    from engine import funnel
    monkeypatch.setenv("MYSIGN_OPS_TOKEN", "t0k")
    before = funnel.report(1)["totals"]
    sign_in(c, "fun1@x.com"); onboard(c)
    c.get("/api/state"); c.get("/api/state")            # repeated reads don't double count
    _send(c, "hello")
    after = funnel.report(1)["totals"]
    for step in ("signed_up", "consented", "onboarded", "first_message"):
        assert after[step] - before[step] == 1, step
    r = c.get("/api/ops/funnel", headers={"x-ops-token": "t0k"})
    assert r.status_code == 200 and "conversion_from_signup" in r.json()


def test_funnel_holds_no_identities():
    from engine import funnel
    from engine.store import load
    funnel.hit("someone-private", "signed_up")
    import datetime as dt
    bucket = load("_study", f"funnel-{dt.date.today().isoformat()}", {})
    assert "someone-private" not in str(bucket)
    assert all(isinstance(v, int) for v in bucket.values())


# ------------------------------------------------------------------ cards

def test_share_cards_render_and_escape(c):
    sign_in(c, "card@x.com")
    c.post("/api/onboard", json={"name": "<Maya & co>", "date": "1999-03-14",
                                 "time": "04:42", "city": "chicago, il"})
    r = c.get("/api/card/chart.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in r.text and "Pisces" in r.text and "Aquarius" in r.text
    assert "&lt;Maya &amp; co&gt;" in r.text and "<Maya" not in r.text
    t = c.get("/api/card/today.svg")
    assert t.status_code == 200 and "<svg" in t.text


def test_cards_need_a_session():
    fresh = TestClient(server.app)
    assert fresh.get("/api/card/chart.svg").status_code == 401


def test_today_carries_meanings_aligned_with_headlines(c):
    sign_in(c, "mean@x.com"); onboard(c)
    t = c.get("/api/today").json()
    assert len(t["meanings"]) == len(t["headlines"])
    if t["headlines"]:
        # Each meaning is about her life (the planets ride along as the
        # headline beside it), and never an empty placeholder.
        planets = {"Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn",
                   "Uranus", "Neptune", "Pluto", "Chiron", "The"}
        assert all(m and m.split()[0] not in planets for m in t["meanings"])


def test_consent_requires_the_ai_agreement(c):
    code = c.post("/api/auth/request", json={"email": "noai@x.com"}).json()["dev_code"]
    c.post("/api/auth/verify", json={"email": "noai@x.com", "code": code})
    assert c.post("/api/consent").status_code == 400
    assert c.post("/api/consent", json={"ai": False}).status_code == 400
    assert c.get("/api/state").json()["consented"] is False
    assert c.post("/api/consent", json={"ai": True}).status_code == 200
    assert c.get("/api/state").json()["consented"] is True


def test_an_old_consent_is_asked_again(c):
    """A changed consent text is shown once to everyone, including existing users."""
    from app import auth
    from engine.store import load, save
    sign_in(c, "oldconsent@x.com")
    uid = last_uid()
    acct = load(uid, "account", {})
    acct.pop("consent_version")
    save(uid, "account", acct)
    assert c.get("/api/state").json()["consented"] is False
    assert c.get("/api/today").status_code == 403
    del auth


def test_a_reply_can_be_reported(c):
    from engine.store import load
    sign_in(c, "report@x.com"); onboard(c)
    uid = last_uid()
    with c.stream("POST", "/api/message", json={"text": "hi"}) as r:
        body = "".join(r.iter_text())
    import json as _json
    import re as _re
    done = _json.loads(_re.search(r"event: done\ndata: (.+)", body).group(1))
    assert done["id"]
    assert c.post("/api/report", json={"id": done["id"], "reason": "made up a house"}).status_code == 200
    assert load(uid, "reports", [])[0]["reason"] == "made up a house"
    assert c.post("/api/report", json={"id": "nope"}).status_code == 404


def test_the_native_app_gets_a_bearer_token_and_the_web_does_not(c):
    from fastapi.testclient import TestClient
    code = c.post("/api/auth/request", json={"email": "ios@x.com"}).json()["dev_code"]
    app_resp = c.post("/api/auth/verify", json={"email": "ios@x.com", "code": code},
                      headers={"x-client": "ios-app"}).json()
    assert app_resp["token"]
    fresh = TestClient(server.app)
    assert fresh.get("/api/state", headers={"authorization": f"Bearer {app_resp['token']}"}
                     ).json()["signed_in"] is True
    code = c.post("/api/auth/request", json={"email": "web@x.com"}).json()["dev_code"]
    assert "token" not in c.post("/api/auth/verify", json={"email": "web@x.com", "code": code}).json()


def test_the_review_account_uses_its_fixed_code_and_never_pages(c, monkeypatch):
    from app import alerts, auth
    monkeypatch.setenv("MYSIGN_REVIEW_EMAIL", "review@mysignapp.com")
    monkeypatch.setenv("MYSIGN_REVIEW_CODE", "48213907")
    r = c.post("/api/auth/request", json={"email": "Review@MySignApp.com"}).json()
    assert r == {"sent": True, "channel": "email"}, "no code may be echoed or emailed for the review account"
    assert c.post("/api/auth/verify", json={"email": "review@mysignapp.com", "code": "000000"}).status_code == 401
    ok = c.post("/api/auth/verify", json={"email": "review@mysignapp.com", "code": "48213907"})
    assert ok.status_code == 200
    paged = []
    monkeypatch.setattr(alerts, "_send", lambda payload: paged.append(payload) or True)
    assert alerts.red_tier(auth.review_uid(), "test") is False and not paged


# ------------------------------------------------------------------ accuracy

def test_the_accuracy_evidence_is_public(c):
    """No sign-in: it is a marketing page, and its numbers are the server's own."""
    r = c.get("/api/accuracy")
    assert r.status_code == 200
    d = r.json()
    assert d["nasa"]["passes"] and d["nasa"]["max_arcsec"] < 1.0
    assert d["ephemeris"]["full_precision"]
    assert d["places"] > 30000
    assert d["time_zones"].startswith("IANA")
    page = c.get("/accuracy")
    assert page.status_code == 200 and "to the arcsecond" in page.text


def test_a_birth_in_the_repeated_hour_is_flagged_and_briefed(c):
    """01:30 on 28 Oct 2001 happened twice in Chicago. The chart is drawn from
    the first, the profile says so, and the model is told what the second
    would change."""
    sign_in(c, "fold@b.com")
    r = c.post("/api/onboard", json={"name": "Ana", "date": "2001-10-28",
                                     "time": "01:30", "city": "Chicago IL"})
    assert r.status_code == 200, r.text
    note = r.json()["birth_time_note"]
    assert note and "happened twice" in note and "rising sign is" in note
    from engine import context
    from engine.store import load
    prof = load(last_uid(), "profile", {})
    lines = context.chart_lines(server._natal(last_uid()), prof)
    assert any(line.startswith("  BIRTH TIME NOTE:") for line in lines)


def test_the_briefing_carries_her_whole_chart_and_the_days_signs(c):
    sign_in(c, "brief@b.com")
    onboard(c)
    uid = last_uid()
    block, _ = server.context.build(uid, server._natal(uid), server._sky(uid), {})
    assert "HER CHART (computed — Swiss Ephemeris" in block
    assert "NASA JPL DE431" in block
    assert "  Chiron " in block and "  Ascendant " in block
    assert any(w in block for w in ("  applying", "  separating"))
    assert "  t.Sun " in block


def test_a_stale_city_index_rebuilds_itself(tmp_path, monkeypatch):
    """The index stores folded names; when folding changes and the data file
    doesn't, an old index must not keep answering ("St. Louis" stopped
    resolving that way once)."""
    import sqlite3

    from app import cities
    db = tmp_path / "c.sqlite"
    cities.build_db(dst=db)
    assert cities._current(db)
    con = sqlite3.connect(db)
    con.execute("PRAGMA user_version = 1")
    con.commit(); con.close()
    assert not cities._current(db)


def test_a_cached_reading_still_gets_todays_wording(c):
    """Only the model's writing is cached. The computed text (what stands out,
    the planet links) is rebuilt on every read, so a rewrite of that text
    reaches people who already had a reading — and the model's opening is
    never thrown away."""
    sign_in(c, "fresh-copy@x.com"); onboard(c)
    first = c.get("/api/reading").json()
    uid = last_uid()
    from engine.store import load, save
    houses, rulers = server._tradition(uid)
    key = f"reading-{houses}-{rulers}"
    stale = load(uid, key, None)
    assert stale, "the reading was not cached"
    stale["standout"] = [{"title": "Mars in Scorpio — strongly placed", "body": "old"}]
    stale["opening"] = "the model's own opening"
    save(uid, key, stale)
    again = c.get("/api/reading").json()
    assert again["standout"] == first["standout"]
    assert again["opening"] == "the model's own opening"


def test_an_authored_profile_gets_todays_wording_and_a_written_one_is_kept(c):
    """'Who you are' is the model's writing only when it wrote one. Otherwise it
    is authored text, and a fix to it must reach people who already have it."""
    sign_in(c, "fresh-profile@x.com"); onboard(c)
    first = c.get("/api/reading").json()
    uid = last_uid()
    from engine.store import load, save
    houses, rulers = server._tradition(uid)
    key = f"reading-{houses}-{rulers}"
    stale = load(uid, key, None)
    stale["opening_generated"] = False
    stale["profile"] = {**stale["profile"], "tagline": "an old authored line"}
    save(uid, key, stale)
    assert c.get("/api/reading").json()["profile"] == first["profile"]
    stale["opening_generated"] = True
    stale["profile"] = {**stale["profile"], "tagline": "the model's own line"}
    save(uid, key, stale)
    assert c.get("/api/reading").json()["profile"]["tagline"] == "the model's own line"


def test_reading_wheel_carries_what_each_chapter_says(c):
    """The chapter reading: every planet has a line from the corpus and, with a
    birth time, a house; every drawn line has a sentence; today's slow planets
    are listed with a real orb."""
    sign_in(c, "wheel5@x.com"); onboard(c)
    w = c.get("/api/reading").json()["wheel"]
    assert all(p["say"].startswith("Your ") for p in w["planets"])
    assert all(1 <= p["house"] <= 12 and p["arena"] for p in w["planets"])
    assert all(a["say"] for a in w["aspects"])
    assert all("Ascendant" in (a["a"], a["b"]) for a in w["asc_aspects"])
    assert all(n["orb"] <= 4 and n["verb"] and n["transit"] in ("Mars", "Jupiter", "Saturn",
               "Uranus", "Neptune", "Pluto") for n in w["now"])


def test_chapters_without_a_birth_time_claim_no_houses(c):
    sign_in(c, "wheel6@x.com"); onboard(c, time="")
    w = c.get("/api/reading").json()["wheel"]
    assert all("house" not in p and p["say"] for p in w["planets"])
    assert "asc_aspects" not in w
    assert all(n["natal"] not in ("Moon", "Ascendant") for n in w["now"])


def test_rising_sign_lines_each_come_with_a_sentence(c):
    sign_in(c, "wheel7@x.com"); onboard(c)
    w = c.get("/api/reading").json()["wheel"]
    assert all(a["say"] for a in w["asc_aspects"])
