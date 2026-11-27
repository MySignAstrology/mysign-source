"""
Invites, linked people, and what a linked person can see of someone's day.

The rules these pin are the privacy promises in the UI copy:
- the public preview gives away a first name and nothing else;
- nobody is linked until the friend accepts with their own chart;
- only the 1-10 of a check-in is ever shared, never what anyone writes;
- "share how I'm feeling" off means even the number stays private;
- deleting an account takes the person out of everyone's People.
"""
import pathlib
import time

from fastapi.testclient import TestClient

import app.server as server
from app import edge
from engine import store
from tests.test_app import last_uid, sign_in  # noqa: F401  (sets the dev-auth env first)


def person(email, name="Pat", date="1996-07-02", time_="21:15"):
    c = TestClient(server.app)
    code = c.post("/api/auth/request", json={"email": email}).json()["dev_code"]
    c.post("/api/auth/verify", json={"email": email, "code": code})
    c.post("/api/consent", json={"ai": True})
    r = c.post("/api/onboard", json={"name": name, "date": date, "time": time_, "city": "Chicago IL"})
    assert r.status_code == 200, r.text
    return c


def invite(c):
    return c.post("/api/invites").json()


def linked_row(c, name):
    rows = c.get("/api/connections").json()["connections"]
    return next(r for r in rows if r["name"] == name)


def test_preview_is_public_and_only_a_first_name():
    a = person("inv-a@x.com", name="Maya Rose")
    inv = invite(a)
    assert inv["url"].endswith(f"/app?invite={inv['token']}")
    anon = TestClient(server.app)
    assert anon.get(f"/api/invites/{inv['token']}").json() == {"from": "Maya", "valid": True}


def test_bad_or_expired_tokens_are_404():
    a = person("inv-exp@x.com")
    anon = TestClient(server.app)
    assert anon.get("/api/invites/nope").status_code == 404
    inv = invite(a)
    rec = store.load("_invites", f"inv-{inv['token']}", None)
    store.save("_invites", f"inv-{inv['token']}", {**rec, "expires": time.time() - 1})
    assert anon.get(f"/api/invites/{inv['token']}").status_code == 404


def test_accept_needs_consent_and_not_your_own_link():
    a, b = person("inv-own-a@x.com", "Maya"), person("inv-own-b@x.com", "Theo")
    inv = invite(a)
    assert a.post(f"/api/invites/{inv['token']}/accept", json={"share_chart": True}).status_code == 400
    assert b.post(f"/api/invites/{inv['token']}/accept", json={"share_chart": False}).status_code == 400
    assert b.get("/api/connections").json()["count"] == 0       # nothing happened


def test_accept_links_both_ways_once():
    a, b = person("inv-both-a@x.com", "Maya"), person("inv-both-b@x.com", "Theo")
    inv = invite(a)
    for _ in range(2):                                           # idempotent
        r = b.post(f"/api/invites/{inv['token']}/accept", json={"share_chart": True})
        assert r.status_code == 200 and r.json()["person"]["name"] == "Maya"
    assert [x["name"] for x in a.get("/api/connections").json()["connections"]] == ["Theo"]
    assert [x["name"] for x in b.get("/api/connections").json()["connections"]] == ["Maya"]
    row = linked_row(a, "Theo")
    assert row["linked"] and row["today"] and row["overall"] is not None


def test_only_the_number_is_shared_and_only_when_on():
    a, b = person("inv-mood-a@x.com", "Maya"), person("inv-mood-b@x.com", "Theo")
    b.post(f"/api/invites/{invite(a)['token']}/accept", json={"share_chart": True, "share_mood": True})
    assert linked_row(a, "Theo")["their_day"] == {"mood": None}  # not checked in yet
    b.post("/api/checkin", json={"mood": 8, "text": "the thing I'd never tell her"})
    assert linked_row(a, "Theo")["their_day"]["mood"] == 8
    everything = str(a.get("/api/connections").json()) + str(a.get("/api/people/today").json())
    assert "never tell her" not in everything
    b.post("/api/profile/share-mood", json={"on": False})
    assert linked_row(a, "Theo")["their_day"] == {"private": True}
    assert '"mood": 8' not in str(a.get("/api/people/today").json())
    assert b.get("/api/state").json()["share_mood"] is False


