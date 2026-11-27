"""
Seed plausible daily rollups into a local data dir, so /ops can be looked at
(and screenshot) with every report filled in, without the analytics lake.

    MYSIGN_DATA=./tests/e2e/.data/x python tests/e2e/seed_reports.py [days]

Shapes follow deploy/rollups/rollup_shape.py. Deterministic: same days, same
numbers. Used by tests/e2e/ops.spec.js; harmless anywhere else.
"""
import datetime as dt
import random
import sys

from app import reports

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 120
R = random.Random(7)


def seg(keys, total, fields):
    out, left = [], total
    for i, k in enumerate(keys):
        n = left if i == len(keys) - 1 else int(left * R.uniform(.3, .6))
        left -= n
        out.append({"key": k, **{f: max(0, int(n * m)) for f, m in fields.items()}})
    return out


def day_data(day, i):
    wave = 1 + .25 * ((i % 7) in (5, 6)) + i / DAYS
    visitors = int(R.uniform(140, 220) * wave)
    vf = {"visits": 1.2, "visitors": 1, "pageviews": 2.6, "private_visits": .08}
    sms, email = int(visitors * .07), int(visitors * .05)
    new = int((sms + email) * .7)
    ret = int(new * .3)
    dau = int(R.uniform(50, 70) * wave)
    sent = int(dau * 1.4)
    opened = int(sent * R.uniform(.18, .3))
    yield "visits", {"totals": {k: int(visitors * m) for k, m in vf.items()}, "by": {
        "utm_source": seg(["instagram", "tiktok", "(none)", "newsletter", "google"], visitors, vf),
        "utm_campaign": seg(["spring_launch", "(none)", "creator_maya", "retarget"], visitors, vf),
        "referrer_domain": seg(["(direct)", "instagram.com", "t.co", "google.com", "reddit.com"], visitors, vf),
        "landing_path": seg(["/", "/compatibility", "/daily", "/about"], visitors, vf),
        "country": seg(["US", "GB", "CA", "AU", "(other)"], visitors, vf),
        "device_class": seg(["mobile", "desktop", "tablet"], visitors, vf)}}
    yield "signups", {"code_requested": {"total": sms + email, "by_channel": {"sms": sms, "email": email}},
                      "verified": {"total": new + ret, "new": new, "returning": ret, "by_channel": {
                          "sms": {"new": int(new * .55), "returning": int(ret * .5)},
                          "email": {"new": new - int(new * .55), "returning": ret - int(ret * .5)}}},
                      "visitors": visitors, "conversion": round(new / visitors, 4),
                      "attribution": {"signups": new, "from_site": int(new * .8), "by": {
                          k: seg(v, new, {"signups": 1, "from_site": .8}) for k, v in {
                              "utm_source": ["instagram", "tiktok", "(none)", "newsletter"],
                              "referrer_domain": ["(direct)", "instagram.com", "t.co"],
                              "landing_path": ["/", "/compatibility", "/daily"]}.items()}}}
    counts = [visitors, int(visitors * .3), sms + email, new + ret, int(new * .95), int(new * .85),
              int(new * .7), int(new * .4), int(new * .3), int(new * .25), int(new * .04)]
    steps = ["visit", "start_open", "code_requested", "verified", "consented", "onboarded",
             "first_message", "returned_day2", "week_one", "paywall_shown", "paid"]
    labels = ["Visited the site", "Opened the app", "Asked for a code", "Verified", "Agreed to terms",
              "Built their chart", "First message", "Back on day 2", "Active in week one",
              "Saw the paywall", "Paid"]
    yield "funnel", {"steps": [{"step": s, "label": lab, "count": c} for s, lab, c in zip(steps, labels, counts)],
                     "extra": {"code_requested_sms": sms, "code_requested_email": email, "verified_new": new,
                               "signed_up": new, "paywall_intent": int(new * .3), "funnel_paid": int(new * .05)}}
    mau = 900 + i * 6
    yield "active", {"dau": dau, "wau": int(dau * 3.1), "mau": mau, "stickiness": round(dau / mau, 3),
                     "new": new, "returning": max(dau - new, 0)}
    feats = [("checkin", "Daily check-in", .7), ("chat", "Chat", .55), ("reading", "Readings", .4),
             ("people", "People", .25), ("memory", "Memory", .15), ("sky", "Sky", .3),
             ("invites", "Invites", .05), ("push", "Push opens", .2)]
    yield "features", {"active_users": dau, "features": [
        {"feature": f, "label": lab, "users": int(dau * s), "uses": int(dau * s * R.uniform(1.2, 3)),
         "uses_per_user": None, "share": round(s, 3)} for f, lab, s in feats]}
    sessions = int(dau * 1.8)
    yield "time", {"sessions": sessions, "users": dau, "seconds": sessions * R.randint(150, 260),
                   "median_session_s": R.randint(120, 200), "p75_session_s": R.randint(260, 420),
                   "sessions_per_user": round(sessions / dau, 2), "seconds_per_user": 380,
                   "by_screen": [{"screen": s, "seconds": int(sessions * w), "users": int(dau * u)} for s, w, u in
                                 [("home", 60, .9), ("thread", 95, .55), ("chart", 30, .3),
                                  ("people", 22, .2), ("settings", 6, .1)]]}
    kinds = [("daily", .7), ("nudge", .2), ("invite", .1)]
    yield "notifications", {
        "totals": {"sent": sent, "opened": opened, "users_sent": int(sent * .8), "users_opened": int(opened * .9),
                   "checked_in": int(opened * .6), "open_rate": round(opened / sent, 3)},
        "by_kind": [{"key": k, "sent": int(sent * w), "opened": int(opened * w), "users_sent": int(sent * w * .8),
                     "users_opened": int(opened * w * .9), "checked_in": int(opened * w * .6), "open_rate": None}
                    for k, w in kinds],
        "by_hour": [{"hour": h, "sent": int(sent * w), "opened": int(opened * w), "users_sent": int(sent * w),
                     "users_opened": int(opened * w), "checked_in": int(opened * w * .6), "open_rate": None}
                    for h, w in [(13, .1), (14, .35), (15, .3), (16, .15), (22, .1)]],
        "hour_basis": "utc"}
    shown = int(dau * .2)
    started = R.randint(0, 3)
    yield "monetization", {
        "totals": {"shown": shown, "shown_users": int(shown * .8), "started": started, "renewed": R.randint(0, 2),
                   "canceled": R.randint(0, 1), "conversion": None, "operator_grants": R.randint(0, 1)},
        "by_source": [{"key": "paywall", "shown": shown, "shown_users": int(shown * .8), "started": started,
                       "renewed": 0, "canceled": 0, "operator_grants": 0, "conversion": None}],
        "by_tier": [{"key": "plus_monthly", "shown": shown, "shown_users": int(shown * .8), "started": started,
                     "renewed": 0, "canceled": 0, "operator_grants": 0, "conversion": None},
                    {"key": "plus_yearly", "shown": 0, "shown_users": 0, "started": R.randint(0, 1),
                     "renewed": 0, "canceled": 0, "operator_grants": 0, "conversion": None}]}
    yield "site", {
        "totals": {"pageviews": int(visitors * 2.6), "countries": 9 + i % 4},
        "steps": [{"step": k, "label": lab, "count": int(visitors * w)} for k, lab, w in
                  [("landed", "Landing page view", 1), ("hero", "Saw the hero", .97),
                   ("scrolled", "Scrolled past the hero", .55), ("opened", "Opened sign-up", .3),
                   ("submitted", "Submitted sign-up", .14), ("code_sent", "Code sent", .12),
                   ("verified", "Code verified", .09)]],
        "channels": [{"key": k, "pageviews": int(visitors * w), "events": int(visitors * w * 3),
                      "scrolled": int(visitors * w * q), "opened": int(visitors * w * q * .4)} for k, w, q in
                     [("paid", 1.2, 0), ("direct", .8, .6), ("instagram", .4, .5), ("google", .2, .7)]],
        "sections": [{"key": k, "uses": int(visitors * w), "people": int(visitors * w)} for k, w in
                     [("hero", .97), ("how", .4), ("accuracy", .3), ("people", .2)]],
        "taps": [{"key": k, "uses": int(visitors * w), "people": int(visitors * w)} for k, w in
                 [("free_submit", .2), ("start_open", .3), ("how_open", .1)]],
        "lengths": [{"key": k, "sessions": int(visitors * w)} for k, w in
                    [("<2s", .3), ("2-10s", .25), ("10-60s", .25), ("1-5m", .15), ("5m+", .05)]],
        "by": {d: [{"key": k, "pageviews": int(visitors * w), "visitors": int(visitors * w / 2)} for k, w in ks]
               for d, ks in {"os_family": [("ios", 1.4), ("android", .7), ("macos", .4)],
                             "browser_family": [("in_app", 1.2), ("safari", .9), ("chrome", .4)],
                             "utm_medium": [("(none)", 1.3), ("paid", 1.2)]}.items()}}
    ev = {"site_events": visitors * 6, "app_events": dau * 40, "server_events": dau * 5}
    yield "usage", {
        "totals": {"events": sum(ev.values()), **ev, "app_opens": int(dau * 2.2)},
        "by_hour": [{"hour": h, "events": int(sum(ev.values()) / 24 * (1 + (h > 12))),
                     "pageviews": int(visitors / 12 * (1 + (h > 12)))} for h in range(24)],
        "hour_basis": "utc",
        "screens": [{"key": k, "uses": int(dau * w), "users": dau} for k, w in
                    [("home", 2), ("chart", 1.1), ("people", .8), ("settings", .2)]],
        "taps": [{"key": k, "uses": int(dau * w), "users": dau} for k, w in
                 [("nav_chart", 1), ("ci_go", .7), ("m_body.button", .3)]],
        "changes": [{"key": "n_time", "uses": 3, "users": 3}],
        "server": [{"key": k, "uses": int(dau * w), "users": dau} for k, w in
                   [("message_sent", 2), ("checked_in", .7), ("auth.verified", .3), ("funnel.onboarded", .1)]]}


