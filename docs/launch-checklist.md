# Launch day

Only after `docs/pilot.md` says GO and every item in Phases 0–9 of the road map
is done. This is the last hour, not the last month.

**A week before — out-of-band AWS steps**

CloudFormation is blocked in us-east-1 by an org SCP, so the two things
CloudFront needs from that region are made by CLI and passed in by ARN.
Everything here is in account 889002527942.

- [x] **Lambda concurrency quota.** The account limit is 1000 (verified; was 10). cdk.json reserves 100 for the
      app function (`reservedConcurrency`, which also pins the scheduler to 1). Staging reserves none and shares the rest.
      Provisioned concurrency (`-c provisionedConcurrency=<N>`) comes out of the reserved 100 — see `docs/environments.md`.
- [ ] **Certificate** (us-east-1, DNS-validated, covers `www.` if you will pass `includeWww`):
      `aws acm request-certificate --domain-name <domain> --subject-alternative-names www.<domain> --validation-method DNS --region us-east-1`,
      then add the CNAME from `aws acm describe-certificate --certificate-arn <arn> --region us-east-1` and wait for `ISSUED`.
- [ ] **WAF (CLOUDFRONT scope lives in us-east-1):** `deploy/waf/create-waf.sh` creates `mysign-edge` from
      `deploy/waf/rules.json` (or converges it if it exists) and prints the ARN. AWS managed IP reputation, known bad
      inputs and common rule sets (body-size rule in COUNT: chat and analytics bodies pass 8 KB), 1000 req / 5 min per IP
      overall, 10 / 5 min per IP on `/api/auth/request` (every call sends a message we pay for), and non-US
      `/api/auth/request` in COUNT (email sign-in is global; block once SMS ships). Put the ARN in `deploy/cdk.json`
      as `webAclArn` (prod) and `stagingWebAclArn` (staging). A prod synth without one refuses to run (`-c allowNoWaf=true` overrides). Watch the COUNT metrics for a week
      before tightening.
- [ ] **Origin token.** Generate it straight into the secret as `origin_token` (`deploy/setup-domain.sh` does exactly this). Never pass it as
      `-c originToken=`: the synth refuses, because a context value is written into the template in plain text.
      Wire `app.add_middleware(OriginGuard)` (from `app/edge.py`) into `app/server.py` if it is not already.
- [ ] **Deploy** (from `deploy/`):
      `cdk deploy -c targetAccount=889002527942 -c targetRegion=us-east-2 -c domainName=<domain> -c certificateArn=<arn> [-c hostedZoneId=<zone>] -c webAclArn=<arn> [-c includeWww=true] [-c sesSandbox=true] [-c reservedConcurrency=<N>]`
