# Holiday 2026: gift guide, Google and Pinterest

Everything here is prepared and nothing is live. Som approves each step that goes live, connects an account or accepts terms.

Why now: Sagittarius season starts Nov 22, Black Friday is Nov 27, and the store has had 8 sessions in 8 days (Store Agent, Nov 5). These channels are untested for mySign, so measure them before spending more time on them.

## Files
| File | What it is |
|---|---|
| `site/gifts/` | The guide: `/gifts` and a page per sign, with `pin-<sign>.jpg` images |
| `tools/gift_guide.py`, `tools/gift_pins.js` | Rebuild the pages and pins from the current catalog |
| `google-listings.csv` | Suggested Shopify title, description and category for each buyable product, with problems to fix |
| `pinterest-pins.csv` | 12 pins in Pinterest's bulk upload layout, Christmas gift shopping signs first |

Rebuild after any stock change: `python tools/shop_catalog.py`, then `python tools/gift_guide.py`, then `node tools/gift_pins.js`, then deploy. A test fails if the guide no longer matches the catalog.

## Step 1: approve the guide
1. Som reads the copy for all 12 signs and approves it.
2. Merge the PR and deploy. Pin images only exist at their public URLs after this step, and `pinterest-pins.csv` depends on them.

## Step 2: fix these before applying to Google
Google reviews the store itself. As far as we know, these are the things it may object to. Its own review screens have the final say.

| Problem | Checked | Owner |
|---|---|---|
| `/policies/contact-information` returns 404, and the refund policy points to it | Nov 5 | Som: add it in Shopify under Settings > Policies |
| Refund policy gives som@xii-studio.io; the app uses support@mysignapp.com | Ops G019 | Som: pick one |
| Refund policy says eligibility "depends on the items". Google may want a clear return window | Nov 5 | Som |
| The shipping policy says costs and times show at checkout. Google listings need real shipping rates | Nov 5 | Som: confirm how Collective shipping rates are set |
| "Best Seller!" and wholesale copy in descriptions | See CSV | Som: approve the edits |
| Silver / Scorpio pendant shows the gold photo | Nov 5 | Som: get a silver photo from Rose & Clay, or leave it out |

## Step 3: Google (about 30 minutes for Som, then Google's review)
1. In Shopify admin, add the **Google & YouTube** sales channel.
2. Connect a Google account that the business owns. Create the Merchant Center account for XII Studios LLC and accept its terms. This must be Som.
3. Choose United States as the only country to begin with. Decline Performance Max and any ad credit offer: free listings only.
4. Approve and apply `google-listings.csv`. Check after a sync that Collective doesn't overwrite the edits. We don't know yet whether it does.
5. If Google asks for product IDs, these items probably have no GTIN. Set that per product rather than making one up.
6. Fix anything Merchant Center flags. Claude can read the flagged list and draft fixes.

## Step 4: Pinterest (about 20 minutes for Som)
1. Create a free Pinterest business account for mySign. Som creates it and enters the password.
2. Claim `mysignapp.com` and `shop.mysignapp.com` with the method Pinterest offers. A DNS or site change needs its own go-ahead.
3. Add the **Pinterest** sales channel in Shopify so products become product pins. Decline ads for now.
4. Create the board "Zodiac gifts by sign".
5. Compare `pinterest-pins.csv` with Pinterest's current bulk upload template, as its columns may have changed. Then upload it. Publish dates are suggested as of Nov 12: Scorpio Nov 14, Sagittarius Nov 18, Capricorn Nov 20, then one sign every 2 days to Dec 10. Change them freely.
6. Pin images show maker photos. Down To Earth gave written OK for social posts if tagged @downtoearthks. Mystic Soul (ritual oil), Circe and Barse have not replied yet. Until they do, Som decides whether pins may show their photos.

Product photos come from the makers through Shopify Collective. Som should confirm they may be used in Pinterest pins as well as on product pages.

## What to measure
Read weekly from Shopify (sessions by referrer) and the Store Agent report, until Dec 21:

- Sessions from Google and Pinterest. Pinterest pins use `utm_source=pinterest`; guide links to Shopify use `utm_source=mysign`.
- Carts and orders from those sessions.
- Which sign pages get traffic.

Keep the channel if it sends visitors who add to cart. If sessions come with no carts, look at the products and prices next. Don't pay for ads until a report or product has sold and its margin after shipping is known.

## Updated Nov 12
- Pins and descriptions now say "from $5.99" and name the newer pieces. Run `node tools/gift_pins.js` after catalog changes.
- `google-listings.csv` covers all 20 products.
- /shop now says "order by Dec 12 for Christmas", based on USPS Ground Advantage's Dec 17 cutoff plus the makers' 1-6 day handling.
- **Report offers.** The guide can link to the couple report once it's published.
- **Instagram and TikTok links to the guide.** Hold them until after the TikTok test ends around Nov 8, so they don't muddy the results.
