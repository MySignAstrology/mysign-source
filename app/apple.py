"""
Apple In-App Purchase, for buying Plus inside the iPhone app.

The App Store is the only way to sell Plus there (App Review 3.1.1), and it
tells us what happened twice: the app hands over the signed transaction right
after a purchase or a restore (`sync`), and Apple's servers send App Store
Server Notifications V2 for everything after that (`notify`). Both are JWS:
three base64url parts signed with a certificate that chains to Apple's root.
Nothing here is believed until that chain and signature have been checked.

Like app/billing.py this file only translates. Every plan change goes through
engine/plan.provider_update, so an Apple subscription and a Stripe one on the
same account are both on record and neither ends the other.

Off unless switched on: `apple_iap` in the stack's secret (MYSIGN_APPLE_IAP=1).

No SDK: `cryptography` already ships for web push, and the chain check is
short enough to read. It is imported on first use, not at import, to stay off
the cold-start path (tests/test_coldstart.py). Not done here: an online revocation (OCSP) check of the
signing certificates, which Apple's own library offers as an option.
"""
import base64
import datetime as dt
import json
import os
import time
import uuid

from engine import plan
from engine.store import delete, load, save

BUNDLE_ID = "app.mysign.ios"
INDEX = "_billing"                # the same out-of-account index Stripe uses
# Apple bills up to a day before the period ends and says so by notification;
# one day of slack keeps her on Plus if that notification is late. A failed
# renewal, a refund or an expiry is its own notification and ends it at once.
GRACE_DAYS = 1
# The App Store environments a signed payload may come from. App Review buys
# in Sandbox against the production server, so production has to accept both.
# "Xcode" (the local StoreKit configuration file) is never in this list: it is
# only accepted through local_testing() below.
ENVIRONMENTS = ("Production", "Sandbox")

# Apple Root CA - G3, the root every App Store JWS chains to. From
# https://www.apple.com/certificateauthority/ ; SHA-256 fingerprint
# 63343abfb89a6a03ebb57e9b3f5fa7be7c4f5c756f3017b3a8c488c3653e9179.
APPLE_ROOT_G3 = b"""-----BEGIN CERTIFICATE-----
MIICQzCCAcmgAwIBAgIILcX8iNLFS5UwCgYIKoZIzj0EAwMwZzEbMBkGA1UEAwwS
QXBwbGUgUm9vdCBDQSAtIEczMSYwJAYDVQQLDB1BcHBsZSBDZXJ0aWZpY2F0aW9u
IEF1dGhvcml0eTETMBEGA1UECgwKQXBwbGUgSW5jLjELMAkGA1UEBhMCVVMwHhcN
MTQwNDMwMTgxOTA2WhcNMzkwNDMwMTgxOTA2WjBnMRswGQYDVQQDDBJBcHBsZSBS
b290IENBIC0gRzMxJjAkBgNVBAsMHUFwcGxlIENlcnRpZmljYXRpb24gQXV0aG9y
aXR5MRMwEQYDVQQKDApBcHBsZSBJbmMuMQswCQYDVQQGEwJVUzB2MBAGByqGSM49
AgEGBSuBBAAiA2IABJjpLz1AcqTtkyJygRMc3RCV8cWjTnHcFBbZDuWmBSp3ZHtf
TjjTuxxEtX/1H7YyYl3J6YRbTzBPEVoA/VhYDKX1DyxNB0cTddqXl5dvMVztK517
IDvYuVTZXpmkOlEKMaNCMEAwHQYDVR0OBBYEFLuw3qFYM4iapIqZ3r6966/ayySr
MA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/BAQDAgEGMAoGCCqGSM49BAMDA2gA
MGUCMQCD6cHEFl4aXTQY2e3v9GwOAEZLuN+yRhHFD/3meoyhpmvOwgPUnPWTxnS4
at+qIxUCMG1mihDK1A3UT82NQz60imOlM27jbdoXt2QfyFMm+YhidDkLF1vLUagM
6BgD56KyKA==
-----END CERTIFICATE-----
"""
ROOTS = []                        # the parsed root(s); filled by _roots() on first use
# Apple marks what a certificate may sign with these extensions: the leaf that
# signs App Store receipts, and the intermediate (WWDR) that issued it.
OID_LEAF = "1.2.840.113635.100.6.11.1"
OID_INTERMEDIATE = "1.2.840.113635.100.6.2.1"

