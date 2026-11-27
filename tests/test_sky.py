"""
Sky events: every date from the ephemeris, every personal claim from geometry,
calm copy, and a big day that costs a cache read.

The reference points are external: NASA's eclipse canon (engine/reference/
eclipses.json, the file engine/accuracy.py already holds us to) and the
published 2026 Mercury retrograde table.
"""
import datetime as dt
import json
import os
import pathlib
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import swisseph as swe
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-sky-"))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

from app import server, skypush
from engine import skyevents as S
from engine.chart import natal
from engine.ephemeris import FLAGS
from engine.store import load, save
from tests.test_app import last_uid, onboard, sign_in

ECLIPSES = pathlib.Path(__file__).parent.parent / "engine" / "reference" / "eclipses.json"
UTC = dt.timezone.utc


def _jd(iso):
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return swe.julday(t.year, t.month, t.day, t.hour + t.minute / 60 + t.second / 3600)


@pytest.fixture
def c():
    return TestClient(server.app)


def _user(c, email, time_="04:42", people=()):
    sign_in(c, email)
    assert onboard(c, time=time_).status_code == 200
    for name, date, t, city in people:
        r = c.post("/api/connections", json={"name": name, "relation": "friend",
                                              "date": date, "time": t, "city": city})
        assert r.status_code == 200, r.text
    return last_uid()


# ------------------------------------------------------------------ the sky

def test_eclipses_match_nasa_within_two_minutes():
    ref = json.loads(ECLIPSES.read_text())
    for kind, rows in (("solar_eclipse", ref["solar"]), ("lunar_eclipse", ref["lunar"])):
        for row in rows:
            nasa = _jd(row["ut"].replace(" ", "T") + "Z")
            found = [e for e in S.eclipses(nasa - 10, nasa + 10) if e["kind"] == kind]
            assert found, row
            assert min(abs(e["jd"] - nasa) for e in found) * 86400 < 120, row


# Published 2026 Mercury retrograde (UTC dates): Feb 26 - Mar 20, Jun 29 -
# Jul 23, Oct 24 - Nov 13.
MERCURY_2026 = [("station_rx", "2026-02-26"), ("station_d", "2026-03-20"),
                ("station_rx", "2026-06-29"), ("station_d", "2026-07-23"),
                ("station_rx", "2026-10-24"), ("station_d", "2026-11-13")]


def test_mercury_stations_2026_match_the_published_table():
    j0 = swe.julday(2026, 1, 1, 0)
    got = [(s["kind"], S._ut(s["jd"]).date().isoformat())
           for s in S.stations(j0, j0 + 365) if s["body"] == "Mercury"]
    assert got == MERCURY_2026


def test_a_station_is_where_the_speed_is_zero():
    j0 = swe.julday(2026, 1, 1, 0)
    for s in S.stations(j0, j0 + 365):
        body = dict(S.STATION_BODIES)[s["body"]]
        assert abs(swe.calc_ut(s["jd"], body, FLAGS)[0][3]) < 1e-4


def test_shadows_come_from_the_station_degrees_not_a_table():
    j0 = swe.julday(2026, 1, 1, 0)
    cycles = S.retro_cycles(j0, j0 + 365)
    assert {c["body"] for c in cycles} >= {"Mercury", "Venus"}
    for cyc in cycles:
        body = dict(S.STATION_BODIES)[cyc["body"]]
        pre = swe.calc_ut(cyc["pre_shadow_jd"], body, FLAGS)[0][0]
        post = swe.calc_ut(cyc["post_shadow_jd"], body, FLAGS)[0][0]
        assert abs(S._wrap(pre - cyc["d"]["lon"])) < 0.01
        assert abs(S._wrap(post - cyc["rx"]["lon"])) < 0.01
        assert cyc["pre_shadow_jd"] < cyc["rx"]["jd"] < cyc["d"]["jd"] < cyc["post_shadow_jd"]


def test_lunations_are_exact_and_eclipses_absorb_their_twin():
    evs, _ = S.sky_events("2026-01-01", 365)
    for e in evs:
        if e["kind"] in ("new_moon", "full_moon"):
            el = (swe.calc_ut(e["jd"], swe.MOON, FLAGS)[0][0]
                  - swe.calc_ut(e["jd"], swe.SUN, FLAGS)[0][0]) % 360
            target = 0 if e["kind"] == "new_moon" else 180
            assert abs(S._wrap(el - target)) < 0.01
    feb = [e for e in evs if e["id"].endswith("20260217T1211") or "20260217" in e["id"]]
    assert [e["kind"] for e in feb] == ["solar_eclipse"]
    assert feb[0]["lunation"] == "new moon" and feb[0]["type"] == "annular"


