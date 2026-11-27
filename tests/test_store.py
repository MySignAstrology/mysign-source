"""
Storage contract, run against both backends.

engine.store picks its backend at import, so the dynamo half calls
engine.store_dynamo directly against a moto table; the file half points
engine.store.ROOT at a fresh temp dir per test.
"""
import os
import threading
import time

import pytest

import engine.store as store


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    return store


@pytest.fixture
def dyn(monkeypatch):
    moto = pytest.importorskip("moto")
    import boto3

    import engine.store_dynamo as sd
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("MYSIGN_TABLE", "mysign-test")
    with moto.mock_aws():
        boto3.client("dynamodb").create_table(
            TableName="mysign-test", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                       {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                  {"AttributeName": "sk", "AttributeType": "S"}])
        monkeypatch.setattr(sd, "_table", None)
        yield sd
        sd._table = None


API = ["load", "save", "keys", "load_range", "load_page", "delete", "delete_all", "incr", "counter", "counters",
       "add_member", "remove_member", "members"]


def _file_fns(s):
    return {n: getattr(s, f"_file_{n}") for n in API}


@pytest.fixture(params=["files", "dynamo"])
def b(request):
    """One namespace of store functions, whichever backend is under test."""
    if request.param == "files":
        s = request.getfixturevalue("files")
        return type("B", (), {k: staticmethod(v) for k, v in _file_fns(s).items()})
    return request.getfixturevalue("dyn")


# ---- contract shared by both backends ---------------------------------------

def test_keys_delete_delete_all(b):
    b.save("u1", "profile", {"a": 1})
    b.save("u1", "episodes", [1, 2])
    b.save("u2", "profile", {"b": 2})
    b.incr("u1", "quota", "msgs")
    assert b.keys("u1") == sorted(["profile", "episodes", "quota#ctr"])
    b.delete("u1", "episodes")
    b.delete("u1", "never-existed")              # no-op
    assert "episodes" not in b.keys("u1")
    assert b.delete_all("u1") == 2               # profile + counter
    assert b.keys("u1") == []
    assert b.load("u1", "profile", "gone") == "gone"
    assert b.load("u2", "profile", None) == {"b": 2}
    assert b.delete_all("nobody") == 0


def test_load_range_is_inclusive_sorted_and_skips_expired(b):
    for d in ("2026-10-01", "2026-10-02", "2026-10-03"):
        b.save("_report", f"visits#{d}", {"d": d})
    b.save("_report", "active#2026-10-02", {"other": True})
    b.save("_report", "visits#2026-10-04", {"d": "x"}, ttl=time.time() - 1)
    got = b.load_range("_report", "visits#2026-10-02", "visits#2026-10-04")
    assert got == [("visits#2026-10-02", {"d": "2026-10-02"}),
                   ("visits#2026-10-03", {"d": "2026-10-03"})]
    assert b.load_range("_report", "zzz#", "zzz#~") == []


def test_file_range_reads_only_the_files_in_range_and_each_once(files, monkeypatch):
    # The ops console asks for 28 days of one report out of ~1,000 files. Reading
    # every file (twice) took ~2 s a request locally and timed the page out.
    for name in ("visits", "active", "funnel"):
        for d in range(1, 31):
            files.save("_report", f"{name}#2026-10-{d:02d}", {"d": d})
    reads = []
    real = files._read
    monkeypatch.setattr(files, "_read", lambda p: reads.append(p.stem) or real(p))
    got = files.load_range("_report", "visits#2026-10-03", "visits#2026-10-09")
    assert [k for k, _ in got] == [f"visits#2026-10-{d:02d}" for d in range(3, 10)]
    assert sorted(reads) == [k for k, _ in got]
    reads.clear()
    rows, _ = files.load_page("_report", "active#", "active#~", 5)
    assert len(rows) == 5 and len(reads) == 5