- [ ] **DNS.** With `-c hostedZoneId=` (the domain's Route 53 zone in this account) the stack writes all of this itself — skip this step.
      Otherwise add every record in the stack outputs: three `DkimRecord*` CNAMEs, `MailFromMx`, `SpfRecord`, `DmarcRecord`, `InboundMx`,
      and the apex/`www` ALIAS to `DomainTarget`. (`InboundMx` goes on the apex — if the domain already has another mail provider, don't add it.)
- [ ] **Activate inbound mail.** CloudFormation can create a receipt rule set but not activate it. Run the `ActivateInbound` output:
      `aws ses set-active-receipt-rule-set --rule-set-name <name> --region us-east-2`. Send a test to support@ and confirm it forwards.
- [ ] **SES production access.** Until granted, SES only sends to verified addresses — deploy with `-c sesSandbox=true` meanwhile and verify
      the forward-to address (`aws ses verify-email-identity --email-address som@xii-studio.io --region us-east-2`).
      Request: `aws sesv2 put-account-details --production-access-enabled --mail-type TRANSACTIONAL --website-url https://<domain> --use-case-description "One-time sign-in codes for My Sign; no marketing mail; bounces and complaints suppressed via configuration set." --region us-east-2`.
      When granted, redeploy without `sesSandbox`. **Granted 2026-10-28 (50,000/day); cdk.json now has sesSandbox=false and reservedConcurrency=100.**
- [ ] **Confirm the alarm subscription.** `aws sns subscribe --topic-arn <AlarmTopic output> --protocol email --notification-endpoint <you> --region us-east-2`,
      then click the confirmation link — an unconfirmed subscription delivers nothing, including `RedTierAlarm`.
- [ ] **Legacy log groups.** Set 90-day retention on the old `/aws/lambda/mysign-Fn…` and `/aws/lambda/mysign-Cron…` groups:
      `aws logs put-retention-policy --log-group-name <name> --retention-in-days 90 --region us-east-2`.
- [ ] **Ops console.** Add `ops_admins` (your sign-in email) to the secret, sign in at `/app`, open `/ops`, add the other operators — then remove `ops_token` if it was ever set. See docs/operations.md "Operator console".

**The app at `/app`** (on staging first, then prod; see docs/environments.md)
- [ ] `/` in a private window is the website; signed in, `/` lands on `/app`. Sign out lands on the website.
- [ ] An **existing** home-screen install (made when the app was at `/`) still opens, now on `/app`, without a reinstall.
      Its push subscription survives: Settings shows notifications on, and `/api/push/test` arrives.
- [ ] Tapping a push about a person (`/app?p=…`) opens that person, with the app closed and with it open in the background.
- [ ] An invite link made **before** the move (`/?invite=…`) and one made after (`/app?invite=…`) both show the invite bar.
- [ ] Offline (airplane mode) an installed app shows its shell; the website never shows the app.
- [ ] Native shell (when it exists): bundled assets, its start page is the app's, and the API base is the staging origin.

**The night before**
- [ ] `cdk deploy` of the exact commit you're launching. Tag it.
- [ ] `tools/eval_voice.py` against the real model — green.
- [ ] Sign in on a fresh account end to end: code arrives, chart renders, push arrives next morning.
- [ ] Alarm topic has a confirmed human subscription. PagerDuty key present. Send a test page.
- [ ] `curl -s -o /dev/null -w '%{http_code}' <Url output>` returns **403** — the raw Function URL refuses anything that skipped CloudFront.
- [ ] Billing alarm set on the AWS account. Spend limit set on the Anthropic key.
- [ ] Quota limits reviewed against pilot usage — 8/min, 120/day still right?
- [ ] Privacy and terms: governing-law placeholder replaced. Both linked from the landing page.
- [ ] Support inbox: someone owns it. Auto-reply says "a person reads this."
- [ ] VAPID key backed up off-AWS.

**The hour before**
- [ ] Landing page CTA points at the app, not a mailto.
- [ ] Domain resolves, HTTPS clean, no mixed content.
- [ ] Console open, roster visible.

**Launch**
- [ ] Post. TikTok first — the demographic is the market, not a segment of it.
- [ ] Watch the `onboarded` event rate for an hour. Watch `quota.refused`. Watch `safety.red`.

**The day after**
- [ ] Read every thread from day one.
- [ ] Funnel: signups → onboarded → day-2 return. Write the three numbers down.

## Legal follow-ups (moved out of the served pages, 2026-11-03)

- [ ] A lawyer reads privacy, terms and refunds, including the liability section and any consumer-law carve-outs.
- [ ] Publish a full postal address for XII Studios LLC once a business address is chosen.
- [ ] Confirm the Florida governing-law line against where the LLC is registered.
- [ ] Confirm the App Store terms section meets Apple's custom EULA minimums, or adopt Apple's standard EULA.
- [ ] Confirm `pagerduty_routing_key` in the production secret, then state PagerDuty's retention on the privacy page.
- [ ] Decide and state a legal-request policy.
- [ ] Set `inference_geo` or confirm where Anthropic processes requests before naming a location.
- [ ] Decide a retention period for long-inactive accounts.
- [ ] Confirm Stripe receipts are on and the webhook includes `customer.subscription.trial_will_end`.