def test_every_listed_degree_is_the_ephemeris_at_that_instant():
    evs, _ = S.sky_events("2026-01-01", 365)
    ids = {"Mercury": swe.MERCURY, "Venus": swe.VENUS, "Mars": swe.MARS,
           "Sun": swe.SUN, "Moon": swe.MOON}
    for e in evs:
        if e["kind"] == "ingress":
            assert abs(S._wrap(swe.calc_ut(e["jd"], swe.SUN, FLAGS)[0][0] - round(e["lon"]))) < 0.001
            continue
        lon = swe.calc_ut(e["jd"], ids[e["body"]], FLAGS)[0][0]
        assert abs(S._wrap(lon - e["lon"])) < 0.001, e["id"]


# ----------------------------------------------------------------- endpoint

def test_endpoint_shape_kinds_and_personal_fields(c):
    uid = _user(c, "sky-shape@x.test", people=[("Theo", "1996-07-02", "21:15", "London, GB")])
    r = c.get("/api/sky/events?days=365")
    assert r.status_code == 200
    assert "private" in r.headers["cache-control"]
    d = r.json()
    kinds = {e["kind"] for e in d["events"]}
    assert {"station_rx", "station_d", "new_moon", "full_moon", "ingress"} <= kinds
    assert kinds & {"solar_eclipse", "lunar_eclipse"}
    rows = {r_["id"]: r_ for r_ in load(uid, "connections", [])}
    nat = server._natal(uid)
    for e in d["events"]:
        assert e["utc"].endswith("Z") and e["sign"] and 0 <= e["deg"] < 30
        assert e["her_house"] in range(1, 13)
        assert e["her_house"] == server.house_of(e["lon"], nat["house_cusps"])
        for t in e["touches"]:
            assert t["orb"] <= S.ORB
        for p in e["people"]:
            # Recompute from the person's own chart: no claimed contact without geometry.
            ch = natal(*rows[p["cid"]]["birth_ut"])
            pts = S.natal_points(ch, rows[p["cid"]].get("birth_time_known"))
            for h in p["points"]:
                ang = dict(S.ASPECT_ANGLES)[h["aspect"]]
                assert abs(abs(S._wrap(e["lon"] - pts[h["point"]])) - ang) <= S.ORB


def test_needs_sign_in_and_a_chart(c):
    assert c.get("/api/sky/events").status_code == 401
    sign_in(c, "sky-nochart@x.test")
    assert c.get("/api/sky/events").status_code == 400


def test_unknown_birth_time_gives_no_houses_moon_or_angles(c):
    _user(c, "sky-notime@x.test", time_="")
    d = c.get("/api/sky/events?days=120").json()
    assert d["time_known"] is False and d["houses_note"]
    for e in d["events"]:
        assert e["her_house"] is None
        assert not {t["point"] for t in e["touches"]} & {"Moon", "Ascendant", "Midheaven"}
        assert "house" not in e["reflect"]


def test_mercury_retrograde_is_active_with_day_counts():
    c = TestClient(server.app)
    uid = _user(c, "sky-active2@x.test", people=[("Theo", "1996-07-02", "21:15", "London, GB")])
    now = dt.datetime(2026, 10, 28, 17, 0, tzinfo=UTC)
    d = server._sky_events_for(uid, 60, now=now)
    merc = next(a for a in d["active"] if a["body"] == "Mercury")
    # Oct 24 02:12 CDT to Nov 13 09:53 CST, counted in her zone: day 5 of 21.
    assert (merc["phase"], merc["day"], merc["of"]) == ("retrograde", 5, 21)
    # What it means for her first, then the planet and the count.
    assert d["banner"]["text"].startswith("A good week to re-read")
    assert "Mercury is retrograde, day 5 of 21" in d["banner"]["text"]
    assert d["tint"] == "retrograde"
    venus = next(a for a in d["active"] if a["body"] == "Venus")
    assert venus["phase"] == "retrograde"
    assert d["next_big"]["kind"] in S.BIG and d["next_big"]["utc"] > "2026-10-28T17"


