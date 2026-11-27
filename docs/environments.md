# Environments

Two stacks from one CDK app, picked by `-c stage=` (default `prod`).
`deploy/stages.py` resolves every setting; `deploy/cdk.json` holds the values.

| | prod | staging |
| --- | --- | --- |
| Stack | `mysign` (termination protection on) | `mysign-staging` (off) |
| Domain | `mysignapp.com` + `www` | `staging.mysignapp.com` (same hosted zone) |
| Certificate | `certificateArn` | `stagingCertificateArn` |
| WAF | `webAclArn` | `stagingWebAclArn` |
| Names | `/mysign/app`, `mysign-places`, … | `/mysign-staging/app`, `mysign-staging-places`, … |
| Table / secret / app logs | RETAIN, deletion protection, AWS Backup | destroyed with the stack, no Backup |
| Mail out | owns the `mysignapp.com` SES identity | sends through prod's identity as `My Sign (staging)`, own configuration set |
| Mail in (support@) | receipt rule set + forwarder | none — SES allows one active rule set per region |
| Reserved concurrency | 100 | none |
| Static files | Lambda (`staticFromS3: false`) | S3 + CloudFront Function (`staticFromS3: true`) |
| Metrics namespace | `MySign` | `MySign/staging` |

Prod is the top-level context in `cdk.json`, untouched. A non-prod stage
reads its entry in `stages` and, from the top level, only the keys in
`stages.SHARED` — it can never pick up prod's certificate, WAF or concurrency
by omission. Per-stage overrides on the command line use `-c <stage><Key>=`
(`-c stagingCertificateArn=…`); a bare `-c certificateArn=` is always prod's.

## Staging, first time

From the repo root, profile `mysign`, account 889002527942.

1. **Certificate** (us-east-1 — CloudFront's rule; CloudFormation is blocked
   there by SCP, so it is made by CLI):

   ```sh
   aws acm request-certificate --domain-name staging.mysignapp.com \
     --validation-method DNS --region us-east-1 --profile mysign
   aws acm describe-certificate --certificate-arn <arn> --region us-east-1 --profile mysign \
     --query 'Certificate.DomainValidationOptions[0].ResourceRecord'
   ```

   Add that CNAME to hosted zone `Z10101752EPEFIUE9F49Q` and wait for `ISSUED`.

2. **Stack without CloudFront.** Leave `stagingCertificateArn` unset. CloudFront
   reads `origin_token` from the stage's own secret at deploy time, and that
   secret does not exist yet.

   ```sh
   ./deploy/build.sh
   cd deploy && AWS_PROFILE=mysign cdk deploy mysign-staging -c stage=staging -c targetAccount=889002527942
   ```

3. **Secret.** Fill the `SecretArn` output: `anthropic_api_key`, `session_secret`,
   `vapid_private` (a new one — never prod's), `origin_token`
   (`openssl rand -hex 32`), optionally `ops_token`. See the table in
   `docs/operations.md`.

4. **Edge.** Put the certificate ARN in `deploy/cdk.json` as
   `stagingCertificateArn` (and `stagingWebAclArn` if staging should sit behind
   the WAF), commit, and deploy again. The stack writes the
   `staging.mysignapp.com` alias record itself.

Staging sends real mail from `hello@mysignapp.com`; SES production access is
account-wide, so treat sign-in codes there as real.

## Static files from S3 (`staticFromS3`)

On: `/static/*`, `/sw.js`, `/manifest.webmanifest` and the HTML pages are served
from a private S3 bucket (`deploy/build/site/`, built by `build.sh`) and never
wake the Lambda. `/api/*`, `/bt/*`, `/ops*` and `/e` still go to it. The
CloudFront Function `deploy/edge/router.js` decides `/`:

- `ms_session` cookie, or `?invite=` / `?p=` / `?sky=` → 302 to `/app` with the query;
- otherwise the website, `site/index.html`;
- `/app`, `/app/<view>` → the app shell; `/privacy`, `/terms`, `/accuracy` → their pages;
- the website's files (`/brand.css`, `/sky.js`, `/site.js`, `/track.js`, `/og.png`,
  `/robots.txt`, `/sitemap.xml`) → themselves.

With the flag off, `app/server.py` makes the same decisions (the zip carries
`site/`), so `/` behaves the same either way, and locally under `./run.sh`.

Cache: `/static/*` a day in the browser and a year at the edge (redeploys
invalidate); `sw.js` and the manifest never cached; HTML revalidated every time,
five minutes at the edge. A response headers policy adds HSTS, `nosniff`,
`Referrer-Policy: strict-origin-when-cross-origin` and `X-Frame-Options: DENY`.
No CSP: the app relies on inline scripts and styles, and an
enforcing policy would break it.

Known differences with the flag on: FastAPI's `/docs` and `/openapi.json` are no
longer reachable (they fall through to S3, which answers 403 for any missing
key).