def test_declining_mood_at_accept_is_respected():
    a, b = person("inv-no-a@x.com", "Maya"), person("inv-no-b@x.com", "Theo")
    b.post(f"/api/invites/{invite(a)['token']}/accept", json={"share_chart": True, "share_mood": False})
    b.post("/api/checkin", json={"mood": 3, "text": ""})
    assert linked_row(a, "Theo")["their_day"] == {"private": True}


def test_people_you_entered_yourself_share_nothing():
    a = person("inv-manual@x.com", "Maya")
    cid = a.get("/api/cities", params={"q": "Chicago"}).json()[0]["id"]
    a.post("/api/connections", json={"name": "Mom", "date": "1970-05-22", "city": cid})
    row = linked_row(a, "Mom")
    assert row["linked"] is False and row["their_day"] is None


def test_deleting_an_account_leaves_everyones_people():
    a, b = person("inv-del-a@x.com", "Maya"), person("inv-del-b@x.com", "Theo")
    b.post(f"/api/invites/{invite(a)['token']}/accept", json={"share_chart": True})
    assert a.get("/api/connections").json()["count"] == 1
    b.delete("/api/account")
    assert a.get("/api/connections").json()["count"] == 0


# ------------------------------------------------ security audit H4 / M2 / M4
#
# p1_social.py and p3_delete.py from the audit, as regressions: a forwarded
# link admitted strangers, accepting an invite switched mood sharing back on
# for everyone, unlinking was one-way, and deletion left invites and reports
# built from the deleted person's chart in other people's rows.



def uid_of(email):
    return server.auth.uid_for(email, create=False)


def accept(c, token, **k):
    return c.post(f"/api/invites/{token}/accept", json={"share_chart": True, **k})


def test_a_link_admits_one_person_and_a_forwarded_copy_gets_nothing():
    a, b = person("sec-one-a@x.com", "Alice"), person("sec-one-b@x.com", "Bob")
    stranger = person("sec-one-c@x.com", "Carol")
    tok = invite(a)["token"]
    assert accept(b, tok).status_code == 200
    r = accept(stranger, tok)
    assert r.status_code == 404 and "already been used" in r.json()["detail"]
    assert stranger.get("/api/connections").json()["count"] == 0
    assert [x["name"] for x in a.get("/api/connections").json()["connections"]] == ["Bob"]
    assert accept(b, tok).status_code == 200                     # Bob again: still fine


def test_the_preview_says_own_link_already_linked_or_used_up_up_front():
    a, b = person("sec-pv-a@x.com", "Alice"), person("sec-pv-b@x.com", "Bob")
    tok = invite(a)["token"]
    assert a.get(f"/api/invites/{tok}").json()["own"] is True
    assert b.get(f"/api/invites/{tok}").json() == {"from": "Alice", "valid": True,
                                                   "own": False, "linked": False}
    accept(b, tok)
    assert b.get(f"/api/invites/{tok}").json()["linked"] is True
    anon = TestClient(server.app).get(f"/api/invites/{tok}").json()
    assert anon == {"from": "Alice", "valid": False, "reason": "used"}


def test_she_sees_who_joined_and_can_cancel_a_link():
    a, b = person("sec-list-a@x.com", "Alice"), person("sec-list-b@x.com", "Bob Stone")
    used, waiting = invite(a)["token"], invite(a)["token"]
    accept(b, used)
    mine = {i["token"]: i for i in a.get("/api/invites").json()["invites"]}
    assert mine[used]["open"] is False and mine[waiting]["open"] is True
    (j,) = mine[used]["joined"]
    assert j["name"] == "Bob" and j["person_id"] == linked_row(a, "Bob Stone")["id"]
    assert "uid" not in str(mine)                                # no account ids
    assert b.delete(f"/api/invites/{waiting}").status_code == 404    # not his
    assert a.delete(f"/api/invites/{waiting}").json() == {"ok": True}
    assert TestClient(server.app).get(f"/api/invites/{waiting}").status_code == 404
    assert waiting not in str(a.get("/api/invites").json())


