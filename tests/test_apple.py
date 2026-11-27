"""
Apple In-App Purchase (app/apple.py): off until switched on, nothing is
believed that Apple didn't sign, a purchase reaches exactly one account, and an
Apple subscription and a Stripe one on the same account never hide each other.

Apple is faked the honest way: a made-up root, intermediate and leaf with
Apple's marker extensions sign real ES256 JWS, and the made-up root stands in
for the pinned one. The chain code under test is the code production runs.
"""
import base64
import datetime as dt
import json
import os
import pathlib
import sys
import tempfile
import time
import uuid

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-apple-"))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

from app import apple, billing, log, server
from engine import plan
from engine.store import load
from tests.test_app import last_uid, sign_in

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOW = dt.datetime.now(dt.timezone.utc)
DAY = 86_400_000
IOS = {"X-Client": "ios-app"}


def ms(days=0):
    return int(time.time() * 1000) + int(days * DAY)


# ------------------------------------------------------------ a stand-in Apple

def _cert(name, key, issuer=None, issuer_key=None, ca=False, oid=None,
          before=NOW - dt.timedelta(days=1), after=NOW + dt.timedelta(days=365)):
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    b = (x509.CertificateBuilder().subject_name(subject)
         .issuer_name(issuer.subject if issuer else subject)
         .public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(before).not_valid_after(after)
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if oid:
        b = b.add_extension(x509.UnrecognizedExtension(x509.ObjectIdentifier(oid), b"\x05\x00"), critical=False)
    return b.sign(issuer_key or key, hashes.SHA256())


class FakeApple:
    """A root, an intermediate and a leaf shaped like Apple's, and a pen."""

    def __init__(self, leaf_oid=apple.OID_LEAF, mid_oid=apple.OID_INTERMEDIATE, **leaf_kw):
        self.root_key = ec.generate_private_key(ec.SECP384R1())
        self.mid_key = ec.generate_private_key(ec.SECP384R1())
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.root = _cert("Test Root", self.root_key, ca=True)
        self.mid = _cert("Test WWDR", self.mid_key, self.root, self.root_key, ca=True, oid=mid_oid)
        self.leaf = _cert("Test Leaf", self.key, self.mid, self.mid_key, oid=leaf_oid, **leaf_kw)

    def jws(self, payload, alg="ES256", chain=None):
        chain = chain or [self.leaf, self.mid, self.root]
        x5c = [base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode() for c in chain]
        enc = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()  # noqa: E731
        head, body = enc({"alg": alg, "x5c": x5c}), enc(payload)
        r, s = decode_dss_signature(self.key.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
        sig = base64.urlsafe_b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).rstrip(b"=").decode()
        return f"{head}.{body}.{sig}"


APPLE = FakeApple()


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("MYSIGN_APPLE_IAP", "1")
    monkeypatch.delenv("MYSIGN_APPLE_LOCAL_TESTING", raising=False)
    monkeypatch.setattr(apple, "ROOTS", [APPLE.root])
    return APPLE


@pytest.fixture
def c():
    return TestClient(server.app)


def _user(c, email):
    sign_in(c, email)
    return last_uid()


def _tx(uid=None, otx=None, product="mysign_plus_monthly", expires=None, signed=None, **kw):
    otx = otx or str(uuid.uuid4().int)[:16]               # a subscription of its own unless named
    tx = {"transactionId": kw.pop("tid", otx), "originalTransactionId": otx,
          "bundleId": "app.mysign.ios", "productId": product,
          "type": "Auto-Renewable Subscription", "environment": "Production",
          "purchaseDate": ms(-1), "expiresDate": ms(30) if expires is None else expires,
          "signedDate": ms() if signed is None else signed, **kw}
    if uid:
        tx["appAccountToken"] = apple.account_token(uid)
    return tx


def _sync(c, jws):
    return c.post("/api/billing/apple/sync", json={"signedTransaction": jws})


def _notify(c, kind, tx, subtype=None, renewal=None, nid=None, bundle="app.mysign.ios", signer=APPLE):
    data = {"bundleId": bundle, "environment": tx.get("environment", "Production"),
            "signedTransactionInfo": signer.jws(tx)}
    if renewal is not None:
        data["signedRenewalInfo"] = signer.jws({"environment": "Production", **renewal})
    n = {"notificationType": kind, "notificationUUID": nid or str(uuid.uuid4()),
         "version": "2.0", "signedDate": ms(), "data": data}
    if subtype:
        n["subtype"] = subtype
    return c.post("/api/billing/apple/notify", json={"signedPayload": signer.jws(n)})


def _until(expires_ms):
    return (apple._day(expires_ms) + dt.timedelta(days=apple.GRACE_DAYS)).isoformat()


# ------------------------------------------------------------------ the switch

def test_nothing_is_for_sale_in_the_app_until_apple_is_switched_on(c, monkeypatch):
    monkeypatch.delenv("MYSIGN_APPLE_IAP", raising=False)
    monkeypatch.setattr(apple, "ROOTS", [APPLE.root])
    uid = _user(c, "off@apple.com")
    assert _sync(c, APPLE.jws(_tx())).status_code == 409
    assert c.post("/api/billing/apple/notify", json={"signedPayload": "x"}).status_code == 404
    offer = c.get("/api/state", headers=IOS).json()["plan"]["apple"]
    assert offer["available"] is False and offer["offer"] is False and offer["token"] is None
    assert plan.get(uid)["tier"] == "free"


def test_only_the_iphone_app_is_told_about_the_app_store(c, on):
    _user(c, "web@apple.com")
    assert "apple" not in c.get("/api/state").json()["plan"]
    offer = c.get("/api/state", headers=IOS).json()["plan"]["apple"]
    assert offer["available"] is True and offer["offer"] is True
    assert offer["products"] == ["mysign_plus_monthly", "mysign_plus_annual"]
    assert uuid.UUID(offer["token"])                       # what StoreKit takes as appAccountToken
    assert c.get("/api/state", headers=IOS).json()["plan"]["apple"]["token"] == offer["token"]


def test_the_pinned_root_is_apples():
    """The real list, not the stand-in: Apple Root CA - G3, by its published fingerprint."""
    assert [r.fingerprint(hashes.SHA256()).hex() for r in apple._roots()] == [
        "63343abfb89a6a03ebb57e9b3f5fa7be7c4f5c756f3017b3a8c488c3653e9179"]
    with pytest.raises(apple.AppleError, match="Apple's root"):
        apple.verify(APPLE.jws(_tx()))                     # the stand-in is not trusted for real


# ------------------------------------------------------------------ sync

def test_a_verified_purchase_grants_plus_from_apple(c, on):
    uid = _user(c, "buy@apple.com")
    expires = ms(7)
    r = _sync(c, on.jws(_tx(uid, expires=expires, offerType=1, offerDiscountType="FREE_TRIAL")))
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["finish"] is True
    p = plan.get(uid)
    assert p["tier"] == "plus" and p["source"] == "apple" and p["until"] == _until(expires)
    assert "paid" in (load(uid, "funnel_seen", []) or [])
    s = c.get("/api/state", headers=IOS).json()["plan"]
    assert s["apple_sub"] == {"status": "active", "product": "mysign_plus_monthly",
                              "trial": True, "renews": True}
    assert s["double"] is False
    assert s["apple"]["offer"] is False                    # nothing more to sell her


def test_a_tampered_transaction_changes_nothing(c, on):
    uid = _user(c, "tamper@apple.com")
    head, body, sig = on.jws(_tx(uid, expires=ms(1))).split(".")
    forged = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    forged["expiresDate"] = ms(3650)
    body = base64.urlsafe_b64encode(json.dumps(forged).encode()).rstrip(b"=").decode()
    assert _sync(c, f"{head}.{body}.{sig}").status_code == 400
    assert _sync(c, "not.a.jws").status_code == 400
    assert plan.get(uid)["tier"] == "free"


def test_a_chain_that_isnt_apples_is_refused(c, on):
    uid = _user(c, "chain@apple.com")
    stranger = FakeApple()                                  # well formed, wrong root
    assert _sync(c, stranger.jws(_tx(uid))).status_code == 400
    # A stranger's leaf with Apple's real intermediate and root stapled on.
    assert _sync(c, stranger.jws(_tx(uid), chain=[stranger.leaf, on.mid, on.root])).status_code == 400
    # Only the leaf, or no chain at all.
    assert _sync(c, on.jws(_tx(uid), chain=[on.leaf])).status_code == 400
    assert _sync(c, on.jws(_tx(uid), alg="none")).status_code == 400
    assert plan.get(uid)["tier"] == "free"


@pytest.mark.parametrize("signer", [
    FakeApple(leaf_oid=None),                               # not a receipt-signing certificate
    FakeApple(mid_oid=None),                                # not issued by Apple's WWDR
    FakeApple(after=NOW - dt.timedelta(hours=1)),           # leaf expired
    FakeApple(before=NOW + dt.timedelta(hours=1)),          # leaf not valid yet
], ids=["leaf-oid", "intermediate-oid", "expired", "not-yet-valid"])
def test_a_certificate_apple_wouldnt_sign_receipts_with_is_refused(c, monkeypatch, signer):
    monkeypatch.setenv("MYSIGN_APPLE_IAP", "1")
    monkeypatch.setattr(apple, "ROOTS", [signer.root])
    uid = _user(c, f"cert{id(signer)}@apple.com")
    assert _sync(c, signer.jws(_tx(uid))).status_code == 400
    assert plan.get(uid)["tier"] == "free"


def test_a_purchase_from_another_app_or_of_an_unknown_product_is_refused(c, on):
    uid = _user(c, "bundle@apple.com")
    assert _sync(c, on.jws(_tx(uid, bundleId="com.someone.else"))).status_code == 400
    assert _sync(c, on.jws(_tx(uid, product="mysign_lifetime"))).status_code == 400
    assert _sync(c, on.jws(_tx(uid, environment="Xcode"))).status_code == 400
    assert plan.get(uid)["tier"] == "free"


def test_the_same_transaction_twice_is_applied_once(c, on, monkeypatch):
    uid = _user(c, "replay@apple.com")
    grants = []
    real = plan.grant
    monkeypatch.setattr(plan, "grant", lambda *a, **k: grants.append(a) or real(*a, **k))
    jws = on.jws(_tx(uid))
    assert _sync(c, jws).json()["code"] == "active"
    assert _sync(c, jws).json()["code"] == "same"
    assert len(grants) == 1 and plan.get(uid)["tier"] == "plus"


def test_an_old_transaction_replayed_after_a_refund_gives_nothing_back(c, on):
    uid = _user(c, "stale@apple.com")
    old = on.jws(_tx(uid, otx="1100", signed=ms(-2)))
    assert _sync(c, old).status_code == 200
    assert _notify(c, "REFUND", _tx(uid, otx="1100", revocationDate=ms(), revocationReason=0)).status_code == 200
    assert plan.get(uid)["tier"] == "free"
    r = _sync(c, old)                                       # the still-valid-looking copy she kept
    assert r.status_code == 200 and r.json()["code"] == "stale"
    assert plan.get(uid)["tier"] == "free"


def test_an_expired_transaction_grants_nothing(c, on):
    uid = _user(c, "lapsed@apple.com")
    r = _sync(c, on.jws(_tx(uid, expires=ms(-3))))
    assert r.status_code == 200 and r.json()["code"] == "expired"
    assert plan.get(uid)["tier"] == "free"


def test_one_subscription_never_unlocks_two_accounts(c, on):
    a = _user(c, "owner@apple.com")
    jws = on.jws(_tx(a, otx="2000"))
    assert _sync(c, jws).status_code == 200
    other = TestClient(server.app)
    b = _user(other, "borrower@apple.com")
    r = _sync(other, jws)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "other_account"
    assert r.json()["detail"]["finish"] is True
    assert plan.get(b)["tier"] == "free" and plan.get(a)["tier"] == "plus"


def test_a_purchase_made_for_another_account_goes_to_that_account(c, on):
    """Bought under A; the phone is now signed in as B before the server heard."""
    a = _user(c, "first@apple.com")
    jws = on.jws(_tx(a, otx="2100"))
    other = TestClient(server.app)
    b = _user(other, "second@apple.com")
    assert _sync(other, jws).status_code == 409
    assert plan.get(a)["tier"] == "plus" and plan.get(b)["tier"] == "free"


def test_restore_moves_a_subscription_whose_account_was_deleted(c, on):
    a = _user(c, "deleted@apple.com")
    tx = _tx(a, otx="2200")
    assert _sync(c, on.jws(tx)).status_code == 200
    assert c.delete("/api/account").json() == {"deleted": True, "apple_subscription": True}
    other = TestClient(server.app)
    b = _user(other, "fresh@apple.com")
    assert _sync(other, on.jws({**tx, "signedDate": ms()})).status_code == 200
    assert plan.get(b)["tier"] == "plus" and plan.get(b)["source"] == "apple"


def test_a_purchase_with_no_account_token_goes_to_whoever_restores_it(c, on):
    uid = _user(c, "codes@apple.com")                       # e.g. an offer code redeemed in the App Store
    assert _sync(c, on.jws(_tx(otx="2300"))).status_code == 200
    assert plan.get(uid)["tier"] == "plus"


# ------------------------------------------------------------------ notifications

def test_a_subscription_notification_reaches_the_account_by_its_token(c, on):
    """SUBSCRIBED can land before the app has synced anything."""
    uid = _user(c, "subscribed@apple.com")
    expires = ms(7)
    assert _notify(c, "SUBSCRIBED", _tx(uid, otx="3000", expires=expires), subtype="INITIAL_BUY",
                   renewal={"autoRenewStatus": 1}).status_code == 200
    p = plan.get(uid)
    assert p["tier"] == "plus" and p["source"] == "apple" and p["until"] == _until(expires)


@pytest.mark.parametrize("kind,subtype", [
    ("DID_RENEW", None), ("DID_RENEW", "BILLING_RECOVERY"), ("OFFER_REDEEMED", "INITIAL_BUY"),
    ("DID_CHANGE_RENEWAL_PREF", "UPGRADE"), ("RENEWAL_EXTENDED", None), ("REFUND_REVERSED", None),
    ("SUBSCRIBED", "RESUBSCRIBE")])
def test_notifications_that_carry_a_paid_period_extend_plus(c, on, kind, subtype):
    uid = _user(c, f"{kind}-{subtype}@apple.com".lower())
    otx = f"31{abs(hash((kind, subtype))) % 10**6}"
    _sync(c, on.jws(_tx(uid, otx=otx, expires=ms(1), signed=ms(-1))))
    later = ms(31)
    assert _notify(c, kind, _tx(uid, otx=otx, tid=otx + "1", expires=later, product="mysign_plus_annual"),
                   subtype=subtype).status_code == 200
    p = plan.get(uid)
    assert p["tier"] == "plus" and p["until"] == _until(later)
    assert load(uid, "apple", {})["product"] == "mysign_plus_annual"


@pytest.mark.parametrize("kind,subtype,extra", [
    ("EXPIRED", "VOLUNTARY", {}),
    ("EXPIRED", "BILLING_RETRY", {}),
    ("GRACE_PERIOD_EXPIRED", None, {}),
    ("REFUND", None, {"revocationDate": 1, "revocationReason": 1}),
    ("REVOKE", None, {"revocationDate": 1}),
], ids=["expired", "expired-after-retry", "grace-over", "refund", "revoke"])
def test_notifications_that_end_it_return_her_to_free_at_once(c, on, kind, subtype, extra):
    uid = _user(c, f"end-{kind}-{subtype}@apple.com".lower())
    otx = f"32{abs(hash((kind, subtype))) % 10**6}"
    _sync(c, on.jws(_tx(uid, otx=otx, signed=ms(-1))))
    assert plan.get(uid)["tier"] == "plus"
    # A refund arrives while the paid period still has weeks to run.
    expires = ms(20) if kind in ("REFUND", "REVOKE") else ms(-0.01)
    assert _notify(c, kind, _tx(uid, otx=otx, expires=expires, **extra), subtype=subtype).status_code == 200
    assert plan.get(uid)["tier"] == "free"
    assert c.get("/api/state").json()["plan"]["apple_sub"] is None


def test_a_grace_period_keeps_plus_and_billing_retry_without_one_does_not(c, on):
    uid = _user(c, "grace@apple.com")
    _sync(c, on.jws(_tx(uid, otx="3300", signed=ms(-1))))
    grace_end = ms(16)
    assert _notify(c, "DID_FAIL_TO_RENEW", _tx(uid, otx="3300", expires=ms(-0.01)), subtype="GRACE_PERIOD",
                   renewal={"gracePeriodExpiresDate": grace_end, "isInBillingRetryPeriod": True,
                            "autoRenewStatus": 1}).status_code == 200
    p = plan.get(uid)
    assert p["tier"] == "plus" and p["until"] == _until(grace_end)
    assert load(uid, "apple", {})["status"] == "grace"

    solo = TestClient(server.app)
    uid2 = _user(solo, "retry@apple.com")
    _sync(solo, on.jws(_tx(uid2, otx="3301", signed=ms(-1))))
    assert _notify(c, "DID_FAIL_TO_RENEW", _tx(uid2, otx="3301", expires=ms(-0.01)),
                   renewal={"isInBillingRetryPeriod": True, "autoRenewStatus": 1}).status_code == 200
    assert plan.get(uid2)["tier"] == "free"
    assert load(uid2, "apple", {})["status"] == "billing_retry"
    # The card goes through on a retry: Plus is back.
    assert _notify(c, "DID_RENEW", _tx(uid2, otx="3301", expires=ms(30)), subtype="BILLING_RECOVERY").status_code == 200
    assert plan.get(uid2)["tier"] == "plus"


def test_turning_auto_renew_off_keeps_plus_to_the_end_of_the_period(c, on):
    uid = _user(c, "autorenew@apple.com")
    expires = ms(12)
    _sync(c, on.jws(_tx(uid, otx="3400", expires=expires, signed=ms(-1))))
    assert _notify(c, "DID_CHANGE_RENEWAL_STATUS", _tx(uid, otx="3400", expires=expires),
                   subtype="AUTO_RENEW_DISABLED", renewal={"autoRenewStatus": 0}).status_code == 200
    assert plan.get(uid)["tier"] == "plus" and plan.get(uid)["until"] == _until(expires)
    assert c.get("/api/state").json()["plan"]["apple_sub"]["renews"] is False


def test_the_same_notification_is_applied_once(c, on, monkeypatch):
    uid = _user(c, "dup@apple.com")
    applied = []
    real = apple.apply
    monkeypatch.setattr(apple, "apply", lambda *a, **k: applied.append(a) or real(*a, **k))
    tx = _tx(uid, otx="3500")
    assert _notify(c, "SUBSCRIBED", tx, nid="n-1").status_code == 200
    assert _notify(c, "SUBSCRIBED", tx, nid="n-1").status_code == 200
    assert len(applied) == 1


def test_notifications_out_of_order_cant_undo_a_newer_one(c, on):
    uid = _user(c, "order@apple.com")
    assert _notify(c, "EXPIRED", _tx(uid, otx="3600", expires=ms(-0.01), signed=ms())).status_code == 200
    # The renewal that was sent first arrives second.
    assert _notify(c, "DID_RENEW", _tx(uid, otx="3600", expires=ms(30), signed=ms(-1))).status_code == 200
    assert plan.get(uid)["tier"] == "free"


def test_a_tampered_or_foreign_notification_changes_nothing(c, on):
    uid = _user(c, "nbad@apple.com")
    tx = _tx(uid, otx="3700")
    assert _notify(c, "SUBSCRIBED", tx, signer=FakeApple()).status_code == 400
    assert _notify(c, "SUBSCRIBED", tx, bundle="com.someone.else").status_code == 400
    assert _notify(c, "SUBSCRIBED", {**tx, "bundleId": "com.someone.else"}).status_code == 400
    assert c.post("/api/billing/apple/notify", json={"signedPayload": "a.b.c"}).status_code == 400
    assert plan.get(uid)["tier"] == "free"


def test_a_test_notification_and_one_for_nobody_answer_ok(c, on):
    n = {"notificationType": "TEST", "notificationUUID": str(uuid.uuid4()),
         "data": {"bundleId": "app.mysign.ios", "environment": "Sandbox"}}
    assert c.post("/api/billing/apple/notify", json={"signedPayload": on.jws(n)}).status_code == 200
    stray = _tx(otx="3800", appAccountToken=str(uuid.uuid4()))
    assert _notify(c, "DID_RENEW", stray).status_code == 200
    assert load(apple.INDEX, "apple-otx-3800", None) is None


# ------------------------------------------------------------------ two providers

def _stripe_on(monkeypatch, uid, live=True):
    """A Stripe subscription as app/billing._sync would record it."""
    return billing._sync({"id": "sub_x", "status": "active" if live else "canceled",
                          "customer": "cus_x", "metadata": {"uid": uid},
                          "current_period_end": int(time.time()) + 20 * 86400})


def test_a_stripe_subscriber_on_iphone_keeps_plus_and_is_offered_nothing(c, on, monkeypatch):
    uid = _user(c, "webplus@apple.com")
    _stripe_on(monkeypatch, uid)
    s = c.get("/api/state", headers=IOS).json()["plan"]
    assert s["tier"] == "plus" and s["source"] == "stripe"
    assert s["apple"]["offer"] is False and s["apple_sub"] is None and s["double"] is False


def test_two_subscriptions_on_one_account_are_one_grant_and_both_on_record(c, on, monkeypatch):
    uid = _user(c, "double@apple.com")
    seen = []
    monkeypatch.setattr(log, "emit", lambda event, **kw: seen.append((event, kw)))
    _stripe_on(monkeypatch, uid)
    # Bought in the App Store anyway (outside the app's paywall, which hides it).
    assert _sync(c, on.jws(_tx(uid, otx="4000", expires=ms(30)))).status_code == 200
    p = plan.get(uid)
    assert p["tier"] == "plus" and p["source"] == "stripe"   # the plan is still one record
    assert plan.providers(uid) == ["stripe", "apple"]
    assert [kw["providers"] for e, kw in seen if e == "billing.double_subscription"] == ["stripe,apple"]
    s = c.get("/api/state").json()["plan"]
    assert s["double"] is True and s["apple_sub"]["status"] == "active"

    # Stripe ends: Apple is still paying, so she keeps Plus, now from Apple.
    _stripe_on(monkeypatch, uid, live=False)
    p = plan.get(uid)
    assert p["tier"] == "plus" and p["source"] == "apple"
    assert c.get("/api/state").json()["plan"]["double"] is False
    # Then Apple ends too.
    _notify(c, "EXPIRED", _tx(uid, otx="4000", expires=ms(-0.01)), subtype="VOLUNTARY")
    assert plan.get(uid)["tier"] == "free"


def test_apple_ending_never_takes_away_a_stripe_subscription(c, on, monkeypatch):
    uid = _user(c, "applefirst@apple.com")
    _sync(c, on.jws(_tx(uid, otx="4100", signed=ms(-1))))
    _stripe_on(monkeypatch, uid)
    _notify(c, "REFUND", _tx(uid, otx="4100", revocationDate=ms()))
    p = plan.get(uid)
    assert p["tier"] == "plus" and p["source"] == "stripe"


def test_a_stripe_grant_from_before_the_ledger_survives_apple_ending(c, on):
    uid = _user(c, "legacy@apple.com")
    plan.grant(uid, "plus", source="stripe", until="2099-01-01")   # as older code wrote it
    _sync(c, on.jws(_tx(uid, otx="4200", signed=ms(-1))))
    _notify(c, "EXPIRED", _tx(uid, otx="4200", expires=ms(-0.01)))
    p = plan.get(uid)
    assert p["tier"] == "plus" and p["source"] == "stripe" and p["until"] == "2099-01-01"


def test_plus_given_by_hand_outlives_an_apple_subscription(c, on):
    uid = _user(c, "pilot@apple.com")
    _sync(c, on.jws(_tx(uid, otx="4300", signed=ms(-1))))
    plan.grant(uid, "plus", source="operator", granted_by="ops")
    _notify(c, "EXPIRED", _tx(uid, otx="4300", expires=ms(-0.01)))
    assert plan.get(uid)["tier"] == "plus"


def test_someone_with_an_apple_subscription_cant_also_check_out_on_the_web(c, on, monkeypatch):
    uid = _user(c, "nocheckout@apple.com")
    _sync(c, on.jws(_tx(uid, otx="4400")))
    monkeypatch.setitem(plan.PRICES, "available", True)
    assert c.post("/api/billing/checkout", json={"plan": "annual"}).status_code == 409


# ------------------------------------------------------------------ Xcode's local certificate

def _xcode():
    """Xcode's StoreKit testing signs with a certificate it made itself."""
    x = FakeApple()
    x.chain = [_cert("StoreKit Testing in Xcode", x.key, ca=True)]
    return x


def test_the_xcode_certificate_is_refused_unless_local_testing_is_asked_for(c, on, monkeypatch):
    uid = _user(c, "xcode@apple.com")
    x = _xcode()
    jws = x.jws(_tx(uid, otx="0", environment="Xcode"), chain=x.chain)
    assert _sync(c, jws).status_code == 400                 # the default
    assert plan.get(uid)["tier"] == "free"

    monkeypatch.setenv("MYSIGN_APPLE_LOCAL_TESTING", "1")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "mysign-prod")
    assert _sync(c, jws).status_code == 400                 # never on Lambda, whatever is set
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME")

    # Even locally, a self-signed payload can't claim to be the App Store's.
    assert _sync(c, x.jws(_tx(uid, otx="0"), chain=x.chain)).status_code == 400
    assert _sync(c, jws).status_code == 200
    assert plan.get(uid)["tier"] == "plus"
    assert load(uid, "apple", {})["environment"] == "Xcode"


