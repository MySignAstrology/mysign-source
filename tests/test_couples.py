"""
Celebrity couple compatibility (/couples).

Holds the page to what it claims: every number traces to a sourced birth
record and to the same engine users get; the status shown is what the public
record says, changed only after a week's confirmation; new partners are
suggested, never published; written reads are grounded; and the public pages
need no sign-in and can't be made to run script.
"""
import datetime as dt
import json
import os
import random
import re
import statistics
import tempfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-couples-"))
os.environ.pop("ANTHROPIC_API_KEY", None)

import app.server as server
from engine import compat, couples_watch, synastry
from engine import couples as C
from engine.store import load, save

client = TestClient(server.app)


@pytest.fixture(autouse=True)
def fresh_store():
    for k in ("relations", "pending", "candidates", "dismissed", "history"):
        save(C.STORE, k, None)
    for c in C.couples():
        save(C.STORE, f"read-{c['slug']}", None)
    yield


# ------------------------------------------------------------------ data


def test_every_person_has_a_sourced_birth_record():
    for c in C.couples():
        for p in c["people"]:
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", p["birth_date"]), p["name"]
            assert p["birth_date"] >= "1800-01-01", f"{p['name']} is outside the ephemeris"
            assert p["lat"] is not None and p["lon"] is not None, p["name"]
            assert p["source"].startswith("https://www.wikidata.org/wiki/Q"), p["name"]
            if p.get("birth_time"):
                assert p.get("time_source") and p.get("utc_offset") is not None, p["name"]


def test_slugs_are_unique_and_readable():
    slugs = [c["slug"] for c in C.couples()]
    assert len(slugs) == len(set(slugs))
    assert "barack-obama-and-michelle-obama" in slugs
    assert "taylor-swift-and-travis-kelce" in slugs
    assert all(re.fullmatch(r"[a-z0-9-]+", s) for s in slugs)


def test_known_sun_signs_come_out_right():
    """Independent spot checks: these birthdays are famous."""
    want = {"Barack Obama": "Leo", "Michelle Obama": "Capricorn", "Taylor Swift": "Sagittarius",
            "Travis Kelce": "Libra", "Beyoncé": "Virgo", "Kim Kardashian": "Libra",
            "Queen Victoria": "Gemini", "John Lennon": "Libra"}
    seen = {p["name"]: C.signs(p)["Sun"] for c in C.couples() for p in c["people"]}
    for name, sign in want.items():
        assert seen[name] == sign, name


def test_obamas_birth_time_is_used_and_nobody_elses_is_guessed():
    ob = next(c for c in C.couples() if c["slug"] == "barack-obama-and-michelle-obama")
    barack, michelle = ob["people"]
    assert C.birth_ut(barack)[1] is True and "Rising" in C.signs(barack)
    assert C.birth_ut(michelle)[1] is False and "Rising" not in C.signs(michelle)


def test_scores_come_from_the_same_engine_users_get():
    c = C.by_slug("beyonce-and-jay-z")
    a, b = c["people"]
    ua, _ = C.birth_ut(a)
    ub, _ = C.birth_ut(b)
    direct = compat.deep({"birth_ut": ua}, {"birth_ut": ub}, kind="romantic",
                         precision_a="unknown", precision_b="unknown", name_b=b["short"])
    assert C.report(c["slug"])["score"] == direct["score"]


# ----------------------------------------------- the calibration this exposed


def test_date_only_pairs_are_scored_against_their_own_reference():
    """Building this page found that two date-only charts were normalised
    against a reference with one birth time known, so the median such pair
    scored ~36, not 50 — every friend added without a time read 'effortful'.
    Out of sample, every combination now centres on an ordinary pair."""
    assert synastry.reference_key(False, False) == "none"
    assert synastry.reference_key(False, True) == synastry.reference_key(True, False) == "partial"
    rng = random.Random(99)

    def rnd():
        d = dt.date(1960, 1, 1) + dt.timedelta(days=rng.randint(0, 16000))
        return [d.year, d.month, d.day, rng.random() * 24, 40.7, -74.0]
    for pa, pb in (("unknown", "unknown"), ("exact", "unknown"), ("unknown", "exact")):
        xs = [compat.deep({"birth_ut": rnd()}, {"birth_ut": rnd()}, precision_a=pa,
                          precision_b=pb)["score"]["overall"] for _ in range(60)]
        assert 42 <= statistics.median(xs) <= 58, (pa, pb, statistics.median(xs))


