"""
Which astrology this is.

The engine had taken positions on contested questions by being written, which is
the wrong way to decide them. These tests hold the default steady — everything
was calibrated against Placidus — while proving the alternatives actually work,
so the decision stays cheap when an astrologer makes it.
"""
import os
import pathlib
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-trad-"))
os.environ["MYSIGN_DEV_AUTH"] = "1"

import app.server as server
from engine import interpret as I
from engine import tradition
from engine.chart import natal
from engine.store import load
from tests.test_app import last_uid, onboard, sign_in

MAYA = (1999, 3, 14, 10.7, 41.8781, -87.6298)


@pytest.fixture
def c():
    return TestClient(server.app)


def test_the_default_is_unchanged():
    """Everything — salience thresholds, the voice test, the corpus — was built
    against Placidus. Changing the default is a product decision, not a tidy-up."""
    assert tradition.house_system() == "placidus"
    assert tradition.DEFAULT_RULERS == "traditional"


@pytest.mark.parametrize("name", sorted(tradition.HOUSE_SYSTEMS))
def test_every_house_system_produces_a_usable_chart(name):
    nat = natal(*MAYA, houses=name)
    houses = [p["house"] for p in nat["planets"].values()]
    assert all(1 <= h <= 12 for h in houses)
    cusps = nat["house_cusps"]
    total = sum((cusps[(i + 1) % 12] - cusps[i]) % 360 for i in range(12))
    assert abs(total - 360) < 1e-6


def test_whole_sign_moves_the_fixture_chart_off_the_twelfth():
    """
    The strongest answer the voice test produced rested on a 12th-house
    stellium. Under whole sign it is a 1st-house stellium and that answer would
    be wrong. This is the stake in the decision, asserted.
    """
    placidus = natal(*MAYA, houses="placidus")
    whole = natal(*MAYA, houses="whole_sign")
    for body in ("Moon", "Uranus", "Neptune"):
        assert placidus["planets"][body]["house"] == 12
        assert whole["planets"][body]["house"] == 1


def test_the_ascendant_is_the_same_in_every_system():
    """They are all built from it; only the other cusps differ."""
    ascs = {s: round(natal(*MAYA, houses=s)["angles"]["Ascendant"]["lon"], 6)
            for s in tradition.HOUSE_SYSTEMS}
    assert len(set(ascs.values())) == 1, ascs


def test_rulership_scheme_changes_the_chart_ruler():
    """
    This was broken: the endpoint stored the scheme and `analyze` read the
    environment, so the setting changed the label and not the astrology.
    """
    nat = natal(*MAYA)
    assert I.analyze(nat, rulerships="traditional")["chart_ruler"]["planet"] == "Saturn"
    assert I.analyze(nat, rulerships="modern")["chart_ruler"]["planet"] == "Uranus"


@pytest.mark.parametrize("bad", ["astrolabe", "PLACIDUS_X", ""])
def test_an_unknown_system_is_refused_not_guessed(bad):
    with pytest.raises(ValueError):
        tradition.house_system(bad or "nope")


def test_the_mixed_pairing_is_flagged():
    assert tradition.describe("placidus", "traditional")["coherent"] is False
    assert tradition.describe("whole_sign", "traditional")["coherent"] is True
    assert tradition.describe("placidus", "modern")["coherent"] is True


# ------------------------------------------------------------------ the API

def test_switching_changes_the_reading(c):
    sign_in(c, "trad1@x.com"); onboard(c)

    before = c.get("/api/reading").json()
    moon_before = next(x for x in before["cards"] if x["label"] == "Moon")["sub"]

    r = c.post("/api/tradition", json={"houses": "whole_sign"})
    assert r.status_code == 200 and r.json()["houses_label"] == "whole sign"

    after = c.get("/api/reading").json()
    moon_after = next(x for x in after["cards"] if x["label"] == "Moon")["sub"]
    assert moon_before == "12th house" and moon_after == "1st house"
    assert after["tradition"]["houses"] == "whole_sign"


def test_the_cache_cannot_serve_the_wrong_tradition(c):
    """Keyed rather than invalidated — switching back must not return the
    other system's cached reading."""
    sign_in(c, "trad2@x.com"); onboard(c)
    uid = last_uid()

    first = c.get("/api/reading").json()
    c.post("/api/tradition", json={"houses": "whole_sign"})
    second = c.get("/api/reading").json()
    c.post("/api/tradition", json={"houses": "placidus"})
    third = c.get("/api/reading").json()

    assert first["cards"] == third["cards"], "switching back gave a different chart"
    assert second["cards"] != first["cards"]
    assert load(uid, "reading-placidus-traditional", None)
    assert load(uid, "reading-whole_sign-traditional", None)


def test_an_unknown_system_is_a_400_not_a_500(c):
    sign_in(c, "trad3@x.com"); onboard(c)
    assert c.post("/api/tradition", json={"houses": "astrolabe"}).status_code == 400
    assert c.post("/api/tradition", json={"rulerships": "vedic"}).status_code == 400


def test_the_current_tradition_is_visible(c):
    sign_in(c, "trad4@x.com"); onboard(c)
    body = c.get("/api/tradition").json()
    assert body["houses"] == "placidus"
    assert set(body["options"]) == set(tradition.HOUSE_SYSTEMS)
    assert "whole sign" in body["options"]["whole_sign"] or body["options"]["whole_sign"]