def test_load_page_walks_a_range_with_a_cursor_both_ways(b):
    for i in range(7):
        b.save("_idx", f"k{i}", {"i": i})
    b.save("_idx", "k3x", {"i": "dead"}, ttl=time.time() - 1)
    b.save("_idx", "z", {"i": "outside"})
    rows, nxt = b.load_page("_idx", "k", "k~", 3)
    assert [sk for sk, _ in rows] == ["k0", "k1", "k2"] and nxt == "k2"
    rows, nxt = b.load_page("_idx", "k", "k~", 3, after=nxt)
    assert [v["i"] for _, v in rows] == [3, 4, 5]
    rows, nxt = b.load_page("_idx", "k", "k~", 3, after=nxt)
    assert [sk for sk, _ in rows] == ["k6"] and nxt is None
    rows, nxt = b.load_page("_idx", "k", "k~", 4, reverse=True)
    assert [sk for sk, _ in rows] == ["k6", "k5", "k4", "k3"]
    rows, nxt = b.load_page("_idx", "k", "k~", 4, after=nxt, reverse=True)
    assert [sk for sk, _ in rows] == ["k2", "k1", "k0"]


def test_dynamo_load_page_keeps_querying_past_expired_items(monkeypatch):
    """No moto here: a table that answers Query the way DynamoDB pages it
    (Limit counts items read, expired ones included; LastEvaluatedKey)."""
    import engine.store_dynamo as sd
    items = [{"pk": "p", "sk": f"k{i}", "v": f'{{"i": {i}}}'} for i in range(6)]
    items[1]["ttl"] = items[2]["ttl"] = 1           # expired, still unswept

    class Table:
        calls = 0

        def query(self, Limit, ScanIndexForward=True, ExclusiveStartKey=None, **kw):
            Table.calls += 1
            rows = items if ScanIndexForward else items[::-1]
            if ExclusiveStartKey:
                at = [r["sk"] for r in rows].index(ExclusiveStartKey["sk"])
                rows = rows[at + 1:]
            out = {"Items": rows[:Limit]}
            if len(rows) > Limit:
                out["LastEvaluatedKey"] = {"pk": "p", "sk": rows[Limit - 1]["sk"]}
            return out
    monkeypatch.setattr(sd, "_table", Table())
    rows, nxt = sd.load_page("p", "k", "k~", 3)
    assert [sk for sk, _ in rows] == ["k0", "k3", "k4"] and nxt == "k4" and Table.calls == 2
    rows, nxt = sd.load_page("p", "k", "k~", 3, after=nxt)
    assert [sk for sk, _ in rows] == ["k5"] and nxt is None


def test_save_none_deletes(b):
    b.save("u1", "thread", [1])
    assert b.save("u1", "thread", None) is None
    assert b.load("u1", "thread", "dflt") == "dflt"
    assert b.keys("u1") == []


def test_expired_item_reads_as_absent(b):
    b.save("u1", "live", {"x": 1}, ttl=time.time() + 3600)
    b.save("u1", "dead", {"x": 1}, ttl=time.time() - 1)
    assert b.load("u1", "live", None) == {"x": 1}
    assert b.load("u1", "dead", "dflt") == "dflt"
    assert b.keys("u1") == ["live"]


def test_incr_limit(b):
    got = [b.incr("u1", "quota", "msgs", limit=3) for _ in range(5)]
    assert got == [1, 2, 3, None, None]
    assert b.counter("u1", "quota", "msgs") == 3
    assert b.incr("u1", "quota", "other", by=5) == 5      # fields are independent
    assert b.counter("u1", "quota", "missing") == 0
    assert b.counter("nobody", "quota", "msgs") == 0
    with pytest.raises(ValueError):
        b.incr("u1", "quota", "ttl")


def test_counters_reads_every_field(b):
    """Field names the caller didn't know ahead (screen:home, tap:nav_ppl)."""
    b.incr("_activity", "_act#d", "screen:home", by=3, ttl=time.time() + 60)
    b.incr("_activity", "_act#d", "tap:nav_ppl", ttl=time.time() + 60)
    assert b.counters("_activity", "_act#d") == {"screen:home": 3, "tap:nav_ppl": 1}
    assert b.counters("_activity", "_act#none") == {}
    b.incr("_activity", "_act#old", "n", ttl=time.time() - 1)
    assert b.counters("_activity", "_act#old") == {}


