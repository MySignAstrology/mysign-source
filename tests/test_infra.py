"""
The edge and the stack: the CloudFront router, the /app shell route, and what
each stage synthesizes.

The synth tests run only where aws-cdk-lib is installed (the CI build job,
a deploy machine); the router tests only where node is. Both skip otherwise.
"""
import json
import pathlib
import shutil
import subprocess
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient

import app.server as server

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
ROUTER = DEPLOY / "edge" / "router.js"


# ------------------------------------------------------------- /app (FastAPI)

@pytest.mark.parametrize("path", ["/app", "/app/", "/app/people", "/app/people/9a8c2c7887ac"])
def test_app_shell_serves_the_app(path):
    r = TestClient(server.app).get(path)
    assert r.status_code == 200
    assert r.text == (ROOT / "app" / "static" / "index.html").read_text()


def test_app_shell_does_not_swallow_files():
    assert TestClient(server.app).get("/app/missing.js").status_code == 404


# ------------------------------------------------------------- router.js

_HARNESS = """
const fs = require('fs');
const handler = new Function(fs.readFileSync(process.argv[1], 'utf8') + '; return handler;')();
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
console.log(JSON.stringify(cases.map(c => handler({request: c}))));
"""


def _route(*requests):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    reqs = [{"method": "GET", "uri": r.get("uri", "/"), "headers": {},
             "querystring": r.get("qs", {}), "cookies": r.get("cookies", {})} for r in requests]
    out = subprocess.run([node, "-e", _HARNESS, str(ROUTER)], input=json.dumps(reqs),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _uri(res):
    assert "statusCode" not in res, res
    return res["uri"]


def _location(res):
    assert res["statusCode"] == 302, res
    return res["headers"]["location"]["value"]


def test_router_root_is_the_site_for_strangers():
    assert _uri(_route({"uri": "/"})[0]) == "/index.html"
    # An unrelated query (utm tags) is not app state.
    assert _uri(_route({"uri": "/", "qs": {"utm_source": {"value": "tiktok"}}})[0]) == "/index.html"


def test_router_signed_in_root_goes_to_the_app_keeping_the_query():
    res = _route({"uri": "/", "cookies": {"ms_session": {"value": "abc"}}},
                 {"uri": "/", "cookies": {"ms_session": {"value": "abc"}},
                  "qs": {"utm_source": {"value": "push"}}})
    assert _location(res[0]) == "/app"
    assert _location(res[1]) == "/app?utm_source=push"


@pytest.mark.parametrize("param", ["invite", "p", "sky"])
def test_router_app_links_go_to_the_app(param):
    res = _route({"uri": "/", "qs": {param: {"value": "x%2By"}, "ref": {"value": ""}}})[0]
    # Values arrive still encoded; re-encoding would turn %2B into %252B.
    assert _location(res) == f"/app?{param}=x%2By&ref"


def test_router_keeps_repeated_params():
    qs = {"p": {"value": "a", "multiValue": [{"value": "a"}, {"value": "b"}]}}
    assert _location(_route({"uri": "/", "qs": qs})[0]) == "/app?p=a&p=b"


@pytest.mark.parametrize("uri,want", [
    ("/app", "/app/index.html"),
    ("/app/", "/app/index.html"),
    ("/app/people/9a8c", "/app/index.html"),
    ("/app/icon.png", "/app/icon.png"),
    ("/privacy", "/privacy.html"),
    ("/terms/", "/terms.html"),
    ("/accuracy", "/accuracy.html"),
    ("/ig", "/ig.html"),
    ("/ig/", "/ig.html"),
    ("/check", "/check.html"),
    ("/gifts", "/gifts/index.html"),
    ("/gifts/", "/gifts/index.html"),
    ("/gifts/scorpio", "/gifts/scorpio.html"),
    ("/gifts/pin-scorpio.jpg", "/gifts/pin-scorpio.jpg"),
    ("/giftshop", "/giftshop"),
    ("/static/icons/icon-192.png", "/static/icons/icon-192.png"),
    ("/application", "/application"),
    *[(f"/{f}", f"/{f}") for f in server.SITE_FILES],      # the website's own files
])
def test_router_rewrites(uri, want):
    assert _uri(_route({"uri": uri})[0]) == want


@pytest.mark.parametrize("uri", ["/shop", "/shop/"])
def test_router_shop_is_our_page(uri):
    res = _route({"uri": uri, "qs": {"utm_source": {"value": "instagram"}}})[0]
    assert _uri(res) == "/shop.html"


@pytest.mark.parametrize("uri,qs,want", [
    ("/shop/products/zodiac-candles", {}, "https://shop.mysignapp.com/products/zodiac-candles"),
    ("/shop/cart", {"utm_source": {"value": "instagram"}}, "https://shop.mysignapp.com/cart?utm_source=instagram"),
])
def test_router_deeper_shop_links_go_to_the_store(uri, qs, want):
    assert _location(_route({"uri": uri, "qs": qs})[0]) == want


def test_router_shop_prefix_is_exact():
    assert _uri(_route({"uri": "/shopping"})[0]) == "/shopping"


def test_server_shop_page_matches_the_edge():
    c = TestClient(server.app, follow_redirects=False)
    r = c.get("/shop?utm_source=ig")
    assert r.status_code == 200
    assert r.text == (ROOT / "site" / "shop.html").read_text()
    r = c.get("/shop/products/zodiac-candles?utm_source=ig")
    assert r.status_code == 302
    assert r.headers["location"] == "https://shop.mysignapp.com/products/zodiac-candles?utm_source=ig"


def test_shop_checkout_is_shopify():
    page = (ROOT / "site" / "shop.html").read_text()
    assert 'const STORE = "https://shop.mysignapp.com"' in page
    assert 'STORE + "/cart/"' in page
    assert '<script src="/shop-catalog.js"' in page


def test_shop_catalog_only_has_what_can_be_bought(tmp_path, monkeypatch):
    import importlib.util
    import json
    spec = importlib.util.spec_from_file_location("shop_catalog", ROOT / "tools" / "shop_catalog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    def v(i, sign, ok):
        return {"id": i, "title": sign, "option1": sign, "price": "25.00", "available": ok}

    feed = {"products": [
        {"handle": "pendant", "title": "Pendant", "options": [{"name": "Sign"}],
         "variants": [v(1, "Aries", True), v(2, "Scorpio", False)]},
        {"handle": "gone", "title": "Aries - Charm", "options": [{"name": "Title"}],
         "variants": [v(3, "Default Title", False)]},
    ]}
    src = tmp_path / "products.json"
    src.write_text(json.dumps(feed))
    monkeypatch.setattr(mod, "OUT", tmp_path / "shop-catalog.js")
    mod.main(str(src))
    out = (tmp_path / "shop-catalog.js").read_text()
    items = json.loads(out[out.index("["):out.rindex("]") + 1])
    assert [p["handle"] for p in items] == ["pendant"]
    assert [x["id"] for x in items[0]["variants"]] == [1]
    # The page drops anything sold out too, in case stock moved after the bake.
    assert "p.variants.filter(v => v.available)" in (ROOT / "site" / "shop.html").read_text()


def test_router_never_redirects_inside_the_app():
    res = _route({"uri": "/app", "cookies": {"ms_session": {"value": "abc"}},
                  "qs": {"invite": {"value": "z"}}})[0]
    assert _uri(res) == "/app/index.html"


# ------------------------------------------------------------- synth

# What prod already holds. A changed logical ID here is a replacement — for the
# RETAIN ones, a new empty resource beside an orphaned full one.
PROD_STATEFUL = {
    "TableCD117FA1": "AWS::DynamoDB::Table",
    "SecretA720EF05": "AWS::SecretsManager::Secret",
    "AppLogsC5DF83A6": "AWS::Logs::LogGroup",
    "CronLogsE8DA42A8": "AWS::Logs::LogGroup",
    "ForwarderLogs55C54B84": "AWS::Logs::LogGroup",
    "InboxA8E05DC3": "AWS::S3::Bucket",
    "Places": "AWS::Location::PlaceIndex",
    "MailDomainBA8757BD": "AWS::SES::EmailIdentity",
    "Edge7038544F": "AWS::CloudFront::Distribution",
    "FnFunctionUrl8C1B0DE8": "AWS::Lambda::Url",
}


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    cdk = pytest.importorskip("aws_cdk")
    from aws_cdk.assertions import Template
    sys.path.insert(0, str(DEPLOY))
    from mysign_stack import MySignStack
    from stages import Stage

    build = tmp_path_factory.mktemp("build")
    with zipfile.ZipFile(build / "mysign.zip", "w") as z:
        z.writestr("run.sh", "#!/bin/sh\n")
    (build / "site" / "app").mkdir(parents=True)
    for f in ("index.html", "app/index.html", "sw.js", "manifest.webmanifest"):
        (build / "site" / f).write_text("x")

    base = json.loads((DEPLOY / "cdk.json").read_text())["context"]
    # cdk.json carries the live deployment's switches (certificate, collector,
    # S3 statics); each test turns on only what it needs, starting from off.
    for key in ("staticFromS3", "collectorDomain", "stagingCertificateArn", "adminCertificateArn"):
        base.pop(key, None)
    base["stages"] = {name: {k: v for k, v in cfg.items() if k != "collectorDomain"}
                      for name, cfg in base.get("stages", {}).items()}

    def run(**context):
        app = cdk.App(context={**base, **context})
        stage = Stage(app.node)
        stack = MySignStack(app, stage.get("stackName"), stage=stage, build_dir=str(build),
                            env=cdk.Environment(account="111111111111", region="us-east-2"))
        return Template.from_stack(stack).to_json()
    return run


def _of(t, kind):
    return {k: v for k, v in t["Resources"].items() if v["Type"] == kind}


def _dist(t):
    (d,) = _of(t, "AWS::CloudFront::Distribution").values()
    return d["Properties"]["DistributionConfig"]


def test_prod_keeps_every_stateful_logical_id_and_name(synth):
    t = synth()
    res = t["Resources"]
    for lid, kind in PROD_STATEFUL.items():
        assert res.get(lid, {}).get("Type") == kind, lid
    for lid in ("TableCD117FA1", "SecretA720EF05", "AppLogsC5DF83A6", "CronLogsE8DA42A8",
                "ForwarderLogs55C54B84"):
        assert res[lid]["DeletionPolicy"] == "Retain", lid
    assert res["TableCD117FA1"]["Properties"]["DeletionProtectionEnabled"] is True
    names = {v["Properties"]["LogGroupName"] for v in _of(t, "AWS::Logs::LogGroup").values()}
    assert names == {"/mysign/app", "/mysign/cron", "/mysign/mailforward"}
    assert res["Places"]["Properties"]["IndexName"] == "mysign-places"
    assert _of(t, "AWS::Backup::BackupPlan") and _of(t, "AWS::SES::ReceiptRuleSet")


def test_prod_edge_is_unchanged_with_the_flags_off(synth):
    t = synth()
    d = _dist(t)
    assert "CacheBehaviors" not in d
    assert "FunctionAssociations" not in d["DefaultCacheBehavior"]
    assert len(d["Origins"]) == 1
    assert sorted(d["Aliases"]) == ["mysignapp.com", "www.mysignapp.com"]
    for kind in ("AWS::S3::Bucket", "AWS::CloudFront::Function", "AWS::Lambda::Alias",
                 "Custom::CDKBucketDeployment"):
        assert len(_of(t, kind)) == (1 if kind == "AWS::S3::Bucket" else 0), kind


def test_staging_is_separate_and_sends_through_prods_identity(synth):
    t = synth(stage="staging", stagingCertificateArn="arn:aws:acm:us-east-1:1:certificate/x")
    names = {v["Properties"]["LogGroupName"] for v in _of(t, "AWS::Logs::LogGroup").values()}
    assert names == {"/mysign-staging/app", "/mysign-staging/cron"}
    for kind in ("AWS::SES::ReceiptRuleSet", "AWS::SES::EmailIdentity",
                 "AWS::Backup::BackupPlan"):
        assert not _of(t, kind), kind
    (table,) = _of(t, "AWS::DynamoDB::Table").values()
    assert table["DeletionPolicy"] == "Delete"
    (cfg,) = _of(t, "AWS::SES::ConfigurationSet").values()
    assert cfg["Properties"]["Name"] == "mysign-staging"
    policies = json.dumps(_of(t, "AWS::IAM::Policy"))
    assert ":identity/mysignapp.com" in policies
    namespaces = {v["Properties"]["MetricTransformations"][0]["MetricNamespace"]
                  for v in _of(t, "AWS::Logs::MetricFilter").values()}
    assert namespaces == {"MySign/staging"}
    records = {v["Properties"]["Name"] for v in _of(t, "AWS::Route53::RecordSet").values()}
    assert records == {"staging.mysignapp.com."}
    assert not any(v["Properties"].get("ReservedConcurrentExecutions")
                   for v in _of(t, "AWS::Lambda::Function").values())


def test_staging_never_inherits_prods_certificate_or_waf(synth):
    t = synth(stage="staging", webAclArn="arn:aws:wafv2:us-east-1:1:global/webacl/prod/x")
    assert not _of(t, "AWS::CloudFront::Distribution")


def test_staging_serves_static_from_s3_and_dynamic_from_lambda(synth):
    t = synth(stage="staging", stagingCertificateArn="arn:aws:acm:us-east-1:1:certificate/x")
    d = _dist(t)
    assert d["Aliases"] == ["staging.mysignapp.com"]
    by_path = {b["PathPattern"]: b for b in d["CacheBehaviors"]}
    assert set(by_path) == {"/api/*", "/bt/*", "/ops*", "/couples*", "/e", "/static/*", "/sw.js",
                            "/manifest.webmanifest"}
    lambda_origin = by_path["/api/*"]["TargetOriginId"]
    assert {by_path[p]["TargetOriginId"] for p in ("/bt/*", "/ops*", "/couples*", "/e")} == {lambda_origin}
    s3_origin = d["DefaultCacheBehavior"]["TargetOriginId"]
    assert s3_origin != lambda_origin
    assert {by_path[p]["TargetOriginId"] for p in ("/static/*", "/sw.js")} == {s3_origin}
    assert len(d["DefaultCacheBehavior"]["FunctionAssociations"]) == 1
    origins = {o["Id"]: o for o in d["Origins"]}
    assert "x-origin-auth" in json.dumps(origins[lambda_origin])
    assert "OriginAccessControlId" in origins[s3_origin]
    assert len(_of(t, "Custom::CDKBucketDeployment")) == 4
    (fn,) = _of(t, "AWS::CloudFront::Function").values()
    assert fn["Properties"]["FunctionConfig"]["Runtime"] == "cloudfront-js-2.0"


# ------------------------------------------------------------- admin platform

def _admin_route(uri, **headers):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    req = {"method": "GET", "uri": uri, "headers": headers, "querystring": {}, "cookies": {}}
    out = subprocess.run(["node", "-e", _HARNESS, str(DEPLOY / "edge" / "admin.js")],
                         input=json.dumps([req]), capture_output=True, text=True, check=True)
    return json.loads(out.stdout)[0]


@pytest.mark.parametrize("uri,to", [
    ("/", "/admin"), ("/manage", "/manage"), ("/manage/", "/manage"), ("/analytics", "/analytics"),
    ("/api/ops/session", "/api/ops/session"), ("/api/auth/request-admin", "/api/auth/request-admin"),
    ("/api/auth/verify-admin", "/api/auth/verify-admin"),
    ("/social/", "/"), ("/social/assets/x.js", "/assets/x.js"), ("/social/api/posts", "/api/posts"),
])
def test_admin_router_passes(uri, to):
    assert _uri(_admin_route(uri)) == to


@pytest.mark.parametrize("uri", ["/app", "/api/state", "/api/auth/request", "/api/auth/verify", "/admin",
                                 "/api/message", "/api/opsx", "/socialx", "/static/ops.html", "/index.html",
                                 "/api/auth/request-admin/x", "/analyticsx", "//", "/e"])
def test_admin_router_refuses_the_rest(uri):
    assert _admin_route(uri)["statusCode"] == 404


def test_admin_router_marks_social_itself():
    r = _admin_route("/social/x", **{"x-admin-edge": {"value": "spoofed"}})
    assert r["headers"]["x-admin-edge"]["value"] == "1"
    assert _admin_route("/social")["headers"]["location"]["value"] == "/social/"


def test_admin_is_off_until_it_has_a_certificate(synth):
    t = synth(adminCertificateArn=None)
    assert "MYSIGN_ADMIN_URL" not in json.dumps(t)
    assert len(_of(t, "AWS::CloudFront::Distribution")) == 1


def test_admin_distribution(synth):
    cert = "arn:aws:acm:us-east-1:1:certificate/admin"
    t = synth(adminCertificateArn=cert)
    (d,) = [v["Properties"]["DistributionConfig"] for v in _of(t, "AWS::CloudFront::Distribution").values()
            if v["Properties"]["DistributionConfig"]["Aliases"] == ["admin.mysignapp.com"]]
    assert "webacl/mysign-edge" in d["WebACLId"]
    assert d["ViewerCertificate"]["AcmCertificateArn"] == cert
    default, (social,) = d["DefaultCacheBehavior"], d["CacheBehaviors"]
    assert social["PathPattern"] == "/social*"
    assert len(default["FunctionAssociations"]) == len(social["FunctionAssociations"]) == 1
    origins = {o["Id"]: o for o in d["Origins"]}
    assert "x-origin-auth" in json.dumps(origins[default["TargetOriginId"]])
    # The app's origin token never goes to the other stack.
    assert "x-origin-auth" not in json.dumps(origins[social["TargetOriginId"]])
    fn = _fns(t)["Fn"]
    assert fn["Properties"]["Environment"]["Variables"]["MYSIGN_ADMIN_URL"] == "https://admin.mysignapp.com"
    records = [v["Properties"]["Name"] for v in _of(t, "AWS::Route53::RecordSet").values()]
    assert "admin.mysignapp.com." in json.dumps(records)


def test_staging_has_no_admin(synth):
    t = synth(stage="staging", stagingCertificateArn="arn:aws:acm:us-east-1:1:certificate/x",
              adminCertificateArn="arn:aws:acm:us-east-1:1:certificate/admin")
    assert "admin.mysignapp.com" not in json.dumps(t)


# ------------------------------------------------------------- analytics

def _fns(t):
    """{"Fn"|"Cron": function} — the app and the scheduler."""
    out = {}
    for lid, f in _of(t, "AWS::Lambda::Function").items():
        for name in ("Fn", "Cron"):
            if lid.startswith(name) and not lid.startswith(name + "Function"):
                out.setdefault(name, f)
    return out


def _role_policies(t, fn):
    role = fn["Properties"]["Role"]["Fn::GetAtt"][0]
    return [p for p in _of(t, "AWS::IAM::Policy").values()
            if {"Ref": role} in p["Properties"]["Roles"]]


@pytest.mark.parametrize("stage,stream", [("prod", "mysign-events"),
                                          ("staging", "mysign-events-staging")])
def test_app_and_cron_may_put_to_their_stages_stream(synth, stage, stream):
    t = synth(stage=stage)
    fns = _fns(t)
    assert set(fns) == {"Fn", "Cron"}
    for name, fn in fns.items():
        assert fn["Properties"]["Environment"]["Variables"]["MYSIGN_EVENTS_STREAM"] == stream
        stmts = [s for p in _role_policies(t, fn)
                 for s in p["Properties"]["PolicyDocument"]["Statement"]
                 if "firehose:PutRecordBatch" in json.dumps(s["Action"])]
        assert len(stmts) == 1, name
        assert json.dumps(stmts[0]["Resource"]).endswith(f':deliverystream/{stream}"]]}}'), name


@pytest.mark.parametrize("stage", ["prod", "staging"])
def test_app_and_cron_share_the_daily_ai_budget(synth, stage):
    """Unset, engine/costs.global_over draws no line at all; both functions
    charge the same day, so both must know it."""
    fns = _fns(synth(stage=stage))
    budgets = {n: f["Properties"]["Environment"]["Variables"].get("MYSIGN_DAILY_BUDGET_USD")
               for n, f in fns.items()}
    assert budgets == {"Fn": "20", "Cron": "20"}
    # Above the alarm, so the alarm is the warning and the budget the floor.
    assert float(budgets["Fn"]) > float(json.loads((DEPLOY / "cdk.json").read_text())
                                        ["context"]["modelSpendDailyUsd"])


def test_the_stream_name_is_the_analytics_stacks(tmp_path_factory):
    cdk = pytest.importorskip("aws_cdk")
    from aws_cdk.assertions import Template
    sys.path.insert(0, str(DEPLOY))
    from analytics_stack import MySignAnalyticsStack
    for stage, want in (("prod", "mysign-events"), ("staging", "mysign-events-staging")):
        app = cdk.App(context={"stage": stage},
                      outdir=str(tmp_path_factory.mktemp(f"analytics-{stage}")))
        t = Template.from_stack(MySignAnalyticsStack(
            app, env=cdk.Environment(account="111111111111", region="us-east-2"))).to_json()
        (s,) = _of(t, "AWS::KinesisFirehose::DeliveryStream").values()
        assert s["Properties"]["DeliveryStreamName"] == want


def test_analytics_events_can_be_left_off(synth):
    t = synth(analyticsEvents="false")
    for fn in _fns(t).values():
        assert "MYSIGN_EVENTS_STREAM" not in fn["Properties"]["Environment"]["Variables"]
    assert "firehose:" not in json.dumps(_of(t, "AWS::IAM::Policy"))


COLLECT = "abc123.lambda-url.us-east-2.on.aws"
STAGING_CERT = "arn:aws:acm:us-east-1:1:certificate/x"


def _collect_behavior(d):
    return next((b for b in d.get("CacheBehaviors", []) if b["PathPattern"] == "/e"), None)


def test_without_a_collector_domain_e_is_untouched(synth):
    assert "CacheBehaviors" not in _dist(synth())
    assert "MYSIGN_COLLECTOR" not in json.dumps(_fns(synth())["Fn"])
    assert not _of(synth(), "AWS::CloudFront::OriginRequestPolicy")


@pytest.mark.parametrize("context", [
    {"collectorDomain": f"https://{COLLECT}/"},                                  # prod, flag off
    {"collectorDomain": COLLECT, "staticFromS3": "true"},                       # prod, flag on
    {"stage": "staging", "stagingCertificateArn": STAGING_CERT,
     "stagingCollectorDomain": COLLECT},                                         # staging
])
def test_a_collector_domain_routes_e_to_the_collector(synth, context):
    t = synth(**context)
    d = _dist(t)
    e = _collect_behavior(d)
    assert e is not None
    origins = {o["Id"]: o for o in d["Origins"]}
    o = origins[e["TargetOriginId"]]
    assert o["DomainName"] == COLLECT
    assert o["CustomOriginConfig"]["OriginProtocolPolicy"] == "https-only"
    assert [h["HeaderName"] for h in o["OriginCustomHeaders"]] == ["x-origin-auth"]
    assert e["TargetOriginId"] != d["DefaultCacheBehavior"]["TargetOriginId"]
    assert len(e["AllowedMethods"]) == 7
    assert e["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"       # CachingDisabled
    assert "FunctionAssociations" not in e
    (pol,) = _of(t, "AWS::CloudFront::OriginRequestPolicy").values()
    cfg = pol["Properties"]["OriginRequestPolicyConfig"]
    assert e["OriginRequestPolicyId"] == {"Ref": next(iter(
        _of(t, "AWS::CloudFront::OriginRequestPolicy")))}
    assert sorted(cfg["HeadersConfig"]["Headers"]) == sorted([
        "CloudFront-Viewer-Country", "CloudFront-Is-Mobile-Viewer",
        "CloudFront-Is-Tablet-Viewer", "CloudFront-Is-Desktop-Viewer",
        "CloudFront-Is-SmartTV-Viewer", "User-Agent", "Referer", "Sec-GPC", "DNT"])
    assert cfg["HeadersConfig"]["HeaderBehavior"] == "whitelist"
    assert cfg["QueryStringsConfig"]["QueryStringBehavior"] == "none"
    assert cfg["CookiesConfig"]["CookieBehavior"] == "none"
    assert _fns(t)["Fn"]["Properties"]["Environment"]["Variables"]["MYSIGN_COLLECTOR"] == "1"


def test_staging_never_inherits_prods_collector(synth):
    t = synth(stage="staging", stagingCertificateArn=STAGING_CERT, collectorDomain=COLLECT)
    d = _dist(t)
    assert _collect_behavior(d)["TargetOriginId"] == next(
        b["TargetOriginId"] for b in d["CacheBehaviors"] if b["PathPattern"] == "/api/*")


def test_provisioned_concurrency_moves_the_url_to_the_alias(synth):
    t = synth(provisionedConcurrency="5")
    (alias,) = _of(t, "AWS::Lambda::Alias").values()
    assert alias["Properties"]["Name"] == "live"
    (url,) = _of(t, "AWS::Lambda::Url").values()
    assert url["Properties"]["Qualifier"] == "live"
    (target,) = _of(t, "AWS::ApplicationAutoScaling::ScalableTarget").values()
    actions = {a["ScheduledActionName"]: a for a in target["Properties"]["ScheduledActions"]}
    assert len(actions) == 2
    caps = sorted((a["Schedule"], a["ScalableTargetAction"]["MaxCapacity"])
                  for a in actions.values())
    assert caps == [("cron(0 12 * * ? *)", 5), ("cron(0 5 * * ? *)", 0)]


def test_deploy_role_trusts_only_main_and_production(synth):
    import aws_cdk as cdk
    from aws_cdk.assertions import Template
    from cicd_stack import CicdStack

    base = json.loads((DEPLOY / "cdk.json").read_text())["context"]
    stack = CicdStack(cdk.App(context=base), "cicd",
                      env=cdk.Environment(account="111111111111", region="us-east-2"))
    t = Template.from_stack(stack).to_json()
    (role,) = _of(t, "AWS::IAM::Role").values()
    (stmt,) = role["Properties"]["AssumeRolePolicyDocument"]["Statement"]
    subs = stmt["Condition"]["StringLike"]["token.actions.githubusercontent.com:sub"]
    assert all(s.endswith((":ref:refs/heads/main", ":environment:production")) for s in subs)
    assert "repo:MySignAstrology/mysign:ref:refs/heads/main" in subs
    assert "repo:MySignAstrology@320017987/mysign@1343248366:environment:production" in subs
    assert not any("*" in s for s in subs)
    (policy,) = _of(t, "AWS::IAM::Policy").values()
    (grant,) = policy["Properties"]["PolicyDocument"]["Statement"]
    assert grant["Action"] == "sts:AssumeRole"
    assert all(":role/cdk-*-" in r for r in grant["Resource"])


def test_uptime_check_loads_the_live_pages_and_pages_on_two_failures(synth):
    t = synth()
    fn = next(v for v in _of(t, "AWS::Lambda::Function").values()
              if "Loads the live site" in str(v["Properties"].get("Description", "")))
    urls = fn["Properties"]["Environment"]["Variables"]["URLS"].split(",")
    assert urls == ["https://mysignapp.com/", "https://mysignapp.com/app",
                    "https://mysignapp.com/api/state"]
    alarm = next(v for k, v in _of(t, "AWS::CloudWatch::Alarm").items() if k.startswith("UptimeAlarm"))
    assert alarm["Properties"]["EvaluationPeriods"] == 2 and alarm["Properties"]["DatapointsToAlarm"] == 2
    assert alarm["Properties"]["AlarmActions"], "it has to tell someone"


def test_ai_spend_and_browser_errors_alarm(synth):
    t = synth()
    alarms = {k: v["Properties"] for k, v in _of(t, "AWS::CloudWatch::Alarm").items()}
    daily = next(v for k, v in alarms.items() if k.startswith("ModelSpendDailyAlarm"))
    assert daily["Threshold"] == 5 and daily["Period"] == 86400 and daily["AlarmActions"]
    assert any(k.startswith("ModelSpendHourlyAlarm") for k in alarms)
    assert any(k.startswith("ClientErrorsAlarm") for k in alarms)
    filters = [v["Properties"] for v in _of(t, "AWS::Logs::MetricFilter").values()]
    spend = [f for f in filters if f["MetricTransformations"][0]["MetricName"] == "ModelSpendUSD"]
    assert len(spend) == 2 and spend[0]["MetricTransformations"][0]["MetricValue"] == "$.usd"


def test_the_firewall_is_attached_to_prod_only(synth):
    prod = synth()
    (dist,) = _of(prod, "AWS::CloudFront::Distribution").values()
    assert "webacl/mysign-edge" in dist["Properties"]["DistributionConfig"]["WebACLId"]


# ------------------------------------------- security audit H2 / H3

def test_prod_refuses_to_synth_without_a_firewall(synth):
    """CloudFormation reads an unset WebACLId as "detach": a prod deploy without
    one silently removes a WAF that was attached by hand (audit H3)."""
    with pytest.raises(SystemExit, match="without a WAF"):
        synth(webAclArn="")
    # Saying so explicitly is allowed, and the distribution then has none.
    assert "WebACLId" not in _dist(synth(webAclArn="", allowNoWaf="true"))


def test_a_regional_web_acl_is_refused_at_synth(synth):
    with pytest.raises(SystemExit, match="CLOUDFRONT-scope"):
        synth(webAclArn="arn:aws:wafv2:us-east-2:111111111111:regional/webacl/x/abc")


def test_the_origin_token_is_never_a_context_value(synth):
    """`-c originToken=` once wrote the live token into a committed template
    (audit H2). Neither stack takes it now; the template holds a reference."""
    with pytest.raises(SystemExit, match="origin token"):
        synth(originToken="c59b61c2")
    with pytest.raises(SystemExit, match="origin token"):
        synth(stage="staging", stagingOriginToken="c59b61c2")
    t = json.dumps(synth())
    assert "{{resolve:secretsmanager:" in t and "origin_token" in t


def test_synth_output_is_never_committed():
    lines = (ROOT / ".gitignore").read_text().splitlines()
    assert "/deploy/cdk.out/" in lines
    tracked = subprocess.run(["git", "ls-files", "deploy/cdk.out"], cwd=ROOT,
                             capture_output=True, text=True).stdout
    assert tracked.strip() == ""
    cfg = json.loads((DEPLOY / "cdk.json").read_text())["context"]
    assert "originToken" not in cfg
    assert "webacl/" in cfg["webAclArn"]


def test_the_waf_rate_limits_code_checks_and_model_routes():
    rules = json.loads((DEPLOY / "waf" / "rules.json").read_text())
    by = {r["Name"]: r for r in rules}
    assert sorted(r["Priority"] for r in rules) == list(range(len(rules)))

    def paths(name):
        st = by[name]["Statement"]["RateBasedStatement"]["ScopeDownStatement"]["OrStatement"]
        return {s["ByteMatchStatement"]["SearchString"] for s in st["Statements"]}
    assert {"/api/auth/verify", "/api/account/identifier/verify"} <= paths("auth-verify-rate")
    assert {"/api/message", "/api/placement/", "/reading"} <= paths("model-rate")
    for name in ("auth-verify-rate", "model-rate"):
        assert by[name]["Action"] == {"Block": {}}
        assert by[name]["Priority"] < by["general-rate"]["Priority"]


def test_a_tripped_sign_in_ceiling_alarms(synth):
    """app/auth.py logs auth.cap when a service-wide email or verify cap trips."""
    t = synth()
    alarms = set(_of(t, "AWS::CloudWatch::Alarm"))
    assert any(k.startswith("AuthCapAlarm") for k in alarms)
    filters = json.dumps([v["Properties"] for v in _of(t, "AWS::Logs::MetricFilter").values()])
    assert "auth.cap" in filters


def test_a_token_version_forces_cloudfront_and_the_app_to_re_read_the_token(synth):
    """A dynamic reference is only re-resolved when its text changes; after a
    rotation the deploy pins it to the secret's new version."""
    plain, pinned = synth(), synth(originTokenVersion="v-123")
    def header(t):
        (d,) = _of(t, "AWS::CloudFront::Distribution").values()
        return json.dumps(d["Properties"]["DistributionConfig"]["Origins"])
    assert "v-123" not in header(plain) and "v-123" in header(pinned)
    fn = next(v for k, v in _of(pinned, "AWS::Lambda::Function").items() if k.startswith("Fn"))
    assert fn["Properties"]["Environment"]["Variables"]["MYSIGN_ORIGIN_TOKEN_VERSION"] == "v-123"


def test_server_ig_page_matches_the_edge():
    c = TestClient(server.app, follow_redirects=False)
    r = c.get("/ig")
    assert r.status_code == 200
    assert r.text == (ROOT / "site" / "ig.html").read_text()
