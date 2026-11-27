"""
Birth time on a wall clock -> the instant it names.

Twice a year, in most of the places our users were born, the wall clock lies.
On the night the clocks go back an hour repeats, so "01:30" names two instants
an hour apart; on the night they go forward an hour is skipped, so "02:30" names
none. `datetime(..., tzinfo=ZoneInfo(tz))` resolves both silently, which is
correct Python and a wrong chart: an hour moves the ascendant about fifteen
degrees, usually a whole sign, and every house with it.

We cannot know which instant the birth record meant, so we pick one the same
way every time — the first, which is what Python and most birth records assume
— and say so. The caller stores the note and hands it to the model, which can
then tell her what changes if her record says otherwise, instead of reading a
chart that might be an hour wrong as though it were certain.
"""
import datetime as dt
import pathlib
import zoneinfo
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc

# zoneinfo prefers the host's /usr/share/zoneinfo over the tzdata package, so a
# Lambda image with old rules would quietly win. Point it at the rules we ship
# so every machine converts a birth time the same way.
#
# Those rules are tzdb compiled WITH backzone (tools/build_zoneinfo.py), not the
# PyPI tzdata wheel. The wheel is tzdb's default build, which since 2022b links
# zones that agree after 1970 and drops their older history: Reykjavik used
# Abidjan's rules (Iceland was UTC-1 until 1968), Oslo and Stockholm used
# Berlin's, Amsterdam used Brussels', Curaçao Puerto Rico's — 69 zones in our
# place index wrong by 15 minutes to two hours before 1970, about a rising sign.
#
# reset_tzpath is process-wide, and every ZoneInfo() in the app — the server's
# "today", local noon, rectify, the People tab — resolves through it, so this
# module is imported from engine/__init__.py before anything can build a zone.
# zoneinfo caches by key, which is why it has to run first rather than whenever
# a birth time is first converted.
#
# Even with backzone, tzdb is authoritative only from 1970; earlier entries are
# the project's best reconstruction (US local daylight time before 1967 most of
# all). The accuracy page says so.
ZONEINFO_DIR = pathlib.Path(__file__).with_name("zoneinfo")
if (ZONEINFO_DIR / "VERSION").exists():
    zoneinfo.reset_tzpath(to=[str(ZONEINFO_DIR)])
    TZ_RULES = f"IANA {(ZONEINFO_DIR / 'VERSION').read_text().strip()}"
else:  # a checkout without the compiled tree: the wheel, without backzone
    try:
        import tzdata
        zoneinfo.reset_tzpath(to=[])
        TZ_RULES = f"IANA {tzdata.IANA_VERSION} (without backzone)"
    except ImportError:
        TZ_RULES = "host system"
zoneinfo.ZoneInfo.clear_cache()

# Before this, tzdb's own maintainers call the record best effort.
AUTHORITATIVE_FROM = 1970

def resolve(y, m, d, hh, mm, tz, fold=0):
    """(utc_datetime, issue). issue is None, or a dict describing the problem:

        {"kind": "repeated", "assumed": "first", "other_utc": datetime}
        {"kind": "skipped",  "assumed": "<offset before the change>"}
    """
    zone = ZoneInfo(tz)
    first = dt.datetime(y, m, d, hh, mm, tzinfo=zone, fold=0)
    second = first.replace(fold=1)
    chosen = second if fold else first
    utc = chosen.astimezone(UTC)

    # How far the clocks moved that night: an hour almost everywhere, but half
    # an hour on Lord Howe, fifteen minutes in Nepal in 1986, and a whole day
    # when Samoa crossed the date line.
    jump = abs(int((second.utcoffset() - first.utcoffset()).total_seconds() // 60))

    # A time that does not survive the round trip never appeared on a clock.
    # The date is compared too: Samoa skipped 30 December 2011 entirely, and
    # "12:00 on the 30th" came back as 12:00 on the 31st — same hour and minute.
    back = utc.astimezone(zone)
    if (back.year, back.month, back.day, back.hour, back.minute) != (y, m, d, hh, mm):
        return utc, {"kind": "skipped", "minutes": jump,
                     "assumed": f"UTC{_offset(chosen)} (the clock "
                                f"{'after' if fold else 'before'} it sprang forward)"}

    if first.utcoffset() != second.utcoffset():
        other = (first if fold else second).astimezone(UTC)
        return utc, {"kind": "repeated", "assumed": "second" if fold else "first",
                     "other_utc": other, "minutes": jump}
    return utc, None


def _span(minutes):
    """'the hour', 'the half hour', 'the 15 minutes', 'the day'."""
    if not minutes or minutes == 60:
        return "the hour"
    if minutes == 30:
        return "the half hour"
    if minutes == 1440:
        return "the day"
    if minutes % 60 == 0:
        return f"the {minutes // 60} hours"
    return f"the {minutes} minutes"


def _offset(t):
    mins = int(t.utcoffset().total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    return f"{sign}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}"


def describe(issue, other_rising=None, other_mc=None):
    """One plain sentence for the profile and the model's briefing."""
    if not issue:
        return None
    span = _span(issue.get("minutes"))
    if issue["kind"] == "skipped":
        if issue.get("minutes") == 1440:
            return ("Her birth date is the day the clocks skipped there — the country "
                    "moved across the date line and that calendar day never happened. "
                    f"Charted as {issue['assumed']}; check the date on the record.")
        return (f"Her birth time falls in {span} the clocks skipped that night, "
                f"so it never appeared on a clock there. Charted as {issue['assumed']}; "
                "a typo or a record kept in standard time is likely.")
    other = "first" if issue["assumed"] == "second" else "second"
    # The Midheaven moves as fast as the Ascendant, so it is named too: the
    # model caveated her rising sign and then read Midheaven contacts as fact.
    mc = f" and her Midheaven is in {other_mc}" if other_mc else ""
    alt = (f" If it was the {other}, her rising sign is {other_rising}{mc}; angle and "
           "house contacts change with it." if other_rising else "")
    return (f"Her birth time falls in {span} that happened twice when the clocks "
            f"went back. Charted as the {issue['assumed']} occurrence.{alt}")
