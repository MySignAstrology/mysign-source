#!/usr/bin/env python3
"""
CDK entrypoint.

The target account is REQUIRED and must be stated explicitly. Without it, CDK
falls back to whatever credentials happen to be in the shell — which is how a
side project ends up deployed into an employer's account. Naming the account
also makes CDK refuse outright if the ambient credentials belong to a different
one, so a wrong-account deploy fails before it creates anything.

  cdk deploy -c targetAccount=111122223333 -c targetRegion=us-east-1

or set MYSIGN_ACCOUNT / MYSIGN_REGION. `-c stage=staging` builds the staging
stack instead of prod; see deploy/stages.py and docs/environments.md.
"""
import os
import sys

import aws_cdk as cdk
from mysign_stack import MySignStack
from stages import Stage

app = cdk.App()

account = app.node.try_get_context("targetAccount") or os.environ.get("MYSIGN_ACCOUNT")
region = (app.node.try_get_context("targetRegion")
          or os.environ.get("MYSIGN_REGION") or "us-east-2")   # Golden Path primary region

if not account:
    sys.exit(
        "\nRefusing to synthesize without an explicit target account.\n\n"
        "  cdk deploy -c targetAccount=<account-id> -c targetRegion=<region>\n"
        "  (or export MYSIGN_ACCOUNT / MYSIGN_REGION)\n\n"
        "This is deliberate. Defaulting to ambient credentials is how personal\n"
        "projects land in a work account. Check `aws sts get-caller-identity`\n"
        "before you fill this in.\n")

stage = Stage(app.node)
MySignStack(
    app,
    stage.get("stackName", "mysign"),
    stage=stage,
    env=cdk.Environment(account=account, region=region),
    # The table and secret are RETAIN, but a deleted stack still takes the
    # URL, alarms and schedule with it. Turn this off by hand to tear down.
    termination_protection=stage.flag("terminationProtection", True),
)

# --- analytics (deploy/analytics_stack.py) ---------------------------------
# Its own stack: mysign-analytics, or mysign-analytics-staging with -c stage=staging.
from analytics_stack import MySignAnalyticsStack  # noqa: E402

MySignAnalyticsStack(app, env=cdk.Environment(account=account, region=region))
# --- end analytics ----------------------------------------------------------

app.synth()