# --------------------------------------------------------------- status

def _c(slug="barack-obama-and-michelle-obama"):
    return C.by_slug(slug)


def _rel(c, kind, start=None, end=None):
    a, b = c["people"]
    return {"a": a["qid"], "b": b["qid"], "kind": kind, "start": start, "end": end}


def test_status_reads_like_the_public_record():
    c = _c()
    assert C.status(c, [_rel(c, "spouse", "1992-10-03")])["label"] == "Married since 1992"
    assert C.status(c, [_rel(c, "spouse", "1992-10-03", "2001-05-01")])["state"] == "divorced"
    assert C.status(c, [_rel(c, "partner", "2020-01-01")])["label"] == "Together since 2020"
    assert C.status(c, [_rel(c, "partner", "2020-01-01", "2022-01-01")])["state"] == "split"
    # spouse beats partner, and an exact start beats a year-only duplicate
    s = C.status(c, [_rel(c, "partner", "1989-01-01", "1992-01-01"),
                     _rel(c, "spouse", "1992-01-01"), _rel(c, "spouse", "1992-10-03")])
    assert s["label"] == "Married since 1992" and s["current"]


def test_a_marriage_ended_by_death_says_so_gently():
    c = _c("john-lennon-and-yoko-ono")
    s = C.status(c, [_rel(c, "spouse", "1969-03-20", "1980-12-08")])
    assert s["label"] == "Married 1969, until John's death in 1980" and not s["current"]


# ------------------------------------------------------- weekly monitoring


def _live_with(c, *extra):
    """The published snapshot, with this pair's statements replaced."""
    pair = {p["qid"] for p in c["people"]}
    base = [r for r in C._snapshot()["relations"] if {r["a"], r["b"]} != pair]
    return base + list(extra)


def test_a_status_change_waits_a_week_before_it_shows():
    c = _c()
    divorce = _live_with(c, _rel(c, "spouse", "1992-10-03", "2026-10-01"))
    t0 = dt.datetime(2026, 11, 2, 9, tzinfo=dt.timezone.utc)

    out = couples_watch.check_status(fetched=divorce, now=t0)
    assert [x["slug"] for x in out["pending"]] == [c["slug"]]
    assert C.status(c)["label"] == "Married since 1992", "published on first sight"

    couples_watch.check_status(fetched=divorce, now=t0 + dt.timedelta(days=3))
    assert C.status(c)["label"] == "Married since 1992", "published inside the week"

    out = couples_watch.check_status(fetched=divorce, now=t0 + dt.timedelta(days=7))
    assert [x["slug"] for x in out["published"]] == [c["slug"]]
    assert C.status(c)["state"] == "divorced"
    assert load(C.STORE, "history", [])[-1]["slug"] == c["slug"]


def test_a_reverted_edit_never_reaches_the_page():
    c = _c()
    t0 = dt.datetime(2026, 11, 2, 9, tzinfo=dt.timezone.utc)
    couples_watch.check_status(fetched=_live_with(c, _rel(c, "spouse", "1992-10-03", "2026-10-01")), now=t0)
    couples_watch.check_status(fetched=C._snapshot()["relations"], now=t0 + dt.timedelta(days=7))
    assert c["slug"] not in (load(C.STORE, "pending", {}) or {})
    assert C.status(c)["label"] == "Married since 1992"


def test_statements_vanishing_entirely_is_ignored():
    c = _c()
    t0 = dt.datetime(2026, 11, 2, 9, tzinfo=dt.timezone.utc)
    for d in (0, 7, 14):
        couples_watch.check_status(fetched=_live_with(c), now=t0 + dt.timedelta(days=d))
    assert C.status(c)["label"] == "Married since 1992"


