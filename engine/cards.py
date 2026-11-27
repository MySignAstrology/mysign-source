"""
Shareable cards, as SVG.

Item 98. Co–Star grew on screenshots; the card is the thing that gets posted.
SVG means no image library in the Lambda, crisp at any size, and the brand mark
and fonts come along for free. She saves or screenshots it herself — the card
is hers, so it needs her session, and there is no public URL for it to leak
through.

Text is escaped. A name is user input.
"""
from xml.sax.saxutils import escape

# Velvet Sky, the same tokens the app uses: wine-black ground, oxblood, blush,
# champagne. A card that looks like another app is an ad for nobody.
BG, OX, BLUSH, CHAMP, INK, INK2 = "#0F0B0D", "#8E2A3B", "#E7A79C", "#E6CFA8", "#F5EEE6", "#BFAFA9"
OX_LIGHT = "#C7586A"        # oxblood lifted enough to read on the dark ground
SERIF = "Instrument Serif,Didot,Georgia,serif"
SANS = "Geist,Helvetica Neue,Arial,sans-serif"
MONO = "Geist Mono,ui-monospace,Menlo,monospace"

MARK = ('<path d="M 60 3 C 61.27 52.02, 66.86 58.52, 109 60 C 66.86 61.48, 61.27 67.98, '
        '60 117 C 58.73 67.98, 53.14 61.48, 11 60 C 53.14 58.52, 58.73 52.02, 60 3 Z"/>')

FONTS = '<style>@import url("/fonts/fonts.css");</style>'

# The night behind every card: wine at the top falling to black, a warm oxblood
# bloom low down where the sky would be setting.
DEFS = (f'<defs><radialGradient id="sky" cx="50%" cy="30%" r="85%">'
        f'<stop offset="0" stop-color="#3A1520"/><stop offset=".55" stop-color="#1C0F14"/>'
        f'<stop offset="1" stop-color="{BG}"/></radialGradient>'
        f'<radialGradient id="ember" cx="50%" cy="100%" r="60%">'
        f'<stop offset="0" stop-color="{OX}" stop-opacity=".45"/>'
        f'<stop offset="1" stop-color="{OX}" stop-opacity="0"/></radialGradient></defs>')


def _frame(w, h, inner, foot="MY SIGN"):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}">{FONTS}{DEFS}'
            f'<rect width="{w}" height="{h}" rx="28" fill="url(#sky)"/>'
            f'<rect width="{w}" height="{h}" rx="28" fill="url(#ember)"/>'
            f'<g transform="translate({w-88},40) scale(.4)" fill="{CHAMP}">{MARK}</g>'
            f'{inner}'
            f'<text x="48" y="{h-40}" font-family="{MONO}" font-size="16" '
            f'letter-spacing="3" fill="{INK2}">{foot}</text></svg>')


# The foot on cards that show planets. True because every position is held to
# NASA JPL Horizons (engine/accuracy.py, /accuracy). Never on pattern_card: that
# card is her own check-ins, not the sky.
SKY_FOOT = "MY SIGN · PLANETS CHECKED AGAINST NASA"


def chart_card(name, big3, birth_line, tradition=None):
    """The big three. The card someone posts the day they sign up."""
    n = escape(name or "")
    rows = ""
    for i, (label, value, sub) in enumerate(big3):
        y = 250 + i * 118
        rows += (f'<text x="48" y="{y}" font-family="{MONO}" font-size="16" '
                 f'letter-spacing="3" fill="{CHAMP}">{escape(label).upper()}</text>'
                 f'<text x="48" y="{y+52}" font-family="{SERIF}" '
                 f'font-size="56" fill="{INK}">{escape(value)}</text>')
        if sub:
            rows += (f'<text x="48" y="{y+84}" font-family="{SANS}" font-size="20" '
                     f'fill="{INK2}">{escape(sub)}</text>')
    head = (f'<text x="48" y="120" font-family="{SERIF}" font-size="44" '
            f'fill="{INK}">{n}</text>'
            f'<text x="48" y="156" font-family="{SANS}" font-size="20" fill="{INK2}">'
            f'{escape(birth_line or "")}</text>')
    foot = (f'<text x="48" y="{680}" font-family="{SANS}" font-size="16" fill="{INK2}">'
            f'{escape(tradition)}</text>') if tradition else ""
    return _frame(720, 760, head + rows + foot, foot=SKY_FOOT)


