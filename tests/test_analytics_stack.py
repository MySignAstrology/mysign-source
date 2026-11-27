"""
The analytics stack, as CloudFormation: what the lake promises (encrypted,
private, raw events gone after 90 days) and what makes it cheap (Parquet,
partitioned, a per-query scan ceiling).
"""
import json
import os
import pathlib
import shutil
import sys

import pytest

cdk = pytest.importorskip("aws_cdk")
from aws_cdk.assertions import Match, Template

ROOT = pathlib.Path(__file__).parent.parent
DEPLOY = ROOT / "deploy"
sys.path.insert(0, str(ROOT))
from app import events

# Appended, not prepended: deploy/app.py would otherwise shadow the app package.
sys.path.append(str(DEPLOY))
os.environ.setdefault("JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION", "1")

from analytics_stack import MySignAnalyticsStack

# Inside the repo (and removed after), not the system temp directory.
OUT = DEPLOY / ".cdk.out.test"


def _synth(**context):
    app = cdk.App(context=context, outdir=str(OUT / (context.get("stage") or "prod")))
    stack = MySignAnalyticsStack(app, env=cdk.Environment(account="111122223333",
                                                          region="us-east-2"))
    return stack, Template.from_stack(stack)


@pytest.fixture(scope="module", autouse=True)
def _clean_out():
    yield
    shutil.rmtree(OUT, ignore_errors=True)


@pytest.fixture(scope="module")
def prod():
    return _synth(alarmTopicArn="arn:aws:sns:us-east-2:111122223333:alarms",
                  domainName="mysignapp.com")


def test_names_follow_the_stage(prod):
    stack, t = prod
    assert stack.stack_name == "mysign-analytics" and stack.termination_protection
    t.has_resource_properties("AWS::Glue::Database",
                              {"DatabaseInput": {"Name": "mysign_analytics"}})
    t.has_resource_properties("AWS::Athena::WorkGroup", {"Name": "mysign"})
    staging, st = _synth(stage="staging")
    assert staging.stack_name == "mysign-analytics-staging"
    st.has_resource_properties("AWS::Glue::Database",
                               {"DatabaseInput": {"Name": "mysign_analytics_staging"}})
    st.has_resource_properties("AWS::Athena::WorkGroup", {"Name": "mysign-staging"})
    st.has_resource_properties("AWS::KinesisFirehose::DeliveryStream",
                               {"DeliveryStreamName": "mysign-events-staging"})
    with pytest.raises(ValueError):
        _synth(stage="dev")