def test_links_expire_on_their_own_and_are_capped():
    a = person("sec-cap-a@x.com", "Alice")
    first = invite(a)
    assert first["expires_in_days"] == 7 and first["max_uses"] == 1
    _, ttl = store._read(store._p("_invites", f"inv-{first['token']}"))
    assert ttl and ttl <= time.time() + 7 * 86400 + 5            # the table forgets it
    for _ in range(server.INVITE_OPEN_MAX - 1):
        invite(a)
    r = a.post("/api/invites")
    assert r.status_code == 429 and "Cancel one" in r.json()["detail"]


def test_mood_sharing_is_opt_in_at_accept():
    a, b = person("sec-optin-a@x.com", "Alice"), person("sec-optin-b@x.com", "Bob")
    accept(b, invite(a)["token"])                                # no share_mood sent
    b.post("/api/checkin", json={"mood": 4, "text": ""})
    assert linked_row(a, "Bob")["their_day"] == {"private": True}


def test_accepting_an_invite_never_resets_the_global_switch():
    a, b = person("sec-flip-a@x.com", "Alice"), person("sec-flip-b@x.com", "Bob")
    c = person("sec-flip-c@x.com", "Carol")
    accept(b, invite(a)["token"], share_mood=True)
    b.post("/api/profile/share-mood", json={"on": False})
    b.post("/api/checkin", json={"mood": 2, "text": "private bad day"})
    r = accept(b, invite(c)["token"], share_mood=True).json()
    assert r["mood_master_off"] is True and r["mood_shared"] is False
    assert b.get("/api/state").json()["share_mood"] is False
    assert linked_row(a, "Bob")["their_day"] == {"private": True}
    assert linked_row(c, "Bob")["their_day"] == {"private": True}


def test_mood_sharing_is_per_person():
    a, b = person("sec-pp-a@x.com", "Alice"), person("sec-pp-b@x.com", "Bob")
    c = person("sec-pp-c@x.com", "Carol")
    accept(b, invite(a)["token"], share_mood=True)
    accept(b, invite(c)["token"], share_mood=True)
    b.post("/api/checkin", json={"mood": 6, "text": ""})
    alice_for_bob = linked_row(b, "Alice")["id"]
    r = b.post(f"/api/connections/{alice_for_bob}/share-mood", json={"on": False})
    assert r.json() == {"on": False, "master": True}
    assert linked_row(a, "Bob")["their_day"] == {"private": True}
    assert linked_row(c, "Bob")["their_day"]["mood"] == 6
    today = b.get(f"/api/connections/{alice_for_bob}/today").json()["person"]
    assert today["i_share_mood"] == {"on": False, "master": True}
    b.post(f"/api/connections/{alice_for_bob}/share-mood", json={"on": True})
    assert linked_row(a, "Bob")["their_day"]["mood"] == 6
    cid = a.get("/api/cities", params={"q": "Chicago"}).json()[0]["id"]
    mom = a.post("/api/connections", json={"name": "Mom", "date": "1970-05-22", "city": cid}).json()
    assert a.post(f"/api/connections/{mom['id']}/share-mood", json={"on": True}).status_code == 400


def test_removing_a_linked_person_unlinks_both_sides():
    a, b = person("sec-unl-a@x.com", "Alice"), person("sec-unl-b@x.com", "Bob")
    accept(b, invite(a)["token"], share_mood=True)
    bob_for_alice = linked_row(a, "Bob")["id"]
    a.get(f"/api/connections/{bob_for_alice}/deep")              # builds a report from Bob's chart
    a.post(f"/api/connections/{bob_for_alice}/checkin", json={"feel": "closer", "note": "n"})
    alice_for_bob = linked_row(b, "Alice")["id"]
    assert b.delete(f"/api/connections/{alice_for_bob}").json() == {"ok": True}
    assert a.get("/api/connections").json()["count"] == 0        # Alice loses Bob too
    ua, ub = uid_of("sec-unl-a@x.com"), uid_of("sec-unl-b@x.com")
    assert store.load(ua, f"compat-{bob_for_alice}", None) is None
    assert store.load(ua, f"pulse-{bob_for_alice}", None) is None
    assert not store.load(ua, "links", None) and not store.load(ub, "links", None)