# Notification types that end access whatever the dates in the payload say.
ENDED = {"EXPIRED": "expired", "GRACE_PERIOD_EXPIRED": "expired",
         "REFUND": "refunded", "REVOKE": "revoked"}


class AppleError(Exception):
    """A payload that isn't Apple's, isn't ours, or isn't whole."""


def enabled():
    return os.environ.get("MYSIGN_APPLE_IAP") == "1"


def local_testing():
    """
    Whether a JWS signed by Xcode's local StoreKit test certificate is accepted.

    That certificate is made on the developer's Mac and chains to nothing, so
    accepting it means accepting anyone's self-signed purchase. An explicit
    opt-in, for a server on a laptop, and never on Lambda whatever the opt-in
    says (the same rule as MYSIGN_DEV_AUTH in app/auth.py).
    """
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return False
    return os.environ.get("MYSIGN_APPLE_LOCAL_TESTING") == "1"


# ------------------------------------------------------------------ the JWS

def _roots():
    if not ROOTS:
        from cryptography import x509
        ROOTS.append(x509.load_pem_x509_certificate(APPLE_ROOT_G3))
    return ROOTS


def _b64(part):
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _within(cert, now):
    return cert.not_valid_before_utc <= now <= cert.not_valid_after_utc


def _has(cert, oid):
    from cryptography import x509
    try:
        cert.extensions.get_extension_for_oid(x509.ObjectIdentifier(oid))
        return True
    except x509.ExtensionNotFound:
        return False


def _chain_key(certs, now):
    """The leaf's public key, once the chain leaf → intermediate → Apple root holds."""
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    if len(certs) != 3:
        raise AppleError("certificate chain is not leaf, intermediate, root")
    leaf, mid, sent_root = certs
    want = sent_root.fingerprint(hashes.SHA256())
    anchor = next((r for r in _roots() if r.fingerprint(hashes.SHA256()) == want), None)
    if anchor is None:
        raise AppleError("chain does not end at Apple's root")
    try:
        leaf.verify_directly_issued_by(mid)
        mid.verify_directly_issued_by(anchor)
    except (InvalidSignature, ValueError, TypeError) as e:
        raise AppleError("certificate chain does not verify") from e
    if not all(_within(c, now) for c in (leaf, mid, anchor)):
        raise AppleError("a certificate in the chain is expired or not yet valid")
    try:
        ca = mid.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    except x509.ExtensionNotFound:
        ca = False
    if not ca or not _has(mid, OID_INTERMEDIATE) or not _has(leaf, OID_LEAF):
        raise AppleError("certificates are not Apple's App Store signing certificates")
    return leaf.public_key()


def verify(jws, now=None):
    """
    The payload of an App Store JWS, if and only if Apple signed it.

    Checked: ES256, the x5c chain up to the pinned Apple root, validity dates,
    Apple's purpose extensions, the signature over header.payload, and that it
    comes from an App Store environment we accept. Raises AppleError otherwise.
    """
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        head_b64, body_b64, sig_b64 = jws.split(".")
        header = json.loads(_b64(head_b64))
        sig = _b64(sig_b64)
        certs = [x509.load_der_x509_certificate(base64.b64decode(c))
                 for c in header.get("x5c") or []]
    except Exception as e:
        raise AppleError("not a signed App Store payload") from e
    if header.get("alg") != "ES256" or len(sig) != 64 or not certs:
        raise AppleError("not an ES256 payload with a certificate chain")

    xcode = False
    try:
        key = _chain_key(certs, now)
    except AppleError:
        if not local_testing():
            raise
        key, xcode = certs[0].public_key(), True     # Xcode's own certificate
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise AppleError("signing key is not an elliptic-curve key")
    der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
    try:
        key.verify(der, f"{head_b64}.{body_b64}".encode(), ec.ECDSA(hashes.SHA256()))
        payload = json.loads(_b64(body_b64))
    except InvalidSignature as e:
        raise AppleError("signature does not match") from e
    except Exception as e:
        raise AppleError("payload is not JSON") from e
    if not isinstance(payload, dict):
        raise AppleError("payload is not an object")
    env = (payload.get("environment") or (payload.get("data") or {}).get("environment")
           or (payload.get("summary") or {}).get("environment"))
    # A locally signed payload may only ever claim to be Xcode's, and Xcode's
    # environment is only ever accepted from the local path.
    if xcode != (env == "Xcode") or (not xcode and env not in ENVIRONMENTS):
        raise AppleError(f"environment {env!r} is not accepted here")
    return payload


