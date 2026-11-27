#!/usr/bin/env bash
# One-time SMS setup for sign-in codes (AWS End User Messaging SMS, us-east-2).
# Run by a person, not by CI: it BUYS a toll-free number (~$2/month, asks
# first) and the carrier registration it prints takes ~2–4 weeks. See docs/sms.md.
#
#   deploy/sms/setup-sms.sh                 # delivery failures -> CloudWatch Logs /mysign/sms
#   deploy/sms/setup-sms.sh <sns-topic-arn> # ...or -> that SNS topic (e.g. the stack's alarm topic)
#
# Safe to re-run: every step looks before it creates. Nothing here turns text
# sign-in on — the app refuses phone sign-in until sms_origination_identity is
# in the secret, and the stack grants send rights only with -c smsEnabled=true.
#
# Why us-east-2: the org SCP denies sms-voice in us-east-1, and the app runs
# in us-east-2 anyway.
set -euo pipefail

PROFILE="${AWS_PROFILE_NAME:-mysign}"
REGION="us-east-2"
CS="mysign-sms"
LOGS="/mysign/sms"
ROLE="mysign-sms-events"
SNS_ARN="${1:-}"
aws_() { aws --profile "$PROFILE" --region "$REGION" --output text "$@"; }
v2() { aws_ pinpoint-sms-voice-v2 "$@"; }

ACCOUNT=$(aws_ sts get-caller-identity --query Account)
echo "→ account $ACCOUNT, region $REGION"
[ "$ACCOUNT" = "889002527942" ] || { echo "not the My Sign account; refusing" >&2; exit 1; }

# ---- 1. configuration set ---------------------------------------------------
# The app names it on every send (MYSIGN_SMS_CONFIG_SET). That is what ties a
# send to the event destination and the protect configuration below; V2 has no
# number-to-config-set association.
if v2 describe-configuration-sets --configuration-set-names "$CS" >/dev/null 2>&1; then
  echo "→ configuration set $CS exists"
else
  v2 create-configuration-set --configuration-set-name "$CS" --tags Key=app,Value=mysign >/dev/null
  echo "→ created configuration set $CS"
fi
v2 set-default-message-type --configuration-set-name "$CS" --message-type TRANSACTIONAL >/dev/null

# ---- 2. event destination: failures only -------------------------------------
# Delivery events carry the destination number, so only failures are kept —
# enough to notice carrier filtering or a blocked country, nothing more.
FAILS="TEXT_INVALID TEXT_INVALID_MESSAGE TEXT_UNREACHABLE TEXT_CARRIER_UNREACHABLE TEXT_BLOCKED TEXT_CARRIER_BLOCKED TEXT_SPAM TEXT_UNKNOWN TEXT_TTL_EXPIRED TEXT_PROTECT_BLOCKED"
HAVE=$(v2 describe-configuration-sets --configuration-set-names "$CS" \
  --query "ConfigurationSets[0].EventDestinations[?EventDestinationName=='failures'].EventDestinationName")
if [ -n "$HAVE" ]; then
  echo "→ event destination 'failures' exists"
elif [ -n "$SNS_ARN" ]; then
  # The topic's policy must let sms-voice.amazonaws.com publish to it.
  # shellcheck disable=SC2086
  v2 create-event-destination --configuration-set-name "$CS" --event-destination-name failures \
    --matching-event-types $FAILS --sns-destination "TopicArn=$SNS_ARN" >/dev/null
  echo "→ failures → SNS $SNS_ARN"
else
  if ! aws_ logs describe-log-groups --log-group-name-prefix "$LOGS" \
       --query "logGroups[?logGroupName=='$LOGS'].logGroupName" | grep -q .; then
    aws_ logs create-log-group --log-group-name "$LOGS"
  fi
  aws_ logs put-retention-policy --log-group-name "$LOGS" --retention-in-days 30
  if ! aws_ iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
    aws_ iam create-role --role-name "$ROLE" >/dev/null --assume-role-policy-document "{
      \"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",
      \"Principal\":{\"Service\":\"sms-voice.amazonaws.com\"},\"Action\":\"sts:AssumeRole\",
      \"Condition\":{\"StringEquals\":{\"aws:SourceAccount\":\"$ACCOUNT\"}}}]}"
    sleep 10  # a brand-new role isn't assumable for a few seconds
  fi
  aws_ iam put-role-policy --role-name "$ROLE" --policy-name write-logs --policy-document "{
    \"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",
    \"Action\":[\"logs:CreateLogStream\",\"logs:PutLogEvents\",\"logs:DescribeLogStreams\"],
    \"Resource\":\"arn:aws:logs:$REGION:$ACCOUNT:log-group:$LOGS:*\"}]}"
  # shellcheck disable=SC2086
  v2 create-event-destination --configuration-set-name "$CS" --event-destination-name failures \
    --matching-event-types $FAILS \
    --cloud-watch-logs-destination "IamRoleArn=arn:aws:iam::$ACCOUNT:role/$ROLE,LogGroupArn=arn:aws:logs:$REGION:$ACCOUNT:log-group:$LOGS" >/dev/null
  echo "→ failures → CloudWatch Logs $LOGS (30 days)"
