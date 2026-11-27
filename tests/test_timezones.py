"""
Historical time zones: births before 1970 in the zones tzdb folded together.

The PyPI tzdata wheel is tzdb's default build, without `backzone`, so since
2022b Reykjavik ran on Abidjan's rules, Oslo and Stockholm on Berlin's,
Amsterdam on Brussels', Curaçao on Puerto Rico's. The audit (astro HIGH-2)
measured 69 zones in the place index wrong before 1970, by 15 minutes to two
hours. Expected offsets are from an independent compile of tzdb 2026f with
backzone (the auditor's, built separately with the system zic), and agree with
the tzdb source comments quoted beside them.
"""
import datetime as dt
import gzip
import os
import zoneinfo

import pytest

import engine  # noqa: F401  — installs the shipped rules, as the app does
from engine import localtime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UTC = dt.timezone.utc

# (zone, local wall time, expected UTC offset in minutes, why)
PRE_1970 = [
    ("Atlantic/Reykjavik", (1960, 1, 15, 12, 0), -60, "Iceland kept UTC-1 until 1968-04-07"),
    ("Atlantic/Reykjavik", (1960, 7, 15, 12, 0), 0, "Icelandic summer time"),
    ("Europe/Oslo", (1962, 7, 15, 12, 0), 120, "Norway DST 1959-1965"),
    ("Arctic/Longyearbyen", (1962, 7, 15, 12, 0), 120, "Svalbard follows Norway"),
    ("Europe/Stockholm", (1945, 7, 15, 12, 0), 60, "Sweden: no summer time 1917-1979 (Berlin had +3)"),
    ("Europe/Copenhagen", (1945, 7, 15, 12, 0), 120, "Danish summer time, not Berlin's double"),
    ("Europe/Amsterdam", (1935, 1, 15, 12, 0), 19 + 32 / 60, "Amsterdam Time +00:19:32"),
    ("Europe/Amsterdam", (1935, 6, 1, 12, 0), 79 + 32 / 60, "Dutch summer time +01:19:32"),
    ("America/Curacao", (1960, 7, 1, 12, 0), -270, "Curaçao -04:30 until 1965"),
    ("Africa/Accra", (1952, 10, 1, 12, 0), 30, "Ghana +00:30 Sep-Dec 1950-55"),
    ("Africa/Dar_es_Salaam", (1950, 1, 1, 12, 0), 165, "Tanganyika +02:45 1948-1960"),
    ("Africa/Bamako", (1955, 6, 1, 12, 0), -60, "French Sudan -01 until 1960"),
]

# Zones and instants the fix must NOT move — the modern results stay identical.
UNCHANGED = [
    ("America/New_York", (2005, 7, 4, 12, 0), -240, "EDT"),
    ("America/New_York", (2005, 12, 1, 12, 0), -300, "EST"),
    ("Australia/Lord_Howe", (2000, 1, 15, 12, 0), 660, "+11 in summer (30-min DST)"),
    ("Australia/Lord_Howe", (2000, 7, 15, 12, 0), 630, "+10:30"),
    ("Asia/Kathmandu", (1985, 6, 1, 12, 0), 330, "+05:30 until 1986"),
    ("Asia/Kathmandu", (1990, 6, 1, 12, 0), 345, "+05:45 from 1986"),
    ("Asia/Kolkata", (1945, 6, 1, 12, 0), 390, "Indian war time +06:30"),
    ("Europe/London", (1944, 6, 1, 12, 0), 120, "British Double Summer Time"),
    ("Atlantic/Reykjavik", (1990, 1, 15, 12, 0), 0, "Iceland on UTC since 1968"),
    ("Europe/Oslo", (2000, 7, 15, 12, 0), 120, "CEST"),
    ("Europe/Amsterdam", (1990, 6, 1, 12, 0), 120, "CEST"),
]


def _minutes(zone, when):
    utc, _ = localtime.resolve(*when, zone)
    local = dt.datetime(*when)
    return (local - utc.replace(tzinfo=None)).total_seconds() / 60


@pytest.mark.parametrize("zone,when,minutes,why", PRE_1970,
                         ids=[f"{z}-{w[0]}-{w[1]:02d}" for z, w, _m, _y in PRE_1970])
def test_pre_1970_births_use_backzone_history(zone, when, minutes, why):
    assert abs(_minutes(zone, when) - minutes) < 0.02, why


