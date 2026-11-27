#!/usr/bin/env bash
# Create (or update in place) the CloudFront web ACL and print its ARN for
# `cdk deploy -c webAclArn=<arn>`.
#
# By CLI rather than CDK because CLOUDFRONT-scope WAF lives in us-east-1, where
# an org SCP denies CloudFormation. One ACL can front both distributions:
# staging takes the same ARN as `-c stagingWebAclArn=<arn>`.
#
# rules.json, in evaluation order:
#   ip-reputation        AWS managed: known botnets/scanners.
#   auth-request-rate    10 req / 5 min / IP on /api/auth/request and
#                        /api/account/identifier/request (both send codes). Every request
#                        sends a sign-in message we pay for; this caps pumping.
#   auth-request-non-us  COUNT only. Email sign-in is global; flip to Block once
#                        SMS (US-only) ships, or scope it to the SMS route.
#   auth-verify-rate     30 req / 5 min / IP on the two code-checking routes
#                        (/api/auth/verify, /api/account/identifier/verify).
#                        The app has its own per-address, per-IP and global
#                        verify limits (app/auth.py); this is the edge copy.
#   model-rate           100 req / 5 min / IP on the routes that can call the
#                        model: /api/message, /api/placement/*, */reading. The
#                        app meters each per user per day (engine/spend.py).
#   general-rate         1000 req / 5 min / IP across the site.
#   known-bad-inputs     AWS managed: Log4j, Java deserialization, etc.
#   common               AWS managed CRS, with SizeRestrictions_BODY (>8 KB) in
#                        COUNT: a 4000-char chat message is up to 16 KB of UTF-8,
#                        and a 50-event /api/activity batch routinely passes 8 KB.
#
#   known-bad-inputs and common start as COUNT for the whole group (attached
#   2026-10-30): chat and journal text is free prose, and a false positive
#   would silently eat someone's message. Review the sampled requests in the
#   WAF console after a week of real traffic, then flip either to None (block).
#
# Usage: deploy/waf/create-waf.sh            (AWS_PROFILE defaults to mysign)
#        WAF_NAME=mysign-edge-test deploy/waf/create-waf.sh
set -euo pipefail

cd "$(dirname "$0")"
export AWS_PROFILE="${AWS_PROFILE:-mysign}"
NAME="${WAF_NAME:-mysign-edge}"
# raw-in-base64-out: SearchString is a blob, which CLI v2 would otherwise
# expect base64-encoded.
waf() {
  aws wafv2 "$1" --scope CLOUDFRONT --region us-east-1 --output text \
    --cli-binary-format raw-in-base64-out "${@:2}"
}
VIS="SampledRequestsEnabled=true,CloudWatchMetricsEnabled=true,MetricName=$NAME"

python3 -c 'import json,sys; json.load(open("rules.json"))' || { echo "rules.json is not valid JSON" >&2; exit 1; }

read -r ID ARN < <(waf list-web-acls \
  --query "WebACLs[?Name=='$NAME'] | [0].[Id,ARN]") || true

if [[ -z "${ID:-}" || "$ID" == "None" ]]; then
  ARN=$(waf create-web-acl --name "$NAME" \
    --default-action Allow={} --visibility-config "$VIS" \
    --rules file://rules.json --query Summary.ARN)
  echo "created $NAME" >&2
else
  # Re-running converges the live ACL on rules.json.
  LOCK=$(waf get-web-acl --name "$NAME" --id "$ID" --query LockToken)
  waf update-web-acl --name "$NAME" --id "$ID" --lock-token "$LOCK" \
    --default-action Allow={} --visibility-config "$VIS" \
    --rules file://rules.json >/dev/null
  echo "updated $NAME" >&2
fi

echo "$ARN"
