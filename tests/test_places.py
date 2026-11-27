"""
The wider birthplace search: towns too small for the bundled list.
"""
import os
import pathlib
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-places-"))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

from app import cities, places, server
from tests.test_app import sign_in


class FakeLocation:
    """Shaped like Amazon Location's SearchPlaceIndexForText response."""
    def __init__(self, results):
        self.results, self.calls = results, []

    def search_place_index_for_text(self, **kw):
        self.calls.append(kw)
        return {"Results": [{"Place": p} for p in self.results]}


HAMLET = {"Label": "Ophir, CO, USA", "Region": "Colorado", "Country": "USA",
          "Geometry": {"Point": [-107.8317, 37.8569]},
          "TimeZone": {"Name": "America/Denver", "Offset": -21600}}


@pytest.fixture
def c():
    return TestClient(server.app)


def test_off_without_an_index(monkeypatch):
    monkeypatch.delenv("MYSIGN_PLACE_INDEX", raising=False)
    assert not places.available() and places.search("ophir") == []


def test_results_resolve_to_a_full_birthplace(monkeypatch):
    monkeypatch.setenv("MYSIGN_PLACE_INDEX", "test-index")
    fake = FakeLocation([HAMLET, {**HAMLET, "TimeZone": None}])
    out = places.search("ophir colorado", client=fake)
    assert len(out) == 1, "a place with no timezone can't make a chart"
    assert fake.calls[0]["IndexName"] == "test-index"
    c = cities.resolve(out[0]["id"])
    assert c["tz"] == "America/Denver" and c["country"] == "US" and c["region"] == "Colorado"
    assert abs(c["lat"] - 37.8569) < 1e-3 and c["label"] == "Ophir, CO, USA"


@pytest.mark.parametrize("bad", [
    "geo|95|0|UTC|Nowhere",               # latitude out of range
    "geo|10|10|Mars/Olympus|Somewhere",   # not a timezone
    "geo|10|10|UTC|",                     # no name
    "geo|ten|10|UTC|Somewhere",
])
def test_a_bad_geo_id_is_refused(bad):
    assert cities.resolve(bad) is None


def test_the_route_is_signed_in_and_capped(c, monkeypatch):
    monkeypatch.setenv("MYSIGN_PLACE_INDEX", "test-index")
    monkeypatch.setattr(places, "_client", lambda: FakeLocation([HAMLET]))
    assert c.get("/api/places?q=ophir").status_code == 401
    sign_in(c, "places1@x.com")
    r = c.get("/api/places?q=ophir").json()
    assert r["available"] and r["results"][0]["label"] == "Ophir, CO, USA"
    monkeypatch.setattr(places, "DAILY_CAP", 1)
    assert c.get("/api/places?q=ophir").status_code == 429


def test_onboarding_accepts_a_wider_result(c, monkeypatch):
    monkeypatch.setenv("MYSIGN_PLACE_INDEX", "test-index")
    monkeypatch.setattr(places, "_client", lambda: FakeLocation([HAMLET]))
    sign_in(c, "places2@x.com")
    cid = c.get("/api/places?q=ophir").json()["results"][0]["id"]
    r = c.post("/api/onboard", json={"name": "Maya", "date": "1999-03-14",
                                     "time": "04:42", "city": cid})
    assert r.status_code == 200, r.text
    assert c.get("/api/state").json().get("signed_in") is not False