def test_a_linked_row_never_carries_the_other_account_id():
    a, b = person("sec-ids-a@x.com", "Alice"), person("sec-ids-b@x.com", "Bob")
    accept(b, invite(a)["token"])
    row = a.get(f"/api/connections/{linked_row(a, 'Bob')['id']}").json()
    assert "linked_uid" not in row and row["linked"] is True
    assert uid_of("sec-ids-b@x.com") not in str(row)


def test_deleting_an_account_leaves_nothing_of_it_in_other_peoples_data():
    a, b = person("sec-del-a@x.com", "Alice"), person("sec-del-b@x.com", "Bob")
    c = person("sec-del-c@x.com", "Carol")
    accept(b, invite(a)["token"])
    accept(c, invite(a)["token"])
    accept(a, invite(c)["token"])                                # already linked: a no-op
    invite(a)                                                    # one never used
    row = linked_row(c, "Alice")
    c.get(f"/api/connections/{row['id']}/deep")
    c.post(f"/api/connections/{row['id']}/checkin", json={"feel": "closer", "note": "sweet"})
    ua = uid_of("sec-del-a@x.com")
    assert a.delete("/api/account").json() == {"deleted": True}
    root = pathlib.Path(store.ROOT)
    left = [str(p.relative_to(root)) for p in root.rglob("*.json")
            if ua in p.read_text(errors="ignore")]
    assert left == []
    uc = uid_of("sec-del-c@x.com")
    assert store.load(uc, f"compat-{row['id']}", None) is None


# ------------------------------------------------------------ links (M2)

def test_links_are_built_on_the_public_url_never_the_host_header(monkeypatch):
    a = person("sec-host-a@x.com", "Alice", time_="")             # no time: may ask mom
    monkeypatch.setenv("MYSIGN_DEV_AUTH", "0")                   # as on Lambda
    monkeypatch.delenv("MYSIGN_PUBLIC_URL", raising=False)
    monkeypatch.delenv("MYSIGN_DOMAIN", raising=False)
    evil = {"host": "abcd1234.lambda-url.us-east-2.on.aws"}
    url = a.post("/api/invites", headers=evil).json()["url"]
    assert url.startswith("https://mysignapp.com/app?invite=")
    bt = a.post("/api/birthtime/request", headers=evil)
    assert bt.status_code == 200 and bt.json()["url"].startswith("https://mysignapp.com/bt/")
    assert "lambda-url" not in bt.text
    monkeypatch.setenv("MYSIGN_DOMAIN", "staging.mysignapp.com")
    assert edge.public_url() == "https://staging.mysignapp.com"
    monkeypatch.setenv("MYSIGN_PUBLIC_URL", "https://example.test/")
    assert edge.public_url() == "https://example.test"


def test_the_inviter_shares_her_mood_only_once_she_says_yes():
    a, b = person("sec-inv-mood-a@x.com", "Alice"), person("sec-inv-mood-b@x.com", "Bob")
    accept(b, invite(a)["token"], share_mood=True)
    a.post("/api/checkin", json={"mood": 7, "text": ""})
    assert linked_row(b, "Alice")["their_day"] == {"private": True}
    a.post(f"/api/connections/{linked_row(a, 'Bob')['id']}/share-mood", json={"on": True})
    assert linked_row(b, "Alice")["their_day"]["mood"] == 7


def test_the_purge_tool_clears_legacy_links_and_indexes_live_ones():
    from tools import purge_invites
    a = person("sec-purge-a@x.com", "Alice")
    ua = uid_of("sec-purge-a@x.com")
    legacy = {"from": ua, "name": "Alice", "created": time.time(),
              "expires": time.time() + 86400, "used_by": []}           # the old shape
    store.save("_invites", "inv-legacyLiveToken01", legacy)
    store.save("_invites", "inv-legacyDeadToken01", {**legacy, "expires": time.time() - 1})
    store.save("_invites", "inv-legacyGoneToken01", {**legacy, "from": "u000000000000"})
    dry = purge_invites.run()
    assert dry["deleted"] >= 2 and dry["updated"] >= 1
    assert store.load("_invites", "inv-legacyGoneToken01", None)       # dry run wrote nothing
    purge_invites.run(write=True)
    assert store.load("_invites", "inv-legacyGoneToken01", None) is None
    assert "legacyLiveToken01" in str(a.get("/api/invites").json())
    a.delete("/api/account")
    assert store.load("_invites", "inv-legacyLiveToken01", None) is None


