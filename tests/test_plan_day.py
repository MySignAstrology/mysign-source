"""
The free cap can't be front-loaded by hopping time zones (audit L3).

Her local date follows the client's X-Tz. Between UTC+14 and UTC-11 three
local dates were in reach at once, so three days' free messages in a burst.
"""
import datetime as dt
import secrets

from engine import plan


def burst(uid, days):
    return sum(plan.claim_message(uid, d)[0]
               for d in days for _ in range(plan.FREE_MESSAGES_PER_DAY + 2))


def test_hopping_zones_reaches_no_more_than_the_latest_day():
    uid = "u-tz-" + secrets.token_hex(4)
    x = dt.date(2026, 10, 30)
    # UTC-11 (yesterday), UTC+14 (tomorrow), then back to UTC-11 and UTC.
    got = burst(uid, [x - dt.timedelta(days=1), x + dt.timedelta(days=1),
                      x - dt.timedelta(days=1), x])
    assert got == 2 * plan.FREE_MESSAGES_PER_DAY          # was 3 days' worth


def test_a_new_day_still_brings_a_new_allowance():
    uid = "u-tz-" + secrets.token_hex(4)
    x = dt.date(2026, 10, 30)
    assert burst(uid, [x]) == plan.FREE_MESSAGES_PER_DAY
    assert burst(uid, [x + dt.timedelta(days=1)]) == plan.FREE_MESSAGES_PER_DAY
    assert plan.messages_today(uid, x) == plan.FREE_MESSAGES_PER_DAY   # counted on the later day


def test_a_refund_goes_back_to_the_day_it_was_taken_from():
    uid = "u-tz-" + secrets.token_hex(4)
    x = dt.date(2026, 10, 30)
    plan.claim_message(uid, x + dt.timedelta(days=1))
    plan.refund_message(uid, x)                           # the earlier zone's date
    assert plan.messages_today(uid, x + dt.timedelta(days=1)) == 0