fi

# ---- 3. protect configuration: US only ---------------------------------------
# The app's own check accepts any NANP number (Canada and the Caribbean share
# +1), and premium-rate destinations are where SMS pumping makes its money.
# This is the hard stop at AWS: every country except the US is blocked.
PC=""
for id in $(v2 describe-protect-configurations --query "ProtectConfigurations[].ProtectConfigurationId"); do
  tag=$(v2 list-tags-for-resource --query "Tags[?Key=='app'].Value" \
    --resource-arn "arn:aws:sms-voice:$REGION:$ACCOUNT:protect-configuration/$id")
  if [ "$tag" = "mysign" ]; then PC="$id"; break; fi
done
if [ -z "$PC" ]; then
  PC=$(v2 create-protect-configuration --deletion-protection-enabled \
    --tags Key=app,Value=mysign --query ProtectConfigurationId)
  echo "→ created protect configuration $PC"
else
  echo "→ protect configuration $PC exists"
fi
UPDATES=$(v2 get-protect-configuration-country-rule-set --protect-configuration-id "$PC" \
  --number-capability SMS --output json | python3 -c '
import json, sys
rules = json.load(sys.stdin)["CountryRuleSet"]
print(json.dumps({c: {"ProtectStatus": "ALLOW" if c == "US" else "BLOCK"} for c in rules}))')
v2 update-protect-configuration-country-rule-set --protect-configuration-id "$PC" \
  --number-capability SMS --country-rule-set-updates "$UPDATES" >/dev/null
ATTACHED=$(v2 describe-configuration-sets --configuration-set-names "$CS" \
  --query "ConfigurationSets[0].ProtectConfigurationId")
if [ "$ATTACHED" != "$PC" ]; then
  v2 associate-protect-configuration --protect-configuration-id "$PC" --configuration-set-name "$CS" >/dev/null
fi
echo "→ $PC: SMS to the US only, attached to $CS"

# ---- 4. toll-free number -----------------------------------------------------
# Reuse ours if a previous run bought one; buying twice costs twice.
NUM_ID=$(v2 describe-phone-numbers \
  --filters Name=iso-country-code,Values=US Name=number-type,Values=TOLL_FREE \
  --query "PhoneNumbers[0].PhoneNumberId")
if [ -z "$NUM_ID" ] || [ "$NUM_ID" = "None" ]; then
  read -r -p "Buy a US toll-free number (~\$2/month)? [y/N] " ok
  [ "$ok" = "y" ] || { echo "stopped before buying"; exit 0; }
  NUM_ID=$(v2 request-phone-number --iso-country-code US --message-type TRANSACTIONAL \
    --number-capabilities SMS --number-type TOLL_FREE --opt-out-list-name Default \
    --deletion-protection-enabled --tags Key=app,Value=mysign --query PhoneNumberId)
  echo "→ requested toll-free number $NUM_ID"
else
  echo "→ toll-free number $NUM_ID exists"
fi
# AWS answers STOP/HELP and keeps the Default list; a send to an opted-out
# number fails, and the app tells the person to use email.
v2 update-phone-number --phone-number-id "$NUM_ID" --opt-out-list-name Default \
  --no-self-managed-opt-outs-enabled >/dev/null
read -r NUM_ARN NUM NUM_STATUS < <(v2 describe-phone-numbers --phone-number-ids "$NUM_ID" \
  --query "PhoneNumbers[0].[PhoneNumberArn,PhoneNumber,Status]")
SECRET=$(aws_ cloudformation describe-stacks --stack-name mysign \
  --query "Stacks[0].Outputs[?OutputKey=='SecretArn'].OutputValue" 2>/dev/null || echo "<stack output SecretArn>")

cat <<EOF

============================================================================
Number:  $NUM  ($NUM_STATUS)
ARN:     $NUM_ARN

Origination identity for the app secret ($SECRET):
  "sms_origination_identity": "$NUM_ARN"
Add it ONLY once the registration below is COMPLETE. Until then carriers drop
the texts (unverified US toll-free numbers can't send) and sign-in by text
would just look broken.

---- 5. toll-free verification (by hand; carriers take ~2–4 weeks) ----------
Easiest in the console: AWS End User Messaging SMS → Registrations → Create →
"US toll-free number registration", then associate $NUM_ID. Or by CLI:

  REG=\$(aws --profile $PROFILE --region $REGION pinpoint-sms-voice-v2 create-registration \\
        --registration-type US_TOLL_FREE_REGISTRATION --query RegistrationId --output text)
  f() { aws --profile $PROFILE --region $REGION pinpoint-sms-voice-v2 put-registration-field-value \\
        --registration-id "\$REG" --field-path "\$1" "\${@:2}"; }

  f companyInfo.companyName        --text-value "<legal company name>"
  f companyInfo.businessType       --select-choices PRIVATE_PROFIT   # or whichever applies; tax id fields may then be required
  f companyInfo.website            --text-value https://mysignapp.com
  f companyInfo.address1           --text-value "<street>"
  f companyInfo.city               --text-value "<city>"
  f companyInfo.state              --text-value "<state>"
  f companyInfo.zipCode            --text-value "<zip>"
  f companyInfo.isoCountryCode     --text-value US
  f contactInfo.firstName          --text-value "<first>"
  f contactInfo.lastName           --text-value "<last>"
  f contactInfo.supportEmail       --text-value support@mysignapp.com
  f contactInfo.supportPhoneNumber --text-value "+1<number>"
  f messagingUseCase.monthlyMessageVolume  --select-choices 1,000
  f messagingUseCase.useCaseCategory       --select-choices ONE_TIME_PASSCODES
  f messagingUseCase.useCaseDetails        --text-value "One-time sign-in codes for the My Sign app. A text is sent only when the user enters their own phone number on the sign-in screen and asks for a code. No marketing, ever."
  f messagingUseCase.privacyPolicyUrl      --text-value https://mysignapp.com/privacy
  f messagingUseCase.termsAndConditionsUrl --text-value https://mysignapp.com/terms
  f messagingUseCase.optInType             --select-choices DIGITAL_FORM
  f messagingUseCase.optInDescription      --text-value "The user enters their own phone number on the sign-in screen and requests a code. Each text is the direct response to that request."
  # Required: a screenshot of the sign-in screen with a phone number entered.
  ATT=\$(aws --profile $PROFILE --region $REGION pinpoint-sms-voice-v2 create-registration-attachment \\
        --attachment-body fileb://signin-screenshot.png --query RegistrationAttachmentId --output text)
  f messagingUseCase.optInImage --registration-attachment-id "\$ATT"
  f messageSamples.messageSample1 --text-value "My Sign: your code is 123456. It works for 15 minutes. Reply STOP to opt out.

@mysignapp.com #123456"

  aws --profile $PROFILE --region $REGION pinpoint-sms-voice-v2 create-registration-association \\
      --registration-id "\$REG" --resource-id $NUM_ID
  aws --profile $PROFILE --region $REGION pinpoint-sms-voice-v2 submit-registration-version \\
      --registration-id "\$REG"

Watch it with describe-registrations: SUBMITTED → REVIEWING → COMPLETE.
If it comes back REQUIRES_UPDATES, fix the named fields and submit again.

---- 6. leave the sandbox and raise the spend limit (support case) ---------
The account is in the SMS SANDBOX (texts only to verified numbers) with a
\$1/month text spend limit. One case covers both:
  Support → Create case → Service limit increase → "AWS End User Messaging SMS",
  region us-east-2: move to production + monthly text spend limit (e.g. \$50).
Reuse the use-case text and sample message above. Once granted:
  aws --profile $PROFILE --region $REGION pinpoint-sms-voice-v2 \\
      set-text-message-spend-limit-override --monthly-limit 50
The app's own ceiling is MYSIGN_SMS_DAILY_CAP (default 500/day). Keep the
monthly AWS limit as the harder backstop.

---- 7. go live (after registration is COMPLETE) ---------------------------
  1. secret: add "sms_origination_identity": "$NUM_ARN"
  2. deploy with -c smsEnabled=true  (send rights, MYSIGN_SMS_CONFIG_SET, cap alarm)
  3. work through the go-live checklist in docs/sms.md
============================================================================
EOF