# ---------------------------------------------------------------- sent cards

def saved(c, name="Theo Lane", date="1997-06-02"):
    r = c.post("/api/connections", json={"name": name, "relation": "talking", "date": date,
                                         "time": "09:15", "city": "Chicago IL"})
    assert r.status_code == 200, r.text
    return r.json()


def token_of(sent):
    return sent["url"].rsplit("/", 1)[1]


def test_the_score_card_carries_names_and_score_never_birth_data():
    a = person("card-a@x.com", name="Maya Rose")
    row = saved(a, "<Theo & Co>")
    r = a.get(f"/api/card/compat/{row['id']}.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert "Maya &amp; &lt;Theo" in r.text and f">{row['overall']}<" in r.text
    assert "mysignapp.com" in r.text
    assert "/app?invite" not in r.text and "/api/i/" not in r.text   # a post is no door
    for leak in ("1997", "06-02", "09:15", "Chicago"):
        assert leak not in r.text, leak
    full = saved(a, "Theodore Lane")
    svg = a.get(f"/api/card/compat/{full['id']}.svg?initials=1").text
    assert "T." in svg and "Theodore" not in svg and "Lane" not in svg
    assert TestClient(server.app).get(f"/api/card/compat/{row['id']}.svg").status_code == 401


def test_a_card_without_an_exact_birth_time_says_it_is_an_estimate():
    a = person("card-est@x.com")
    row = a.post("/api/connections", json={"name": "Theo", "date": "1997-06-02"}).json()
    assert "An estimate" in a.get(f"/api/card/compat/{row['id']}.svg").text
    exact = saved(a, "Sam")
    assert "An estimate" not in a.get(f"/api/card/compat/{exact['id']}.svg").text


def test_a_sent_link_shows_her_name_and_score_and_nothing_about_them():
    a = person("send-a@x.com", name="Maya Rose")
    row = saved(a, "Theo Lane")
    sent = a.post(f"/api/connections/{row['id']}/send").json()
    assert sent["url"].endswith(f"/api/i/{token_of(sent)}") and sent["score"] == row["overall"]
    anon = TestClient(server.app)
    assert anon.get(f"/api/invites/{token_of(sent)}").json() == \
        {"from": "Maya", "valid": True, "score": row["overall"]}
    page = anon.get(sent["url"].replace(sent["url"].split("/api/")[0], ""), follow_redirects=False)
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert f"Maya got {row['overall']} with you" in page.text
    assert f"/app?invite={token_of(sent)}" in page.text and 'content="noindex"' in page.text
    for leak in ("Theo", "Lane", "1997", "Chicago", "Rose"):
        assert leak not in page.text, leak


def test_sending_twice_is_one_link_and_a_plain_invite_stays_a_first_name():
    a = person("send-twice@x.com")
    row = saved(a)
    one = a.post(f"/api/connections/{row['id']}/send").json()
    two = a.post(f"/api/connections/{row['id']}/send").json()
    assert one["url"] == two["url"] and len(a.get("/api/invites").json()["invites"]) == 1
    assert a.get("/api/invites").json()["invites"][0]["for"] == "Theo"
    plain = invite(a)
    assert TestClient(server.app).get(f"/api/invites/{plain['token']}").json() == \
        {"from": "Pat", "valid": True}


def test_a_link_preview_never_counts_or_uses_up_the_link():
    from engine import funnel
    a = person("send-preview@x.com")
    t = token_of(a.post(f"/api/connections/{saved(a)['id']}/send").json())
    before = funnel.report(1)["totals"]["card_opened"]
    anon = TestClient(server.app)
    for _ in range(3):
        anon.get(f"/api/i/{t}", follow_redirects=False)
    assert funnel.report(1)["totals"]["card_opened"] == before
    assert anon.get(f"/api/invites/{t}").json()["valid"] is True
    anon.get(f"/api/invites/{t}")                      # the app asks twice on load
    assert funnel.report(1)["totals"]["card_opened"] == before + 1


def test_bad_used_or_cancelled_links_fall_back_with_no_name_or_score():
    a = person("send-bad@x.com", name="Maya")
    row = saved(a)
    b = person("send-bad-b@x.com", name="Ari", date="1999-06-21")
    t = token_of(a.post(f"/api/connections/{row['id']}/send").json())
    assert b.post(f"/api/invites/{t}/accept", json={"share_chart": True}).status_code == 200
    anon = TestClient(server.app)
    for tok in (t, "no-such-token-at-all"):
        page = anon.get(f"/api/i/{tok}", follow_redirects=False)
        assert "Maya" not in page.text and "<script>" not in page.text
    # The app is what explains a dead link, so it needs the token to ask about.
    used = anon.get(f"/api/i/{t}", follow_redirects=False).text
    assert f"/app?invite={t}" in used and "<title>My Sign</title>" in used
    bad = anon.get("/api/i/%3Cscript%3Ealert(1)", follow_redirects=False)
    assert bad.status_code == 200 and "invite=" not in bad.text and "<script>" not in bad.text


def test_the_right_person_joining_takes_over_her_saved_row():
    a = person("merge-a@x.com", name="Maya")
    row = saved(a, "Theo", date="1999-06-21")
    assert a.post(f"/api/connections/{row['id']}/checkin",
                  json={"feel": "strained", "note": "kept"}).status_code == 200
    t = token_of(a.post(f"/api/connections/{row['id']}/send").json())
    b = person("merge-b@x.com", name="Theo Lane", date="1999-06-21")
    assert b.post(f"/api/invites/{t}/accept", json={"share_chart": True}).status_code == 200
    rows = a.get("/api/connections").json()
    rows = rows["connections"] if isinstance(rows, dict) else rows
    assert [r["id"] for r in rows] == [row["id"]]      # one Theo, same id, so her notes stay
    assert a.get(f"/api/connections/{row['id']}/deep").json()["person"]["linked"] is True
    assert "kept" in a.get(f"/api/connections/{row['id']}/today").text
    assert a.post(f"/api/connections/{row['id']}/send").status_code == 409


def test_a_forwarded_link_never_overwrites_the_person_she_saved():
    a = person("merge-x@x.com", name="Maya")
    row = saved(a, "Theo", date="1997-06-02")
    t = token_of(a.post(f"/api/connections/{row['id']}/send").json())
    stranger = person("merge-y@x.com", name="Ari", date="1999-06-21")
    assert stranger.post(f"/api/invites/{t}/accept", json={"share_chart": True}).status_code == 200
    rows = a.get("/api/connections").json()
    rows = rows["connections"] if isinstance(rows, dict) else rows
    assert sorted(r["name"] for r in rows) == ["Ari", "Theo"]
    assert next(r for r in rows if r["id"] == row["id"])["name"] == "Theo"


def test_forgetting_someone_cancels_the_link_sent_about_them():
    a = person("send-drop@x.com")
    row = saved(a)
    t = token_of(a.post(f"/api/connections/{row['id']}/send").json())
    assert a.delete(f"/api/connections/{row['id']}").status_code == 200
    assert TestClient(server.app).get(f"/api/invites/{t}").status_code == 404
    assert a.get("/api/invites").json()["invites"] == []
    assert a.post("/api/connections/nope/send").status_code == 404
    assert TestClient(server.app).post(f"/api/connections/{row['id']}/send").status_code == 401


def test_the_funnel_counts_sent_opened_and_joined():
    from engine import funnel
    before = funnel.report(1)["totals"]
    a = person("send-funnel@x.com")
    t = token_of(a.post(f"/api/connections/{saved(a)['id']}/send").json())
    b = person("send-funnel-b@x.com", name="Ari", date="1999-06-21")
    b.get(f"/api/invites/{t}")
    b.post(f"/api/invites/{t}/accept", json={"share_chart": True})
    after = funnel.report(1)["totals"]
    for step in ("card_sent", "card_opened", "card_joined"):
        assert after[step] == before[step] + 1, step


def test_someone_removed_cannot_come_back_through_their_old_link():
    a, b = person("rm-a@x.com", "Alice"), person("rm-b@x.com", "Bob")
    tok = invite(a)["token"]
    assert accept(b, tok).status_code == 200
    a.delete(f"/api/connections/{linked_row(a, 'Bob')['id']}")
    r = accept(b, tok)
    assert r.status_code == 404 and "already been used" in r.text
    assert a.get("/api/connections").json()["connections"] == []
    assert b.get(f"/api/invites/{tok}").json()["linked"] is False
    assert accept(b, invite(a)["token"]).status_code == 200        # a new link is her say


def test_two_people_accepting_at_once_only_one_gets_in(monkeypatch):
    a = person("race-a@x.com", "Alice")
    b, c = person("race-b@x.com", "Bob"), person("race-c@x.com", "Carol")
    tok = invite(a)["token"]
    assert accept(b, tok).status_code == 200
    # Carol loaded the record before Bob's accept was written.
    stale = store.load("_invites", f"inv-{tok}", None)
    stale.update(used_by=[], accepted=[])
    monkeypatch.setattr(server, "_invite", lambda t: stale)
    assert accept(c, tok).status_code == 404
    assert [r["name"] for r in a.get("/api/connections").json()["connections"]] == ["Bob"]


def test_a_row_with_no_birth_date_is_never_taken_over():
    a = person("nodate-a@x.com", "Maya")
    row = saved(a, "Mom")
    t = token_of(a.post(f"/api/connections/{row['id']}/send").json())
    uid = store.load("_invites", f"inv-{t}", None)["from"]
    rows = store.load(uid, "connections", [])
    del rows[0]["birth_date"]
    store.save(uid, "connections", rows)
    b = person("nodate-b@x.com", "Ari", date="1999-06-21")
    buid = last_uid()
    prof = store.load(buid, "profile", {})      # no date on either side must not count as a match
    del prof["birth_date"]
    store.save(buid, "profile", prof)
    assert b.post(f"/api/invites/{t}/accept", json={"share_chart": True}).status_code == 200
    names = sorted(r["name"] for r in a.get("/api/connections").json()["connections"])
    assert names == ["Ari", "Mom"]


def test_when_they_leave_her_saved_row_and_notes_come_back():
    a = person("back-a@x.com", "Maya")
    row = saved(a, "Theo", date="1999-06-21")
    assert a.post(f"/api/connections/{row['id']}/checkin",
                  json={"feel": "strained", "note": "kept"}).status_code == 200
    t = token_of(a.post(f"/api/connections/{row['id']}/send").json())
    b = person("back-b@x.com", "Theodore Lane", date="1999-06-21")
    b.post(f"/api/invites/{t}/accept", json={"share_chart": True})
    assert linked_row(a, "Theo")["id"] == row["id"]            # her name for them stays
    b.delete(f"/api/connections/{linked_row(b, 'Maya')['id']}")
    (back,) = a.get("/api/connections").json()["connections"]
    assert back["id"] == row["id"] and back["name"] == "Theo" and not back.get("linked_uid")
    assert back["born"] == row["born"]                          # what she typed, not theirs
    deep = a.get(f"/api/connections/{row['id']}/deep")
    assert deep.status_code == 200 and deep.json()["person"]["linked"] is False
    assert "kept" in a.get(f"/api/connections/{row['id']}/today").text
    assert a.post(f"/api/connections/{row['id']}/send").status_code == 200


def test_a_used_link_stops_showing_the_score_and_forgets_their_name_with_them():
    a = person("used-a@x.com", "Maya")
    row = saved(a, "Theo")
    t = token_of(a.post(f"/api/connections/{row['id']}/send").json())
    b = person("used-b@x.com", "Ari", date="1999-06-21")
    b.post(f"/api/invites/{t}/accept", json={"share_chart": True})
    assert TestClient(server.app).get(f"/api/invites/{t}").json() == \
        {"from": "Maya", "valid": False, "reason": "used"}
    a.delete(f"/api/connections/{row['id']}")
    assert "Theo" not in str(store.load("_invites", f"inv-{t}", None))
