"""
The role GitHub Actions deploys with (.github/workflows/deploy.yml).

Its own stack and its own CDK app (cicd.py), deployed once by hand: the app
stack must not be able to edit the credentials that deploy it, and a plain
`cdk deploy` of the app should still mean exactly one stack.

The role can do one thing — assume the CDK bootstrap roles — so everything a
deploy can touch is decided by `cdk bootstrap`, not here. Only code on main or
a job in the GitHub environment `production` gets a token; pull requests and
other branches get none. But the bootstrap roles are shared by both stacks, so
a workflow on main *could* deploy prod: the reviewer gate on `production` is
policy, not IAM. Real separation needs a second account (or bootstrap
qualifier) for staging.
"""
import aws_cdk as cdk
from aws_cdk import Duration, Stack
from aws_cdk import aws_iam as iam
from constructs import Construct

ISSUER = "token.actions.githubusercontent.com"


class CicdStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kw):
        super().__init__(scope, cid, **kw)
        ctx = self.node.try_get_context
        repo = ctx("githubRepo")                 # MySignAstrology/mysign
        ids = ctx("githubRepoIds")               # <ownerId>/<repoId>

        # One per account. If it already exists, CloudFormation cannot adopt
        # it: pass its ARN instead (`-c githubOidcProviderArn=`).
        arn = ctx("githubOidcProviderArn")
        if not arn:
            arn = iam.OidcProviderNative(
                self, "GitHubOidc", url=f"https://{ISSUER}",
                client_ids=["sts.amazonaws.com"]).oidc_provider_arn

        # GitHub may mint `sub` with immutable ids (repo:Owner@1/repo@2:...)
        # instead of names; a rename then cannot hand the role to a new repo.
        # Accept both until we know which this org gets.
        owner, name = repo.split("/")
        owner_id, repo_id = str(ids).split("/")
        subjects = [f"repo:{r}:{s}"
                    for r in (repo, f"{owner}@{owner_id}/{name}@{repo_id}")
                    for s in ("ref:refs/heads/main", "environment:production")]

        role = iam.Role(
            self, "DeployRole",
            role_name="mysign-github-deploy",
            description=f"GitHub Actions deploys for {repo} (main -> staging, environment production -> prod)",
            max_session_duration=Duration.hours(1),
            assumed_by=iam.WebIdentityPrincipal(arn, conditions={
                "StringEquals": {f"{ISSUER}:aud": "sts.amazonaws.com"},
                "StringLike": {f"{ISSUER}:sub": subjects},
            }))
        role.add_to_policy(iam.PolicyStatement(
            actions=["sts:AssumeRole"],
            resources=[f"arn:aws:iam::{self.account}:role/cdk-*-{kind}-role-{self.account}-{self.region}"
                       for kind in ("deploy", "file-publishing", "lookup")]))

        cdk.CfnOutput(self, "DeployRoleArn", value=role.role_arn,
                      description="GitHub repo variable AWS_DEPLOY_ROLE_ARN")
