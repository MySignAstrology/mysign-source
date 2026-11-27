"""
The analytics lake: events in, Parquet in S3, Athena on top.

Separate from the app stack on purpose. Nothing here can take the app down, it
deploys on its own cadence, and tearing it out is one `cdk destroy` that leaves
the table, the secret and every person's history where they were.

    collector Lambda (/e, anonymous) ─┐
                                      ├─> Firehose ─> S3 raw/events/dt=…/source=…/  (Parquet)
    app Lambda (/api/activity, uid) ──┘                     │
                                                    Glue table `events` ─> Athena workgroup

No crawler: the Glue table is written here with partition projection on `dt`
and `source`, so a new day is queryable the moment Firehose writes it.

What it costs at today's scale is close to nothing — Firehose bills per GB
ingested (plus the Parquet conversion), S3 holds megabytes, and the workgroup's
per-query scan cutoff is what keeps a careless `SELECT *` from being a bill.
The 128 MB / 5 minute buffer means at most one file per partition per five
minutes; smaller buffers make many tiny files, which Athena reads slowly.

Retention matches the privacy policy: raw events (which carry ids) expire after
90 days; `agg/` holds rollups with no ids and is kept. See docs/analytics.md.

Context:
  stage            prod (default) | staging — names everything, and staging
                   tears down cleanly where prod retains
  domainName       our own hosts, so internal navigation isn't a "referral"
  appSecretArn     the app's secret, whose `origin_token` (the x-origin-auth
                   token CloudFront sends) is resolved into the collector at
                   deploy time. There is no `originToken` flag: a token passed
                   as context lands in the template in plain text (audit H2)
  alarmTopicArn    the app stack's alarm topic (its AlarmTopic output)
  collectConcurrency   reserved concurrency for the collector (default 10)
  athenaCutoffBytes    per-query scan ceiling (default 1 GB)
  appTableName     the app stack's table (its TableName output), where the
                   rollups put their /ops summaries; prod reads this key,
                   another stage only <stage>AppTableName or its `stages`
                   entry. Absent: the rollups write S3 `agg/` only.
"""
import os
import sys
from typing import Optional

from aws_cdk import (
    Aws,
    CfnOutput,
    Duration,
    Fn,
    RemovalPolicy,
    SecretValue,
    Stack,
    SymlinkFollowMode,
)
from aws_cdk import (
    aws_athena as athena,
)
from aws_cdk import (
    aws_cloudwatch as cw,
)
from aws_cdk import (
    aws_cloudwatch_actions as cw_actions,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_events_targets as targets,
)
from aws_cdk import (
    aws_glue as glue,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_kinesisfirehose as firehose,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_sns as sns,
)
from constructs import Construct

STAGES = ("prod", "staging")
RAW_DAYS = 90               # the privacy policy's number; change both or neither
RESULTS_DAYS = 30
ERRORS_DAYS = 14
# Backfilled objects carry an expiry tag so that history copied in late still
# leaves within 90 days of when it happened, not 90 days after the copy.
BACKFILL_TAG = "mysign-expire-days"
BACKFILL_DAYS = (1, 7, 30, 60)
SOURCES = ("site", "app", "server", "native", "backfill")   # app/events.py SOURCES
PROJECTION_START = "2026-01-01"

# The envelope (app/events.py `envelope`, docs/analytics.md). `source` and
# `dt` are partitions, not columns: Glue won't have a name be both.
CTX_TYPE = ("struct<utm_source:string,utm_medium:string,utm_campaign:string,"
            "referrer_domain:string,landing_path:string,country:string,"
            "device_class:string,os_family:string,browser_family:string>")