def test_expired_counter_restarts(b):
    b.incr("u1", "day", "n", ttl=time.time() - 1)
    b.incr("u1", "day", "n", ttl=time.time() - 1)
    assert b.counter("u1", "day", "n") == 0
    assert b.incr("u1", "day", "n", limit=1, ttl=time.time() + 60) == 1
    assert b.incr("u1", "day", "n", limit=1, ttl=time.time() + 60) is None


def test_members(b):
    for m in ["u1a2b3c4d5e6", "deadbeef", "a@b.co"]:
        b.add_member("roster", m)
    b.add_member("roster", "deadbeef")           # idempotent
    b.add_member("other", "zzz")
    assert b.members("roster") == sorted(["u1a2b3c4d5e6", "deadbeef", "a@b.co"])
    b.remove_member("roster", "deadbeef")
    b.remove_member("roster", "not-there")
    assert b.members("roster") == sorted(["u1a2b3c4d5e6", "a@b.co"])
    assert b.members("empty") == []
    for bad in ["../etc", "a/b", "", "x" * 201, "sp ace"]:
        with pytest.raises(ValueError):
            b.add_member("roster", bad)
    with pytest.raises(ValueError):
        b.members("../up")


# ---- backend-specific -------------------------------------------------------

def test_dynamo_writes_ttl_attribute(dyn):
    at = int(time.time()) + 3600
    dyn.save("u1", "k", {"x": 1}, ttl=at)
    item = dyn.table().get_item(Key={"pk": "u1", "sk": "k"})["Item"]
    assert int(item["ttl"]) == at
    dyn.incr("u1", "q", "n", ttl=at)
    item = dyn.table().get_item(Key={"pk": "u1", "sk": "q#ctr"})["Item"]
    assert int(item["ttl"]) == at and int(item["n"]) == 1
    dyn.save("u1", "plain", {"x": 1})
    assert "ttl" not in dyn.table().get_item(Key={"pk": "u1", "sk": "plain"})["Item"]


def test_dynamo_pagination(dyn):
    """Query pages at 1MB; 30 items of ~100KB forces several pages."""
    blob = "x" * 100_000
    for i in range(30):
        dyn.save("big", f"k{i:02d}", blob)
    assert len(dyn.keys("big")) == 30
    assert dyn.delete_all("big") == 30
    assert dyn.keys("big") == []


def test_dynamo_size_guard(dyn):
    with pytest.raises(ValueError, match="write ceiling"):
        dyn.save("u1", "huge", "x" * 400_000)


def test_files_concurrent_incr_respects_limit(files):
    results, barrier = [], threading.Barrier(50)

    def go():
        barrier.wait()
        results.append(files._file_incr("u1", "quota", "msgs", limit=5))

    ts = [threading.Thread(target=go) for _ in range(50)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(r for r in results if r is not None) == [1, 2, 3, 4, 5]
    assert results.count(None) == 45
    assert files._file_counter("u1", "quota", "msgs") == 5


def test_files_concurrent_add_member(files):
    barrier = threading.Barrier(50)

    def go(i):
        barrier.wait()
        files._file_add_member("roster", f"u{i:012x}")

    ts = [threading.Thread(target=go, args=(i,)) for i in range(50)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(files._file_members("roster")) == 50


def test_files_layout_and_legacy_reads(files):
    """Existing data files (no ttl envelope) still load; no stray tmp/lock files listed."""
    d = files.ROOT / "u1"
    d.mkdir()
    (d / "profile.json").write_text('{"sun": "Leo"}')
    assert files._file_load("u1", "profile", None) == {"sun": "Leo"}
    files._file_incr("u1", "q", "n")
    assert files._file_keys("u1") == ["profile", "q#ctr"]
    assert not [p for p in os.listdir(d) if p.startswith(".") and p != ".lock"]


def test_public_api_exported():
    for name in [*API, "append_episode", "log_mention"]:
        assert callable(getattr(store, name))