def test_lake_is_private_encrypted_and_forgets_raw_events(prod):
    _, t = prod
    t.has_resource_properties("AWS::S3::Bucket", {
        "BucketEncryption": {"ServerSideEncryptionConfiguration": [
            {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]},
        "PublicAccessBlockConfiguration": {"BlockPublicAcls": True, "BlockPublicPolicy": True,
                                           "IgnorePublicAcls": True, "RestrictPublicBuckets": True},
        "LifecycleConfiguration": {"Rules": Match.array_with([
            Match.object_like({"Prefix": "raw/", "ExpirationInDays": 90, "Status": "Enabled"}),
            Match.object_like({"Prefix": "athena-results/", "ExpirationInDays": 30}),
            Match.object_like({"Prefix": "errors/", "ExpirationInDays": 14}),
        ])},
    })
    t.has_resource("AWS::S3::Bucket", {"DeletionPolicy": "Retain"})
    rules = next(iter(t.find_resources("AWS::S3::Bucket").values()))[
        "Properties"]["LifecycleConfiguration"]["Rules"]
    # Nothing expires agg/: rollups carry no ids and are the history.
    assert not [r for r in rules if str(r.get("Prefix", "")).startswith("agg")]
    assert all(r["ExpirationInDays"] <= 90 for r in rules if "ExpirationInDays" in r)
    policy = json.dumps(t.find_resources("AWS::S3::BucketPolicy"))
    assert "aws:SecureTransport" in policy


def test_firehose_writes_partitioned_parquet(prod):
    _, t = prod
    t.has_resource_properties("AWS::KinesisFirehose::DeliveryStream", {
        "DeliveryStreamName": "mysign-events",
        "DeliveryStreamType": "DirectPut",
        "ExtendedS3DestinationConfiguration": Match.object_like({
            "Prefix": "raw/events/dt=!{partitionKeyFromQuery:dt}/source=!{partitionKeyFromQuery:source}/",
            "ErrorOutputPrefix": Match.string_like_regexp("^errors/"),
            "BufferingHints": {"IntervalInSeconds": 300, "SizeInMBs": 128},
            "DynamicPartitioningConfiguration": Match.object_like({"Enabled": True}),
            "ProcessingConfiguration": {"Enabled": True, "Processors": [Match.object_like({
                "Type": "MetadataExtraction",
                "Parameters": Match.array_with([Match.object_like({
                    "ParameterName": "MetadataExtractionQuery",
                    "ParameterValue": Match.string_like_regexp(r"dt: .*source: \.source")})]),
            })]},
            "DataFormatConversionConfiguration": Match.object_like({
                "Enabled": True,
                "OutputFormatConfiguration": {"Serializer": {"ParquetSerDe": {"Compression": "SNAPPY"}}},
                "SchemaConfiguration": Match.object_like({"DatabaseName": "mysign_analytics",
                                                          "TableName": "events"}),
            }),
        }),
    })


def test_glue_table_projects_partitions_and_matches_the_envelope(prod):
    _, t = prod
    tables = {r["Properties"]["TableInput"]["Name"]: r["Properties"]["TableInput"]
              for r in t.find_resources("AWS::Glue::Table").values()}
    assert set(tables) == {"events", "events_json"}
    assert tables["events"]["StorageDescriptor"]["SerdeInfo"]["SerializationLibrary"].endswith(
        "ParquetHiveSerDe")
    assert tables["events_json"]["StorageDescriptor"]["Columns"] == \
        tables["events"]["StorageDescriptor"]["Columns"]
    ti = tables["events"]
    p = ti["Parameters"]
    assert p["projection.enabled"] == "true" and p["projection.dt.type"] == "date"
    assert p["projection.source.values"].split(",") == ["site", "app", "server", "native",
                                                        "backfill"]
    assert [k["Name"] for k in ti["PartitionKeys"]] == ["dt", "source"]
    cols = {c["Name"] for c in ti["StorageDescriptor"]["Columns"]}
    env = events.envelope(events.clean_event({"type": "tap", "name": "x", "at": 0}, 0),
                          source="app")
    assert cols == set(env) - {"source"}         # every envelope field has a column


def test_athena_workgroup_caps_every_query(prod):
    _, t = prod
    t.has_resource_properties("AWS::Athena::WorkGroup", {"WorkGroupConfiguration": Match.object_like({
        "EnforceWorkGroupConfiguration": True,
        "BytesScannedCutoffPerQuery": 1024 ** 3,
        "ResultConfiguration": Match.object_like({
            "EncryptionConfiguration": {"EncryptionOption": "SSE_S3"}}),
    })})


def test_collector_is_small_and_can_only_put_to_the_stream(prod):
    _, t = prod
    t.has_resource_properties("AWS::Lambda::Function", {
        "Runtime": "python3.13", "Architectures": ["arm64"], "Handler": "handler.handler",
        "Environment": {"Variables": Match.object_like({"SITE_HOSTS": "mysignapp.com"})},
    })
    t.has_resource_properties("AWS::Lambda::Url", {"AuthType": "NONE"})
    t.has_resource_properties("AWS::Logs::LogGroup", {"LogGroupName": "/mysign/collect",
                                                      "RetentionInDays": 90})
    t.has_resource_properties("AWS::IAM::Policy", {"PolicyDocument": {"Statement": [
        {"Action": "firehose:PutRecordBatch", "Effect": "Allow",
         "Resource": {"Fn::GetAtt": [Match.any_value(), "Arn"]}}]}})
    t.has_resource_properties("AWS::CloudWatch::Alarm", {
        "MetricName": "Errors", "AlarmActions": ["arn:aws:sns:us-east-2:111122223333:alarms"]})
    for out in ("StreamName", "StreamArn", "LakeBucket", "Workgroup", "GlueDatabase",
                "CollectorUrl", "CollectorDomain"):
        t.has_output(out, {})


def test_collector_asset_ships_the_apps_rules(prod):
    stack, _ = prod
    shipped = list(pathlib.Path(stack.node.root.synth().directory).glob("asset.*/events.py"))
    assert shipped, "collector asset has no events.py"
    rules = (DEPLOY.parent / "app" / "events.py").read_bytes()
    assert any(p.read_bytes() == rules and not p.is_symlink() for p in shipped)


# ------------------------------------------------ rollups (deploy/rollups)

def _rollup_statements(t):
    for p in t.find_resources("AWS::IAM::Policy").values():
        st = p["Properties"]["PolicyDocument"]["Statement"]
        if any("athena:StartQueryExecution" in s["Action"] for s in st):
            return st
    raise AssertionError("no rollup policy")


def _actions(st):
    return {a for s in st for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])}


def test_rollups_run_hourly_and_daily_and_alarm(prod):
    stack, t = prod
    t.has_resource_properties("AWS::Lambda::Function", {
        "Handler": "handler.handler", "Runtime": "python3.13", "Timeout": 300,
        "Environment": {"Variables": {"WORKGROUP": "mysign", "DATABASE": "mysign_analytics",
                                      "LAKE_BUCKET": Match.any_value()}}})
    schedules = {r["Properties"]["ScheduleExpression"]: json.loads(r["Properties"]["Targets"][0]["Input"])
                 for r in t.find_resources("AWS::Events::Rule").values()}
    assert schedules == {"cron(5 * * * ? *)": {"mode": "hourly"},
                         "cron(30 2 * * ? *)": {"mode": "daily"}}
    t.has_resource_properties("AWS::Logs::LogGroup", {"LogGroupName": "/mysign/rollups"})
    alarms = [a["Properties"] for a in t.find_resources("AWS::CloudWatch::Alarm").values()]
    assert len(alarms) == 2 and all(a["AlarmActions"] == ["arn:aws:sns:us-east-2:111122223333:alarms"]
                                    for a in alarms)
    t.has_output("RollupFunction", {})
    shipped = {p.name for p in pathlib.Path(stack.node.root.synth().directory).glob("asset.*/*.py")}
    assert {"handler.py", "rollup_sql.py", "rollup_shape.py"} <= shipped


