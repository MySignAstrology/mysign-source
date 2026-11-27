"""
tools/backfill_birth_ut.py: existing accounts were charted with the old,
backzone-less rules and keep that instant until it is recomputed. The tool must
find exactly those, change nothing on a dry run, fix them on --apply, drop what
was derived from the old instant, and find nothing the second time.
"""
import uuid

from app import cities
from engine.store import load, save
from tools import backfill_birth_ut as B


def _city(q):
    c = cities.resolve(cities.search(q)[0]["id"])
    return c


def _account(profile, connections=()):
    uid = "u" + uuid.uuid4().hex[:12]
    save(uid, "account", {"tz": profile["tz"], "consented_at": 1})
    save(uid, "profile", profile)
    save(uid, "connections", list(connections))
    return uid


def _reykjavik_1960():
    """Reykjavík 1960-01-15 12:00 as the old rules stored it: Abidjan's UTC+0,
    so 12:00 UT. Iceland was UTC-1, so the instant is 13:00 UT."""
    c = _city("Reykjavik")
    return {"name": "Sigrun", "birth_date": "1960-01-15", "birth_time": "12:00",
            "birth_city": c["label"], "birth_time_known": True, "tz": c["tz"],
            "birth_ut": [1960, 1, 15, 12.0, c["lat"], c["lon"]], "country": "IS",
            "people": [], "birth_time_note": None}


def _oslo_person():
    """Oslo 1962-07-15 10:00, stored with Berlin's +1 (09:00 UT); Norway had
    summer time then, +2, so 08:00 UT."""
    c = _city("Oslo")
    return {"id": "p1", "name": "Kari", "relation": "friend", "kind": "friend",
            "birth_date": "1962-07-15", "birth_time": "10:00", "birth_city": c["id"],
            "birth_time_known": True, "time_precision": "exact",
            "birth_ut": [1962, 7, 15, 9.0, c["lat"], c["lon"]],
            "checked_at": "2026-10-01T00:00:00+00:00"}


def test_dry_run_reports_and_writes_nothing():
    uid = _account(_reykjavik_1960(), [_oslo_person()])
    seen = []
    assert B.run(uid, apply=False, out=seen.append) == 2
    assert "+60.0 min" in seen[0] and "self" in seen[0]
    assert "-60.0 min" in seen[1] and "person p1" in seen[1]
    assert load(uid, "profile", None)["birth_ut"][3] == 12.0
    assert load(uid, "connections", [])[0]["birth_ut"][3] == 9.0


def test_apply_corrects_invalidates_and_is_idempotent():
    uid = _account(_reykjavik_1960(), [_oslo_person()])
    houses, rulers = B.S._tradition(uid)
    save(uid, f"reading-{houses}-{rulers}", {"stale": True, "profile": {}})
    assert B.run(uid, apply=True, out=lambda _l: None) == 2
    assert load(uid, "profile", None)["birth_ut"][3] == 13.0
    row = load(uid, "connections", [])[0]
    assert row["birth_ut"][3] == 8.0
    assert "overall" in row and row.get("rescored_at")         # rescored on the new charts
    assert load(uid, f"reading-{houses}-{rulers}", None) is None
    assert load(uid, "salience_bands", None)
    # Second pass: nothing left to change.
    assert B.run(uid, apply=True, out=lambda _l: None) == 0


def test_modern_and_unaffected_births_are_left_alone():
    c = _city("Paris")
    prof = {"name": "Ana", "birth_date": "1995-06-01", "birth_time": "08:30",
            "birth_city": c["label"], "birth_time_known": True, "tz": c["tz"],
            "birth_ut": [1995, 6, 1, 6.5, c["lat"], c["lon"]], "people": []}
    uid = _account(prof)
    assert B.run(uid, apply=False, out=lambda _l: None) == 0


def test_unknown_time_recomputes_local_noon():
    prof = {**_reykjavik_1960(), "birth_time": None, "birth_time_known": False}
    uid = _account(prof)
    B.run(uid, apply=True, out=lambda _l: None)
    assert load(uid, "profile", None)["birth_ut"][3] == 13.0      # noon at UTC-1


def test_person_whose_place_moved_is_skipped_not_relocated():
    row = {**_oslo_person(), "birth_ut": [1962, 7, 15, 9.0, 10.0, 10.0]}
    uid = _account(_reykjavik_1960(), [row])
    seen = []
    B.run(uid, apply=True, out=seen.append)
    assert any("SKIPPED" in s for s in seen)
    assert load(uid, "connections", [])[0]["birth_ut"][:4] == [1962, 7, 15, 9.0]


def test_cli_defaults_to_dry_run():
    uid = _account(_reykjavik_1960())
    assert B.main(["--uid", uid]) == 1
    assert load(uid, "profile", None)["birth_ut"][3] == 12.0



def test_cli_finds_accounts_in_the_users_set(capsys):
    """Sign-up records people in the users set; the legacy _auth/users roster
    is empty on DynamoDB, so reading only that checked nobody in prod."""
    from engine import store
    uid = _account(_reykjavik_1960())
    store.add_member("users", uid)
    assert B.main([]) >= 1
    assert uid in capsys.readouterr().out
    assert load(uid, "profile", None)["birth_ut"][3] == 12.0