The service worker (`mysign-v3`) keeps scope `/`, because existing push
subscriptions belong to that registration. It caches the `/app` shell and falls
back to it offline only for `/app` paths; it never stores a redirect and never
answers the website with the app. The manifest's `start_url` is `/app` with
`id: "/"`, the identity installs from before the move already have.

**Turning it on in prod**, after staging has run on it for a while:

```sh
cd deploy && AWS_PROFILE=mysign cdk diff mysign -c staticFromS3=true    # expect: Site bucket, OAC, Router, behaviors
AWS_PROFILE=mysign cdk deploy mysign -c staticFromS3=true
```

then set `"staticFromS3": true` in `cdk.json` so the next deploy keeps it.
Turning it off again restores the single Lambda behavior; the bucket stays
(RETAIN in prod).

## Analytics (`mysign-analytics`, `collectorDomain`)

The event lake is its own stack (`deploy/analytics_stack.py`, `docs/analytics.md`).
The main stack points at it by name and by context, never by export, so either
can be deployed, updated or deleted alone. Per stage, in this order:

1. **Analytics stack** — `mysign-analytics` / `mysign-analytics-staging`:

   ```sh
   cd deploy && AWS_PROFILE=mysign cdk deploy mysign-analytics -c targetAccount=889002527942 \
     -c appSecretArn=<the app's SecretArn> -c alarmTopicArn=<the app's alarm topic> \
     -c domainName=mysignapp.com
   # staging: add -c stage=staging (stack mysign-analytics-staging, the staging secret)
   ```

2. **Its `CollectorDomain` output** (`<id>.lambda-url.us-east-2.on.aws`):

   ```sh
   aws cloudformation describe-stacks --stack-name mysign-analytics --profile mysign \
     --query "Stacks[0].Outputs[?OutputKey=='CollectorDomain'].OutputValue" --output text
   ```

3. **Main stack with it.** Put it in `cdk.json` — `collectorDomain` for prod,
   `collectorDomain` inside `stages.staging` for staging (or pass
   `-c collectorDomain=` / `-c stagingCollectorDomain=` once) — and deploy.
   That adds the CloudFront `/e` behavior to the collector and tells the app
   (`MYSIGN_COLLECTOR`) that the sign-in screen may count.

The app and cron functions always get `MYSIGN_EVENTS_STREAM`
(`mysign-events` / `mysign-events-<stage>`) and `firehose:PutRecordBatch` on
it; `-c analyticsEvents=false` leaves them out. Deployed before the analytics
stack, every forward fails fast and is logged, and nothing else is affected.
Without `collectorDomain`, `/e` stays where it was (the app, which 404s), and
prod's distribution is unchanged.

## Provisioned concurrency (`provisionedConcurrency`)

About 7% of requests hit a cold start averaging 5.3 s. `-c provisionedConcurrency=<N>`
adds a `live` alias with N warm environments between `provisionedFromHourUtc`
(12:00 UTC) and `provisionedToHourUtc` (05:00 UTC) — US daytime — and none
overnight. Set both hours to `null` for always-on. Provisioned environments are
billed whether used or not, and they come out of the 100 reserved for the
function. Account limit: 1000 concurrent executions (verified); prod reserves 100.

Turning it on moves the Function URL onto the alias, which is a new URL:
CloudFront's origin changes with it in the same deploy, and the old URL is
deleted. The schedule applies from its next 12:00 UTC, not at deploy time.

## Deploying from GitHub Actions

`.github/workflows/deploy.yml` runs by hand only (Actions → deploy → Run
workflow): pick `staging` (the default, from `main`) or `production`, which
runs in the GitHub environment `production`. Pushes, tags and pull requests
never deploy. It can't sign in to AWS until the setup below is done; until
then, deploy from a terminal.

One-time setup:

1. **AWS (account admin).** Deploy the role stack:

   ```sh
   cd deploy && AWS_PROFILE=mysign cdk deploy --app "python3 cicd.py" -c targetAccount=889002527942
   ```

   It creates GitHub's OIDC provider and the role `mysign-github-deploy`, which
   may only assume the CDK bootstrap roles. If the provider already exists the
   deploy fails on it; rerun with
   `-c githubOidcProviderArn=arn:aws:iam::889002527942:oidc-provider/token.actions.githubusercontent.com`.
   An org SCP denies `iam:ListOpenIDConnectProviders` to the operator role, so
   whether it exists could not be checked — and the same SCP may deny creating
   one, in which case an org administrator has to create the provider (or the
   role) and this stack is deployed with its ARN.

2. **GitHub (repo admin).** Settings → Environments → `production`: required
   reviewers, deployment branch `main`. Settings → Variables: `AWS_DEPLOY_ROLE_ARN`
   (the `DeployRoleArn` output) and `AWS_ACCOUNT_ID` = `889002527942`.

The role trusts `repo:MySignAstrology/mysign` on `refs/heads/main` and in
environment `production`, in both the name form and GitHub's immutable-id form
(`repo:MySignAstrology@320017987/mysign@1343248366:…`). Staging and prod share
the CDK bootstrap roles, so the reviewer gate on `production` is what keeps
prod deploys deliberate — IAM alone does not.
