"""
Ten thousand conversations: 5,000 scenarios, each run twice — typed in the chat
and said out loud to the psychic's orb — so every scenario also measures whether
the two channels give the same answer.

Deterministic: the same seed gives the same conversations, so a number from one
prompt version is comparable with the next. The wording comes from
evals/convo_bank.py; this file only combines it.

A scenario: a person (birth data, plan), maybe a check-in, an intent, an opening
message and up to three follow-ups. The voice copy of each message is what
browser speech recognition would hand over: lowercase, no punctuation, text-speak
spelled out, now and then a filler or a misheard word.
"""
import random
import re

from evals import convo_bank as B

SEED = 20261119
N_SCENARIOS = 5000

# Cities the app resolves (app/places), spread over time zones and hemispheres.
CITIES = ["Chicago IL", "Austin, Texas, United States", "New York NY", "Los Angeles CA",
          "Miami FL", "Seattle WA", "Denver CO", "Atlanta GA", "Toronto, Ontario, Canada",
          "Mexico City", "London, England, United Kingdom", "Manchester, England, United Kingdom",
          "Dublin, Ireland", "Paris, France", "Berlin, Germany", "Madrid, Spain", "Lagos, Nigeria",
          "Nairobi", "Mumbai", "Manila, Philippines", "Sydney, New South Wales, Australia",
          "Melbourne, Victoria, Australia", "Auckland", "São Paulo, Brazil",
          "Buenos Aires, Argentina", "Bogotá, Colombia", "Lima, Peru", "Phoenix AZ", "Houston TX",
          "Boston MA"]
NAMES = ["Maya", "Ava", "Jordan", "Priya", "Sofia", "Zoe", "Imani", "Lucía", "Hana", "Chloe",
         "Amara", "Grace", "Nia", "Elena", "Riley", "Sam", "Alex", "Noor", "Bella", "Kai",
         "Leah", "Mia", "Tasha", "Yuki", "Isla", "Camila", "Dani", "Fatima", "Jade", "Ruby"]
CHECKIN_NOTES = ["", "", "didn't sleep", "work is a lot", "kind of hopeful today", "hungover",
                 "anxious about nothing", "good day honestly", "fought with my mom",
                 "he hasn't texted", "tired", "excited for the weekend", "lonely", "meh"]
GENERIC_FOLLOWUPS = ["short version", "ok the deep cut", "why tho", "so what do i actually do",
                     "wait really?", "lol ok", "that's so accurate", "hm not sure about that",
                     "thanks", "ok goodnight"]
TURNS = [(1, 30), (2, 35), (3, 23), (4, 12)]      # (turns in the conversation, weight)


# Checks an intent needs that the bank leaves to us.
EXTRA_CHECKS = {"who-are-you": ["says_its_ai"]}


def _pick_weighted(rng, items, weight):
    return rng.choices(items, weights=[weight(i) for i in items], k=1)[0]


def _fill(rng, text):
    slots = {"person": lambda: rng.choice(B.PEOPLE), "rel": lambda: rng.choice(B.RELS),
             "sign": lambda: rng.choice(B.SIGNS), "planet": lambda: rng.choice(B.PLANETS),
             "day": lambda: rng.choice(B.DAYS)}
    return re.sub(r"\{(\w+)\}", lambda m: slots[m.group(1)]() if m.group(1) in slots else m.group(0), text)


def spoken(rng, text):
    """A typed message as speech recognition would deliver it."""
    t = text.lower()
    for pat, rep in B.SPOKEN["MISHEARS"]:
        t = re.sub(pat, rep, t)
    t = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", t)
    t = re.sub(r"[^\w\s'áéíóúñü]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if t and rng.random() < 0.35:
        t = f"{rng.choice(B.SPOKEN['FILLERS'])} {t}"
    return t or "huh"           # a typed "?" is, out loud, a "huh"


def _persona(rng):
    y = rng.randint(1965, 2006)
    known = rng.random() > 0.1
    return {"name": rng.choice(NAMES),
            "date": f"{y}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            "time": f"{rng.randint(0, 23):02d}:{rng.choice([0, 7, 15, 30, 42, 55]):02d}" if known else "",
            "city": rng.choice(CITIES),
            "plan": "plus" if rng.random() < 0.2 else "free"}


def scenarios(n=N_SCENARIOS, seed=SEED):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        intent = _pick_weighted(rng, B.INTENTS, lambda x: x.get("weight", 1))
        turns = 1 if intent["tier"] == "red" else _pick_weighted(rng, TURNS, lambda t: t[1])[0]
        msgs = [_fill(rng, rng.choice(intent["openers"]))]
        pool = list(intent.get("followups") or []) + GENERIC_FOLLOWUPS
        for _ in range(turns - 1):
            msgs.append(_fill(rng, rng.choice(pool)))
        checkin = None
        if rng.random() < 0.55:
            checkin = {"mood": rng.choices(range(1, 11), weights=[1, 2, 4, 6, 8, 9, 9, 7, 4, 2])[0],
                       "text": rng.choice(CHECKIN_NOTES)}
        sid = f"s{i:04d}-{intent['id']}"
        out.append({"id": sid, "intent": intent["id"], "tier": intent["tier"],
                    "checks": list(intent.get("checks") or []) + EXTRA_CHECKS.get(intent["id"], []), "persona": _persona(rng),
                    "checkin": checkin, "text": msgs,
                    "voice": [spoken(rng, m) for m in msgs]})
    return out


def conversations(n=N_SCENARIOS, seed=SEED):
    """Every scenario in both channels: 2n conversations."""
    for s in scenarios(n, seed):
        for ch in ("text", "voice"):
            yield {**s, "channel": ch, "messages": s[ch], "cid": f"{s['id']}-{ch}"}


def sample(scs, k, seed=SEED):
    """A stratified sample: every intent represented, in proportion."""
    by = {}
    for s in scs:
        by.setdefault(s["intent"], []).append(s)
    rng = random.Random(seed + 1)
    picked = [rng.choice(v) for v in by.values()]
    rest = [s for s in scs if s not in picked]
    rng.shuffle(rest)
    return (picked + rest)[:max(k, len(picked))]
