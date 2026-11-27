"""
The free reading on the website: a stranger's first taste, before any sign-up.

Computed text only, so it costs no model call and can't invent anything. Three
sections are shown; the rest are named, never sent, so "locked" means locked.
Nothing here is stored: the birth details live for one request.
"""
from engine import interpret as I
from engine import profile

# First choice leads and is the line people share.
_ORDER = ("toxic_trait", "in_love", "misread", "when_mad", "at_work")
_NEEDS_SUN = {"toxic_trait", "misread"}
_NEEDS_MOON = {"misread"}
_SHOWN = 3


def build(chart, time_known, birth_ymd):
    a = I.analyze(chart, time_known=time_known, birth_ymd=birth_ymd)
    b = a["big3"]
    # Without a birth time a Sun or Moon can sit either side of a sign change
    # that day. A line resting on one it can't vouch for is left out. Venus, Mars
    # and Mercury use the noon chart, the same as the app does.
    sun_ok, moon_ok = a["sun_sign_certain"], a["moon_sign_certain"]
    out = profile.fallback(chart, a, time_known)

    picked = []
    for key in _ORDER:
        if not out.get(key) or (key in _NEEDS_SUN and not sun_ok) or (key in _NEEDS_MOON and not moon_ok):
            continue
        picked.append(key)
    picked = picked[:_SHOWN]
    titles = dict(profile.SECTIONS)

    signs = []
    if sun_ok:
        signs.append(f"{b['sun']['sign']} Sun")
    if moon_ok:
        signs.append(f"{b['moon']['sign']} Moon")
    if time_known and b.get("rising"):
        signs.append(f"{b['rising']['sign']} rising")

    return {
        "signs": signs,
        "time_known": time_known,
        "sections": [{"key": k, "title": titles[k], **out[k]} for k in picked],
        "locked": [t for k, t in profile.SECTIONS if k not in picked and out.get(k)],
    }


def glimpse(chart, birth_ymd):
    """
    One line from a birthday alone, before the visitor has given a city or a time.

    The chart is the noon one, so only what a date can vouch for is used: the
    Sun's sign, and only when the Sun doesn't change sign that day. On the one
    day in thirty it does, there is no line, and the caller asks for the city
    first instead of guessing.
    """
    full = build(chart, False, birth_ymd)
    first = full["sections"][0] if full["sections"] else None
    sun = [s for s in full["signs"] if s.endswith(" Sun")]
    if not first or not sun:
        return {"signs": [], "section": None}
    return {"signs": sun, "section": first}