def test_cached_per_day_and_a_new_birth_time_misses(c, monkeypatch):
    _user(c, "sky-cache@x.test", time_="")
    first = c.get("/api/sky/events").json()
    calls = []
    real = S.personalise
    monkeypatch.setattr(S, "personalise", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert c.get("/api/sky/events").json() == first and not calls
    assert c.post("/api/birthtime", json={"time": "04:42"}).status_code == 200
    again = c.get("/api/sky/events").json()
    assert calls and again["time_known"] is True
    assert all(e["her_house"] for e in again["events"])


def test_check_in_notes_and_moods_never_reach_the_payload(c):
    _user(c, "sky-priv@x.test", people=[("Theo", "1996-07-02", "21:15", "London, GB")])
    cid = c.get("/api/connections").json()["connections"][0]["id"]
    c.post(f"/api/connections/{cid}/checkin", json={"feel": "strained", "note": "SECRET-NOTE-42"})
    c.post("/api/checkin", json={"text": "MY-PRIVATE-DAY", "mood": 3})
    body = c.get("/api/sky/events?days=365").text
    assert "SECRET-NOTE-42" not in body and "MY-PRIVATE-DAY" not in body
    assert '"mood"' not in body and "strained" not in body


def test_copy_is_calm_across_a_whole_year(c):
    _user(c, "sky-lint@x.test", people=[("Theo", "1996-07-02", "21:15", "London, GB")])
    d = c.get("/api/sky/events?days=365").json()
    texts = S.strings(d)
    assert texts
    for t in texts:
        assert not S.lint(t), (t, S.lint(t))
    now = dt.datetime(2026, 10, 28, 17, 0, tzinfo=UTC)
    for a in S.active(S.sky_events("2026-10-27", 60)[1], now, UTC, lambda a, b: []):
        assert not S.lint(a["banner"])


def test_two_hundred_concurrent_requests_on_a_cached_day_stay_fast(monkeypatch):
    c = TestClient(server.app)
    _user(c, "sky-load@x.test", people=[("Theo", "1996-07-02", "21:15", "London, GB")])
    assert c.get("/api/sky/events").status_code == 200        # warm the cache

    def hit(_=None):
        t = time.perf_counter()
        r = c.get("/api/sky/events")
        assert r.status_code == 200
        return time.perf_counter() - t

    # The machine's own speed, measured here: one cached request at a time.
    base = sorted(hit() for _ in range(20))[10]

    # A big day is a cache read, so the burst must never reach the ephemeris
    # or the geometry. Counted, not timed, so it holds on any hardware.
    computed = []
    real_sky, real_pers = S.sky_events, S.personalise
    monkeypatch.setattr(S, "sky_events", lambda *a, **k: computed.append("sky") or real_sky(*a, **k))
    monkeypatch.setattr(S, "personalise", lambda *a, **k: computed.append("p") or real_pers(*a, **k))

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=20) as pool:
        times = sorted(pool.map(hit, range(200)))
    wall = time.perf_counter() - t0
    p95 = times[int(len(times) * 0.95)]

    assert not computed, f"{len(computed)} recomputes during a cached burst"
    # A cached read is a few ms on a laptop and ~10 ms on a GitHub runner;
    # 50 ms means real work crept onto the cached path.
    assert base < 0.05, f"one cached request took {base:.3f}s"
    # Contention: the burst does the same work as 200 serial requests, so it
    # should take about as long. A lock convoy or stampede blows this up.
    assert wall < 3 * 200 * base + 0.5, (wall, base)
    # Tail: in-process requests share the GIL, so with 20 in flight a request
    # waits behind the others: p95 measured ~20-40x one request on a laptop
    # and ~60x on a Python 3.10 GitHub runner (0.369s at a 6.1ms baseline, which
    # failed a 60x bound by a millisecond). 120x is twice the worst seen. The
    # fixed 0.3s is the original bound and still applies on a fast machine; the
    # hard guarantees are the recompute count and the wall-time check above.
    assert p95 < max(0.3, 120 * base), (p95, base, times[-5:])


# --------------------------------------------------------------------- push