def test_rollup_role_is_scoped(prod):
    _, t = prod
    st = _rollup_statements(t)
    assert "dynamodb:PutItem" not in _actions(st)          # no appTableName: S3 only
    assert not any(s["Resource"] == "*" for s in st)
    blob = json.dumps(st)
    assert "workgroup/mysign" in blob and "s3:DeleteObject" not in blob
    assert "/agg/visits/*" in blob and "/raw/events/*" in blob and "/raw/events-json/*" in blob
    writes = [s for s in st if "s3:PutObject" in _actions([s])]
    assert all("/raw/" not in json.dumps(s["Resource"]) for s in writes)
    assert "APP_TABLE" not in json.dumps(t.find_resources("AWS::Lambda::Function"))


def test_rollup_writes_only_the_report_partition_of_its_stages_table():
    _, t = _synth(appTableName="mysign-Table-ABC")
    st = [s for s in _rollup_statements(t) if s["Action"] == "dynamodb:PutItem"]
    assert len(st) == 1 and "table/mysign-Table-ABC" in json.dumps(st[0]["Resource"])
    assert st[0]["Condition"] == {"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": ["_report"]}}
    # Staging never falls back to prod's key.
    _, s = _synth(stage="staging", appTableName="mysign-Table-ABC")
    assert "dynamodb:PutItem" not in _actions(_rollup_statements(s))
    _, s = _synth(stage="staging", stagingAppTableName="mysign-staging-Table-X")
    st = [x for x in _rollup_statements(s) if x["Action"] == "dynamodb:PutItem"]
    assert "table/mysign-staging-Table-X" in json.dumps(st[0]["Resource"])
    s.has_resource_properties("AWS::Lambda::Function", {"Environment": {"Variables": Match.object_like(
        {"APP_TABLE": "mysign-staging-Table-X", "WORKGROUP": "mysign-staging"})}})


def test_collector_token_is_resolved_at_deploy_never_read_at_runtime():
    """The collector is anonymous and internet-facing. It gets the origin token
    as a deploy-time reference, and no permission to read the app secret —
    which also holds the model key and the session-signing key."""
    arn = "arn:aws:secretsmanager:us-east-2:111122223333:secret:App-abc123"
    _, t = _synth(appSecretArn=arn)
    fn = next(r for r in t.find_resources("AWS::Lambda::Function").values()
              if "Anonymous analytics" in str(r["Properties"].get("Description", "")))
    env = fn["Properties"]["Environment"]["Variables"]
    assert "MYSIGN_SECRET_ARN" not in env
    assert f"{{{{resolve:secretsmanager:{arn}:SecretString:origin_token" in str(env["MYSIGN_ORIGIN_TOKEN"])
    policies = str(t.find_resources("AWS::IAM::Policy"))
    assert "secretsmanager:GetSecretValue" not in policies



def test_a_token_version_changes_the_collectors_configuration_not_just_the_reference():
    """CloudFormation skipped the collector's configuration update when the
    pinned reference was the only change, so the version rides along in plain
    text and the environment always differs after a rotation."""
    arn = "arn:aws:secretsmanager:us-east-2:111122223333:secret:App-abc123"
    def env(**kw):
        _, t = _synth(appSecretArn=arn, **kw)
        fn = next(r for r in t.find_resources("AWS::Lambda::Function").values()
                  if "Anonymous analytics" in str(r["Properties"].get("Description", "")))
        return fn["Properties"]["Environment"]["Variables"]
    plain, pinned = env(), env(originTokenVersion="v-123")
    assert "MYSIGN_ORIGIN_TOKEN_VERSION" not in plain
    assert pinned["MYSIGN_ORIGIN_TOKEN_VERSION"] == "v-123"
    assert "v-123" in str(pinned["MYSIGN_ORIGIN_TOKEN"])

def test_staging_collector_never_takes_prods_secret():
    arn = "arn:aws:secretsmanager:us-east-2:111122223333:secret:App-abc123"
    _, t = _synth(stage="staging", appSecretArn=arn)
    assert arn not in str(t.to_json())


def test_the_collector_never_takes_the_token_as_context():
    """A context value is written into the template in plain text (audit H2)."""
    with pytest.raises(SystemExit, match="origin token"):
        _synth(originToken="c59b61c2")
