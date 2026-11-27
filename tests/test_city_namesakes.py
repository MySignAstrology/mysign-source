"""
Two towns, one label. The place list used to keep only the first place per
name|region|cc, so ~6,200 real towns could not be chosen and the namesake that
was kept was offered under exactly the label of the one she was born in —
Morehead, KY on Chicago time for a birth in Morehead, KY on New York time. And
a bare name from an API client ("Springfield", "Paris") was quietly given the
most populous match. See tools/build_cities.py and app/cities.lookup().
"""
import gzip
import os
import pathlib
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-namesakes-"))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

from app import cities, server
from tests.test_app import sign_in
from tools import build_cities


@pytest.fixture
def c():
    return TestClient(server.app)


# ------------------------------------------------------------ the data itself

def test_both_moreheads_in_kentucky_can_be_picked_and_say_which_is_which():
    hits = [h for h in cities.search("morehead") if h["label"] == "Morehead, KY, US"]
    assert len(hits) == 2, hits
    by_tz = {h["tz"]: h for h in hits}
    assert set(by_tz) == {"America/New_York", "America/Chicago"}
    east, west = by_tz["America/New_York"], by_tz["America/Chicago"]
    assert east["id"] != west["id"]
    # The details differ, and say the zone, because the zones differ.
    assert "Rowan County" in east["detail"] and "New York time" in east["detail"]
    assert "Muhlenberg County" in west["detail"] and "Chicago time" in west["detail"]
    place = cities.resolve(east["id"])
    assert place["tz"] == "America/New_York" and abs(place["lon"] - -83.43) < 0.01


def test_ids_already_stored_still_name_the_same_place():
    # A sample of ids from the build before namesakes were kept, with where
    # they pointed. A saved profile or person must not move.
    before = {
        "Morehead|Kentucky|US": (38.1840, -83.4327, "America/New_York"),
        "Mount Vernon|Indiana|US": (37.9323, -87.8950, "America/Chicago"),
        "Sparks|Texas|US": (30.9285, -97.3597, "America/Chicago"),
        "Springfield|Missouri|US": (37.2153, -93.2982, "America/Chicago"),
        "Chicago|Illinois|US": (41.8500, -87.6500, "America/Chicago"),
        "London|England|GB": (51.5085, -0.1257, "Europe/London"),
        "Lagos|Faro|PT": (37.1028, -8.6742, "Europe/Lisbon"),
    }
    for cid, (lat, lon, tz) in before.items():
        p = cities.resolve(cid)
        assert p, cid
        assert (abs(p["lat"] - lat) < 0.01 and abs(p["lon"] - lon) < 0.01
                and p["tz"] == tz), (cid, p)


def test_renamed_region_ids_still_resolve():
    p = cities.resolve("Annecy|Rhône-Alpes|FR")
    assert p and p["id"] == "Annecy|Auvergne-Rhône-Alpes|FR"


def test_every_id_is_unique():
    db = cities._db()
    n, distinct = db.execute("SELECT count(*), count(DISTINCT id) FROM places").fetchone()
    assert n == distinct and n > 236_000


def test_a_label_shared_by_two_places_always_carries_a_detail():
    # At most one place per shared label (the city itself, "Hanoi") goes
    # without; every namesake beside it says where it is, and no two say the same.
    db = cities._db()
    blank = db.execute("""SELECT label, count(*) n, sum(detail = '') blank FROM places
                          GROUP BY label HAVING n > 1 AND blank > 1""").fetchall()
    assert not blank, [tuple(d) for d in blank[:5]]
    same = db.execute("""SELECT label, detail FROM places WHERE detail != ''
                         GROUP BY label, detail HAVING count(*) > 1""").fetchall()
    assert not same, [tuple(d) for d in same[:5]]


# ----------------------------------------------------------- the build tool

def _geonames_row(gid, name, lat, lon, cc, admin1, admin2, pop, tz):
    c = [""] * 19
    c[0], c[1], c[2], c[4], c[5], c[8], c[10], c[11], c[14], c[17] = (
        gid, name, name, lat, lon, cc, admin1, admin2, pop, tz)
    return "\t".join(c)