COLUMNS = (
    ("event_id", "string"), ("ts", "bigint"), ("received_at", "bigint"),
    ("app_version", "string"), ("anon_id", "string"), ("uid", "string"),
    ("session_id", "string"), ("type", "string"), ("name", "string"),
    ("screen", "string"), ("props", "map<string,string>"), ("ctx", CTX_TYPE),
)
# Firehose's dynamic partitioning: the day from the event's own clock (ms),
# not the arrival time, so a late batch still lands in the right day.
PARTITION_QUERY = '{dt: (.ts / 1000 | floor | strftime("%Y-%m-%d")), source: .source}'
RAW_PREFIX = "raw/events/"
JSON_PREFIX = "raw/events-json/"
# deploy/rollups/rollup_sql.py REPORTS (tested to match): each gets its own agg/ prefix.
ROLLUP_REPORTS = ("visits", "signups", "funnel", "active", "features", "time",
                  "retention", "notifications", "monetization", "site", "usage")


def stage_of(scope):
    stage = scope.node.try_get_context("stage") or "prod"
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}, not {stage!r}")
    return stage


def staged(scope, key):
    """A context value for this stage, the way deploy/stages.py reads it: prod
    takes the top-level key; any other stage only -c <stage><Key> or its own
    `stages` entry, never prod's by omission."""
    stage, node = stage_of(scope), scope.node
    if stage == "prod":
        return node.try_get_context(key)
    v = node.try_get_context(stage + key[0].upper() + key[1:])
    if v is None:
        v = ((node.try_get_context("stages") or {}).get(stage) or {}).get(key)
    return v


def stack_name(scope):
    stage = stage_of(scope)
    return "mysign-analytics" if stage == "prod" else f"mysign-analytics-{stage}"