def test_local_testing_still_takes_the_real_chain_and_never_calls_it_xcode(c, on, monkeypatch):
    monkeypatch.setenv("MYSIGN_APPLE_LOCAL_TESTING", "1")
    uid = _user(c, "localreal@apple.com")
    assert _sync(c, on.jws(_tx(uid, otx="5100", environment="Xcode"))).status_code == 400
    assert _sync(c, on.jws(_tx(uid, otx="5100", environment="Sandbox"))).status_code == 200


# ------------------------------------------------------------------ leaving

def test_deleting_the_account_forgets_the_links_and_says_apple_still_bills(c, on):
    uid = _user(c, "leave@apple.com")
    tx = _tx(uid, otx="6000")
    _sync(c, on.jws(tx))
    token = load(uid, "apple", {})["token"]
    # The client reads this before the final confirmation.
    assert c.get("/api/state").json()["plan"]["apple_sub"]["status"] == "active"
    assert c.delete("/api/account").json() == {"deleted": True, "apple_subscription": True}
    assert load(apple.INDEX, f"apple-token-{token}", None) is None
    assert load(apple.INDEX, "apple-otx-6000", None) is None
    # Apple's next renewal finds no account and does nothing.
    assert _notify(c, "DID_RENEW", {**tx, "signedDate": ms()}).status_code == 200
    assert load(uid, "plan", None) is None