def transaction(jws, now=None):
    """A verified transaction for one of our subscriptions in our app."""
    tx = verify(jws, now)
    if tx.get("bundleId") != BUNDLE_ID:
        raise AppleError("transaction is for a different app")
    if tx.get("productId") not in plan.PRODUCTS:
        raise AppleError("transaction is for an unknown product")
    if not tx.get("originalTransactionId"):
        raise AppleError("transaction has no original transaction id")
    return tx


# -------------------------------------------------------------- whose it is

def account_token(uid):
    """
    The UUID the app passes to StoreKit as appAccountToken when she buys.
    Apple signs it into every transaction and notification for that
    subscription, which is how a purchase finds its account. It says nothing
    about her; it is made once and kept.
    """
    a = load(uid, "apple", None) or {}
    if not a.get("token"):
        a["token"] = str(uuid.uuid4())
        save(uid, "apple", a)
        save(INDEX, f"apple-token-{a['token']}", {"uid": uid})
    return a["token"]


def _account(uid):
    return uid if uid and load(uid, "account", None) else None


def _owner(tx):
    """
    The account a subscription belongs to: the one already holding it, else
    the one whose token was on the purchase. None when that account is gone
    or the purchase carried no token (bought outside the app).
    """
    token = str(tx.get("appAccountToken") or "").lower()
    by_token = _account((load(INDEX, f"apple-token-{token}", None) or {}).get("uid")) if token else None
    # Xcode numbers its test transactions from 0 again every session, so there
    # the id says nothing about who bought; the token on the purchase does.
    if tx.get("environment") == "Xcode" and by_token:
        return by_token
    held = load(INDEX, f"apple-otx-{tx['originalTransactionId']}", None) or {}
    return _account(held.get("uid")) or by_token