def today_card(name, date_line, band, headline, meaning):
    """The day's top aspect. The card someone posts when it was uncannily right."""
    n = escape(name or "")
    band_txt = {"high": "A loud day", "medium": "Something moving", "low": "A quiet day"}.get(band, "")
    body = _wrap(escape(meaning or ""), 40)
    lines = "".join(f'<text x="48" y="{372 + i*34}" font-family="{SANS}" '
                    f'font-size="24" fill="{INK}">{line}</text>' for i, line in enumerate(body[:5]))
    inner = (f'<text x="48" y="110" font-family="{MONO}" font-size="16" '
             f'letter-spacing="3" fill="{CHAMP}">{escape(date_line).upper()}</text>'
             f'<text x="48" y="170" font-family="{SERIF}" font-size="48" '
             f'fill="{INK}">{escape(band_txt)}</text>'
             f'<text x="48" y="260" font-family="{MONO}" font-size="20" '
             f'fill="{CHAMP}">{escape(headline or "")}</text>'
             f'{lines}'
             f'<text x="48" y="600" font-family="{SANS}" font-size="18" fill="{INK2}">'
             f'{n}</text>')
    return _frame(720, 680, inner)


def line_card(date_line, line, why):
    """
    The line of the day, story-sized (1080x1920) — the format people actually
    post. The line is the hero; the transit that chose it sits underneath as
    the receipt, which is what makes it ours rather than a quote account.
    Velvet Sky, like every other card.
    """
    W, H = 1080, 1920
    words = _wrap(escape(line or ""), 18)
    size = 92 if len(words) <= 4 else 78
    top = 760 - (len(words) * size) // 2
    body = "".join(f'<text x="96" y="{top + i * int(size * 1.18)}" font-family="{SERIF}" '
                   f'font-size="{size}" fill="{INK}">{w}</text>' for i, w in enumerate(words[:7]))
    # The receipt wraps rather than running off the card: a monospace glyph is
    # ~0.6em wide plus the letter-spacing, across the 888px between margins.
    # Shrink a step at a time until it fits in three lines; whole words only.
    because = (("because " + why) if why else "a quiet sky").upper()
    for rsize in (30, 28, 26, 24):
        rlines = _wrap(because, int((W - 192) / (rsize * 0.6 + 2)))
        if len(rlines) <= 3:
            break
    ry = top + len(words[:7]) * int(size * 1.18) + 70
    reason = "".join(f'<text x="96" y="{ry + i * int(rsize * 1.4)}" '
                     f'font-family="{MONO}" font-size="{rsize}" letter-spacing="2" fill="{CHAMP}">'
                     f'{escape(rl)}</text>' for i, rl in enumerate(rlines))
    inner = (f'<text x="96" y="230" font-family="{MONO}" font-size="30" letter-spacing="5" '
             f'fill="{CHAMP}">{escape((date_line or "").upper())}</text>'
             f'<text x="96" y="300" font-family="{SANS}" font-size="34" fill="{INK2}">'
             f'my line for today</text>{body}{reason}')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
            f'{FONTS}{DEFS}<rect width="{W}" height="{H}" fill="url(#sky)"/>'
            f'<rect width="{W}" height="{H}" fill="url(#ember)"/>'
            f'<g transform="translate({W-200},120) scale(.9)" fill="{CHAMP}">{MARK}</g>'
            f'{inner}<text x="96" y="{H-120}" font-family="{MONO}" font-size="30" '
            f'letter-spacing="6" fill="{INK2}">MY SIGN</text></svg>')


def _mood_hue(m):
    """The app's 1-10 colour (moodHue in index.html), so the card matches."""
    m = max(1, min(10, int(m)))
    return f"hsl({250 + (m - 1) * 13} {18 + (m - 1) * 5}% {56 + (m - 1) * .6:.1f}%)"