class MySignAnalyticsStack(Stack):
    def __init__(self, scope: Construct, cid: Optional[str] = None, **kw):
        name = stack_name(scope)
        kw.setdefault("stack_name", name)
        kw.setdefault("termination_protection", stage_of(scope) == "prod")
        super().__init__(scope, cid or name, **kw)
        ctx = self.node.try_get_context
        stage = ctx("stage") or "prod"
        prod = stage == "prod"
        sfx = "" if prod else f"-{stage}"
        db_name = "mysign_analytics" if prod else f"mysign_analytics_{stage}"
        keep = RemovalPolicy.RETAIN if prod else RemovalPolicy.DESTROY

        # ------------------------------------------------ the lake
        expiring = [
            s3.LifecycleRule(id="raw", prefix="raw/", expiration=Duration.days(RAW_DAYS)),
            s3.LifecycleRule(id="athena-results", prefix="athena-results/",
                             expiration=Duration.days(RESULTS_DAYS)),
            s3.LifecycleRule(id="errors", prefix="errors/", expiration=Duration.days(ERRORS_DAYS)),
            s3.LifecycleRule(id="multipart", abort_incomplete_multipart_upload_after=Duration.days(2)),
        ] + [
            # When rules overlap S3 applies the shortest expiry, so these only
            # ever shorten the 90 days above.
            s3.LifecycleRule(id=f"backfill-{d}d", prefix="raw/",
                             tag_filters={BACKFILL_TAG: str(d)}, expiration=Duration.days(d))
            for d in BACKFILL_DAYS
        ]
        lake = s3.Bucket(
            self, "Lake",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            # agg/ has no rule: rollups carry no ids, and they are the history.
            lifecycle_rules=expiring,
            removal_policy=keep,
            auto_delete_objects=not prod,
        )

        # ------------------------------------------------ the catalog
        db = glue.CfnDatabase(
            self, "Db", catalog_id=Aws.ACCOUNT_ID,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=db_name, description="My Sign product analytics (docs/analytics.md)"))
        def events_table(cid, name, prefix, fmt, what):
            parquet = fmt == "parquet"
            t = glue.CfnTable(
                self, cid, catalog_id=Aws.ACCOUNT_ID, database_name=db_name,
                table_input=glue.CfnTable.TableInputProperty(
                    name=name, description=what, table_type="EXTERNAL_TABLE",
                    parameters={
                        "classification": fmt,
                        "projection.enabled": "true",
                        "projection.dt.type": "date",
                        "projection.dt.format": "yyyy-MM-dd",
                        "projection.dt.range": f"{PROJECTION_START},NOW",
                        "projection.dt.interval": "1",
                        "projection.dt.interval.unit": "DAYS",
                        "projection.source.type": "enum",
                        "projection.source.values": ",".join(SOURCES),
                        "storage.location.template":
                            f"s3://{lake.bucket_name}/{prefix}dt=${{dt}}/source=${{source}}/",
                    },
                    partition_keys=[glue.CfnTable.ColumnProperty(name="dt", type="string"),
                                    glue.CfnTable.ColumnProperty(name="source", type="string")],
                    storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                        columns=[glue.CfnTable.ColumnProperty(name=n, type=t) for n, t in COLUMNS],
                        location=f"s3://{lake.bucket_name}/{prefix}",
                        input_format=("org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
                                      if parquet else "org.apache.hadoop.mapred.TextInputFormat"),
                        output_format=("org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
                                       if parquet else
                                       "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"),
                        serde_info=glue.CfnTable.SerdeInfoProperty(serialization_library=(
                            "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
                            if parquet else "org.openx.data.jsonserde.JsonSerDe")),
                    ),
                ))
            t.node.add_dependency(db)
            return t

        table = events_table("Events", "events", RAW_PREFIX, "parquet",
                             "One row per event, from Firehose. Envelope: app/events.py.")
        # The same envelope as JSON lines, for tools/backfill_events.py. Kept
        # apart because one table can't mix Parquet and JSON files; query both
        # through the events_all view (docs/analytics.md).
        events_table("EventsJson", "events_json", JSON_PREFIX, "json",
                     "Backfilled events as JSON lines (tools/backfill_events.py).")

        # ------------------------------------------------ the stream
        fh_logs = logs.LogGroup(
            self, "FirehoseLogs", log_group_name=f"/mysign{sfx}/analytics-firehose",
            retention=logs.RetentionDays.THREE_MONTHS, removal_policy=keep)
        fh_stream = logs.LogStream(self, "FirehoseS3Delivery", log_group=fh_logs,
                                   log_stream_name="S3Delivery", removal_policy=keep)

        role = iam.Role(
            self, "FirehoseRole",
            assumed_by=iam.ServicePrincipal(
                "firehose.amazonaws.com",
                conditions={"StringEquals": {"sts:ExternalId": Aws.ACCOUNT_ID}}))
        role.add_to_policy(iam.PolicyStatement(
            actions=["s3:AbortMultipartUpload", "s3:GetBucketLocation", "s3:GetObject",
                     "s3:ListBucket", "s3:ListBucketMultipartUploads", "s3:PutObject"],
            resources=[lake.bucket_arn, f"{lake.bucket_arn}/raw/events/*",
                       f"{lake.bucket_arn}/errors/*"]))
        glue_arn = f"arn:{Aws.PARTITION}:glue:{Aws.REGION}:{Aws.ACCOUNT_ID}"
        role.add_to_policy(iam.PolicyStatement(
            actions=["glue:GetTable", "glue:GetTableVersion", "glue:GetTableVersions"],
            resources=[f"{glue_arn}:catalog", f"{glue_arn}:database/{db_name}",
                       f"{glue_arn}:table/{db_name}/events"]))
        fh_logs.grant_write(role)

        F = firehose.CfnDeliveryStream
        stream = F(
            self, "Stream",
            delivery_stream_name=f"mysign-events{sfx}",
            delivery_stream_type="DirectPut",
            delivery_stream_encryption_configuration_input=F.DeliveryStreamEncryptionConfigurationInputProperty(
                key_type="AWS_OWNED_CMK"),
            extended_s3_destination_configuration=F.ExtendedS3DestinationConfigurationProperty(
                bucket_arn=lake.bucket_arn,
                role_arn=role.role_arn,
                prefix=f"{RAW_PREFIX}dt=!{{partitionKeyFromQuery:dt}}/"
                       "source=!{partitionKeyFromQuery:source}/",
                error_output_prefix="errors/!{firehose:error-output-type}/dt=!{timestamp:yyyy-MM-dd}/",
                # 128 MB or five minutes, whichever first. Dynamic partitioning
                # needs at least 64 MB; at our volume the clock always wins.
                buffering_hints=F.BufferingHintsProperty(size_in_m_bs=128, interval_in_seconds=300),
                compression_format="UNCOMPRESSED",       # Parquet compresses itself (Snappy)
                cloud_watch_logging_options=F.CloudWatchLoggingOptionsProperty(
                    enabled=True, log_group_name=fh_logs.log_group_name,
                    log_stream_name=fh_stream.log_stream_name),
                dynamic_partitioning_configuration=F.DynamicPartitioningConfigurationProperty(
                    enabled=True, retry_options=F.RetryOptionsProperty(duration_in_seconds=300)),
                processing_configuration=F.ProcessingConfigurationProperty(
                    enabled=True,
                    processors=[F.ProcessorProperty(type="MetadataExtraction", parameters=[
                        F.ProcessorParameterProperty(parameter_name="MetadataExtractionQuery",
                                                     parameter_value=PARTITION_QUERY),
                        F.ProcessorParameterProperty(parameter_name="JsonParsingEngine",
                                                     parameter_value="JQ-1.6"),
                    ])]),
                data_format_conversion_configuration=F.DataFormatConversionConfigurationProperty(
                    enabled=True,
                    input_format_configuration=F.InputFormatConfigurationProperty(
                        deserializer=F.DeserializerProperty(open_x_json_ser_de=F.OpenXJsonSerDeProperty())),
                    output_format_configuration=F.OutputFormatConfigurationProperty(
                        serializer=F.SerializerProperty(parquet_ser_de=F.ParquetSerDeProperty(
                            compression="SNAPPY"))),
                    schema_configuration=F.SchemaConfigurationProperty(
                        catalog_id=Aws.ACCOUNT_ID, database_name=db_name, table_name="events",
                        region=Aws.REGION, role_arn=role.role_arn, version_id="LATEST"),
                ),
            ),
        )
        stream.node.add_dependency(table)
        # The role's policy must exist before Firehose first assumes it, or the
        # stream is created and then fails its first delivery.
        stream.node.add_dependency(role)

        # ------------------------------------------------ queries
        cutoff = int(ctx("athenaCutoffBytes") or 1024 ** 3)
        workgroup = athena.CfnWorkGroup(
            self, "AthenaWorkgroup",
            name=f"mysign{sfx}",
            description="My Sign analytics. Results expire after 30 days.",
            state="ENABLED",
            recursive_delete_option=not prod,
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                # Enforced, so a client can't point results somewhere unencrypted
                # or lift the scan ceiling for itself.
                enforce_work_group_configuration=True,
                bytes_scanned_cutoff_per_query=cutoff,
                publish_cloud_watch_metrics_enabled=True,
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=f"s3://{lake.bucket_name}/athena-results/",
                    encryption_configuration=athena.CfnWorkGroup.EncryptionConfigurationProperty(
                        encryption_option="SSE_S3")),
            ))

        # ------------------------------------------------ the collector
        collect_logs = logs.LogGroup(
            self, "CollectLogs", log_group_name=f"/mysign{sfx}/collect",
            retention=logs.RetentionDays.THREE_MONTHS, removal_policy=keep)
        env = {"STREAM_NAME": stream.ref}
        if ctx("domainName"):
            env["SITE_HOSTS"] = str(ctx("domainName"))
        # The token CloudFront sends as x-origin-auth, resolved into the
        # environment at deploy time and never read at runtime: the collector
        # is anonymous and internet-facing, and a grant to read the app secret
        # would hand it the model key and the session-signing key along with
        # the one value it needs. Per stage, so staging can't be pointed at
        # prod's secret by omission.
        if staged(self, "originToken") not in (None, "", "null"):
            sys.exit("\nRefusing to synthesize: the origin token is never passed as "
                     "context (it would be written into the template in plain text). "
                     "Set appSecretArn and the collector resolves it from the secret.\n")
        if staged(self, "appSecretArn"):
            # Pinned to a version after a rotation, for the same reason as the
            # app stack: an unchanged reference is never re-resolved.
            ver = staged(self, "originTokenVersion")
            env["MYSIGN_ORIGIN_TOKEN"] = SecretValue.secrets_manager(
                str(staged(self, "appSecretArn")), json_field="origin_token",
                version_id=str(ver) if ver else None).unsafe_unwrap()
            # And a plain copy of the version beside it. When the reference is
            # the only thing in the environment that changes, CloudFormation
            # updates the code and skips the configuration: the 2026-10-30
            # rotation left the collector on the old token (CloudTrail showed
            # no UpdateFunctionConfiguration) while CloudFront sent the new one.
            if ver:
                env["MYSIGN_ORIGIN_TOKEN_VERSION"] = str(ver)
        conc = ctx("collectConcurrency")
        conc = 10 if conc in (None, "") else (None if str(conc) == "null" else int(conc))
        collector = lambda_.Function(
            self, "Collector",
            description="Anonymous analytics events -> Firehose (deploy/collect)",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            # events.py there is a symlink to app/events.py; ALWAYS copies what
            # it points at, so the asset carries the app's rules, byte for byte.
            code=lambda_.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "collect"),
                follow_symlinks=SymlinkFollowMode.ALWAYS,
                exclude=["__pycache__", "*.pyc"]),
            memory_size=256,
            timeout=Duration.seconds(5),
            # A ceiling on what a flood of junk can cost; ~500 req/s at 20 ms each.
            reserved_concurrent_executions=conc,
            log_group=collect_logs,
            environment=env,
        )
        collector.add_to_role_policy(iam.PolicyStatement(
            actions=["firehose:PutRecordBatch"], resources=[stream.attr_arn]))
        # No secret grant: without a token the handler refuses every request,
        # so a deploy that forgets it breaks analytics loudly instead of
        # opening the collector.
        # Public, like the app's: CloudFront reaches it unsigned, and the
        # handler itself checks x-origin-auth.
        url = collector.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)

        alarm = cw.Alarm(
            self, "CollectorErrorAlarm",
            metric=collector.metric_errors(period=Duration.minutes(5), statistic="Sum"),
            threshold=0, evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description="Analytics collector is erroring (Firehose unreachable?).",
        )
        if ctx("alarmTopicArn"):
            alarm.add_alarm_action(cw_actions.SnsAction(
                sns.Topic.from_topic_arn(self, "AlarmTopic", str(ctx("alarmTopicArn")))))

        # ------------------------------------------------ rollups (deploy/rollups)
        # Athena → agg/<report>/dt=<day>/ in the lake, and a summary per report
        # per day into the app table for /ops (app/reports.py). Hourly: today so
        # far (and yesterday until 03:00 UTC). Daily 02:30 UTC: yesterday, final,
        # plus the retention snapshot. docs/analytics.md, "Rollups".
        rollup_logs = logs.LogGroup(
            self, "RollupLogs", log_group_name=f"/mysign{sfx}/rollups",
            retention=logs.RetentionDays.THREE_MONTHS, removal_policy=keep)
        app_table = staged(self, "appTableName")
        rollup_env = {"WORKGROUP": workgroup.name, "DATABASE": db_name,
                      "LAKE_BUCKET": lake.bucket_name}
        if app_table:
            rollup_env["APP_TABLE"] = str(app_table)
        rollup = lambda_.Function(
            self, "Rollup",
            description="Analytics rollups: Athena -> agg/ + /ops summaries (deploy/rollups)",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "rollups"),
                exclude=["__pycache__", "*.pyc"]),
            memory_size=512,
            # The handler stops waiting on Athena 20 s before this.
            timeout=Duration.minutes(5),
            # An overlapping run is harmless: writes are whole-day overwrites
            # and a partial never replaces a final.
            retry_attempts=0,
            log_group=rollup_logs,
            environment=rollup_env,
        )
        wg_arn = f"arn:{Aws.PARTITION}:athena:{Aws.REGION}:{Aws.ACCOUNT_ID}:workgroup/{workgroup.name}"
        rollup.add_to_role_policy(iam.PolicyStatement(
            actions=["athena:StartQueryExecution", "athena:GetQueryExecution",
                     "athena:BatchGetQueryExecution", "athena:GetQueryResults",
                     "athena:StopQueryExecution", "athena:GetWorkGroup"],
            resources=[wg_arn]))
        rollup.add_to_role_policy(iam.PolicyStatement(
            actions=["glue:GetDatabase", "glue:GetTable", "glue:GetTables",
                     "glue:GetPartition", "glue:GetPartitions"],
            resources=[f"{glue_arn}:catalog", f"{glue_arn}:database/{db_name}",
                       f"{glue_arn}:table/{db_name}/events",
                       f"{glue_arn}:table/{db_name}/events_json"]))
        rollup.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:ListBucket", "s3:GetBucketLocation"], resources=[lake.bucket_arn]))
        rollup.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:GetObject"],
            resources=[f"{lake.bucket_arn}/{RAW_PREFIX}*", f"{lake.bucket_arn}/{JSON_PREFIX}*"]))
        rollup.add_to_role_policy(iam.PolicyStatement(
            # Athena writes and reads results with the caller's credentials.
            actions=["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload",
                     "s3:ListMultipartUploadParts"],
            resources=[f"{lake.bucket_arn}/athena-results/*"]))
        rollup.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:PutObject"],
            resources=[f"{lake.bucket_arn}/agg/{r}/*" for r in ROLLUP_REPORTS]))
        if app_table:
            rollup.add_to_role_policy(iam.PolicyStatement(
                actions=["dynamodb:PutItem"],
                resources=[f"arn:{Aws.PARTITION}:dynamodb:{Aws.REGION}:{Aws.ACCOUNT_ID}"
                           f":table/{app_table}"],
                # Only the report partition: this role can't touch a person's items.
                conditions={"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": ["_report"]}}))
        for rid, cron, mode in (("RollupHourly", events.Schedule.cron(minute="5"), "hourly"),
                                ("RollupDaily", events.Schedule.cron(minute="30", hour="2"),
                                 "daily")):
            events.Rule(self, rid, schedule=cron,
                        description=f"Analytics rollups, {mode} (deploy/rollups)",
                        targets=[targets.LambdaFunction(
                            rollup, event=events.RuleTargetInput.from_object({"mode": mode}),
                            retry_attempts=0)])
        rollup_alarm = cw.Alarm(
            self, "RollupErrorAlarm",
            metric=rollup.metric_errors(period=Duration.minutes(5), statistic="Sum"),
            threshold=0, evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description="Analytics rollups failed (an Athena query, or a write). "
                              "See /mysign/rollups logs.",
        )
        if ctx("alarmTopicArn"):
            topic = self.node.try_find_child("AlarmTopic") or sns.Topic.from_topic_arn(
                self, "AlarmTopic", str(ctx("alarmTopicArn")))
            rollup_alarm.add_alarm_action(cw_actions.SnsAction(topic))
        CfnOutput(self, "RollupFunction", value=rollup.function_name,
                  description="Rerun: deploy/rollups/handler.py, or invoke with {from, to}")
        # ------------------------------------------------ end rollups

        # ------------------------------------------------ for the app stack
        CfnOutput(self, "StreamName", value=stream.ref,
                  description="App Lambda env MYSIGN_EVENTS_STREAM")
        CfnOutput(self, "StreamArn", value=stream.attr_arn,
                  description="App Lambda needs firehose:PutRecordBatch on this")
        CfnOutput(self, "LakeBucket", value=lake.bucket_name)
        CfnOutput(self, "Workgroup", value=workgroup.name)
        CfnOutput(self, "GlueDatabase", value=db_name)
        CfnOutput(self, "CollectorUrl", value=url.url)
        CfnOutput(self, "CollectorDomain", value=Fn.select(2, Fn.split("/", url.url)),
                  description="CloudFront origin for the /e behavior")
        self.stream, self.lake, self.collector_url = stream, lake, url
