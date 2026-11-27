#!/usr/bin/env bash
# Rotate CloudFront's x-origin-auth token with no window of 403s.
#
#   deploy/rotate-origin-token.sh begin    # new token current, old kept as previous;
#                                          # prints the secret's new VersionId (not secret)
#   cdk deploy mysign -c originTokenVersion=<id>
#                                          # CloudFront sends the new token; fresh app
#                                          # containers accept both (app/edge.py)
#   cdk deploy mysign-analytics -c originTokenVersion=<id>
#                                          # the collector takes the new one (a few
#                                          # minutes of anonymous events may 403)
#   deploy/rotate-origin-token.sh finish   # drop the old token; prints a new VersionId
#   cdk deploy mysign -c originTokenVersion=<finish id>
#                                          # restarts the app so the old token is refused
#
# A dynamic reference is only re-read when its text changes, hence the version.
#
# Tokens are generated and moved inside Python and never printed.
set -euo pipefail
PHASE=${1:?usage: rotate-origin-token.sh begin|finish}
export AWS_PROFILE="${AWS_PROFILE:-mysign}" AWS_REGION="${AWS_REGION:-us-east-2}" AWS_PAGER=
# boto3 can't use an `aws login` session without botocore[crt]; plain
# temporary credentials work everywhere.
eval "$(aws configure export-credentials --profile "$AWS_PROFILE" --format env)"
unset AWS_PROFILE
STACK="${STACK:-mysign}"
SECRET=$(aws cloudformation describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='SecretArn'].OutputValue" --output text)
[[ -n "$SECRET" && "$SECRET" != "None" ]] || { echo "no SecretArn output on $STACK" >&2; exit 1; }
PHASE="$PHASE" SECRET="$SECRET" python3 -c '
import json, os, secrets, boto3
sm = boto3.client("secretsmanager"); arn = os.environ["SECRET"]; phase = os.environ["PHASE"]
data = json.loads(sm.get_secret_value(SecretId=arn)["SecretString"])
if phase == "begin":
    if data.get("origin_token_previous"):
        raise SystemExit("a rotation is already in progress: run finish first")
    if not data.get("origin_token"):
        raise SystemExit("no origin_token to rotate")
    data["origin_token_previous"] = data["origin_token"]
    data["origin_token"] = secrets.token_hex(32)
elif phase == "finish":
    if not data.pop("origin_token_previous", None):
        raise SystemExit("no rotation in progress")
else:
    raise SystemExit("phase must be begin or finish")
vid = sm.put_secret_value(SecretId=arn, SecretString=json.dumps(data))["VersionId"]
print(phase + ": done. originTokenVersion=" + vid)'