def week_card(range_line, days, avg, words, best_line):
    """
    Her week in feelings, story-sized. Bars for the days she checked in, the
    average, her feeling words and her best day. Only the fixed vocabulary and
    numbers: nothing she typed is ever on a card.
    days: [(weekday letter, mood or None)] Monday first.
    """
    W, H = 1080, 1920
    base, top, bw, gap = 1120, 560, 104, 22
    x0 = (W - (7 * bw + 6 * gap)) // 2
    bars = ""
    for i, (letter, m) in enumerate(days[:7]):
        x = x0 + i * (bw + gap)
        if m:
            h = int((base - top) * m / 10)
            bars += (f'<rect x="{x}" y="{base - h}" width="{bw}" height="{h}" rx="18" fill="{_mood_hue(m)}"/>'
                     f'<text x="{x + bw // 2}" y="{base - h - 22}" text-anchor="middle" font-family="{SANS}" '
                     f'font-size="34" fill="{INK}">{m}</text>')
        else:
            bars += f'<rect x="{x}" y="{base - 14}" width="{bw}" height="14" rx="7" fill="{INK2}" opacity=".25"/>'
        bars += (f'<text x="{x + bw // 2}" y="{base + 56}" text-anchor="middle" font-family="{MONO}" '
                 f'font-size="28" fill="{INK2}">{escape(letter)}</text>')
    wl = ", ".join(escape(w) for w in (words or [])[:3])
    inner = (f'<text x="96" y="230" font-family="{MONO}" font-size="30" letter-spacing="5" '
             f'fill="{CHAMP}">{escape((range_line or "").upper())}</text>'
             f'<text x="96" y="330" font-family="{SERIF}" font-size="96" fill="{INK}">my week,</text>'
             f'<text x="96" y="430" font-family="{SERIF}" font-size="96" font-style="italic" '
             f'fill="{BLUSH}">in feelings</text>{bars}'
             f'<text x="96" y="1330" font-family="{SANS}" font-size="40" fill="{INK}">'
             f'averaging {escape(str(avg))} out of 10</text>'
             + (f'<text x="96" y="1400" font-family="{SANS}" font-size="40" fill="{INK}">mostly {wl}</text>' if wl else "")
             + f'<text x="96" y="1470" font-family="{MONO}" font-size="30" letter-spacing="2" '
             f'fill="{CHAMP}">{escape((best_line or "").upper())}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
            f'{FONTS}{DEFS}<rect width="{W}" height="{H}" fill="url(#sky)"/>'
            f'<rect width="{W}" height="{H}" fill="url(#ember)"/>'
            f'<g transform="translate({W-200},120) scale(.9)" fill="{CHAMP}">{MARK}</g>'
            f'{inner}<text x="96" y="{H-120}" font-family="{MONO}" font-size="30" '
            f'letter-spacing="6" fill="{INK2}">MY SIGN</text></svg>')