def retention(day):
    points = ["d1", "d7", "d30", "w1", "w2", "w3", "w4", "w5", "w6", "w7", "w8"]
    curve = {"d1": .42, "d7": .24, "d30": .13, "w1": .35, "w2": .27, "w3": .22, "w4": .19,
             "w5": .17, "w6": .15, "w7": .14, "w8": .13}
    age = {"d1": 1, "d7": 7, "d30": 30, "w1": 7, "w2": 14, "w3": 21, "w4": 28, "w5": 35,
           "w6": 42, "w7": 49, "w8": 56}
    out = {"as_of": day.isoformat(), "points": points}
    for label, keys in (("cohorts", ["all"]), ("by_channel", ["sms", "email"]),
                        ("by_utm_source", ["instagram", "tiktok", "(other)"])):
        rows = []
        for w in range(10, 0, -1):
            start = day - dt.timedelta(days=7 * w)
            cohort = f"{start.isocalendar()[0]}-W{start.isocalendar()[1]:02d}"
            for k in keys:
                users = R.randint(40, 90) // len(keys)
                row = {"cohort": cohort, "key": k, "users": users, "counts": {}}
                for p in points:
                    if (day - start).days >= age[p]:
                        n = int(users * curve[p] * R.uniform(.8, 1.2))
                        row[p] = round(n / users, 3)
                        row["counts"][p + "_n"], row["counts"][p + "_base"] = n, users
                    else:
                        row[p] = None
                        row["counts"][p + "_n"], row["counts"][p + "_base"] = 0, 0
                rows.append(row)
        out[label] = rows
    return out


def main():
    today = dt.datetime.now(dt.timezone.utc).date()
    for i in range(DAYS):
        day = today - dt.timedelta(days=DAYS - 1 - i)
        final = day < today - dt.timedelta(days=1)
        for name, data in day_data(day, i):
            reports.put(name, day.isoformat(), data, final=final)
        reports.put("retention", day.isoformat(), retention(day), final=final)
    print(f"seeded {DAYS} days of rollups")


if __name__ == "__main__":
    main()
