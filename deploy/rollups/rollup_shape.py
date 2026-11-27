"""
From query rows to what is kept: the long-term `agg/` rows and the compact
summary /ops reads from DynamoDB.

Small counts. A segment (a utm_source, a referrer, a landing path, a country,
a signup channel …) with fewer than `k` people in it is folded into one
"(other)" row, and an "(other)" row that is itself under `k` is dropped. The
day's totals are never suppressed, so segments may sum to less than the total.
At launch volumes this hides most segments; that is the point.

Size. A summary is capped (MAX_BYTES): every segment list keeps its top
`keep` rows by its main metric and folds the long tail into "(other)", and
`keep` halves until the JSON fits.

Nothing in here sees an id: the queries aggregate them away, and `check_no_ids`
refuses a row that still carries one.
"""
import json

import rollup_sql as Q

MIN_COUNT = 5
MAX_BYTES = 350_000
KEEP = 50
OTHER = "(other)"
NONE = "(none)"
IDS = frozenset(Q.IDS)


def check_no_ids(rows):
    for r in rows:
        bad = IDS & set(r)
        if bad:
            raise ValueError(f"an aggregate row carries {sorted(bad)}")
    return rows


def _n(v):
    return 0 if v is None else v


def _rate(a, b, nd=3):
    return round(a / b, nd) if b else None


def fold(rows, metric, k=MIN_COUNT, keep=None, sum_fields=None):
    """
    Rows with `metric` < k (and, past `keep`, the smallest rows) folded into
    one "(other)" row; that row is dropped if it is itself under k. Sorted by
    `metric`, largest first. `sum_fields` are added up into "(other)"; the
    rest of its fields are left out.
    """
    rows = sorted(rows, key=lambda r: (-_n(r.get(metric)), str(r.get("key"))))
    fields = sum_fields or [f for f in (rows[0] if rows else {}) if f != "key"
                            and isinstance(rows[0][f], (int, float))]
    out, rest = [], []
    for r in rows:
        if r.get("key") == OTHER or _n(r.get(metric)) < k or (keep is not None
                                                             and len(out) >= keep):
            rest.append(r)
        else:
            out.append(r)
    if rest:
        other = {"key": OTHER, **{f: sum(_n(r.get(f)) for r in rest) for f in fields}}
        if _n(other.get(metric)) >= k:
            out.append(other)
    return out


def _by(rows, dims, metric, fields, k):
    by = {}
    for dim in dims:
        seg = [{"key": r["key"] if r["key"] not in (None, "") else NONE,
                **{f: _n(r.get(f)) for f in fields}}
               for r in rows if r["dim"] == dim]
        by[dim] = fold(seg, metric, k, sum_fields=fields)
    return by


def _total(rows, fields):
    t = next((r for r in rows if r["dim"] in ("total", "all")), {})
    return {f: _n(t.get(f)) for f in fields}


def _flat(day, by, extra=None):
    """Segment rows as `agg/` lines."""
    out = []
    for dim, seg in by.items():
        for r in seg:
            out.append({"dt": day, "dim": dim, **(extra or {}), **r})
    return out


# ---------------------------------------------------------------- per report

def visits(day, res, k):
    rows = res["main"]
    fields = ["visits", "visitors", "pageviews", "private_visits"]
    by = _by(rows, Q.VISIT_DIMS, "visitors", fields, k)
    totals = _total(rows, fields)
    return ({"totals": totals, "by": by},
            [{"dt": day, "dim": "total", "key": None, **totals}] + _flat(day, by))