def year_card(year, moods):
    """
    The year in colour, story-sized: months across, days down, one square per
    day she checked in, coloured by her 1-10. Numbers only; nothing she typed.
    moods: {"YYYY-MM-DD": mood}
    """
    import calendar
    W, H = 1080, 1920
    x0, y0, cell, gap = 96, 420, 64, 10
    out = ""
    for mi in range(12):
        x = x0 + mi * (cell + gap)
        out += (f'<text x="{x + cell // 2}" y="{y0 - 24}" text-anchor="middle" font-family="{MONO}" '
                f'font-size="26" fill="{INK2}">{"JFMAMJJASOND"[mi]}</text>')
        for day in range(1, calendar.monthrange(year, mi + 1)[1] + 1):
            m = moods.get(f"{year}-{mi + 1:02d}-{day:02d}")
            y = y0 + (day - 1) * (cell // 2 + 8)
            fill = _mood_hue(m) if m else INK2
            op = "1" if m else ".12"
            out += (f'<rect x="{x}" y="{y}" width="{cell}" height="{cell // 2}" rx="7" '
                    f'fill="{fill}" opacity="{op}"/>')
    n = len(moods)
    inner = (f'<text x="96" y="230" font-family="{MONO}" font-size="30" letter-spacing="5" '
             f'fill="{CHAMP}">{year}</text>'
             f'<text x="96" y="330" font-family="{SERIF}" font-size="88" fill="{INK}">my year, '
             f'<tspan font-style="italic" fill="{BLUSH}">in colour</tspan></text>{out}'
             f'<text x="96" y="1690" font-family="{SANS}" font-size="36" fill="{INK}">'
             f'{n} days checked in</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
            f'{FONTS}{DEFS}<rect width="{W}" height="{H}" fill="url(#sky)"/>'
            f'<rect width="{W}" height="{H}" fill="url(#ember)"/>'
            f'<g transform="translate({W-200},120) scale(.9)" fill="{CHAMP}">{MARK}</g>'
            f'{inner}<text x="96" y="{H-120}" font-family="{MONO}" font-size="30" '
            f'letter-spacing="6" fill="{INK2}">MY SIGN</text></svg>')


def _wrap(text, width):
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width and line:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


def _name(text, limit=18):
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"


# What each band means against the reference, where 50 is an ordinary pair
# (synastry._band). A low score says so: the honest number is the brand.
COMPAT_LINES = {
    "rare": "Most pairs never get this close.",
    "strong": "Well above an ordinary pair.",
    "promising": "A little above an ordinary pair.",
    "mixed": "About what an ordinary pair gets.",
    "effortful": "Below an ordinary pair. Real work, some real reward.",
    "hard going": "Well below an ordinary pair. That's the honest number.",
}


def compat_card(me, them, score, band, estimate=False, line=None):
    """
    The two of them overall, not just today: the number people send each other.
    Same rule as pair_card, names only and never birth data. The address is on
    it because this is the card that gets posted where nobody can tap a link.
    """
    w, h, cy, r, off = 1080, 1350, 470, 230, 120
    pair = f"{escape(_name(me))} &amp; {escape(_name(them))}"
    lens_op = round(.12 + .5 * max(0, min(99, score)) / 100, 3)
    lines = "".join(f'<text x="{w // 2}" y="{990 + i * 56}" text-anchor="middle" '
                    f'font-family="{SERIF}" font-size="46" fill="{INK}">{ln}</text>'
                    for i, ln in enumerate(_wrap(escape(line or COMPAT_LINES.get(band, "")), 36)[:3]))
    note = (f'<text x="{w // 2}" y="1190" text-anchor="middle" font-family="{SANS}" '
            f'font-size="26" fill="{INK2}">An estimate: no exact birth time yet</text>'
            if estimate else "")
    inner = (
        f'<text x="{w // 2}" y="150" text-anchor="middle" font-family="{SERIF}" font-style="italic" '
        f'font-size="64" fill="{INK}">{pair}</text>'
        f'<defs><clipPath id="lc"><circle cx="{w // 2 - off}" cy="{cy}" r="{r}"/></clipPath></defs>'
        f'<circle cx="{w // 2 + off}" cy="{cy}" r="{r}" fill="{BLUSH}" opacity="{lens_op}" '
        f'clip-path="url(#lc)"/>'
        f'<circle cx="{w // 2 - off}" cy="{cy}" r="{r}" fill="none" stroke="{CHAMP}" stroke-width="2"/>'
        f'<circle cx="{w // 2 + off}" cy="{cy}" r="{r}" fill="none" stroke="{BLUSH}" stroke-width="2"/>'
        f'<text x="{w // 2}" y="{cy + 50}" text-anchor="middle" font-family="{MONO}" '
        f'font-size="170" fill="{CHAMP}">{int(score)}</text>'
        f'<text x="{w // 2}" y="{cy + 310}" text-anchor="middle" font-family="{MONO}" font-size="30" '
        f'letter-spacing="6" fill="{BLUSH}">{escape(str(band)).upper()}</text>'
        f'{lines}{note}')
    return _frame(w, h, inner, foot=SKY_FOOT + " · mysignapp.com")


def pair_card(me, them, date_line, score, delta, mood, headline):
    """
    The two of them, today. The People tab is the product and until now it was
    the one thing that couldn't be posted.

    Portrait 1080x1350 so it sits whole in a feed or a story. Names only, as she
    typed them (or an initial) — never a birth date, time or place, because one
    of the two people on this card didn't make it. The number, the mood and the
    headline all come from engine/daily.py; nothing here is written for the card.
    """
    w, h, cy, r, off = 1080, 1350, 430, 210, 110
    pair = f"{escape(_name(me))} &amp; {escape(_name(them))}"
    if delta > 0:
        arrow, tone = f"▲ {delta}", BLUSH
    elif delta < 0:
        arrow, tone = f"▼ {-delta}", OX_LIGHT
    else:
        arrow, tone = "level", INK2
    body = _wrap(escape(headline or ""), 34)[:4]
    lines = "".join(f'<text x="{w // 2}" y="{1000 + i * 46}" text-anchor="middle" '
                    f'font-family="{SANS}" font-size="34" fill="{INK}">{ln}</text>'
                    for i, ln in enumerate(body))
    # The lens: where the two rings overlap, warmed by the score itself.
    lens_op = round(.12 + .5 * max(0, min(99, score)) / 100, 3)
    inner = (
        f'<defs><clipPath id="lc"><circle cx="{w // 2 - off}" cy="{cy}" r="{r}"/></clipPath></defs>'
        f'<circle cx="{w // 2 + off}" cy="{cy}" r="{r}" fill="{BLUSH}" opacity="{lens_op}" '
        f'clip-path="url(#lc)"/>'
        f'<circle cx="{w // 2 - off}" cy="{cy}" r="{r}" fill="none" stroke="{CHAMP}" stroke-width="2"/>'
        f'<circle cx="{w // 2 + off}" cy="{cy}" r="{r}" fill="none" stroke="{BLUSH}" stroke-width="2"/>'
        f'<text x="{w // 2}" y="{cy + 40}" text-anchor="middle" font-family="{MONO}" '
        f'font-size="150" fill="{CHAMP}">{int(score)}</text>'
        f'<text x="{w // 2}" y="{cy + 104}" text-anchor="middle" font-family="{MONO}" '
        f'font-size="34" fill="{tone}">{arrow}</text>'
        f'<text x="{w // 2}" y="760" text-anchor="middle" font-family="{SERIF}" '
        f'font-size="64" fill="{INK}">{pair}</text>'
        f'<text x="{w // 2}" y="812" text-anchor="middle" font-family="{MONO}" font-size="22" '
        f'letter-spacing="4" fill="{INK2}">{escape(date_line or "").upper()}</text>'
        f'<text x="{w // 2}" y="900" text-anchor="middle" font-family="{SERIF}" font-style="italic" '
        f'font-size="48" fill="{BLUSH}">{escape(mood or "")}</text>'
        f'{lines}')
    return _frame(w, h, inner, foot=SKY_FOOT + " · mysignapp.com")


# team2/memory: the pattern she found in her own data.
def pattern_card(c):
    """
    A verified pattern, for posting. Every figure is the verify() result the
    reveal showed — the support, the lift, the p-value — and the claim is about
    what SHE logged, never about a sign. No dates and no names: the condition
    and the count are the story, the days are hers.
    """
    w, h = 1080, 1350
    cond = _wrap(escape(c["condition"][:1].upper() + c["condition"][1:] + ","), 30)[:3]
    outc = _wrap(escape(f"I logged {c['outcome']}"), 30)[:2]
    lines = "".join(f'<text x="{w // 2}" y="{700 + i * 62}" text-anchor="middle" '
                    f'font-family="{SERIF}" font-size="54" fill="{INK}">{ln}</text>'
                    for i, ln in enumerate(cond + outc))
    inner = (
        f'<text x="{w // 2}" y="200" text-anchor="middle" font-family="{MONO}" font-size="24" '
        f'letter-spacing="5" fill="{CHAMP}">FOUND IN MY OWN DATA</text>'
        f'<text x="{w // 2}" y="470" text-anchor="middle" font-family="{MONO}" '
        f'font-size="190" fill="{CHAMP}">{int(c["hit"])}/{int(c["of"])}</text>'
        f'<text x="{w // 2}" y="560" text-anchor="middle" font-family="{SERIF}" font-style="italic" '
        f'font-size="44" fill="{BLUSH}">times</text>'
        f'{lines}'
        f'<text x="{w // 2}" y="1120" text-anchor="middle" font-family="{MONO}" font-size="28" '
        f'fill="{INK2}">{escape(c.get("lift_text") or str(c["lift"]) + "x my usual")}</text>'
        f'<text x="{w // 2}" y="1170" text-anchor="middle" font-family="{SANS}" font-size="24" '
        f'fill="{INK2}">straight from my own check-ins</text>')
    return _frame(w, h, inner, foot="MY SIGN · mysignapp.com")
