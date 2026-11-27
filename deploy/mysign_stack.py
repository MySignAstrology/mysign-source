"""
One Lambda, one table, one secret, one URL.

Deliberately not a container: pyswisseph publishes manylinux wheels, so a zip
built with pip's --platform flag runs on Lambda without Docker anywhere in the
loop. That keeps `cdk deploy` runnable from a laptop with nothing installed.

Streaming matters here — the chat is server-sent events. A Function URL in
RESPONSE_STREAM mode plus the Lambda Web Adapter gives us that with an unmodified
FastAPI app. Buffered mode would make every reply land as one silent lump.
"""
import os
import re
import sys

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    SecretValue,
    Stack,
)
from aws_cdk import (
    aws_applicationautoscaling as appscaling,
)
from aws_cdk import (
    aws_backup as backup,
)
from aws_cdk import (
    aws_certificatemanager as acm,
)
from aws_cdk import (
    aws_cloudfront as cf,
)
from aws_cdk import (
    aws_cloudfront_origins as origins,
)
from aws_cdk import (
    aws_cloudwatch as cw,
)
from aws_cdk import (
    aws_cloudwatch_actions as cw_actions,
)
from aws_cdk import (
    aws_dynamodb as ddb,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_events_targets as targets,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_location as location,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_route53 as r53,
)
from aws_cdk import (
    aws_route53_targets as r53_targets,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_s3_deployment as s3deploy,
)
from aws_cdk import (
    aws_secretsmanager as sm,
)
from aws_cdk import (
    aws_ses as ses,
)
from aws_cdk import (
    aws_ses_actions as ses_actions,
)
from aws_cdk import (
    aws_sns as sns,
)
from aws_cdk import (
    aws_sqs as sqs,
)
from constructs import Construct
from stages import Stage

LWA_ACCOUNT = "753240598075"          # AWS Lambda Web Adapter, published by AWS
HERE = os.path.dirname(__file__)

# A CLOUDFRONT-scope web ACL. It can only live in us-east-1 and its ARN says
# `global`; a REGIONAL one (the other kind wafv2 makes) is refused by
# CloudFront at deploy time, twenty minutes in, so it is refused here instead.
CLOUDFRONT_ACL = re.compile(r"arn:aws:wafv2:us-east-1:\d{12}:global/webacl/[\w-]+/[\w-]+")


def refuse_cli_secrets(node, stage):
    """
    The origin token is never a context value. `-c originToken=<value>` once
    put the live token into the synthesized template in plain text, and the
    synth output was committed (audit H2). The stack reads it from the secret
    as a {{resolve:secretsmanager}} reference, which is all a template ever
    holds now; there is no flag left to pass it by.
    """
    for key in ("originToken", stage + "OriginToken"):
        if node.try_get_context(key) not in (None, "", "null"):
            sys.exit(f"\nRefusing to synthesize: `{key}` was passed as context. The origin "
                     "token lives only in the stage's secret (`origin_token`); the stack "
                     "resolves it from there at deploy time. Drop the flag.\n")


def web_acl(st):
    """
    The WAF for this stage's CloudFront distribution, or a loud refusal.

    The ACL itself is made by deploy/waf/create-waf.sh, not here: CLOUDFRONT
    scope lives in us-east-1, where an org SCP denies CloudFormation. So the
    stack can only attach it, by ARN, from cdk.json. Every rate limit the app
    leaves to the edge (sign-in sends, the general per-IP ceiling, IP
    reputation) exists only while it is attached, and CloudFormation reads an
    unset WebACLId as "detach": a prod deploy without it silently removes a
    firewall someone attached by hand (audit H3). So prod refuses to synth
    without one; `-c allowNoWaf=true` is the explicit, logged way to say so.
    Other stages warn, because a staging stack without the WAF is a choice.
    """
    acl = st.get("webAclArn")
    if acl:
        if not CLOUDFRONT_ACL.fullmatch(str(acl)):
            sys.exit(f"\nwebAclArn {acl!r} is not a CLOUDFRONT-scope web ACL "
                     "(arn:aws:wafv2:us-east-1:<account>:global/webacl/<name>/<id>).\n")
        return str(acl)
    if st.prod and not st.flag("allowNoWaf"):
        sys.exit("\nRefusing to synthesize prod without a WAF. Set webAclArn in "
                 "deploy/cdk.json (deploy/waf/create-waf.sh prints it), or pass "
                 "-c allowNoWaf=true if detaching it is really the intent.\n")
    print(f"\n*** WARNING: stage {st.name!r} deploys CloudFront with NO WAF attached. "
          f"Set {'webAclArn' if st.prod else st.name + 'WebAclArn'} to attach "
          "deploy/waf/create-waf.sh's ACL. ***\n", file=sys.stderr)
    return None


class MySignStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, stage: Stage | None = None,
                 build_dir: str | None = None, **kw):
        super().__init__(scope, cid, **kw)
        st = self.stage = stage or Stage(self.node)
        refuse_cli_secrets(self.node, st.name)
        ctx = st.get
        build = build_dir or os.path.join(HERE, "build")
        reserved = st.num("reservedConcurrency")
        # Every physical name hangs off this; prod's is "mysign", as it always was.
        prefix = self.prefix = ctx("namePrefix", "mysign")
        # Prod's alarms read un-dimensioned metrics in "MySign". A second stage
        # writing there would page prod for staging's errors.
        self.ns = "MySign" if st.prod else f"MySign/{st.name}"
        # Staging exists to be torn down and rebuilt; prod's data never is.
        keep = st.flag("retainData", True)
        removal = RemovalPolicy.RETAIN if keep else RemovalPolicy.DESTROY

        table = ddb.Table(
            self, "Table",
            partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            # Sign-in codes carry an epoch `ttl`; DynamoDB deletes them itself,
            # so an expired code is gone, not merely refused.
            time_to_live_attribute="ttl",
            # RETAIN covers a stack delete; this covers a console click.
            deletion_protection=keep,
            removal_policy=removal,     # never destroy someone's history
        )

        # PITR is 35 days of continuous restore but lives and dies with the
        # table's own settings. A Backup vault is a second, separate copy.
        if st.flag("backup", True):
            plan = backup.BackupPlan.daily35_day_retention(self, "Backup")
            plan.add_selection("Table", resources=[backup.BackupResource.from_dynamo_db_table(table)])

        # Created empty. Populate with:
        #   anthropic_api_key  — your key
        #   session_secret     — random; rotating it signs everyone out
        #   vapid_private      — PEM; rotating it kills every push subscription
        #   vapid_contact      — mailto: address for the VAPID claim
        #   pagerduty_routing_key — Events API v2 integration key
        #   from_email / support_email — only without a domain; with one, CDK sets them
        #   ops_token          — enables /api/ops; sent as the x-ops-token header
        #   origin_token       — CloudFront's x-origin-auth; see app/edge.py. Never
        #                        a -c flag: refuse_cli_secrets()
        secret = sm.Secret(
            self, "Secret",
            description="My Sign — Anthropic API key and the session signing secret",
            removal_policy=removal,
        )

        lwa = lambda_.LayerVersion.from_layer_version_arn(
            self, "LWA",
            f"arn:aws:lambda:{self.region}:{LWA_ACCOUNT}:layer:LambdaAdapterLayerX86:"
            f"{self.node.try_get_context('lwaLayerVersion')}")

        # Explicit groups, so retention is ours rather than "forever". Named
        # outside /aws/lambda/ because the live functions already own their
        # auto-created groups there, and claiming the same name would fail.
        app_logs = logs.LogGroup(
            self, "AppLogs", log_group_name=f"/{prefix}/app",
            retention=logs.RetentionDays.THREE_MONTHS, removal_policy=removal)
        cron_logs = logs.LogGroup(
            self, "CronLogs", log_group_name=f"/{prefix}/cron",
            retention=logs.RetentionDays.THREE_MONTHS, removal_policy=removal)

        zip_path = os.path.join(build, "mysign.zip")
        if not os.path.exists(zip_path):
            raise FileNotFoundError(f"{zip_path} missing — run deploy/build.sh first")

        fn = lambda_.Function(
            self, "Fn",
            runtime=lambda_.Runtime.PYTHON_3_11,     # pyswisseph ships no cp312 wheel
            architecture=lambda_.Architecture.X86_64,
            handler="run.sh",
            code=lambda_.Code.from_asset(zip_path),
            layers=[lwa],
            memory_size=1024,
            # X-Ray. The request id in every log line already ties a failure to
            # its trace; this makes the trace exist.
            tracing=lambda_.Tracing.ACTIVE,
            timeout=Duration.seconds(120),           # a long model turn plus rollover
            log_group=app_logs,
            # Off by default: with the account's concurrency limit at 10,
            # Lambda refuses any reservation that leaves fewer than 10
            # unreserved. Set it once the quota increase lands.
            reserved_concurrent_executions=reserved,
            environment={
                "AWS_LAMBDA_EXEC_WRAPPER": "/opt/bootstrap",
                "AWS_LWA_INVOKE_MODE": "response_stream",
                "PORT": "8000",
                "MYSIGN_TABLE": table.table_name,
                "MYSIGN_SECRET_ARN": secret.secret_arn,
            },
        )
        table.grant_read_write_data(fn)
        secret.grant_read(fn)

        # The wider birthplace search (app/places.py), for towns under 500
        # people that the bundled list can't hold. HERE returns a timezone with
        # each place; Storage, because the coordinates become birth data.
        place_index = location.CfnPlaceIndex(
            self, "Places",
            index_name=f"{prefix}-places",
            data_source="Here",
            pricing_plan="RequestBasedUsage",
            data_source_configuration=location.CfnPlaceIndex.DataSourceConfigurationProperty(
                intended_use="Storage"),
            description="My Sign: birthplaces too small for the bundled city list",
        )
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["geo:SearchPlaceIndexForText"],
            resources=[place_index.attr_arn]))
        fn.add_environment("MYSIGN_PLACE_INDEX", place_index.index_name)

        domain = ctx("domainName")
        cert_arn = ctx("certificateArn")
        # With the domain's Route 53 zone in this account, the stack writes its
        # own DNS (DKIM, MAIL FROM, DMARC, MX, the ALIAS) instead of printing
        # records to paste. Route 53 is global, so the us-east-1 SCP is no bar.
        # A subdomain stage (staging.) writes into the apex's zone.
        zone_id = ctx("hostedZoneId")
        zone_name = ctx("zoneName") or domain
        zone = (r53.PublicHostedZone.from_public_hosted_zone_attributes(
                    self, "Zone", hosted_zone_id=zone_id, zone_name=zone_name)
                if domain and zone_id else None)

        # Where operational failures surface. Subscribe email or chat here; the
        # PagerDuty page for red-tier safety events is separate and fires from
        # app/alerts.py, because that is not a system failure.
        alarms = sns.Topic(self, "Alarms", display_name="My Sign alarms" if st.prod
                           else f"My Sign {st.name} alarms")

        # A morning push that fails silently looks exactly like a quiet day —
        # which is a legitimate output of this product. That ambiguity is why
        # the scheduler needs a dead-letter queue rather than just logs.
        cron_dlq = sqs.Queue(self, "CronDLQ", retention_period=Duration.days(14))

        # The scheduler is a second function off the same asset, with a plain
        # handler and no web adapter. EventBridge invokes Lambda directly; routing
        # a cron event through an HTTP adapter would mean inventing a request.
        cron = lambda_.Function(
            self, "Cron",
            runtime=lambda_.Runtime.PYTHON_3_11,
            architecture=lambda_.Architecture.X86_64,
            handler="app.cron.handler",
            code=lambda_.Code.from_asset(zip_path),
            memory_size=1024,
            timeout=Duration.minutes(5),
            retry_attempts=1,
            dead_letter_queue=cron_dlq,
            log_group=cron_logs,
            # One at a time: two overlapping runs would push the same morning
            # twice. Only reservable once the account limit allows it.
            reserved_concurrent_executions=1 if reserved else None,
            environment={
                "MYSIGN_TABLE": table.table_name,
                "MYSIGN_SECRET_ARN": secret.secret_arn,
            },
        )
        table.grant_read_write_data(cron)
        secret.grant_read(cron)

        # ---- ops health (app/health.py) ---------------------------------------
        # /api/ops/health reads CloudWatch for the System health tab. The app's
        # own name comes from AWS_LAMBDA_FUNCTION_NAME; these are the rest.
        # Read-only: metric data, and Logs Insights over the app's log group for
        # cold starts. GetMetricData and GetQueryResults take no resource ARN.
        fn.add_environment("MYSIGN_CRON_FN_NAME", cron.function_name)
        fn.add_environment("MYSIGN_METRIC_NS", self.ns)
        fn.add_environment("MYSIGN_APP_LOG_GROUP", app_logs.log_group_name)
        # From context, not from `dist`: the distribution already depends on this
        # function's URL, and CloudFormation won't have the circle.
        if st.get("siteDistributionId"):
            fn.add_environment("MYSIGN_SITE_DISTRIBUTION", str(st.get("siteDistributionId")))
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["cloudwatch:GetMetricData", "logs:GetQueryResults"], resources=["*"]))
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["logs:StartQuery"], resources=[app_logs.log_group_arn]))
        # ---- end ops health -----------------------------------------------------

        # ------------------------------------------------ analytics events
        # The app's events (and the cron's push_sent) go to the lake through
        # the Firehose stream the analytics stack owns (deploy/analytics_stack.py
        # names it mysign-events / mysign-events-<stage>). By name, not by
        # export: either stack can be updated or deleted without the other.
        # Deploy the analytics stack first; until the stream exists each
        # forward fails, is logged, and the request carries on (app/activity.py).
        # -c analyticsEvents=false leaves both functions without it.
        if st.flag("analyticsEvents", True):
            stream = "mysign-events" if st.prod else f"mysign-events-{st.name}"
            stream_arn = self.format_arn(service="firehose", resource="deliverystream",
                                         resource_name=stream)
            for f in (fn, cron):
                f.add_environment("MYSIGN_EVENTS_STREAM", stream)
                f.add_to_role_policy(iam.PolicyStatement(
                    actions=["firehose:PutRecordBatch"], resources=[stream_arn]))

        # One alarm per DLQ, self-resolving — it clears when the queue drains,
        # rather than one notification per failed item.
        cw.Alarm(
            self, "CronDLQAlarm",
            metric=cron_dlq.metric_approximate_number_of_messages_visible(
                period=Duration.minutes(5), statistic="Maximum"),
            threshold=0, evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description="Morning push failed and landed in the DLQ.",
        ).add_alarm_action(cw_actions.SnsAction(alarms))

        # The scheduler going quiet is as bad as it erroring: nobody gets a
        # morning notification and nothing raises. Alarm on absence too.
        cw.Alarm(
            self, "CronSilentAlarm",
            metric=cron.metric_invocations(period=Duration.hours(3), statistic="Sum"),
            threshold=1, evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.BREACHING,
            alarm_description="Scheduler has not run in three hours.",
        ).add_alarm_action(cw_actions.SnsAction(alarms))

        cw.Alarm(
            self, "ApiErrorAlarm",
            metric=fn.metric_errors(period=Duration.minutes(5), statistic="Sum"),
            threshold=5, evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description="App Lambda is erroring.",
        ).add_alarm_action(cw_actions.SnsAction(alarms))

        # A throttled request is a person staring at a spinner, and nothing
        # in the app's own logs says so — the invocation never started.
        cw.Alarm(
            self, "ApiThrottleAlarm",
            metric=fn.metric_throttles(period=Duration.minutes(5), statistic="Sum"),
            threshold=0, evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description="App Lambda is being throttled (concurrency limit).",
        ).add_alarm_action(cw_actions.SnsAction(alarms))

        # Every 15 minutes. Each user is pushed when their own local clock passes
        # 07:30, so the job has to run often enough to catch every timezone.
        events.Rule(
            self, "MorningRule",
            schedule=events.Schedule.cron(minute="0/15"),
            targets=[targets.LambdaFunction(cron)],
        )

        # Weekly, Monday 09:00 UTC: celebrity couples' status from Wikidata,
        # any missing written reads, and web research for new milestones
        # (engine/couples_watch.py). Weekly is enough
        # for marriages and divorces, and the debounce needs runs a week apart.
        events.Rule(
            self, "CouplesWatchRule",
            schedule=events.Schedule.cron(minute="0", hour="9", week_day="MON"),
            targets=[targets.LambdaFunction(
                cron, event=events.RuleTargetInput.from_object({"job": "couples_watch"}))],
        )

        # Public, because CloudFront has to reach it unsigned. Sign-in is the
        # app's own; with a domain, app/edge.py also refuses anything that did
        # not come through CloudFront. With warm capacity the URL is the alias's,
        # since provisioned concurrency only serves a qualified invoke.
        pc = st.num("provisionedConcurrency", 0)
        target = self._live(fn, pc) if pc else fn
        url = target.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            invoke_mode=lambda_.InvokeMode.RESPONSE_STREAM,
        )

        # ------------------------------------------------ mail
        # Mail needs only the domain, not CloudFront: sign-in works off the bare
        # Function URL as soon as SES verifies the DKIM records.
        if domain:
            self._mail(ctx("mailDomain") or domain, fn, alarms, zone)
        else:
            # Sign-in codes go out over SES. Without this the app fails closed and
            # nobody can log in — the correct failure, but a useless deploy.
            fn.add_to_role_policy(iam.PolicyStatement(
                actions=["ses:SendEmail", "ses:SendRawEmail"], resources=["*"]))
        if st.flag("smsEnabled"):        # sign-in codes by text; see docs/sms.md
            self._sms(fn, alarms)

        # ------------------------------------------------ a real address
        # Items 46, 47, 49. Nothing here is created until a domain AND a
        # certificate are passed in.
        #
        # The certificate must live in us-east-1 regardless of the stack region;
        # that is a CloudFront rule, not ours. An SCP blocks CloudFormation in
        # us-east-1, so it (and any CLOUDFRONT-scope WAF) is made by CLI and
        # passed in by ARN — see docs/launch-checklist.md.
        if domain and cert_arn:
            # The token lives in the secret (the app reads it from there too).
            # Resolving it at deploy time means a plain `cdk deploy` keeps
            # CloudFront and the app agreeing; a missing key fails the deploy
            # rather than silently 403ing every request.
            #
            # A dynamic reference only resolves when its text changes, so after
            # a rotation (deploy/rotate-origin-token.sh) the deploy passes
            # -c originTokenVersion=<the secret's new VersionId>: that pins the
            # reference to the new version, which changes the template, which
            # makes CloudFront re-read it — and restarts the app below.
            tok_ver = st.get("originTokenVersion")
            token = (SecretValue.secrets_manager(secret.secret_arn, json_field="origin_token",
                                                 version_id=str(tok_ver)).unsafe_unwrap()
                     if tok_ver else secret.secret_value_from_json("origin_token").unsafe_unwrap())
            if tok_ver:
                # Running containers loaded the old token at cold start; a new
                # configuration starts fresh ones that read current + previous.
                fn.add_environment("MYSIGN_ORIGIN_TOKEN_VERSION", str(tok_ver))
            acl = web_acl(st)
            names = [domain] + ([f"www.{domain}"] if st.flag("includeWww") else [])
            app_route = {
                # 60s, the most CloudFront allows without a quota ticket.
                # A model turn can sit that long before its first token.
                "origin": origins.FunctionUrlOrigin(
                    url, read_timeout=Duration.seconds(60),
                    custom_headers={"x-origin-auth": token}),
                "viewer_protocol_policy": cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                "allowed_methods": cf.AllowedMethods.ALLOW_ALL,
                # Nothing is cacheable: every response depends on the
                # session cookie, and replies stream.
                "cache_policy": cf.CachePolicy.CACHING_DISABLED,
                "origin_request_policy": cf.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
            }
            # Off in prod until staging has proved it: with the flag off the
            # distribution is exactly the single Lambda behaviour it always was.
            site = self._site(build, keep) if st.flag("staticFromS3") else None
            collect = self._collect_route(token)
            if collect:
                # The app tells the sign-in screen it may count (/api/state).
                fn.add_environment("MYSIGN_COLLECTOR", "1")
            if site:
                default, extra = self._edge_routes(site, app_route, collect)
            else:
                default = cf.BehaviorOptions(**app_route)
                extra = {"/e": collect} if collect else None
            dist = cf.Distribution(
                self, "Edge",
                default_behavior=default,
                additional_behaviors=extra,
                domain_names=names,
                certificate=acm.Certificate.from_certificate_arn(self, "Cert", cert_arn),
                http_version=cf.HttpVersion.HTTP2_AND_3,
                web_acl_id=acl or None,
            )
            if site:
                self._publish(site, build, dist)
            self._admin(fn, app_route, acl, zone)
            CfnOutput(self, "DomainTarget", value=dist.distribution_domain_name,
                      description=f"ALIAS/CNAME {', '.join(names)} -> this")
            if zone:
                alias = r53.RecordTarget.from_alias(r53_targets.CloudFrontTarget(dist))
                for n in names:
                    sub = n[:-len(zone_name)].rstrip(".") or None
                    key = (sub or "Apex").capitalize()
                    r53.ARecord(self, f"Edge{key}A", zone=zone, record_name=sub, target=alias)
                    r53.AaaaRecord(self, f"Edge{key}Aaaa", zone=zone, record_name=sub, target=alias)


        # ------------------------------------------------ what normal costs
        # Item 96. The quota caps abuse; this shows what a normal day looks like
        # so a spike is recognisable. Model spend is inferred from message
        # events, since tokens are billed by the provider, not by AWS.
        messages = logs.MetricFilter(
            self, "MessagesMetric",
            log_group=app_logs,
            metric_namespace=self.ns, metric_name="Messages",
            filter_pattern=logs.FilterPattern.string_value("$.event", "=", "message"),
            metric_value="1",
        )
        refused = logs.MetricFilter(
            self, "QuotaRefusedMetric",
            log_group=app_logs,
            metric_namespace=self.ns, metric_name="QuotaRefused",
            filter_pattern=logs.FilterPattern.string_value("$.event", "=", "quota.refused"),
            metric_value="1",
        )
        red = logs.MetricFilter(
            self, "RedTierMetric",
            log_group=app_logs,
            metric_namespace=self.ns, metric_name="RedTier",
            filter_pattern=logs.FilterPattern.string_value("$.event", "=", "safety.red"),
            metric_value="1",
        )

        # ------------------------------------------------ what must reach a human
        # Without a PagerDuty key a crisis is a log line nobody reads. This is
        # the floor: the alarm topic hears about every red-tier event within a
        # minute, PagerDuty or not.
        cw.Alarm(
            self, "RedTierAlarm",
            metric=red.metric(period=Duration.minutes(1), statistic="Sum"),
            threshold=0, evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description="Red-tier safety event: someone may be in crisis. Open /ops now.",
        ).add_alarm_action(cw_actions.SnsAction(alarms))

        def log_alarm(cid, groups, pattern, threshold, minutes, why):
            for g in groups:
                logs.MetricFilter(
                    self, f"{cid}Metric{g.node.id}", log_group=g,
                    metric_namespace=self.ns, metric_name=cid,
                    filter_pattern=pattern, metric_value="1")
            cw.Alarm(
                self, f"{cid}Alarm",
                metric=cw.Metric(namespace=self.ns, metric_name=cid, statistic="Sum",
                                 period=Duration.minutes(minutes)),
                threshold=threshold, evaluation_periods=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                alarm_description=why,
            ).add_alarm_action(cw_actions.SnsAction(alarms))

        event = logs.FilterPattern.string_value
        # A page that could not be delivered is itself the page.
        log_alarm("AlertUnrouted", [app_logs, cron_logs], event("$.event", "=", "alert.unrouted"),
                  1, 5, "A page had nowhere to go (PagerDuty key missing). Check /ops.")
        # The app catches its own exceptions and returns 500s, so Lambda's
        # Errors metric stays at zero while people see failures. Logs do not.
        log_alarm("AppErrorLines", [app_logs, cron_logs], event("$.level", "=", "error"),
                  5, 5, "Five or more error-level log lines in five minutes.")
        log_alarm("AuthUnavailable", [app_logs], event("$.event", "=", "auth.unavailable"),
                  1, 5, "Sign-in is refusing everyone: mail sender unset or SES rejecting.")
        # A service-wide sign-in ceiling tripped (app/auth.py _service_cap):
        # email codes paused to protect SES's sending reputation, or code
        # checks refused because something is guessing at scale.
        log_alarm("AuthCap", [app_logs], event("$.event", "=", "auth.cap"),
                  1, 5, "A service-wide sign-in cap tripped (email sends or code checks). "
                        "Check auth.cap and auth.limited in /mysign/app.")
        log_alarm("SecretUnreadable", [app_logs, cron_logs],
                  event("$.event", "=", "secret.unreadable"),
                  1, 5, "Secrets Manager read failed: app is in demo mode, sign-in is off.")
        # Crashes on her phone (app/static/index.html reports them to
        # /api/client-error). A few are noise from old browsers; a burst means
        # a release broke something nobody tested.
        log_alarm("ClientErrors", [app_logs], event("$.event", "=", "client.error"),
                  25, 15, "25+ JavaScript errors from people's browsers in 15 minutes.")

        # The Anthropic bill, which AWS budgets can't see: every model call logs
        # its cost (engine/meter.py) and this sums it. Limits in cdk.json.
        for g in (app_logs, cron_logs):
            logs.MetricFilter(
                self, f"ModelSpendMetric{g.node.id}", log_group=g,
                metric_namespace=self.ns, metric_name="ModelSpendUSD",
                filter_pattern=event("$.event", "=", "model.usage"), metric_value="$.usd")
        spend = cw.Metric(namespace=self.ns, metric_name="ModelSpendUSD", statistic="Sum")
        daily = float(ctx("modelSpendDailyUsd") or 5)
        hourly = float(ctx("modelSpendHourlyUsd") or 1.5)
        for cid, period, limit, why in (
                ("ModelSpendDaily", Duration.days(1), daily,
                 f"AI (Anthropic) spend passed ${daily:g} in a day."),
                ("ModelSpendHourly", Duration.hours(1), hourly,
                 f"AI (Anthropic) spend passed ${hourly:g} in an hour: a loop, abuse, or a spike.")):
            cw.Alarm(
                self, f"{cid}Alarm", metric=spend.with_(period=period),
                threshold=limit, evaluation_periods=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                alarm_description=why,
            ).add_alarm_action(cw_actions.SnsAction(alarms))
        # The app's own line (engine/costs.global_over). Past it in a UTC day,
        # every conversation moves to the lighter model and free extras show
        # their authored versions; nothing is refused and crisis replies are
        # never touched. Well above the alarm: the alarm is the warning, this is
        # the floor while someone looks. Both functions charge the same day.
        budget = float(ctx("modelDailyBudgetUsd") or daily * 4)
        for f in (fn, cron):
            f.add_environment("MYSIGN_DAILY_BUDGET_USD", f"{budget:g}")

        # Is the site up, as a visitor sees it? CloudFront's own 5xx metric lives
        # in us-east-1, where the org SCP denies both CloudFormation and SNS, so
        # an alarm there could notify no one. This loads the real pages from
        # here every five minutes instead — which also catches S3, routing and
        # app failures, not only the edge. Raises on any failure; two failed
        # checks in a row page the alarm topic.
        if domain:
            canary = lambda_.Function(
                self, "Uptime",
                description="Loads the live site every 5 minutes; raises if a page fails",
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler="index.handler",
                timeout=Duration.seconds(40),
                memory_size=128,
                environment={"URLS": ",".join(f"https://{domain}{p}"
                                              for p in ("/", "/app", "/api/state"))},
                code=lambda_.Code.from_inline(
                    "import os, urllib.request\n"
                    "def handler(event, context):\n"
                    "    bad = []\n"
                    "    for u in os.environ['URLS'].split(','):\n"
                    "        try:\n"
                    "            req = urllib.request.Request(u, headers={'User-Agent': 'mysign-uptime'})\n"
                    "            urllib.request.urlopen(req, timeout=10).read(256)\n"
                    "        except Exception as e:\n"
                    "            bad.append(f'{u} {type(e).__name__}: {e}')\n"
                    "    if bad:\n"
                    "        raise RuntimeError('; '.join(bad))\n"
                    "    return 'ok'\n"),
            )
            events.Rule(self, "UptimeRule", schedule=events.Schedule.rate(Duration.minutes(5)),
                        targets=[targets.LambdaFunction(canary, retry_attempts=0)])
            cw.Alarm(
                self, "UptimeAlarm",
                metric=canary.metric_errors(period=Duration.minutes(5), statistic="Sum"),
                threshold=0, evaluation_periods=2, datapoints_to_alarm=2,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                alarm_description=f"{domain} is failing for visitors: a page did not load twice in a row.",
            ).add_alarm_action(cw_actions.SnsAction(alarms))

        board = cw.Dashboard(self, "Board", dashboard_name=prefix)
        board.add_widgets(
            cw.GraphWidget(title="Messages / hour (≈ model spend)",
                           left=[messages.metric(period=Duration.hours(1), statistic="Sum")], width=12),
            cw.GraphWidget(title="Quota refusals",
                           left=[refused.metric(period=Duration.hours(1), statistic="Sum")], width=6),
            cw.GraphWidget(title="Red-tier events",
                           left=[red.metric(period=Duration.hours(1), statistic="Sum")], width=6),
        )
        board.add_widgets(
            cw.GraphWidget(title="AI spend (USD / hour)",
                           left=[spend.with_(period=Duration.hours(1))], width=12),
            cw.GraphWidget(title="Browser errors / 15 min",
                           left=[cw.Metric(namespace=self.ns, metric_name="ClientErrors",
                                           statistic="Sum", period=Duration.minutes(15))], width=12),
        )
        board.add_widgets(
            cw.GraphWidget(title="App: invocations / errors",
                           left=[fn.metric_invocations(), fn.metric_errors()], width=8),
            cw.GraphWidget(title="App: duration p50 / p95",
                           left=[fn.metric_duration(statistic="p50"), fn.metric_duration(statistic="p95")], width=8),
            cw.GraphWidget(title="DynamoDB: consumed capacity",
                           left=[table.metric_consumed_read_capacity_units(),
                                 table.metric_consumed_write_capacity_units()], width=8),
        )

        CfnOutput(self, "Url", value=url.url)
        CfnOutput(self, "SecretArn", value=secret.secret_arn)
        CfnOutput(self, "TableName", value=table.table_name)
        CfnOutput(self, "CronFunction", value=cron.function_name)
        CfnOutput(self, "AlarmTopic", value=alarms.topic_arn)
        CfnOutput(self, "CronDeadLetterQueue", value=cron_dlq.queue_url)
        CfnOutput(self, "AppLogGroup", value=app_logs.log_group_name)

    # ------------------------------------------------------------ warm capacity
    def _live(self, fn, n):
        """
        A `live` alias carrying provisioned concurrency: N environments already
        initialised, so a request never waits out the ~5s cold start.

        Billed by the hour whether used or not, so by default it runs only
        across the US day — provisionedFromHourUtc to provisionedToHourUtc —
        and drops to zero overnight. Set either hour to null for always-on.
        Capacity counts against reservedConcurrency, not on top of it.
        """
        st = self.stage
        up, down = st.num("provisionedFromHourUtc"), st.num("provisionedToHourUtc")
        if up is None or down is None:
            return lambda_.Alias(self, "Live", alias_name="live", version=fn.current_version,
                                 provisioned_concurrent_executions=n)
        alias = lambda_.Alias(self, "Live", alias_name="live", version=fn.current_version)
        # The schedule owns the number. Setting it on the alias as well would
        # have every deploy reset it, whatever the hour.
        scaling = alias.add_auto_scaling(min_capacity=0, max_capacity=n)
        scaling.scale_on_schedule("Day", min_capacity=n, max_capacity=n,
                                  schedule=appscaling.Schedule.cron(hour=str(up), minute="0"))
        scaling.scale_on_schedule("Night", min_capacity=0, max_capacity=0,
                                  schedule=appscaling.Schedule.cron(hour=str(down), minute="0"))
        return alias

    # ------------------------------------------------------------ static at the edge
    def _site(self, build, keep):
        """
        The pages, icons and worker, out of S3 instead of a Lambda invoke. A
        cold start used to stand between a visitor and a static HTML file.
        """
        site_dir = os.path.join(build, "site")
        if not os.path.isdir(site_dir):
            raise FileNotFoundError(f"{site_dir} missing — run deploy/build.sh first")
        # Prod keeps the bucket on teardown: everything in it is rebuilt from
        # the repo, but auto-delete means a custom-resource Lambda holding
        # delete rights on the live site, which prod does not need. Staging is
        # meant to be destroyed cleanly.
        return s3.Bucket(
            self, "Site",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN if keep else RemovalPolicy.DESTROY,
            auto_delete_objects=not keep,
        )

    def _collect_route(self, token):
        """
        /e -> the analytics collector's Function URL (deploy/collect), or None.

        The collector lives in the analytics stack. Its domain comes in as
        context — -c collectorDomain=<the CollectorDomain output> for prod,
        -c stagingCollectorDomain=... (or the stages map) for staging — rather
        than an export or an SSM lookup: no CloudFormation dependency between
        the stacks, and a synth that doesn't know it simply leaves /e where it
        was. Deploy order: analytics stack, then this one with the domain.
        """
        domain = self.stage.get("collectorDomain")
        if not domain:
            return None
        domain = str(domain).strip().split("://")[-1].split("/")[0]
        # What the collector reads (app/events.py request_ctx, no_ids) and
        # nothing else: no cookies, no query string, no Host.
        policy = cf.OriginRequestPolicy(
            self, "CollectHeaders",
            comment="Analytics collector: device, country, GPC/DNT; no cookies or query",
            header_behavior=cf.OriginRequestHeaderBehavior.allow_list(
                "CloudFront-Viewer-Country", "CloudFront-Is-Mobile-Viewer",
                "CloudFront-Is-Tablet-Viewer", "CloudFront-Is-Desktop-Viewer",
                "CloudFront-Is-SmartTV-Viewer", "User-Agent", "Referer", "Sec-GPC", "DNT"),
            query_string_behavior=cf.OriginRequestQueryStringBehavior.none(),
            cookie_behavior=cf.OriginRequestCookieBehavior.none(),
        )
        return cf.BehaviorOptions(
            # The collector checks the same x-origin-auth as the app (it reads
            # origin_token from the app's secret; see analytics_stack appSecretArn).
            origin=origins.HttpOrigin(
                domain, protocol_policy=cf.OriginProtocolPolicy.HTTPS_ONLY,
                custom_headers={"x-origin-auth": token}),
            viewer_protocol_policy=cf.ViewerProtocolPolicy.HTTPS_ONLY,
            allowed_methods=cf.AllowedMethods.ALLOW_ALL,
            cache_policy=cf.CachePolicy.CACHING_DISABLED,
            origin_request_policy=policy,
        )

    def _edge_routes(self, site, app_route, collect=None):
        """
        S3 by default; the app's dynamic paths to Lambda. Every path the Lambda
        answers must be listed here, or it falls to S3 and 403s.
        """
        headers = cf.ResponseHeadersPolicy(
            self, "SecurityHeaders",
            # No CSP. The app runs inline script and style and pulls Google
            # Fonts; a policy loose enough to allow that guards little, and a
            # tight one blanks the page.
            security_headers_behavior=cf.ResponseSecurityHeadersBehavior(
                # No includeSubDomains: mail.<domain> and future hosts are not
                # ours to commit to HTTPS for a year from here.
                strict_transport_security=cf.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(365), include_subdomains=False,
                    override=True),
                content_type_options=cf.ResponseHeadersContentTypeOptions(override=True),
                referrer_policy=cf.ResponseHeadersReferrerPolicy(
                    referrer_policy=cf.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                    override=True),
                frame_options=cf.ResponseHeadersFrameOptions(
                    frame_option=cf.HeadersFrameOption.DENY, override=True),
            ),
        )
        s3_origin = origins.S3BucketOrigin.with_origin_access_control(site)
        static = {"origin": s3_origin, "response_headers_policy": headers, "compress": True,
                  "viewer_protocol_policy": cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                  "allowed_methods": cf.AllowedMethods.ALLOW_GET_HEAD}
        # Pages carry `max-age=0, must-revalidate, s-maxage=300` (see _publish);
        # this lets the s-maxage stand and caps it, and each deploy invalidates.
        pages = cf.CachePolicy(
            self, "PageCache",
            comment="HTML: edge holds minutes, browsers always revalidate",
            min_ttl=Duration.seconds(0), default_ttl=Duration.minutes(1),
            max_ttl=Duration.hours(1),
            enable_accept_encoding_gzip=True, enable_accept_encoding_brotli=True,
        )
        router = cf.Function(
            self, "Router",
            comment="/ -> site or app; /app/* -> app shell; /privacy -> privacy.html",
            code=cf.FunctionCode.from_file(file_path=os.path.join(HERE, "edge", "router.js")),
            runtime=cf.FunctionRuntime.JS_2_0,
        )
        default = cf.BehaviorOptions(
            **static, cache_policy=pages,
            function_associations=[cf.FunctionAssociation(
                function=router, event_type=cf.FunctionEventType.VIEWER_REQUEST)])
        app = cf.BehaviorOptions(**app_route, response_headers_policy=headers)
        return default, {
            "/api/*": app,
            "/bt/*": app,
            "/ops*": app,
            # Celebrity couples: rendered by the app so each page has its own
            # title and share card; cached briefly at the edge (Cache-Control).
            "/couples*": app,
            # The analytics collector when its domain is known (_collect_route);
            # until then the app's, as it is with the flag off (a 404). Never
            # S3, never through the router.
            "/e": collect or app,
            # Not content-hashed, so browsers hold a day; the edge holds until
            # the next deploy's invalidation.
            "/static/*": cf.BehaviorOptions(**static,
                                            cache_policy=cf.CachePolicy.CACHING_OPTIMIZED),
            # A stale worker pins every installed copy to an old release; these
            # two go to S3 on every request (they are tiny).
            "/sw.js": cf.BehaviorOptions(**static, cache_policy=cf.CachePolicy.CACHING_DISABLED),
            "/manifest.webmanifest": cf.BehaviorOptions(
                **static, cache_policy=cf.CachePolicy.CACHING_DISABLED),
        }

    def _admin(self, fn, app_route, acl, zone):
        """
        admin.<domain>: one sign-in for the social media tool, the operator
        console and analytics (app/admin.py, deploy/edge/admin.js). Its own
        distribution, so nothing here can loosen the public one.

        Off until adminDomain and adminCertificateArn are set. The certificate
        is made by CLI like the others (us-east-1 is closed to CloudFormation).
        The social media tool is a separate stack; its CloudFront domain comes
        in as context (socialOrigin), as the collector's does.
        """
        st = self.stage
        domain, cert = st.get("adminDomain"), st.get("adminCertificateArn")
        if not (domain and cert):
            return
        fn.add_environment("MYSIGN_ADMIN_URL", f"https://{domain}")
        route = cf.Function(
            self, "AdminRouter", comment="admin: pages, console API, /social; nothing else",
            code=cf.FunctionCode.from_file(file_path=os.path.join(HERE, "edge", "admin.js")),
            runtime=cf.FunctionRuntime.JS_2_0)
        routed = [cf.FunctionAssociation(function=route,
                                         event_type=cf.FunctionEventType.VIEWER_REQUEST)]
        headers = cf.ResponseHeadersPolicy(
            self, "AdminHeaders",
            security_headers_behavior=cf.ResponseSecurityHeadersBehavior(
                content_security_policy=cf.ResponseHeadersContentSecurityPolicy(
                    # The pages are one file each with inline script and style.
                    content_security_policy=(
                        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; "
                        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
                    override=True),
                strict_transport_security=cf.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(365), include_subdomains=False,
                    override=True),
                content_type_options=cf.ResponseHeadersContentTypeOptions(override=True),
                referrer_policy=cf.ResponseHeadersReferrerPolicy(
                    referrer_policy=cf.HeadersReferrerPolicy.SAME_ORIGIN, override=True),
                frame_options=cf.ResponseHeadersFrameOptions(
                    frame_option=cf.HeadersFrameOption.DENY, override=True)))
        extra = {}
        social = st.get("socialOrigin")
        if social:
            extra["/social*"] = cf.BehaviorOptions(
                origin=origins.HttpOrigin(
                    str(social), protocol_policy=cf.OriginProtocolPolicy.HTTPS_ONLY),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cf.AllowedMethods.ALLOW_ALL,
                cache_policy=cf.CachePolicy.CACHING_DISABLED,
                origin_request_policy=cf.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                function_associations=routed)
        dist = cf.Distribution(
            self, "AdminEdge", comment="My Sign admin platform",
            default_behavior=cf.BehaviorOptions(**app_route, response_headers_policy=headers,
                                                function_associations=routed),
            additional_behaviors=extra,
            domain_names=[domain],
            certificate=acm.Certificate.from_certificate_arn(self, "AdminCert", cert),
            price_class=cf.PriceClass.PRICE_CLASS_100,
            http_version=cf.HttpVersion.HTTP2_AND_3,
            web_acl_id=acl or None)
        if zone:
            target = r53.RecordTarget.from_alias(r53_targets.CloudFrontTarget(dist))
            sub = str(domain)[:-len(zone.zone_name)].rstrip(".")
            r53.ARecord(self, "AdminA", zone=zone, record_name=sub, target=target)
            r53.AaaaRecord(self, "AdminAaaa", zone=zone, record_name=sub, target=target)
        CfnOutput(self, "AdminUrl", value=f"https://{domain}")

    def _publish(self, site, build, dist):
        """
        Four uploads of one build directory, split by how long each kind of
        file may be cached. Each owns its files outright — the include and
        exclude filters scope the upload *and* the pruning — so no deployment
        deletes another's files.
        """
        src = [s3deploy.Source.asset(os.path.join(build, "site"))]
        cache = s3deploy.CacheControl.from_string
        assets = s3deploy.BucketDeployment(
            self, "SiteAssets", sources=src, destination_bucket=site,
            exclude=["*.html", "sw.js", "manifest.webmanifest"],
            cache_control=[cache("public, max-age=86400, s-maxage=31536000")])
        worker = s3deploy.BucketDeployment(
            self, "SiteWorker", sources=src, destination_bucket=site,
            exclude=["*"], include=["sw.js"],
            content_type="text/javascript", cache_control=[cache("no-cache")])
        manifest = s3deploy.BucketDeployment(
            self, "SiteManifest", sources=src, destination_bucket=site,
            exclude=["*"], include=["manifest.webmanifest"],
            content_type="application/manifest+json", cache_control=[cache("no-cache")])
        # Last, and the one that invalidates: by the time the edge forgets the
        # old pages, everything they point at is already in place.
        pages = s3deploy.BucketDeployment(
            self, "SitePages", sources=src, destination_bucket=site,
            exclude=["*"], include=["*.html"],
            content_type="text/html; charset=utf-8",
            cache_control=[cache("public, max-age=0, must-revalidate, s-maxage=300")],
            distribution=dist, distribution_paths=["/*"])
        pages.node.add_dependency(assets, worker, manifest)

    # ---------------------------------------------------------------- mail
    def _mail(self, domain, fn, alarms, zone=None):
        st, prefix = self.stage, self.prefix
        forward_to = st.get("forwardTo", "som@xii-studio.io")
        sandbox = st.flag("sesSandbox")
        # One stage owns the domain's identity and DNS; another sends through
        # it by ARN. A second EmailIdentity for the same domain would fight
        # the first over its DKIM records.
        own = st.flag("mailIdentity", True)
        # SES allows one active receipt rule set per region, and it is prod's.
        inbound = st.flag("inboundMail", True)

        # Suppression stops us mailing an address that already bounced or
        # complained — the two things that get an SES account paused. The
        # event destination puts each one in front of a human.
        config = ses.ConfigurationSet(
            self, "MailConfig",
            configuration_set_name=prefix,
            suppression_reasons=ses.SuppressionReasons.BOUNCES_AND_COMPLAINTS,
            reputation_metrics=True,
        )
        config.add_event_destination(
            "ToAlarms",
            destination=ses.EventDestination.sns_topic(alarms),
            events=[ses.EmailSendingEvent.BOUNCE, ses.EmailSendingEvent.COMPLAINT,
                    ses.EmailSendingEvent.REJECT],
        )

        # Verifying the domain emits three DKIM CNAMEs; add them and SES marks
        # it verified. The config set is the identity's default, so every send
        # is tracked whether or not the caller names it. Given the zone, CDK
        # writes the DKIM CNAMEs and the MAIL FROM MX/SPF itself.
        identity = ses.EmailIdentity(
            self, "MailDomain",
            identity=ses.Identity.public_hosted_zone(zone) if zone else ses.Identity.domain(domain),
            mail_from_domain=f"mail.{domain}",
            configuration_set=config,
        ) if own else None

        sender = "My Sign" if st.prod else f"My Sign ({st.name})"
        fn.add_environment("MYSIGN_FROM_EMAIL", f"{sender} <hello@{domain}>")
        fn.add_environment("MYSIGN_SUPPORT_EMAIL", f"support@{domain}")
        fn.add_environment("MYSIGN_SES_CONFIG_SET", config.configuration_set_name)

        ses_arn = f"arn:{self.partition}:ses:{self.region}:{self.account}"
        send = [f"{ses_arn}:identity/{domain}",
                f"{ses_arn}:configuration-set/{config.configuration_set_name}"]
        # In the SES sandbox, IAM also checks the *recipient's* identity, so a
        # policy scoped to our domain cannot reach a verified test inbox.
        # -c sesSandbox=true until production access is granted.
        app_send = send + ([f"{ses_arn}:identity/*"] if sandbox else [])
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["ses:SendEmail", "ses:SendRawEmail"], resources=app_send))

        # Account-level reputation. AWS reviews at 5% bounces / 0.1%
        # complaints; alarming at the line gives time to act before a pause.
        # Account-wide, so only the identity's owner alarms on it.
        reputation = (("MailBounceRate", "Reputation.BounceRate", 0.05),
                      ("MailComplaintRate", "Reputation.ComplaintRate", 0.001))
        for cid, name, limit in reputation if own else ():
            cw.Alarm(
                self, f"{cid}Alarm",
                metric=cw.Metric(namespace="AWS/SES", metric_name=name,
                                 statistic="Maximum", period=Duration.hours(1)),
                threshold=limit, evaluation_periods=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                alarm_description=f"SES {name} above {limit:.1%}: sending may be paused.",
            ).add_alarm_action(cw_actions.SnsAction(alarms))

        if inbound:
            self._inbound(domain, prefix, forward_to, send, config, alarms, zone)

        if not own:
            return
        if zone:
            r53.TxtRecord(self, "DmarcTxt", zone=zone, record_name="_dmarc",
                          values=[f"v=DMARC1; p=quarantine; rua=mailto:dmarc@{domain}"])

        # ------------------------------------------------ DNS to add by hand
        # (already written above when hostedZoneId is passed)
        for i in range(3):
            CfnOutput(self, f"DkimRecord{i + 1}",
                      value=f"{identity.dkim_records[i].name} CNAME {identity.dkim_records[i].value}")
        CfnOutput(self, "MailFromMx",
                  value=f"mail.{domain} MX 10 feedback-smtp.{self.region}.amazonses.com")
        CfnOutput(self, "SpfRecord",
                  value=f'mail.{domain} TXT "v=spf1 include:amazonses.com -all"')
        CfnOutput(self, "DmarcRecord",
                  value=f'_dmarc.{domain} TXT "v=DMARC1; p=quarantine; rua=mailto:dmarc@{domain}"')

    # -------------------------------------------- inbound: support@ etc.
    def _inbound(self, domain, prefix, forward_to, send, config, alarms, zone):
        # SES receives, S3 keeps the raw message, a small Lambda re-sends it to
        # a person. Ninety days: long enough to find a lost thread, short enough
        # that the bucket is not an archive of other people's mail.
        inbox = s3.Bucket(
            self, "Inbox",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(90))],
            removal_policy=RemovalPolicy.RETAIN,
        )
        # No explicit SES PutObject grant: ses_actions.S3 below writes exactly
        # that statement (ses.amazonaws.com, inbound/*, aws:SourceAccount).

        fwd_logs = logs.LogGroup(
            self, "ForwarderLogs", log_group_name=f"/{prefix}/mailforward",
            retention=logs.RetentionDays.THREE_MONTHS, removal_policy=RemovalPolicy.RETAIN)
        forwarder = lambda_.Function(
            self, "MailForwarder",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(os.path.join(HERE, "mailforward")),
            memory_size=256,
            timeout=Duration.seconds(30),
            log_group=fwd_logs,
            environment={
                "BUCKET": inbox.bucket_name,
                "PREFIX": "inbound/",
                "FORWARD_TO": forward_to,
                "SENDER": f"forwarder@{domain}",
                "CONFIG_SET": config.configuration_set_name,
            },
        )
        inbox.grant_read(forwarder, "inbound/*")
        # The forward target is the one recipient it ever needs, which is also
        # the identity the sandbox wants it to hold permission on.
        ses_arn = f"arn:{self.partition}:ses:{self.region}:{self.account}"
        forwarder.add_to_role_policy(iam.PolicyStatement(
            actions=["ses:SendRawEmail"],
            resources=send + [f"{ses_arn}:identity/{forward_to}"]))
        cw.Alarm(
            self, "MailForwarderErrorAlarm",
            metric=forwarder.metric_errors(period=Duration.minutes(5), statistic="Sum"),
            threshold=0, evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description="Support mail arrived but could not be forwarded. It is in S3.",
        ).add_alarm_action(cw_actions.SnsAction(alarms))

        rule_set = ses.ReceiptRuleSet(self, "InboundRules",
                                      receipt_rule_set_name=f"{prefix}-inbound")
        rule_set.add_rule(
            "Support",
            recipients=[f"support@{domain}", f"hello@{domain}", f"dmarc@{domain}"],
            scan_enabled=True,
            actions=[
                ses_actions.S3(bucket=inbox, object_key_prefix="inbound/"),
                ses_actions.Lambda(function=forwarder),
            ],
        )

        if zone:
            # Apex MX: support@/hello@ land in SES. Remove it before pointing
            # the domain at another mail provider.
            r53.MxRecord(self, "InboundMxRecord", zone=zone, values=[
                r53.MxRecordValue(host_name=f"inbound-smtp.{self.region}.amazonaws.com",
                                  priority=10)])

        CfnOutput(self, "InboundMx",
                  value=f"{domain} MX 10 inbound-smtp.{self.region}.amazonaws.com")
        # CloudFormation can create a rule set but cannot make it the active one.
        CfnOutput(self, "ActivateInbound",
                  value=f"aws ses set-active-receipt-rule-set --rule-set-name "
                        f"{rule_set.receipt_rule_set_name} --region {self.region}")

    # ---------------------------------------------------------------- sms
    def _sms(self, fn, alarms):
        """
        Sign-in codes by text (AWS End User Messaging SMS). The number, the
        configuration set and the carrier registration are made by hand with
        deploy/sms/setup-sms.sh — a toll-free number is bought, not declared,
        and its registration takes weeks. This only lets the app send through
        them and watches the spend ceiling. The app still refuses phone
        sign-in until sms_origination_identity is in the secret.
        """
        config_set = "mysign-sms"
        arn = f"arn:{self.partition}:sms-voice:{self.region}:{self.account}"
        # Our own numbers, pools and protect configurations in this region and
        # the one configuration set. Not sender IDs: the US has none.
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["sms-voice:SendTextMessage"],
            resources=[f"{arn}:phone-number/*", f"{arn}:pool/*",
                       f"{arn}:protect-configuration/*",
                       f"{arn}:configuration-set/{config_set}"]))
        fn.add_environment("MYSIGN_SMS_CONFIG_SET", config_set)
        domain = self.stage.get("domainName")
        if domain:
            # The WebOTP line ("@domain #code") lets the browser fill the code.
            fn.add_environment("MYSIGN_DOMAIN", domain)

        # The daily cap tripping means texts are refused for everyone until
        # midnight UTC — almost always someone pumping the form.
        cap = logs.MetricFilter(
            self, "SmsCapMetric", log_group=fn.log_group,
            metric_namespace=self.ns, metric_name="SmsCap",
            filter_pattern=logs.FilterPattern.string_value("$.event", "=", "auth.sms_cap"),
            metric_value="1")
        cw.Alarm(
            self, "SmsCapAlarm",
            metric=cap.metric(period=Duration.minutes(5), statistic="Sum"),
            threshold=1, evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description="SMS daily cap reached: text sign-in is off until midnight UTC. "
                              "Likely SMS pumping — check auth.sms_limited in /mysign/app.",
        ).add_alarm_action(cw_actions.SnsAction(alarms))