def test_new_partners_are_suggested_not_published():
    kim = next(p for c in C.couples() for p in c["people"] if p["name"] == "Kim Kardashian")
    newcomer = {"a": kim["qid"], "b": "Q999999999", "b_name": "Somebody New",
                "kind": "partner", "start": "2026-06-01", "end": None}
    old_flame = {"a": kim["qid"], "b": "Q999999998", "b_name": "Long Ago",
                 "kind": "partner", "start": "2001-01-01", "end": "2002-01-01"}
    now = dt.datetime(2026, 11, 2, 9, tzinfo=dt.timezone.utc)
    couples_watch.check_status(fetched=C._snapshot()["relations"] + [newcomer, old_flame], now=now)
    cands = load(C.STORE, "candidates", {})
    assert list(cands) == [f"{kim['qid']}-Q999999999"]
    assert cands[f"{kim['qid']}-Q999999999"]["who"] == "Somebody New"
    assert len(C.couples()) == len(json.loads((C.DATA / "couples.json").read_text())["couples"])
    assert couples_watch.dismiss(f"{kim['qid']}-Q999999999")
    couples_watch.check_status(fetched=C._snapshot()["relations"] + [newcomer], now=now)
    assert not load(C.STORE, "candidates", {})


# ---------------------------------------------------------------- reads


class Stub:
    def __init__(self, payload):
        self.payload, self.seen, self.calls = payload, {}, 0
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.seen, self.calls = kw, self.calls + 1
        return SimpleNamespace(stop_reason="end_turn", content=[
            SimpleNamespace(type="text", text=json.dumps(self.payload))])


def _payload(c, bad=False):
    a, b = c["people"]
    sa, sb = C.signs(a), C.signs(b)
    item = lambda t, by: {"text": t, "because": by}   # noqa: E731
    wrong = next(s for s in C.SIGNS if s != sa["Venus"])
    return {"headline": "Two Leos walk into a ballroom.", "vibe": "Big energy, bigger table.",
            "why_it_works": item("They admire each other.", f"{a['short']}'s Sun in {sa['Sun']}"),
            "where_it_rubs": item("Pace.", f"{a['short']}'s Venus in {wrong}" if bad
                                  else f"{b['short']}'s Sun in {sb['Sun']}"),
            "the_chemistry": item("Sparks.", f"{b['short']}'s Mars in {sb['Mars']}"),
            "the_long_game": item("Built to last.", "composite Sun in "
                                  + C.report(c["slug"])["composite"]["signs"]["Sun"]),
            "verdict": "A double act."}


def test_reads_are_structured_grounded_and_careful():
    c = _c()
    stub = Stub(_payload(c, bad=True))
    read = couples_watch.write_read(c, client=stub)
    prompt = stub.seen["messages"][0]["content"]
    assert stub.seen["output_config"]["format"]["type"] == "json_schema"
    assert "Say nothing about their private lives" in prompt
    assert "Never predict a breakup" in prompt
    keys = [s["key"] for s in read["sections"]]
    assert "where_it_rubs" not in keys, "a Venus in the wrong sign must not be shown"
    assert {"why_it_works", "the_chemistry", "the_long_game"} <= set(keys)


def test_reads_are_written_once_and_redone_when_status_changes():
    c = _c()
    stub = Stub(_payload(c))
    out = couples_watch.write_reads(limit=1, client=stub)
    assert out["written"] == [C.couples()[0]["slug"]] and stub.calls == 1
    couples_watch.write_reads(limit=1, client=stub)
    assert stub.calls == 2 and load(C.STORE, f"read-{C.couples()[1]['slug']}", None)
    # pretend the first couple's status moved on
    rec = load(C.STORE, f"read-{c['slug']}", None)
    save(C.STORE, f"read-{c['slug']}", {**rec, "status": "Married since 1900"})
    before = stub.calls
    couples_watch.write_reads(limit=1, client=stub)
    assert stub.calls == before + 1
    assert load(C.STORE, f"read-{c['slug']}", None)["status"].startswith("Married since 1992|")


def test_no_key_means_no_reads_and_no_errors():
    out = couples_watch.write_reads(limit=3)
    assert out == {"written": [], "failed": [], "skipped": "no model key"}


def test_failed_reads_count_toward_the_cap():
    class Broken:
        calls = 0
        messages = SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))
    out = couples_watch.write_reads(limit=2, client=Broken())
    assert out["written"] == [] and len(out["failed"]) == 2


