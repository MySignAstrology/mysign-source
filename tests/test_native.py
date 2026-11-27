"""The iPhone shell: the rules App Review and iOS hold it to, checked in the repo."""
import json
import plistlib
import re
from pathlib import Path
from types import SimpleNamespace

from app.server import DEVICE_COOKIE, _device_id

NATIVE = Path(__file__).resolve().parent.parent / "native"


def _request(headers=None, cookies=None):
    return SimpleNamespace(headers=headers or {}, cookies=cookies or {})


def test_the_app_ships_its_page_rather_than_loading_the_site():
    config = json.loads((NATIVE / "capacitor.config.json").read_text())
    assert config["appId"] == "app.mysign.ios"
    assert config["webDir"] == "www"
    assert "server" not in config      # App Review 4.2.3 rejects a remote URL


def test_ios_27_gets_the_scene_lifecycle_or_the_app_stops_at_launch():
    info = plistlib.loads((NATIVE / "ios/App/App/Info.plist").read_bytes())
    scene = info["UIApplicationSceneManifest"]["UISceneConfigurations"]["UIWindowSceneSessionRoleApplication"][0]
    assert scene["UISceneDelegateClassName"].endswith(".SceneDelegate")
    assert scene["UISceneStoryboardFile"] == "Main"


def test_the_app_is_phone_only_and_portrait():
    info = plistlib.loads((NATIVE / "ios/App/App/Info.plist").read_bytes())
    assert info["UISupportedInterfaceOrientations"] == ["UIInterfaceOrientationPortrait"]
    assert "TARGETED_DEVICE_FAMILY = 1;" in (NATIVE / "ios/App/App.xcodeproj/project.pbxproj").read_text()


def test_the_permission_prompts_say_why_and_never_say_psychic():
    # Every reviewer reads these in the iOS prompt. "Psychic" invites a 1.1.6 /
    # 4.3 read of the app as fortune telling (docs/app-store-launch.md).
    info = plistlib.loads((NATIVE / "ios/App/App/Info.plist").read_bytes())
    prompts = {k: v for k, v in info.items() if k.endswith("UsageDescription")}
    assert {"NSMicrophoneUsageDescription", "NSSpeechRecognitionUsageDescription"} <= prompts.keys()
    for text in prompts.values():
        assert text.startswith("My Sign ")
        assert not re.search(r"psychic|fortune|predict", text, re.I)


def test_the_page_never_shows_or_reads_out_psychic():
    # Her reader is a character with a crystal ball, but App Review reads the
    # word itself as fortune telling. Code names (Psychic, /api/psychic) are fine.
    page = (NATIVE.parent / "app/static/index.html").read_text()
    labels = re.findall(r'(?:aria-label|title|placeholder|alt)="([^"]*)"', page)
    shown = re.sub(r"<script\b.*?</script>|<style\b.*?</style>|<!--.*?-->", "", page, flags=re.S)
    shown = re.sub(r"<[^>]+>", " ", shown)
    assert not [t for t in labels if re.search("psychic", t, re.I)]
    assert not re.search("psychic", shown, re.I)


def test_the_device_header_counts_only_for_the_app():
    assert _device_id(_request({"x-client": "ios-app", "x-device": "phone"})) == "phone"
    assert _device_id(_request({"x-device": "phone"}, {DEVICE_COOKIE: "browser"})) == "browser"


def test_a_clean_checkout_has_every_file_the_xcode_project_copies():
    # Ignored by Capacitor's template, and only `cap add` writes it. Without it
    # the archive fails on anything but the machine that generated the project.
    assert (NATIVE / "ios/App/App/config.xml").is_file()


def test_privacy_manifest_matches_the_app_privacy_label():
    """Apple's privacy report is built from this manifest, so it must declare
    what the App Store label declares: linked to the user, never tracking."""
    m = plistlib.loads((NATIVE / "ios/App/App/PrivacyInfo.xcprivacy").read_bytes())
    assert m["NSPrivacyTracking"] is False and m["NSPrivacyTrackingDomains"] == []
    prefix = "NSPrivacyCollectedDataType"
    declared = {d[prefix][len(prefix):] for d in m["NSPrivacyCollectedDataTypes"]}
    assert declared == {"EmailAddress", "Name", "PhoneNumber", "OtherUserContent",
                        "CustomerSupport", "UserID", "DeviceID",
                        "ProductInteraction", "PurchaseHistory"}
    for d in m["NSPrivacyCollectedDataTypes"]:
        assert d[prefix + "Linked"] is True and d[prefix + "Tracking"] is False
        assert d[prefix + "Purposes"]


ROOT = NATIVE.parent
SHELL_PAGES = ["privacy", "terms", "refunds", "support", "cookies", "accuracy", "couples"]


def test_pages_the_app_opens_tag_themselves_as_in_app():
    # App Review 3.1.1: from the in-app browser no link may reach the web app
    # or the home page's prices, both of which lead to Stripe Checkout.
    bridge = (NATIVE / "bridge.js").read_text()
    assert "searchParams.set('app', '1')" in bridge
    for name in SHELL_PAGES:
        assert '<script src="/static/inapp.js"></script>' in (ROOT / "app/static" / f"{name}.html").read_text(), name
    for name in ("shop", "check"):
        assert '<script src="/static/inapp.js"></script>' in (ROOT / "site" / f"{name}.html").read_text(), name


def test_a_paid_report_is_not_sold_inside_the_app():
    # App Review 3.1.1: a PDF report is digital content, so the in-app browser
    # must not offer it or lead to its page in the shop. Physical gifts stay.
    js = (ROOT / "app/static/inapp.js").read_text()
    digital = re.compile(re.search(r"const DIGITAL = /(.+)/i;", js).group(1).replace("\\/", "/"), re.I)
    assert digital.search("shop.mysignapp.com/products/couple-compatibility-report")
    assert digital.search("shop.mysignapp.com/products/2027-Year-Report")
    assert not digital.search("shop.mysignapp.com/products/zodiac-pillar-candle")
    assert not digital.search("shop.mysignapp.com/collections/all")
    assert 'html.inapp [data-digital], html.inapp [data-section="check_report_offer"]' in js
    shop = (ROOT / "site/shop.html").read_text()
    assert ".filter(p => !(INAPP && isDigital(p)))" in shop


def test_the_in_app_allowlist_never_includes_the_app_or_home():
    js = (ROOT / "app/static/inapp.js").read_text()
    safe = re.search(r"const SAFE = /(.+)/;", js).group(1)
    pattern = re.compile(safe.replace("\\/", "/"))
    for path in ["/", "/app", "/app/settings", "/check", "/ig", "/gifts"]:
        assert not pattern.search(path), path
    for path in ["/privacy", "/terms", "/support", "/couples/obamas", "/shop"]:
        assert pattern.search(path), path


def test_the_shell_promises_nothing_that_isnt_there():
    # 2.1 / 2.3.1: no "coming soon" or "in an update" inside the App Store shell.
    page = (ROOT / "app/static/index.html").read_text()
    for gone in ("arrive in an app update", "needs the next app update", "label:'Coming soon'"):
        assert gone not in page, gone
    assert "if (WRAPPED && !purchasable(prices))" in page