def signups(day, res, k):
    req = {"total": 0, "by_channel": {}}
    ver = {"total": 0, "new": 0, "returning": 0, "by_channel": {}}
    visitors = 0
    for r in res["counts"]:
        if r["metric"] == "site.visitors":
            visitors = _n(r["people"])
        elif r["metric"] == "auth.code_requested":
            req["total"] += _n(r["people"])
            req["by_channel"][r["channel"]] = req["by_channel"].get(r["channel"], 0) + _n(r["people"])
        elif r["metric"] == "auth.verified":
            kind = "new" if r["is_new"] == "true" else "returning"
            ver["total"] += _n(r["people"])
            ver[kind] += _n(r["people"])
            ch = ver["by_channel"].setdefault(r["channel"], {"new": 0, "returning": 0})
            ch[kind] += _n(r["people"])
    att = res["attribution"]
    fields = ["signups", "from_site"]
    by = _by(att, Q.ATTRIBUTION_DIMS, "signups", fields, k)
    tot = _total(att, fields)
    data = {"code_requested": req, "verified": ver, "visitors": visitors,
            "conversion": _rate(ver["new"], visitors, 4),
            "attribution": {"signups": tot["signups"], "from_site": tot["from_site"], "by": by}}
    agg = ([{"dt": day, "dim": "code_requested", "key": c, "people": n}
            for c, n in req["by_channel"].items()]
           + [{"dt": day, "dim": "verified", "key": c, **v} for c, v in ver["by_channel"].items()]
           + [{"dt": day, "dim": "visitors", "key": None, "people": visitors}]
           + _flat(day, by, {"metric": "attribution"}))
    return data, agg


def funnel(day, res, k):
    r = (res["main"] or [{}])[0]
    steps = [{"step": key, "label": label, "count": _n(r.get(key))}
             for key, label, _ in Q.FUNNEL]
    extra = {key: _n(r.get(key)) for key, _ in Q.FUNNEL_EXTRA}
    agg = [{"dt": day, "step": s["step"], "count": s["count"]} for s in steps] + [
        {"dt": day, "step": key, "count": v} for key, v in extra.items()]
    return {"steps": steps, "extra": extra}, agg


def active(day, res, k):
    r = (res["main"] or [{}])[0]
    dau, mau, new = _n(r.get("dau")), _n(r.get("mau")), _n(r.get("new_users"))
    data = {"dau": dau, "wau": _n(r.get("wau")), "mau": mau,
            "stickiness": _rate(dau, mau), "new": new, "returning": max(dau - new, 0)}
    return data, [{"dt": day, **data}]


def features(day, res, k):
    got = {r["feature"]: r for r in res["main"]}
    act = _n(got.get("_active", {}).get("users"))
    out = []
    for key, label, _ in Q.FEATURES:
        r = got.get(key, {})
        users, uses = _n(r.get("users")), _n(r.get("uses"))
        out.append({"feature": key, "label": label, "users": users, "uses": uses,
                    "uses_per_user": _rate(uses, users, 2), "share": _rate(users, act)})
    return ({"active_users": act, "features": out},
            [{"dt": day, **{f: v for f, v in r.items() if f != "label"}} for r in out])


def time(day, res, k):
    s = (res["sessions"] or [{}])[0]
    sessions, users = _n(s.get("sessions")), _n(s.get("users"))
    seconds = round(_n(s.get("seconds")))
    signed = round(_n(s.get("signed_in_seconds")))
    screens = sorted(({"screen": r["screen"], "seconds": _n(r["seconds"]), "users": _n(r["users"])}
                      for r in res["screens"]), key=lambda r: -r["seconds"])
    data = {"sessions": sessions, "users": users, "seconds": seconds,
            "median_session_s": _round(s.get("median_s")), "p75_session_s": _round(s.get("p75_s")),
            "sessions_per_user": _rate(_n(s.get("signed_in_sessions")), users, 2),
            "seconds_per_user": _rate(signed, users, 0),
            "by_screen": screens}
    agg = [{"dt": day, **{f: v for f, v in data.items() if f != "by_screen"}}] + [
        {"dt": day, "dim": "screen", **r} for r in screens]
    return data, agg


def _round(v):
    return None if v is None else round(v)


def _cohort_row(r):
    out = {"cohort": r["cohort"], "key": r["key"] if r["key"] not in (None, "") else NONE,
           "users": _n(r["users"])}
    for p, _, _ in Q.RETENTION_POINTS:
        out[f"{p}_n"], out[f"{p}_base"] = _n(r.get(f"{p}_n")), _n(r.get(f"{p}_base"))
    return out


def _with_rates(r):
    out = {"cohort": r["cohort"], "key": r["key"], "users": r["users"]}
    for p, _, _ in Q.RETENTION_POINTS:
        out[p] = _rate(r[f"{p}_n"], r[f"{p}_base"])
    out["counts"] = {f"{p}{s}": r[f"{p}{s}"] for p, _, _ in Q.RETENTION_POINTS
                     for s in ("_n", "_base")}
    return out