def test_deleting_an_account_without_an_apple_subscription_says_nothing_extra(c, on):
    _user(c, "plain@apple.com")
    assert c.delete("/api/account").json() == {"deleted": True}


# ------------------------------------------------------------------ one price list

def test_the_storekit_file_sells_exactly_what_the_plan_table_says():
    """native/ios/App/App/MySign.storekit is what Xcode sells locally. Its ids,
    prices, trial and group ranking must be the ones in engine/plan.py."""
    sk = json.loads((ROOT / "native/ios/App/App/MySign.storekit").read_text())
    [group] = sk["subscriptionGroups"]
    assert group["name"] == "My Sign Plus"
    subs = {s["productID"]: s for s in group["subscriptions"]}
    assert set(subs) == set(plan.PRODUCTS) == set(billing.LOOKUP.values())
    period = {"month": "P1M", "year": "P1Y"}
    for pid, meta in plan.PRODUCTS.items():
        price, s = plan.PRICES[meta["plan"]], subs[pid]
        assert "$" + s["displayPrice"] == price["price"]
        assert s["recurringSubscriptionPeriod"] == period[price["period"]]
        intro = s["introductoryOffer"]
        assert intro["paymentMode"] == "free"
        assert intro["subscriptionPeriod"] == f"P{plan.PRICES['trial_days'] // 7}W"
        assert s["familyShareable"] is False
    assert subs["mysign_plus_annual"]["groupNumber"] == 1   # annual is the higher level
    assert subs["mysign_plus_monthly"]["groupNumber"] == 2
    assert sk["products"] == []                             # subscriptions only: no question packs