def _day(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date()


def apply(uid, tx, renewal=None, kind=None, subtype=None, now=None):
    """
    Make her plan match one verified transaction, whatever carried it.

    What she has follows from the signed dates, not from the name of the
    notification: revoked means refunded, an expiry date in the past means
    over, a grace period (Apple still retrying her card, and we agreed to
    wait) extends it. The few notification types in ENDED end it outright.
    An older picture of the same subscription never overwrites a newer one.
    """
    now_ms = int((now or time.time()) * 1000)
    renewal = renewal or {}
    a = load(uid, "apple", None) or {}
    otx, stamp = tx["originalTransactionId"], int(tx.get("signedDate") or 0)
    same = a.get("otx") == otx
    if same and stamp < int(a.get("signed") or 0):
        return "stale"

    expires = int(tx.get("expiresDate") or 0)
    grace = int(renewal.get("gracePeriodExpiresDate") or 0)
    if tx.get("revocationDate"):
        status, live = ENDED.get(kind, "refunded"), False
    elif kind in ENDED:
        status, live = ENDED[kind], False
    elif grace > now_ms:
        status, live = "grace", True
    elif expires > now_ms:
        status, live = "active", True
    else:
        status = "billing_retry" if kind == "DID_FAIL_TO_RENEW" else "expired"
        live = False
    until = (_day(max(expires, grace)) + dt.timedelta(days=GRACE_DAYS)).isoformat() if live else None

    if same and stamp == int(a.get("signed") or 0) and a.get("status") == status \
            and a.get("until") == until and (plan.get(uid)["tier"] == "plus") == live:
        return "same"                             # a replay: nothing to do twice

    auto = renewal.get("autoRenewStatus")
    a.update(otx=otx, product=tx["productId"], transaction=tx.get("transactionId"),
             expires=expires, until=until, status=status, signed=stamp,
             environment=tx.get("environment"),
             trial=tx.get("offerType") == 1 and status == "active",
             auto_renew=a.get("auto_renew", True) if auto is None else bool(auto))
    save(uid, "apple", a)
    save(INDEX, f"apple-otx-{otx}", {"uid": uid})
    plan.provider_update(uid, "apple", live, until=until, product=tx["productId"])
    if live:
        try:
            from engine import funnel
            funnel.hit(uid, "paid")              # once per person, as Stripe's does
        except Exception:
            pass
    return status


# ------------------------------------------------------------ from the app

def sync(uid, jws):
    """
    The app hands over a signed transaction: just bought, restored, or renewed
    while it was open. Returns {"ok", "code", "plan"}; the app finishes the
    transaction with StoreKit only when `finish` is true, which means the
    server has it on record for whoever it belongs to.
    """
    tx = transaction(jws)
    owner = _owner(tx)
    if owner and owner != uid:
        # Bought under another My Sign account that still exists. It stays
        # theirs: one subscription never unlocks two accounts.
        apply(owner, tx)
        return {"ok": False, "finish": True, "code": "other_account"}
    account_token(uid)
    status = apply(uid, tx)
    return {"ok": True, "finish": True, "code": status, "plan": plan.get(uid)}


# ------------------------------------------------------------- from Apple

def notify(signed_payload):
    """
    One App Store Server Notification (V2). Idempotent on notificationUUID:
    Apple retries, and the same notification is applied once.
    """
    n = verify(signed_payload)
    nid = n.get("notificationUUID") or ""
    if nid and load(INDEX, f"apple-evt-{nid}", None):
        return "duplicate"
    kind, subtype, data = n.get("notificationType", ""), n.get("subtype"), n.get("data") or {}
    result = "ignored"
    if kind == "TEST":
        result = "test"
    elif data.get("signedTransactionInfo"):
        if data.get("bundleId") != BUNDLE_ID:
            raise AppleError("notification is for a different app")
        tx = transaction(data["signedTransactionInfo"])
        renewal = verify(data["signedRenewalInfo"]) if data.get("signedRenewalInfo") else None
        uid = _owner(tx)
        # No account: deleted, or bought outside the app and not opened since.
        # The app's own sync claims it the next time she opens it.
        result = apply(uid, tx, renewal, kind, subtype) if uid else "unowned"
    if nid:
        save(INDEX, f"apple-evt-{nid}", 1, ttl=int(time.time()) + 30 * 86400)
    return result


# ------------------------------------------------------------- for the app

def subscription(uid, live=None):
    """Her Apple subscription while it gives her Plus, for Settings and for
    the warning before she deletes her account. None otherwise. `live` is
    plan.providers(uid) when the caller already has it."""
    if "apple" not in (plan.providers(uid) if live is None else live):
        return None
    a = load(uid, "apple", None) or {}
    return {"status": a.get("status"), "product": a.get("product"),
            "trial": bool(a.get("trial")), "renews": bool(a.get("auto_renew", True))}


def offer(uid):
    """
    What the iPhone app needs to sell Plus: whether the shop is open, her
    appAccountToken, the product ids, and whether to offer them at all.
    Someone who already has Plus (a web subscription, a hand grant) is never
    offered a second one.
    """
    on = enabled()
    return {"available": on,
            "token": account_token(uid) if on else None,
            "products": list(plan.PRODUCTS),
            "offer": on and plan.get(uid)["tier"] != "plus"}


def forget(uid):
    """Account deletion: drop the index lines that point at her. Apple does
    not let an app cancel the subscription itself; the client says so first."""
    a = load(uid, "apple", None) or {}
    if a.get("token"):
        delete(INDEX, f"apple-token-{a['token']}")
    if a.get("otx"):
        delete(INDEX, f"apple-otx-{a['otx']}")
