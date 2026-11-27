# Billing (Stripe, web)

The iPhone app sells Plus through Apple instead: see `docs/ios-iap.md`. Both
write the plan through `plan.provider_update`, one line per provider.

`app/billing.py` sells Plus through Stripe Checkout and keeps `engine/plan` in step
through signed webhooks. It is **off** until all three of these are in the stage's
secret (`MYSIGN_SECRET_ARN`):

| secret field | what |
|---|---|
| `stripe_secret_key` | `sk_test_…` (sandbox) or `sk_live_…` |
| `stripe_webhook_secret` | `whsec_…` from the webhook endpoint below |
| `billing` | `stripe` — the deliberate switch |

Turning it on makes Plus buyable **and** starts enforcing the free limits that
`PRICES["available"]` has held open (people cap, Plus depth). Do it on staging first.

## Stripe dashboard, once per account (sandbox, then live)

1. **Prices.** Product *My Sign Plus* with two recurring prices, found by lookup key so
   they can be replaced without a deploy: `mysign_plus_monthly` ($9.99/mo) and
   `mysign_plus_annual` ($59.99/yr).
2. **Webhook endpoint** → `https://<domain>/api/billing/webhook`, events:
   `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted`,
   `customer.subscription.trial_will_end`, `invoice.paid`, `invoice.payment_failed`.
   Its signing secret is `stripe_webhook_secret`.
3. **Customer portal**: cancel at period end, switch between the two prices, update card.
4. **Emails** (Settings → Billing → Subscriptions and emails): turn on receipts and
   failed-payment emails. The trial reminder is ours (sent on `trial_will_end`, 3 days
   before billing), so Stripe's own trial reminder can stay off.

## How a purchase flows

Paywall button → `POST /api/billing/checkout` → Stripe Checkout (7-day trial, once per
person) → back to `/app?plus=welcome` → webhook → the subscription is fetched fresh from
Stripe and applied → `plan.grant(uid, "plus", source="stripe", until=period end + 3 days)`.

Cancel, unpaid, expired → free (unless Plus came from the operator console). Deleting the
account cancels the subscription immediately. Each event id is applied once.

## Testing in the sandbox

`stripe listen --forward-to localhost:8000/api/billing/webhook` prints a `whsec_` for
local runs; card `4242 4242 4242 4242`, any future date, any CVC.