# ---------------------------------------------------------------- pages


def test_public_api_needs_no_sign_in():
    c = TestClient(server.app)
    r = c.get("/api/couples")
    assert r.status_code == 200 and len(r.json()["couples"]) == len(C.couples())
    d = c.get("/api/couples/barack-obama-and-michelle-obama").json()
    assert d["status"] == "Married since 1992"
    assert d["people"][0]["time_known"] and d["people"][0]["time_source"]
    assert d["breakdown"]["themes"] and d["breakdown"]["dimensions"]
    assert c.get("/api/couples/nobody-and-noone").status_code == 404


def test_the_breakdown_names_people_and_never_says_you():
    d = client.get("/api/couples/beyonce-and-jay-z").json()["breakdown"]
    words = json.dumps(d)
    assert not re.search(r"\b[Yy]ou(r|rs|'re)?\b", words), "second person leaked into a third-person page"
    assert "Beyoncé's" in words or "Jay-Z's" in words


def test_pages_render_with_their_own_title_and_cannot_run_script():
    r = client.get("/couples/taylor-swift-and-travis-kelce")
    assert r.status_code == 200
    assert "<title>Taylor Swift &amp; Travis Kelce compatibility:" in r.text
    assert 'href="https://mysignapp.com/couples/taylor-swift-and-travis-kelce"' in r.text
    r = client.get("/couples/%3Cscript%3Ealert(1)%3C%2Fscript%3E")
    assert r.status_code == 404 and "<script>alert(1)" not in r.text
    idx = client.get("/couples")
    assert idx.status_code == 200 and "Famous couples" in idx.text
    # the embedded data can't close its own script tag
    blob = idx.text.split('<script id="data" type="application/json">', 1)[1].split("</script>", 1)[0]
    assert json.loads(blob)["view"] == "list"


def test_every_couple_is_in_the_sitemap():
    sm = (C.DATA.parent.parent / "site" / "sitemap.xml").read_text()
    for c in C.couples():
        assert f"/couples/{c['slug']}</loc>" in sm


def test_ops_controls_are_not_public():
    """Signed out is refused; with no operators configured the routes 404,
    which is app/ops.py hiding the ops surface on purpose."""
    assert client.get("/api/ops/couples").status_code in (401, 403, 404)
    assert client.post("/api/ops/couples/run").status_code in (401, 403, 404)
    assert client.post("/api/ops/couples/dismiss/x").status_code in (401, 403, 404)
    assert client.post("/api/ops/couples/story/x/approve").status_code in (401, 403, 404)


def test_the_scheduler_routes_the_weekly_job(monkeypatch):
    from app import cron
    called = {}
    monkeypatch.setattr(couples_watch, "run", lambda remaining=None: called.setdefault("ran", True) and {"ok": 1})
    monkeypatch.setattr("app.bootstrap.load_secrets", lambda: None)
    assert cron.handler({"job": "couples_watch"}) == {"ok": 1} and called["ran"]


def test_third_person_rewrites():
    t = C.third_person
    assert t("Between you there's almost no water.") == "Between them there's almost no water."
    assert t("One of you can feel judged.") == "One of them can feel judged."
    assert t("You will argue about pace.") == "They will argue about pace."
    assert t("Your Moon and yours.") == "Their Moon and theirs."


def test_wikidata_never_patches_ssl_for_the_whole_process(monkeypatch):
    """The cron Lambda crashed with a RecursionError: truststore.inject_into_ssl()
    swapped ssl.SSLContext process-wide and botocore's next client recursed."""
    import io
    import ssl

    from engine import wikidata
    before = ssl.SSLContext
    seen = {}

    def fake_urlopen(req, timeout=None, context=None):
        seen["context"] = context
        return io.BytesIO(b'{"results": {"bindings": []}}')
    monkeypatch.setattr(wikidata.urllib.request, "urlopen", fake_urlopen)
    assert wikidata.sparql("SELECT 1") == []
    assert ssl.SSLContext is before
    assert isinstance(seen["context"], ssl.SSLContext)
    import boto3
    boto3.session.Session(region_name="us-east-2").client("dynamodb")