def retention(day, res, k):
    rows = [dict(_cohort_row(r), dim=r["dim"]) for r in res["main"]]
    fields = ["users"] + [f"{p}{s}" for p, _, _ in Q.RETENTION_POINTS for s in ("_n", "_base")]
    data = {"as_of": day, "points": [p for p, _, _ in Q.RETENTION_POINTS]}
    agg = []
    for dim, label in (("all", "cohorts"), ("channel", "by_channel"),
                       ("utm_source", "by_utm_source")):
        out = []
        for cohort in sorted({r["cohort"] for r in rows if r["dim"] == dim}):
            seg = [{f: r[f] for f in ["key", *fields]} for r in rows
                   if r["dim"] == dim and r["cohort"] == cohort]
            if dim == "all":
                seg = [dict(s, key="all") for s in seg]
            else:
                seg = fold(seg, "users", k, sum_fields=fields)
            out += [dict(s, cohort=cohort) for s in seg]
        data[label] = [_with_rates(r) for r in out]
        agg += [{"dt": day, "dim": dim, **r} for r in out]
    return data, agg


def notifications(day, res, k):
    rows = res["main"]
    fields = ["sent", "opened", "users_sent", "users_opened", "checked_in"]
    t = _total(rows, fields)
    t["open_rate"] = _rate(t["opened"], t["sent"])

    def seg(dim, keyname):
        out = []
        for r in rows:
            if r["dim"] != dim:
                continue
            key = r["key"] if r["key"] not in (None, "") else NONE
            row = {keyname: int(key) if keyname == "hour" and key != NONE else key,
                   **{f: _n(r.get(f)) for f in fields}}
            row["open_rate"] = _rate(row["opened"], row["sent"])
            out.append(row)
        return out

    by_kind = sorted(seg("kind", "key"), key=lambda r: -r["sent"])
    by_hour = sorted(seg("hour", "hour"), key=lambda r: (r["hour"] == NONE, str(r["hour"]).zfill(2)))
    data = {"totals": t, "by_kind": by_kind, "by_hour": by_hour, "hour_basis": "utc"}
    agg = ([{"dt": day, "dim": "total", **t}] + [{"dt": day, "dim": "kind", **r} for r in by_kind]
           + [{"dt": day, "dim": "hour", **r} for r in by_hour])
    return data, agg


def monetization(day, res, k):
    rows = res["main"]
    # started/renewed/canceled leave out operator hand grants (operator_grants).
    fields = ["shown", "shown_users", "started", "renewed", "canceled", "operator_grants"]
    t = _total(rows, fields)
    t["conversion"] = _rate(t["started"], t["shown_users"])

    def seg(dim):
        out = []
        for r in rows:
            if r["dim"] == dim:
                row = {"key": r["key"] if r["key"] not in (None, "") else NONE,
                       **{f: _n(r.get(f)) for f in fields}}
                row["conversion"] = _rate(row["started"], row["shown_users"])
                out.append(row)
        return sorted(out, key=lambda r: (-r["shown"], -r["started"], r["key"]))

    data = {"totals": t, "by_source": seg("source"), "by_tier": seg("tier")}
    agg = ([{"dt": day, "dim": "total", **t}]
           + [{"dt": day, "dim": "source", **r} for r in data["by_source"]]
           + [{"dt": day, "dim": "tier", **r} for r in data["by_tier"]])
    return data, agg


def _closed(rows, fields, metric, dim=None):
    """Rows of a set we define ourselves: never folded for being small, only
    for being many (names come from the client, so cap them)."""
    seg = [{"key": r["key"] if r["key"] not in (None, "") else NONE,
            **{f: _n(r.get(f)) for f in fields}}
           for r in rows if dim is None or r["dim"] == dim]
    return fold(seg, metric, 1, keep=KEEP, sum_fields=fields)