@pytest.mark.parametrize("zone,when,minutes,why", UNCHANGED,
                         ids=[f"{z}-{w[0]}-{w[1]:02d}" for z, w, _m, _y in UNCHANGED])
def test_modern_and_already_correct_offsets_unchanged(zone, when, minutes, why):
    assert abs(_minutes(zone, when) - minutes) < 0.02, why


def test_every_zoneinfo_in_the_process_reads_the_shipped_rules():
    """reset_tzpath is process-wide: a plain ZoneInfo anywhere in the app (the
    server's "today", local noon, rectify, the People tab) sees backzone too,
    not only the onboarding path."""
    import app.server  # noqa: F401  — whatever it imports must not undo this
    assert (str(localtime.ZONEINFO_DIR),) == zoneinfo.TZPATH
    z = zoneinfo.ZoneInfo("Atlantic/Reykjavik")
    assert dt.datetime(1960, 1, 15, 12, tzinfo=z).utcoffset() == dt.timedelta(hours=-1)


def test_rules_label_names_backzone():
    assert "backzone" in localtime.TZ_RULES
    assert localtime.AUTHORITATIVE_FROM == 1970


def test_every_birthplace_zone_is_in_the_shipped_tree():
    """A zone the place index uses but the tree lacks would fall through to the
    wheel and silently lose its history again."""
    with gzip.open(os.path.join(ROOT, "app", "data", "cities.tsv.gz"), "rt") as fh:
        zones = {line.rstrip("\n").split("\t")[6] for line in fh}
    missing = sorted(z for z in zones if not (localtime.ZONEINFO_DIR / z).is_file())
    assert not missing


def test_tree_is_fat_tzif():
    """tools/build_zoneinfo.py compiles with -b fat: a slim Ojinaga from an old
    zic read an hour wrong in November 2022."""
    z = zoneinfo.ZoneInfo("America/Ojinaga")
    assert dt.datetime(2022, 10, 31, 12, tzinfo=z).utcoffset() == dt.timedelta(hours=-6)


def test_merge_prefers_backzone_zones_and_links(tmp_path):
    """The build's merge, on a toy tzdb: a main-data link is replaced by the
    backzone Zone of the same name, and a backzone link re-points an alias."""
    from tools import build_zoneinfo as b
    for f in b.MAIN:
        (tmp_path / f).write_text("")
    (tmp_path / "africa").write_text("Zone Africa/Abidjan -0:16:08 - LMT 1912\n\t\t\t0:00 - GMT\n")
    (tmp_path / "backward").write_text("Link Africa/Abidjan Atlantic/Reykjavik\n"
                                       "Link Africa/Abidjan Iceland\n")
    (tmp_path / "backzone").write_text("Zone Atlantic/Reykjavik -1:28 - LMT 1908\n\t\t\t0:00 - GMT\n"
                                       "Link Atlantic/Reykjavik Iceland\n")
    text = b.merged(tmp_path)
    assert "Link Africa/Abidjan Atlantic/Reykjavik" not in text
    assert "Link Africa/Abidjan Iceland" not in text
    assert "Zone Atlantic/Reykjavik" in text and "Link Atlantic/Reykjavik Iceland" in text


def test_model_is_told_pre_1970_zone_rules_are_a_reconstruction():
    from engine import context
    from engine.chart import natal
    chart = natal(1960, 1, 15, 13.0, 64.15, -21.94)
    old = {"birth_time_known": True, "birth_ut": [1960, 1, 15, 13.0, 64.15, -21.94]}
    new = {**old, "birth_ut": [1990, 1, 15, 12.0, 64.15, -21.94]}
    unknown = {**old, "birth_time_known": False}
    note = "TIME ZONE NOTE"
    assert any(note in line for line in context.chart_lines(chart, old))
    assert not any(note in line for line in context.chart_lines(chart, new))
    # No time, no angles: an hour's doubt changes nothing she is told.
    assert not any(note in line for line in context.chart_lines(chart, unknown))


def test_accuracy_page_says_where_tzdb_stops_being_authoritative():
    with open(os.path.join(ROOT, "app", "static", "accuracy.html"), encoding="utf-8") as fh:
        page = fh.read()
    assert "backzone" in page and "authoritative from 1970" in page