def test_build_keeps_namesakes_pins_the_old_owner_and_carries_dropped_places(tmp_path):
    src = tmp_path / "cities500.txt"
    src.write_text("\n".join([
        # GeoNames lists the Chicago-time Morehead first in this dump...
        _geonames_row("1", "Morehead", "37.2711", "-87.1764", "US", "KY", "177",
                      "7622", "America/Chicago"),
        _geonames_row("2", "Morehead", "38.1840", "-83.4327", "US", "KY", "205",
                      "7045", "America/New_York"),
        _geonames_row("3", "Lexington", "38.0498", "-84.4586", "US", "KY", "067",
                      "320000", "America/New_York"),
    ]) + "\n", encoding="utf-8")
    a1 = tmp_path / "admin1.txt"
    a1.write_text("US.KY\tKentucky\tKentucky\t1\n", encoding="utf-8")
    a2 = tmp_path / "admin2.txt"
    a2.write_text("US.KY.177\tMuhlenberg County\tx\t1\nUS.KY.205\tRowan County\tx\t2\n",
                  encoding="utf-8")
    prev = tmp_path / "prev.tsv.gz"
    with gzip.open(prev, "wt", encoding="utf-8") as f:
        # ...but the previous build gave the bare id to the Eastern one, and had
        # a town GeoNames has since dropped.
        f.write("Morehead\t\tKentucky\tUS\t38.1840\t-83.4327\tAmerica/New_York\t7045\n")
        f.write("Gone\t\tKentucky\tUS\t37.0\t-85.0\tAmerica/New_York\t600\n")
    places, stats = build_cities.build(str(src), str(a1), str(a2), str(prev))
    by_gid = {p["gid"]: p for p in places}
    assert by_gid["2"]["suffix"] == "", "the stored id keeps its town"
    assert by_gid["1"]["suffix"] == "1", "the namesake gets its own id"
    assert by_gid["2"]["detail"] == "Rowan County · New York time"
    assert by_gid["1"]["detail"] == "Muhlenberg County · Chicago time"
    assert by_gid["3"]["detail"] == ""
    assert any(p["name"] == "Gone" for p in places) and stats["carried"] == 1
    assert stats["namesakes"] == 1 and stats["zone_split_labels"] == 1


# ------------------------------------------------- a bare name from a client

@pytest.mark.parametrize("value", ["Springfield", "Paris", "London", "Morehead",
                                   "Mount Vernon, IN"])
def test_a_bare_name_that_is_several_places_is_refused_with_candidates(value):
    place, candidates = cities.lookup(value)
    assert place is None and len(candidates) >= 2
    assert all("|" in c["id"] for c in candidates)


@pytest.mark.parametrize("value,cid", [
    ("Paris, TX", "Paris|Texas|US"),
    ("chicago, il", "Chicago|Illinois|US"),
    ("Chicago IL", "Chicago|Illinois|US"),
    ("Los Angeles CA", "Los Angeles|California|US"),
    ("London, GB", "London|England|GB"),
    ("New York City", "New York City|New York|US"),
    ("nyc", "New York City|New York|US"),
    ("Thibodaux", "Thibodaux|Louisiana|US"),
])
def test_a_name_that_is_one_place_still_resolves(value, cid):
    assert cities.resolve(value)["id"] == cid


def test_onboard_refuses_an_ambiguous_name_and_lists_the_choices(c):
    sign_in(c, "namesake-onboard@x.test")
    r = c.post("/api/onboard", json={"name": "Maya", "date": "1999-03-14",
                                     "time": "04:42", "city": "Springfield"})
    assert r.status_code == 400
    d = r.json()["detail"]
    assert d["code"] == "ambiguous_city" and "Pick one" in d["message"]
    labels = {x["label"] for x in d["candidates"]}
    assert {"Springfield, MO, US", "Springfield, IL, US"} <= labels
    pick = next(x for x in d["candidates"] if x["label"] == "Springfield, IL, US")
    r = c.post("/api/onboard", json={"name": "Maya", "date": "1999-03-14",
                                     "time": "04:42", "city": pick["id"]})
    assert r.status_code == 200


def test_connections_refuse_an_ambiguous_name_too(c):
    sign_in(c, "namesake-people@x.test")
    assert c.post("/api/onboard", json={"name": "Maya", "date": "1999-03-14",
                                        "time": "04:42", "city": "Chicago IL"}).status_code == 200
    r = c.post("/api/connections", json={"name": "Theo", "relation": "friend",
                                         "date": "1996-07-02", "time": "21:15", "city": "Paris"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "ambiguous_city"
    r = c.post("/api/connections", json={"name": "Theo", "relation": "friend",
                                         "date": "1996-07-02", "time": "21:15",
                                         "city": "Paris, TX"})
    assert r.status_code == 200, r.text


def test_an_unknown_name_still_gets_the_plain_message(c):
    sign_in(c, "namesake-none@x.test")
    r = c.post("/api/onboard", json={"name": "Maya", "date": "1999-03-14",
                                     "time": "04:42", "city": "Narnia"})
    assert r.status_code == 400 and "couldn't find Narnia" in r.json()["detail"]


# ------------------------------------------------------------------ typos

def test_a_typo_offers_the_near_miss_but_never_resolves_to_it():
    hits = cities.search("Chicgo")
    assert hits and hits[0]["id"] == "Chicago|Illinois|US" and hits[0]["fuzzy"]
    assert cities.resolve("Chicgo") is None