def site(day, res, k):
    st = (res["steps"] or [{}])[0]
    steps = [{"step": key, "label": label, "count": _n(st.get(key))}
             for key, label, _ in Q.SITE_STEPS]
    channels = sorted(({"key": r["key"], **{f: _n(r.get(f)) for f in
                                            ("pageviews", "events", "scrolled", "opened")}}
                       for r in res["channels"]), key=lambda r: (-r["pageviews"], r["key"]))
    got = {r["key"]: _n(r["sessions"]) for r in res["lengths"]}
    lengths = [{"key": key, "sessions": got.get(key, 0)} for key, _, _ in Q.VISIT_LENGTHS]
    cl, fields = res["clients"], ["pageviews", "visitors"]
    total = next((r for r in cl if r["dim"] == "total"), {})
    data = {"totals": {"pageviews": _n(total.get("pageviews")),
                       "countries": _n(total.get("countries"))},
            "steps": steps, "channels": channels,
            "sections": _closed(res["names"], ["uses", "people"], "uses", "section"),
            "taps": _closed(res["names"], ["uses", "people"], "uses", "tap"),
            "lengths": lengths,
            "by": {"os_family": _closed(cl, fields, "pageviews", "os_family"),
                   "browser_family": _closed(cl, fields, "pageviews", "browser_family"),
                   # A campaign tag is free text: small ones fold, like the visits dimensions.
                   **_by(cl, ("utm_medium",), "visitors", fields, k)}}
    agg = ([{"dt": day, "dim": "total", "key": None, **data["totals"]}]
           + [{"dt": day, "dim": "step", "key": s["step"], "count": s["count"]} for s in steps]
           + _flat(day, {"channel": channels, "section": data["sections"], "tap": data["taps"],
                         "length": lengths, **data["by"]}))
    return data, agg


def usage(day, res, k):
    src = {r["key"]: r for r in res["sources"]}
    ev = {s: _n(src.get(s, {}).get("events")) for s in Q.LIVE_SOURCES}
    totals = {"events": sum(ev.values()), "site_events": ev["site"],
              "app_events": ev["app"] + ev["native"], "server_events": ev["server"],
              "app_opens": sum(_n(src.get(s, {}).get("opens")) for s in ("app", "native"))}
    by_hour = sorted(({"hour": int(r["hour"]), "events": _n(r["events"]),
                       "pageviews": _n(r["pageviews"])} for r in res["hours"]),
                     key=lambda r: r["hour"])
    names = {label: _closed(res["names"], ["uses", "users"], "uses", dim) for dim, label in
             (("screen", "screens"), ("tap", "taps"), ("change", "changes"), ("server", "server"))}
    data = {"totals": totals, "by_hour": by_hour, "hour_basis": "utc", **names}
    agg = ([{"dt": day, "dim": "total", "key": None, **totals}]
           + [{"dt": day, "dim": "hour", **r} for r in by_hour] + _flat(day, names))
    return data, agg


SHAPERS = {"visits": visits, "signups": signups, "funnel": funnel, "active": active,
           "features": features, "time": time, "retention": retention,
           "notifications": notifications, "monetization": monetization,
           "site": site, "usage": usage}


def shape(report, day, results, k=MIN_COUNT):
    """(summary for DynamoDB, rows for agg/). `results` is {query name: rows}."""
    data, agg = SHAPERS[report](day, results, k)
    check_no_ids(agg)
    return cap(data), agg


# ---------------------------------------------------------------- size

def _size(obj):
    return len(json.dumps(obj, separators=(",", ":"), default=str).encode())


def _lists(obj):
    """Every list of segment dicts in a summary, as (parent, key)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                yield obj, k
            else:
                yield from _lists(v)


def cap(data, limit=MAX_BYTES, keep=KEEP):
    """Shrink segment lists (top `keep`, the rest as "(other)") until it fits."""
    if _size(data) <= limit:
        return data
    data = json.loads(json.dumps(data, default=str))
    while keep >= 1:
        for parent, k in list(_lists(data)):
            rows = parent[k]
            if len(rows) <= keep:
                continue
            # Counts add up; rates (floats) don't, so "(other)" leaves them out.
            nums = [f for f, v in rows[0].items() if isinstance(v, int)
                    and not isinstance(v, bool) and f != "truncated"]
            head, tail = rows[:keep], rows[keep:]
            other = {f: sum(r.get(f) or 0 for r in tail if isinstance(r.get(f), int))
                     for f in nums}
            ident = next((f for f in ("key", "screen", "feature", "cohort", "step")
                          if f in rows[0]), "key")
            folded = sum(r.get("truncated") or 1 for r in tail)
            parent[k] = head + [{ident: OTHER, **other, "truncated": folded}]
        if _size(data) <= limit:
            data["truncated"] = True
            return data
        keep //= 2
    raise ValueError(f"summary still over {limit} bytes after truncation")
