"""
E2E helper (team2/memory): give a just-created test account ~90 days of
episodes containing one strong Moon-in-Scorpio pattern, plus a dated note about
a saved person, then run the weekly job. Test-only; MYSIGN_DATA must point at
the e2e store the spec's server is using.

    python tests/e2e/plant_memory.py someone@example.test Jake
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ["MYSIGN_DEV_AUTH"] = "1"

from app import auth
from engine import memory as M
from engine.store import save

SIGNS = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra",
         "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]


def main(email, person):
    uid = auth.uid_for(email, create=False)
    if not uid:
        sys.exit(f"no account for {email}")
    today = dt.date.today()
    eps = []
    for i in range(90, 0, -1):
        d = today - dt.timedelta(days=i)
        ms = SIGNS[(i * 2 // 5) % 12]
        mood = 3 if ms == "Scorpio" else (6, 7, 8, 5, 7, 4, 6, 8, 7, 6, 3, 7)[i % 12]
        words = f"{person} went quiet after I asked about the weekend" if i == 9 else None
        eps.append({"date": d.isoformat(), "mood": mood, "summary": words or "a day",
                    "her_words": [words] if words else [],
                    "people": [person] if words else [], "topics": [],
                    "person_ids": M.person_ids(uid, [person]) if words else [],
                    "sky_state": {"moon_phase": "waxing gibbous", "moon_sign": ms,
                                  "aspect_keys": [], "active_houses": []}})
    save(uid, "episodes", eps)
    print(len(M.weekly_pattern_job(uid)))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