def _push_user(c, email, monkeypatch):
    uid = _user(c, email, people=[("Theo", "1996-07-02", "21:15", "London, GB")])
    save(uid, "push", [{"endpoint": "https://push.example/x", "keys": {"p256dh": "a", "auth": "b"}}])
    sent = []
    monkeypatch.setattr("app.push.send", lambda u, title, body, url="/", **_: sent.append(
        (u, title, body, url)) or {"sent": 1, "pruned": 0, "failed": 0})
    monkeypatch.setattr("app.auth.all_users", lambda: [uid])
    return uid, sent


# Two days before Mercury stations direct (Nov 13 15:53 UT), 10:00 in Chicago.
PUSH_NOW = dt.datetime(2026, 11, 11, 16, 0, tzinfo=UTC)


def test_event_push_is_dark_without_the_flag(c, monkeypatch):
    _uid, sent = _push_user(c, "sky-push-flag@x.test", monkeypatch)
    c.post("/api/sky/prefs", json={"event_push": True})
    monkeypatch.delenv("MYSIGN_SKY_EVENT_PUSH", raising=False)
    assert skypush.run(PUSH_NOW) == {"enabled": False, "pushed": 0} and not sent
    assert c.get("/api/sky/prefs").json() == {"event_push": True, "available": False}


def test_event_push_honours_opt_out(c, monkeypatch):
    _uid, sent = _push_user(c, "sky-push-off@x.test", monkeypatch)
    monkeypatch.setenv("MYSIGN_SKY_EVENT_PUSH", "1")
    assert skypush.run(PUSH_NOW)["pushed"] == 0 and not sent          # never opted in
    c.post("/api/sky/prefs", json={"event_push": True})
    c.post("/api/sky/prefs", json={"event_push": False})
    assert skypush.run(PUSH_NOW)["pushed"] == 0 and not sent


def test_event_push_once_per_event_calm_and_no_upsell(c, monkeypatch):
    _uid, sent = _push_user(c, "sky-push-on@x.test", monkeypatch)
    monkeypatch.setenv("MYSIGN_SKY_EVENT_PUSH", "1")
    c.post("/api/sky/prefs", json={"event_push": True})
    assert skypush.run(PUSH_NOW)["pushed"] == 1
    assert skypush.run(PUSH_NOW + dt.timedelta(hours=1))["pushed"] == 0
    assert len(sent) == 1
    _u, _title, body, url = sent[0]
    # Her life first, then when, in words: no "1d 23h".
    assert "Mercury turns direct in 2 days." in body
    assert not re.search(r"\d+d \d+h", body)
    assert url == "/app?sky=events"
    low = body.lower()
    for bad in ("$", "plus", "upgrade", "unlock", "trial", "price"):
        assert bad not in low
    assert not S.lint(body)


def test_event_push_waits_for_daytime(c, monkeypatch):
    _uid, sent = _push_user(c, "sky-push-night@x.test", monkeypatch)
    monkeypatch.setenv("MYSIGN_SKY_EVENT_PUSH", "1")
    c.post("/api/sky/prefs", json={"event_push": True})
    night = dt.datetime(2026, 11, 11, 7, 0, tzinfo=UTC)               # 01:00 in Chicago
    assert skypush.run(night)["pushed"] == 0 and not sent


def test_account_deletion_takes_the_cache_and_prefs(c):
    uid = _user(c, "sky-del@x.test")
    c.get("/api/sky/events")
    c.post("/api/sky/prefs", json={"event_push": True})
    assert c.delete("/api/account").status_code == 200
    assert load(uid, "sky-prefs", None) is None


# ------------------------------------------------------- review fixes (team2fix)

