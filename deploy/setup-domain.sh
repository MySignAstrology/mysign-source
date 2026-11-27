#!/usr/bin/env bash
# Put My Sign on its own domain. Idempotent: safe to re-run after any failure.
#
#   deploy/setup-domain.sh mysignapp.com
#
# 1. us-east-1 certificate (CLI; the SCP blocks CloudFormation there),
#    DNS-validated through the domain's Route 53 zone
# 2. origin token, kept in the secret (the stack resolves it from there)
# 3. cdk deploy with the domain: CloudFront, SES domain identity, and every
#    DNS record (DKIM, MAIL FROM, DMARC, MX, ALIAS)
# 4. activate inbound mail (support@ -> forwardTo)
set -euo pipefail
cd "$(dirname "$0")"

DOMAIN=${1:?usage: setup-domain.sh <domain>}
ACCOUNT=889002527942
REGION=us-east-2
SANDBOX=${SES_SANDBOX:-false}        # production access granted 2026-10-28
export AWS_PAGER=
export AWS_REGION=$REGION AWS_DEFAULT_REGION=$REGION   # export-credentials itself needs one
eval "$(aws configure export-credentials --profile mysign --format env)"
[ "$(aws sts get-caller-identity --query Account --output text)" = "$ACCOUNT" ] \
  || { echo "credentials are not for $ACCOUNT"; exit 1; }

ZONE=$(aws route53 list-hosted-zones-by-name --dns-name "$DOMAIN" \
         --query "HostedZones[?Name=='$DOMAIN.'].Id | [0]" --output text | sed 's|/hostedzone/||')
[ -n "$ZONE" ] && [ "$ZONE" != None ] || { echo "no hosted zone for $DOMAIN"; exit 1; }
echo "→ zone $ZONE"

# ---------------------------------------------------------------- certificate
CERT=$(aws acm list-certificates --region us-east-1 \
         --query "CertificateSummaryList[?DomainName=='$DOMAIN'] | [0].CertificateArn" --output text)
if [ -z "$CERT" ] || [ "$CERT" = None ]; then
  CERT=$(aws acm request-certificate --region us-east-1 --domain-name "$DOMAIN" \
           --subject-alternative-names "www.$DOMAIN" --validation-method DNS \
           --query CertificateArn --output text)
fi
echo "→ cert $CERT"
for _ in $(seq 20); do     # validation records appear a few seconds after the request
  RECS=$(aws acm describe-certificate --region us-east-1 --certificate-arn "$CERT" \
           --query 'Certificate.DomainValidationOptions[].ResourceRecord' --output json)
  [ "$(echo "$RECS" | python3 -c 'import json,sys;print(sum(1 for r in json.load(sys.stdin) or [] if r))')" -gt 0 ] && break
  sleep 3
done
BATCH=$(mktemp)
echo "$RECS" | python3 -c '
import json, sys
seen, changes = set(), []
for r in json.load(sys.stdin):
    if r and r["Name"] not in seen:
        seen.add(r["Name"])
        changes.append({"Action": "UPSERT", "ResourceRecordSet": {
            "Name": r["Name"], "Type": r["Type"], "TTL": 300,
            "ResourceRecords": [{"Value": r["Value"]}]}})
print(json.dumps({"Changes": changes}))' > "$BATCH"
aws route53 change-resource-record-sets --hosted-zone-id "$ZONE" \
  --change-batch file://"$BATCH" --query ChangeInfo.Status --output text
echo "→ waiting for the certificate to issue"
aws acm wait certificate-validated --region us-east-1 --certificate-arn "$CERT"

# ---------------------------------------------------------------- origin token
# Kept in the secret; the stack resolves it from there at deploy time.
SECRET=$(aws cloudformation describe-stacks --region $REGION --stack-name mysign \
           --query "Stacks[0].Outputs[?OutputKey=='SecretArn'].OutputValue" --output text)
SECRET="$SECRET" python3 -c '
import json, os, secrets, boto3
sm = boto3.client("secretsmanager"); arn = os.environ["SECRET"]
data = json.loads(sm.get_secret_value(SecretId=arn)["SecretString"])
if not data.get("origin_token"):
    data["origin_token"] = secrets.token_hex(32)
    sm.put_secret_value(SecretId=arn, SecretString=json.dumps(data))'

# ---------------------------------------------------------------- deploy
# cdk.json holds the live domain settings, so a plain `cdk deploy` keeps them;
# these flags only matter for a different domain or a fresh certificate.
npx -y aws-cdk@2 deploy --require-approval never \
  -c targetAccount=$ACCOUNT -c targetRegion=$REGION \
  -c domainName="$DOMAIN" -c hostedZoneId="$ZONE" -c certificateArn="$CERT" \
  -c includeWww=true -c sesSandbox="$SANDBOX"

# ---------------------------------------------------------------- inbound mail
aws ses set-active-receipt-rule-set --rule-set-name mysign-inbound --region $REGION
echo "→ done. https://$DOMAIN"
