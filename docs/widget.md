# Home-screen widget: `GET /api/widget`

Her morning in one small JSON payload, for an iOS/Android widget (the
Capacitor app, later) or a PWA shortcut. **Free forever** — there is no plan
check on this route, and a test (`tests/test_habit.py::test_widget_ignores_plan_entirely`)
fails if one is added. Co-Star moved its widget behind Pro-Star in 2025; ours
stays free on purpose.

## Request

```
GET /api/widget
Cookie: <session>            # same session as the app; 401 when signed out
If-None-Match: "<etag>"      # optional; 304 when nothing changed
```

## Response

```json
{
  "date": "2026-10-28",
  "my_score": 62,
  "band": "medium",
  "line": {"text": "…line of the day…", "why": "Because Venus trines your Moon."},
  "moon_phase": "Waxing Gibbous",
  "moon_illumination_pct": 84,
  "moon_sign": "Aries",
  "top":     {"cid": "9a8c2c7887ac", "name": "Mom",    "score": 22, "delta": -9,
              "reason": "Today's Mars sets off the square between Mom's Saturn and your Ascendant",
              "lasts": "lasts most of the week", "url": "/app?p=9a8c2c7887ac"},
  "easiest": {"cid": "…", "name": "Jordan", "score": 88, "delta": 4, "reason": "…",
              "lasts": "…", "url": "/app?p=…"},
  "url": "/app"
}
```

- `line` is exactly `/api/today`'s `line` (engine/lines.py): same text, same why.
- `top` is the head of `daily.rank_people` — the person today's sky moved most.
  `easiest` is the highest score today. Both are `null` when she has no people.
- Every reason is a `daily.day_score` reason for that pair at her local noon;
  nothing here is written by a model.
- Names are first names only. No mood, no check-in text, ever — the widget
  sits on a lock screen.

## Caching

- Server: cached per user per local day for 15 minutes (`WIDGET_TTL_S`), and
  dropped when her birth time changes. A cached read is a single store read.
- HTTP: `Cache-Control: private, max-age=300`, `ETag`, `Vary: Cookie`. Refresh on
  a timer with `If-None-Match`; an unchanged day costs a 304 with no body.

## For the Capacitor widget (later)

1. The widget extension cannot share the WebView's cookie jar. Have the app
   copy the session cookie into the shared App Group keychain after sign-in, and
   send it as `Cookie:` from the extension.
2. Refresh with a `TimelineProvider` every 30-60 minutes; iOS budgets refreshes,
   and the payload changes at most a few times a day.
3. Tap targets: the whole widget opens `url` (`/app`); the person row opens
   `top.url` (`/app?p={cid}`), which the app already deep-links to that person.
4. Small family: `my_score` + `moon_sign`. Medium: add `line.text` and `top.name`
   with `top.lasts`. Keep text at AA contrast over whatever gradient is used.
