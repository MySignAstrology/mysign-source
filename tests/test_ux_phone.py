"""
The phone UX audit's server-side fixes (audit/ux.md): where her crisis line
comes from, Settings that draw without waiting on the reading, people added
with a birthday only, typo-tolerant city search, and a paywall with nothing to
buy. Each test names the finding it pins.
"""
import os
import tempfile

import pytest

os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-test-"))
os.environ["MYSIGN_DEV_AUTH"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)

from fastapi.testclient import TestClient

import app.server as server
from app import cities
from engine import placement_kb


@pytest.fixture
def c():
    return TestClient(server.app)


def sign_in(c, email, tz=None):
    if tz:
        c.headers["X-Tz"] = tz
    code = c.post("/api/auth/request", json={"email": email}).json()["dev_code"]
    c.post("/api/auth/verify", json={"email": email, "code": code})
    c.post("/api/consent", json={"ai": True})
    return c


def onboard(c, city="Lagos|Lagos|NG", time="04:42", **extra):
    return c.post("/api/onboard", json={"name": "Ada", "date": "1994-06-02", "time": time,
                                        "city": city, **extra})


# ----------------------------------------------------- H2: current country

@pytest.mark.parametrize("tz,cc", [("America/New_York", "US"), ("Europe/London", "GB"),
                                   ("Africa/Lagos", "NG"), ("Asia/Kolkata", "IN"),
                                   ("America/Toronto", "CA"), ("Etc/UTC", None), ("", None),
                                   ("Not/AZone", None)])
def test_country_from_the_browser_time_zone(tz, cc):
    assert cities.country_for_tz(tz) == cc


def test_born_in_lagos_living_in_new_york_gets_the_us_line(c):
    """H2: the crisis country was the birth city's. Born in Lagos, browsing
    from New York: the line is 988, and the birth country is kept apart."""
    sign_in(c, "lagos-ny@x.com", tz="America/New_York")
    r = onboard(c, city="Lagos|Lagos|NG").json()
    assert r["country"] == "US" and r["country_source"] == "timezone"
    assert "988" in r["crisis_footer"]                    # H3: footer right away
    s = c.get("/api/state").json()
    assert s["country"] == "US" and s["birth_country"] == "NG"
    assert "988" in s["crisis_footer"]


def test_the_country_she_confirms_wins_over_the_time_zone(c):
    sign_in(c, "confirmed@x.com", tz="America/New_York")
    r = onboard(c, city="Lagos|Lagos|NG", country="gb").json()
    assert r["country"] == "GB" and r["country_source"] == "confirmed"


def test_a_made_up_country_on_the_form_falls_back_to_the_zone(c):
    sign_in(c, "zz-form@x.com", tz="Europe/London")
    assert onboard(c, city="Lagos|Lagos|NG", country="ZZ").json()["country"] == "GB"


def test_no_zone_at_all_falls_back_to_the_birth_country(c):
    c.headers.pop("X-Tz", None)
    sign_in(c, "nozone@x.com")
    assert onboard(c, city="Lagos|Lagos|NG").json()["country"] == "NG"


def test_state_offers_a_guess_before_onboarding(c):
    sign_in(c, "guess@x.com", tz="Europe/Berlin")
    s = c.get("/api/state").json()
    assert s["onboarded"] is False and s["country_guess"] == "DE"


def test_country_must_be_a_real_one(c):
    """Low 4: "zz" saved silently and handed her the directory."""
    sign_in(c, "zz@x.com", tz="America/Chicago")
    onboard(c, city="chicago, il")
    assert c.post("/api/profile/country", json={"country": "zz"}).status_code == 400
    r = c.post("/api/profile/country", json={"country": "ie"})
    assert r.status_code == 200 and r.json()["country"] == "IE"


def test_country_list_has_names_for_the_picker(c):
    rows = c.get("/api/countries").json()["countries"]
    codes = {r["code"] for r in rows}
    assert {"US", "GB", "NG", "IN"} <= codes and "CS" not in codes
    assert next(r for r in rows if r["code"] == "GB")["name"] == "United Kingdom"
    assert [r["name"] for r in rows] == sorted(r["name"] for r in rows)


# --------------------------------------------- H4: Settings without the reading

def test_state_carries_what_settings_draws(c):
    """H4: Settings waited fifteen seconds on /api/reading for the birth line,
    the add-time row and the morning-people toggle. State has them now."""
    sign_in(c, "settings@x.com", tz="America/Chicago")
    onboard(c, city="chicago, il", time="")
    c.post("/api/profile/push-people", json={"on": False})
    s = c.get("/api/state").json()
    assert "(time unknown)" in s["birth"] and "Chicago" in s["birth"]
    assert s["time_known"] is False
    assert s["push_people"] is False


# --------------------------------------------- M4: a person with a birthday only

def test_a_person_can_be_added_without_a_birth_city(c):
    sign_in(c, "nocity@x.com", tz="America/Chicago")
    onboard(c, city="chicago, il")
    r = c.post("/api/connections", json={"name": "Jo", "date": "1996-04-20"})
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["place_known"] is False and row["birth_time_known"] is False
    assert row["time_precision"] == "unknown" and "(place unknown)" in row["born"]
    assert c.get("/api/connections/" + row["id"]).status_code == 200


def test_a_time_without_a_city_is_set_aside_not_read_as_utc(c):
    sign_in(c, "nocity-time@x.com", tz="America/Chicago")
    onboard(c, city="chicago, il")
    row = c.post("/api/connections", json={"name": "Jo", "date": "1996-04-20",
                                           "time": "09:15"}).json()
    assert row["birth_time"] is None and row["time_precision"] == "unknown"
    assert row["birth_time_known"] is False


# ------------------------------------------------------- M3: typos in a city

# The suggester itself is main's (#89, cities._near_misses); these pin what the
# picker's "did you mean" relies on.
@pytest.mark.parametrize("typed,want", [("chcago", "Chicago, IL, US")])
def test_a_typo_still_finds_the_city(typed, want):
    rows = cities.search(typed)
    assert rows and rows[0]["label"].startswith(want) and rows[0]["fuzzy"] is True


def test_gibberish_still_finds_nothing():
    assert cities.search("zzzzqx") == []


def test_an_exact_match_is_not_marked_as_a_guess():
    assert not any(r.get("fuzzy") for r in cities.search("chicago"))


def test_resolve_never_takes_a_typo_guess():
    """A plain-name legacy id must not resolve to a city she didn't name."""
    assert cities.resolve("Narnia") is None


# ------------------------------------------------------------ H5: the paywall

def test_waitlist_intent_is_counted(c):
    sign_in(c, "waitlist@x.com", tz="America/Chicago")
    onboard(c, city="chicago, il")
    r = c.post("/api/plan/intent", json={"plan": "waitlist", "surface": "chat_cap"})
    assert r.status_code == 200 and r.json()["available"] is False


# ------------------------------------------------- M14: "Leo's personality"

def test_the_moon_explainer_does_not_read_as_the_sign_leo():
    for point in ("sun", "moon"):
        about = placement_kb.ROLE[point]["about"]
        assert "Leo's" not in about and "astrologer Alan Leo" in about


def test_the_crisis_line_is_for_where_she_lives_not_where_she_was_born(c):
    """H2 through engine/crisis.py: born in Mumbai, living in London, she gets
    the UK line, never India's 14416."""
    from engine import crisis
    sign_in(c, "mumbai-london@x.com", tz="Europe/London")
    r = onboard(c, city="Mumbai|Maharashtra|IN").json()
    assert r["crisis_footer"] == crisis.footer("GB")
    assert "14416" not in r["crisis_footer"]
