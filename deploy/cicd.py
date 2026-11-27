#!/usr/bin/env python3
"""
CDK app for the GitHub Actions deploy role. One-time, by hand, from deploy/:

  cdk deploy --app "python3 cicd.py" -c targetAccount=889002527942

See docs/environments.md. Same explicit-account rule as app.py.
"""
import os
import sys

import aws_cdk as cdk
from cicd_stack import CicdStack

app = cdk.App()
account = app.node.try_get_context("targetAccount") or os.environ.get("MYSIGN_ACCOUNT")
region = (app.node.try_get_context("targetRegion")
          or os.environ.get("MYSIGN_REGION") or "us-east-2")
if not account:
    sys.exit("\nRefusing to synthesize without an explicit target account "
             "(-c targetAccount=<id> or MYSIGN_ACCOUNT).\n")

CicdStack(app, "mysign-cicd", env=cdk.Environment(account=account, region=region),
          termination_protection=True)
app.synth()
