"""
Pull secrets into the environment before anything reads them.

Import order is load-bearing: `server.DEMO` reads the environment at import time,
so this has to run first. Importing it at the top of server.py is the whole
mechanism — there is no lazier place to put it.
"""
import json
import os

KEYS = {"anthropic_api_key": "ANTHROPIC_API_KEY",
        # The psychic room's studio voices (app/psychic.py). Unset: device voices.
        "elevenlabs_api_key": "ELEVENLABS_API_KEY",
        "session_secret": "MYSIGN_SECRET",
        "vapid_private": "MYSIGN_VAPID_PRIVATE",
        "vapid_contact": "MYSIGN_VAPID_CONTACT",
        "pagerduty_routing_key": "PAGERDUTY_ROUTING_KEY",
        "from_email": "MYSIGN_FROM_EMAIL",
        "support_email": "MYSIGN_SUPPORT_EMAIL",
        "review_email": "MYSIGN_REVIEW_EMAIL",
        "review_code": "MYSIGN_REVIEW_CODE",
        "ops_token": "MYSIGN_OPS_TOKEN",          # break-glass only; see docs/operations.md
        "ops_admins": "MYSIGN_OPS_ADMINS",        # comma-separated emails, first admins
        "team_markers": "MYSIGN_TEAM_MARKERS",    # marks our own accounts in /ops; not a credential
        "origin_token": "MYSIGN_ORIGIN_TOKEN",
        "origin_token_previous": "MYSIGN_ORIGIN_TOKEN_PREVIOUS",
        # Stripe (app/billing.py). Plus becomes buyable only when all three
        # are set and billing is "stripe" — the switch is deliberate.
        "stripe_secret_key": "STRIPE_SECRET_KEY",
        "stripe_webhook_secret": "STRIPE_WEBHOOK_SECRET",
        "billing": "MYSIGN_BILLING",
        # Apple In-App Purchase (app/apple.py): "1" opens the shop in the iPhone app.
        "apple_iap": "MYSIGN_APPLE_IAP",
        # Notifications in the iPhone app (app/apns.py). All of the first four,
        # or nothing is sent: "1", the .p8 key's text, its key id, the team id.
        # apns_env is "sandbox" only where Xcode-run builds are the clients.
        "apns": "MYSIGN_APNS",
        "apns_key": "MYSIGN_APNS_KEY",
        "apns_key_id": "MYSIGN_APNS_KEY_ID",
        "apns_team_id": "MYSIGN_APNS_TEAM_ID",
        "apns_env": "MYSIGN_APNS_ENV",
        # Unset until the toll-free number's carrier registration is approved;
        # until then phone sign-in refuses cleanly (app/auth.py sms_enabled).
        "sms_origination_identity": "MYSIGN_SMS_ORIGIN"}


def load_secrets():
    """
    Never fatal.

    This runs at import, so anything it raises takes the whole application down
    — not one route, all of them, including anything that would tell you why. A
    freshly created secret holds a placeholder that is not JSON, and the first
    deploy died on json.loads before serving a single API request.

    An unpopulated secret is a degraded deploy, not a broken one: the app
    already falls back to demo mode without a model key and refuses sign-in
    without a mail sender. Both are visible states. A crash loop is not.
    """
    arn = os.environ.get("MYSIGN_SECRET_ARN")
    if not arn:
        return []                       # local dev reads plain env vars

    try:
        import boto3
        raw = boto3.client("secretsmanager").get_secret_value(SecretId=arn).get("SecretString")
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            raise ValueError(f"secret is a {type(data).__name__}, expected an object")
    except Exception as e:
        from app import log
        log.error("secret.unreadable", exc=e, arn_tail=arn[-12:])
        return []

    loaded = []
    for k, env in KEYS.items():
        if data.get(k) and not os.environ.get(env):
            os.environ[env] = data[k]
            loaded.append(env)
    from app import log
    log.emit("secret.loaded", vars_loaded=loaded)
    return loaded
