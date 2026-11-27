"""
Enrol one participant.

  python -m rig.enroll --name Maya --phone +13125550142 \
      --birth 1999-03-14 --time 04:42 --tz America/Chicago \
      --lat 41.8781 --lon -87.6298

Birth time is required. Without it there are no houses, and six of the eight
conversations in the voice test depended on house placement — a no-time
participant is testing a different product.
"""
import argparse
import datetime as dt
import uuid
from zoneinfo import ZoneInfo

from engine.store import load, save

CONSENT = """You're in the My Sign pilot. Before we start, the honest version:

• For 14 days I'll text you each morning and you can text back anytime.
• A real person reads every message before it sends. This is not automated.
• I keep what you tell me so I can refer back to it later — that's the whole
  point of the study.
• Your messages are used to evaluate the product and nothing else. No ads, no
  sharing, no selling. You can ask for everything to be deleted at any time.
• I'm not a therapist and not a crisis service. If things get heavy, call or
  text 988.
• Text STOP any time and it ends, no questions.

Reply YES if that all works for you."""


def main():
    ap = argparse.ArgumentParser()
    for f in ("name", "phone", "birth", "time", "tz"):
        ap.add_argument(f"--{f}", required=True)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    a = ap.parse_args()

    y, m, d = (int(x) for x in a.birth.split("-"))
    hh, mm = (int(x) for x in a.time.split(":"))
    local = dt.datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(a.tz))
    utc = local.astimezone(dt.timezone.utc)
    hour_ut = utc.hour + utc.minute / 60

    p = {
        "user_id": f"p{uuid.uuid4().hex[:6]}",
        "name": a.name,
        "phone": a.phone,
        "tz": a.tz,
        "birth": [utc.year, utc.month, utc.day, round(hour_ut, 4), a.lat, a.lon],
        "birth_local": f"{a.birth} {a.time} {a.tz}",
        "enrolled_at": dt.date.today().isoformat(),
        "consented": False,
    }
    ps = load("_study", "participants", [])
    ps.append(p)
    save("_study", "participants", ps)
    print(f"{p['name']} -> {p['user_id']}   birth UT {utc:%Y-%m-%d %H:%M}")
    print("\nSend this, and set consented=True only after they reply YES:\n")
    print(CONSENT)


if __name__ == "__main__":
    main()