# ------------------------------------------------------------------ the app's side

def _calls(run):
    return [step[0] for step in run["log"] if step[0] != "event"]


@pytest.fixture(scope="module")
def shop():
    """native/bridge.js run under node with StoreKit and the server faked."""
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    out = subprocess.run(["node", str(ROOT / "tests/bridge_shop.js")], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_app_finishes_a_transaction_only_after_the_server_has_it(shop):
    buy = shop["buy"]
    assert buy["result"]["ok"] is True
    steps = _calls(buy)
    assert steps[-3:] == ["purchase", "sync", "finish"]          # in that order
    # The account token from the server is what StoreKit is given.
    purchase = next(s for s in buy["log"] if s[0] == "purchase")
    assert purchase == ["purchase", "mysign_plus_annual", "11111111-2222-4333-8444-555555555555"]
    assert next(s for s in buy["log"] if s[0] == "sync") == ["sync", "h.p.s", "Bearer"]

    # No answer, or a refusal: it stays unfinished, so StoreKit hands it back later.
    for case in ("offline", "refused"):
        assert "finish" not in _calls(shop[case]) and shop[case]["result"]["ok"] is False
    assert shop["offline"]["result"]["unreachable"] is True
    # The server has it on record for its real owner: finished, but not hers.
    assert _calls(shop["other"])[-2:] == ["sync", "finish"] and shop["other"]["result"]["ok"] is False


def test_the_app_never_opens_apples_sheet_for_someone_who_shouldnt_buy(shop):
    for case in ("has_plus", "closed"):
        assert "purchase" not in _calls(shop[case])
        assert shop[case]["result"] == {"ok": False, "outcome": "unavailable"}
        assert shop[case]["ready"] is False
    assert shop["has_plus"]["open"] is True and shop["closed"]["open"] is False
    assert shop["cancelled"]["result"] == {"ok": False, "outcome": "cancelled"}
    assert "sync" not in _calls(shop["cancelled"])


def test_the_app_tells_the_server_what_it_holds_at_launch_on_renewal_and_on_restore(shop):
    assert _calls(shop["boot"]) == ["products", "entitlements", "sync", "finish"]
    assert _calls(shop["renewal"])[-2:] == ["sync", "finish"]
    assert ["sync", "renew.jws.x", "Bearer"] in shop["renewal"]["log"]
    assert _calls(shop["restore"])[-3:] == ["restore", "sync", "finish"]
    assert shop["restore"]["result"] == {"found": 1, "ok": True, "message": ""}
    # A lapsed subscription is recorded and finished, but restores nothing.
    assert _calls(shop["lapsed"])[-3:] == ["restore", "sync", "finish"]
    assert shop["lapsed"]["result"]["ok"] is False


def test_the_in_app_paywall_says_what_apple_requires_and_sells_nowhere_else():
    page = (ROOT / "app/static/index.html").read_text()
    block = page[page.index("// ---- Plus inside the iPhone app"):page.index("function cToggle(open)")]
    # The auto-renew disclosure, word for word (3.1.2).
    assert ("Payment will be charged to your Apple Account. Subscription renews automatically unless "
            "cancelled at least 24 hours before the end of the period. Manage in Settings → Apple Account → "
            "Subscriptions.") in block
    for needed in ('href="/terms"', 'href="/privacy"', "Restore purchases", "The first ${trial.days} days are free",
                   "After the trial it is"):
        assert needed in block, needed
    # No route to the web checkout or the Stripe portal, no price typed in, no "save 50%".
    selling = block[:block.index("async function appleSubWarning")]
    for banned in ("/api/billing/checkout", "/api/billing/portal", "mysignapp.com", "stripe", "Stripe",
                   "$9.99", "$59.99", "save 50", "50%"):
        assert banned not in selling, banned
    # Only the shell has a store: a browser's paywall is the one it always was.
    assert "const STORE = (WRAPPED && window.MySignStore) || null;" in block
    assert "const purchasable = prices => !!(prices && prices.available) && !WRAPPED;" in page


def test_the_shell_registers_the_store_and_ships_no_third_party_billing_sdk():
    native = ROOT / "native"
    assert "registerPluginInstance(PlusStorePlugin())" in (native / "ios/App/App/MySignViewController.swift").read_text()
    deps = json.loads((native / "package.json").read_text())["dependencies"]
    assert not [d for d in deps if "revenuecat" in d.lower() or "purchases" in d.lower()]
    scheme = (native / "ios/App/App.xcodeproj/xcshareddata/xcschemes/App.xcscheme").read_text()
    assert "MySign.storekit" in scheme and "AppTests" in scheme
    # The StoreKit file is for testing: it is never copied into the app itself.
    project = (native / "ios/App/App.xcodeproj/project.pbxproj").read_text()
    resources = project[project.index("504EC3021FED79650016851F /* Resources */ = {"):]
    assert "MySign.storekit" not in resources[:resources.index("};")]


# ------------------------------------------------------------------ when the free limits bite

@pytest.mark.parametrize("stripe,app_store", [(False, False), (True, False), (False, True), (True, True)],
                         ids=["both-off", "stripe-only", "apple-only", "both-on"])
def test_the_free_limits_bite_once_either_shop_is_open(c, monkeypatch, stripe, app_store):
    """A cap with nothing to buy is just a wall, so with both shops shut nobody
    is limited, exactly as before. Either shop opening is something to buy."""
    from app import social
    monkeypatch.setitem(plan.PRICES, "available", stripe)
    monkeypatch.setitem(plan.APP_STORE, "available", app_store)
    selling = stripe or app_store
    assert plan.for_sale() is selling

    free = _user(c, f"limits-{stripe}-{app_store}@apple.com")
    assert plan.may_add_person(free, plan.FREE_PEOPLE - 1) is True          # under the cap either way
    assert plan.may_add_person(free, plan.FREE_PEOPLE) is (not selling)     # the people cap
    assert social.may_add_circle(free, social.SOC.FREE_CIRCLES) is (not selling)   # the circle cap
    assert plan.full_view(free) is (not selling)                            # Plus depth
    assert server._plus_view(free) is (not selling)

    plan.grant(free, "plus", source="operator")                             # Plus is never limited
    assert plan.may_add_person(free, 500) and social.may_add_circle(free, 50) and plan.full_view(free)

    # The App Store being open never opens the web checkout: the browser draws
    # its buy buttons from prices.available, and that stays Stripe's alone.
    assert c.get("/api/state").json()["plan"]["prices"]["available"] is stripe
    if not stripe:
        other = TestClient(server.app)
        _user(other, f"nocheckout-{app_store}@apple.com")
        assert other.post("/api/billing/checkout", json={"plan": "annual"}).status_code == 409


@pytest.mark.parametrize("env,expected", [({}, [False, False]), ({"MYSIGN_APPLE_IAP": "1"}, [False, True])],
                         ids=["nothing-set", "apple-iap-set"])
def test_startup_reads_each_shops_switch(tmp_path, env, expected):
    """app.server sets both flags at import, from the stack's settings."""
    import subprocess
    clean = {k: v for k, v in os.environ.items()
             if k not in ("MYSIGN_APPLE_IAP", "MYSIGN_BILLING", "MYSIGN_SECRET_ARN")}
    probe = ("import json; import app.server; from engine import plan; "
             "print(json.dumps([plan.PRICES['available'], plan.APP_STORE['available']]))")
    out = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, check=True,
                         env={**clean, **env, "MYSIGN_DATA": str(tmp_path), "PYTHONPATH": str(ROOT)}).stdout
    assert json.loads(out.strip().splitlines()[-1]) == expected