def test_patterns_today_is_the_reverified_heads_up_not_the_stored_claim(c):
    """A stored claim is whatever the proposer wrote; the Sky tab shows the
    line unlocks.heads_up builds from a fresh verify() instead."""
    from engine import memory as M
    from engine import unlocks
    from tests.test_memory import plant
    uid = _user(c, "sky-pat@x.test")
    day = server._today(uid)
    sign = server._sky_state(server._sky(uid, day))["moon_sign"]
    plant(uid, sign=sign, end=day)
    pats = M.weekly_pattern_job(uid)
    assert pats
    # Poison the stored claim and add a fake that verify() would never return.
    pats[0]["claim"] = "STORED-CLAIM-NOT-SHOWN"
    fake = {"claim": "FAKE-CLAIM", "condition": {"kind": "moon_sign", "value": sign,
                                                 "window_days": 0},
            "outcome": {"kind": "mood_above", "value": "9"}, "support": "9 of the last 9",
            "lift": 9.0, "p_value": 0.0001, "confidence": "high", "n_tests": 1}
    save(uid, "patterns", pats + [fake])
    d = server._sky_events_for(uid, 30)
    got = d["patterns_today"]
    assert got and "CLAIM" not in json.dumps(got)
    want = unlocks.heads_up(uid, server._sky_state(server._sky(uid, day)), day.isoformat())
    assert [g["text"] for g in got] == [w["text"] for w in want]
    assert got[0]["text"].startswith("Heads-up from your own check-ins")
    assert got[0]["support"] in got[0]["text"] and got[0]["p_text"].startswith("p")


def test_the_day_cache_is_saved_with_a_two_day_ttl(c, monkeypatch):
    uid = _user(c, "sky-ttl@x.test")
    seen = []
    real = server.save
    monkeypatch.setattr(server, "save", lambda u, k, o, ttl=None: (
        seen.append((k, ttl)), real(u, k, o, ttl=ttl))[1])
    server._sky_events_for(uid, 30)
    ttls = [t for k, t in seen if k.startswith("skyev-")]
    assert ttls and ttls[0] is not None
    assert abs(ttls[0] - (time.time() + 2 * 86400)) < 60


def test_a_profile_without_the_flag_reads_as_time_known(c):
    """Default True, like _natal: a profile from before the flag kept its houses."""
    uid = _user(c, "sky-legacy-flag@x.test")
    prof = load(uid, "profile", {})
    prof.pop("birth_time_known", None)
    save(uid, "profile", prof)
    d = server._sky_events_for(uid, 30)
    assert d["time_known"] is True and d["houses_note"] is None
    assert any(e["her_house"] for e in d["events"])


def test_event_push_skips_users_without_a_device_before_computing(c, monkeypatch):
    uid, sent = _push_user(c, "sky-push-nodev@x.test", monkeypatch)
    monkeypatch.setenv("MYSIGN_SKY_EVENT_PUSH", "1")
    c.post("/api/sky/prefs", json={"event_push": True})
    save(uid, "push", None)
    computed = []
    monkeypatch.setattr(server, "_sky_events_for", lambda *a, **k: computed.append(a) or {})
    assert skypush.run(PUSH_NOW)["pushed"] == 0
    assert not computed and not sent


def test_event_push_defers_when_the_budget_is_spent(c, monkeypatch):
    _uid, sent = _push_user(c, "sky-push-budget@x.test", monkeypatch)
    monkeypatch.setenv("MYSIGN_SKY_EVENT_PUSH", "1")
    c.post("/api/sky/prefs", json={"event_push": True})
    computed = []
    real = server._sky_events_for
    monkeypatch.setattr(server, "_sky_events_for",
                        lambda *a, **k: computed.append(a) or real(*a, **k))
    out = skypush.run(PUSH_NOW, remaining=lambda: skypush.RESERVE_S - 1)
    assert out == {"enabled": True, "pushed": 0, "deferred": 1}
    assert not computed and not sent
    # The next run, with time to spare, picks her up.
    assert skypush.run(PUSH_NOW, remaining=lambda: 999)["pushed"] == 1


def test_event_push_runs_users_in_a_pool(c, monkeypatch):
    uid, sent = _push_user(c, "sky-push-pool@x.test", monkeypatch)
    monkeypatch.setenv("MYSIGN_SKY_EVENT_PUSH", "1")
    c.post("/api/sky/prefs", json={"event_push": True})
    others = []
    for i in range(3):
        c2 = TestClient(server.app)
        u = _user(c2, f"sky-push-pool{i}@x.test")
        save(u, "push", [{"endpoint": f"https://push.example/{i}", "keys": {"p256dh": "a", "auth": "b"}}])
        c2.post("/api/sky/prefs", json={"event_push": True})
        others.append(u)
    monkeypatch.setattr("app.auth.all_users", lambda: [uid] + others)
    out = skypush.run(PUSH_NOW, workers=4)
    assert out["pushed"] == 4 and out["deferred"] == 0
    assert sorted(s[0] for s in sent) == sorted([uid] + others)
