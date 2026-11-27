"""
Build the zodiac gift guide: site/gifts/index.html and one page per sign, plus
the sitemap lines. Read from site/shop-catalog.js, which only lists what can be
bought, so run tools/shop_catalog.py first and this after it.

    python tools/gift_guide.py

Maker descriptions are not reused: some carry wholesale copy and claims we can't
back ("Best Seller!"). Each card's words below stick to facts from the listing.

A pendant card needs Gold in stock. The makers' photo for a sign shows the gold
pendant, so a Silver-only sign would show a photo of the wrong piece.
"""
import html
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
OUT = SITE / "gifts"
BASE = "https://mysignapp.com"

SIGNS = {
    "aries": {"name": "Aries", "glyph": "♈", "dates": "Mar 21 – Apr 19", "start": (3, 21), "element": "fire", "ruler": "Mars",
                  "who": "They would rather start something than wait for it, so a gift they can use today beats one that needs a plan.",
                  "pick": "Go for something with a bit of heat and no instructions."},
    "taurus": {"name": "Taurus", "glyph": "♉", "dates": "Apr 20 – May 20", "start": (4, 20), "element": "earth", "ruler": "Venus",
                   "who": "They notice how things feel, smell and last. A small thing made well lands better than a big thing made fast.",
                   "pick": "Choose by texture and scent. They will notice."},
    "gemini": {"name": "Gemini", "glyph": "♊", "dates": "May 21 – Jun 20", "start": (5, 21), "element": "air", "ruler": "Mercury",
                   "who": "They collect ideas and stories, and they get bored of anything with only one thing to say.",
                   "pick": "Pick something with a story they can retell."},
    "cancer": {"name": "Cancer", "glyph": "♋", "dates": "Jun 21 – Jul 22", "start": (6, 21), "element": "water", "ruler": "the Moon",
                   "who": "They remember who gave them what. A gift for them is really proof that you were paying attention.",
                   "pick": "Go for something that makes home feel better, or that they can keep close."},
    "leo": {"name": "Leo", "glyph": "♌", "dates": "Jul 23 – Aug 22", "start": (7, 23), "element": "fire", "ruler": "the Sun",
                "who": "They like being chosen on purpose. Anything that says “I saw this and thought of you” does the job.",
                "pick": "Choose the piece they will get asked about."},
    "virgo": {"name": "Virgo", "glyph": "♍", "dates": "Aug 23 – Sep 22", "start": (8, 23), "element": "earth", "ruler": "Mercury",
                  "who": "They will read the label. They like things that are useful, well made and exactly what they claim to be.",
                  "pick": "Pick the one with the clearest details."},
    "libra": {"name": "Libra", "glyph": "♎", "dates": "Sep 23 – Oct 22", "start": (9, 23), "element": "air", "ruler": "Venus",
                  "who": "They care how a thing looks and how it makes a room or an outfit feel. Packaging counts.",
                  "pick": "Go for something pretty enough to leave out on display."},
    "scorpio": {"name": "Scorpio", "glyph": "♏", "dates": "Oct 23 – Nov 21", "start": (10, 23), "element": "water", "ruler": "Mars and, in modern astrology, Pluto",
                    "who": "They are private, and they can tell a thoughtful gift from a generic one. Keep it personal and don’t overexplain it.",
                    "pick": "Pick something for quiet evenings, not for showing off."},
    "sagittarius": {"name": "Sagittarius", "glyph": "♐", "dates": "Nov 22 – Dec 21", "start": (11, 22), "element": "fire", "ruler": "Jupiter",
                        "who": "They want room to move, and a gift that needs dusting won’t last. Think about what can come along with them.",
                        "pick": "Choose something small enough to pack."},
    "capricorn": {"name": "Capricorn", "glyph": "♑", "dates": "Dec 22 – Jan 19", "start": (12, 22), "element": "earth", "ruler": "Saturn",
                      "who": "They would rather have one good thing than five fun ones, and they will use it for years.",
                      "pick": "Go for the piece that looks like it will last."},
    "aquarius": {"name": "Aquarius", "glyph": "♒", "dates": "Jan 20 – Feb 18", "start": (1, 20), "element": "air", "ruler": "Saturn and, in modern astrology, Uranus",
                     "who": "They like what other people don’t have yet. Ordinary gifts tend to get a polite thank you and a drawer.",
                     "pick": "Choose the most unusual thing here."},
    "pisces": {"name": "Pisces", "glyph": "♓", "dates": "Feb 19 – Mar 20", "start": (2, 19), "element": "water", "ruler": "Jupiter and, in modern astrology, Neptune",
                   "who": "They live partly in their own head and like anything that adds to the mood. Scent and ritual suit them well.",
                   "pick": "Pick something that changes how a room feels."},
}

# Facts from each listing, in our words. Order on the page is by price.
CARDS = {
    "zodiac-pendant-necklace": ("Double-sided pendant",
                                "Their sign’s emblem on one side, its constellation on the other. Stainless steel, adjustable from 16 to 18 inches."),
    "scented-zodiac-crystal-candle": ("Crystal candle",
                                      "Scented, with crystal chips in the wax and a sliding box that’s ready to give. Burns for about 21 hours."),
    "zodiac-perfume-oil-set-12-gemstone-infused-zodiac-scents": ("Perfume oil",
                                                                 "A 10 ml rollerball in a coconut oil base, paired with a gemstone for their sign."),
    "zodiac-magick-ritual-kits": ("Ritual kit",
                                  "A chime candle, an essential oil rollerball, a crystal, and herbs or flowers, chosen for their sign. Handmade in small batches."),
    "zodiac-oils": ("Ritual oil",
                    "A half-ounce coconut-oil ritual oil with essential oils matched to their sign, for a bath, a candle or a dab on the wrist."),
    "zodiac-pillar-candle": ("Pillar candle with pendant",
                             "A 6-inch scented pillar candle for their sign, with a pewter sign pendant to keep once it has burned down."),
    "zodiac-tarot-card-astrology-necklace": ("Tarot card necklace",
                                             "A small tarot-card charm with their sign on an 18-inch chain, in gold plate or stainless steel. Water and tarnish resistant."),
    "zodiac-stretch-bracelet": ("Stretch bracelet",
                                "Their sign and its spirit animal, engraved on the centerpiece bead of a stretch bracelet. An easy add-on."),
    "written-in-the-stars-zodiac-constellation-necklace": ("Constellation necklace",
                                                           "Their constellation, the stars they were born under, as a necklace. The big gift on this page."),
}
CHARM = ("Gemstone charm necklace", "A bronze zodiac charm with a gemstone on a bar chain, adjustable from 16 to 18 inches, with a 2 inch extender.")


def for_a(name):
    return f"an {name}" if name[0] in "AEIOU" else f"a {name}"


def esc(s):
    return html.escape(str(s), quote=True)


def money(n):
    return f"${n:g}" if float(n).is_integer() else f"${n:.2f}"


def catalog():
    text = (SITE / "shop-catalog.js").read_text()
    return json.loads(text[text.index("= [") + 2:text.rindex("]") + 1])


def picks(sign, products):
    """One card per product that can be bought in this sign, cheapest first."""
    out = []
    for p in products:
        variants = [v for v in p["variants"] if v["available"] and v["signs"] == sign]
        if not variants:
            continue
        if p["handle"] == "zodiac-pendant-necklace":
            variants = [v for v in variants if v["option1"] == "Gold"]
            if not variants:
                continue
        words = CARDS.get(p["handle"]) or (CHARM if "charm-necklace" in p["handle"] else None)
        if not words:
            continue
        v = min(variants, key=lambda x: x["price"])
        out.append({"handle": p["handle"], "vendor": p["vendor"], "name": words[0], "blurb": words[1], "price": v["price"],
                        "variant": v["id"], "image": v.get("image") or (p["images"][0] if p["images"] else ""),
                        "more": len({x["price"] for x in variants}) > 1})
    return sorted(out, key=lambda c: c["price"])


STYLE = """
body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);line-height:1.5}
.wrap{max-width:1040px;margin:0 auto;padding:0 20px}
.g-top{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:18px 0;font-size:15px}
.g-top a{color:var(--ink);text-decoration:none}.g-top .home{font-family:var(--head);font-size:22px}
.g-top a:hover{text-decoration:underline}
.eyebrow{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-2);margin:32px 0 8px}
h1{font-family:var(--head);font-weight:500;font-size:clamp(38px,8vw,64px);line-height:1.04;margin:0 0 14px}
h2{font-family:var(--head);font-weight:500;font-size:28px;margin:44px 0 14px}
.lede{font-size:19px;max-width:640px;margin:0 0 10px;color:var(--ink-2)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px;margin:24px 0;padding:0;list-style:none}
.card{display:flex;flex-direction:column;height:100%;background:var(--surface-solid);border:1px solid var(--rule);border-radius:var(--r);overflow:hidden;color:inherit;text-decoration:none}
.card:hover{border-color:var(--rule-2)}
.card img{width:100%;height:auto;aspect-ratio:1;object-fit:cover;display:block;background:var(--ground-2)}
.card .body{padding:14px 16px 16px;display:flex;flex-direction:column;gap:6px;flex:1}
.card .maker{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.card h3{font-size:17px;font-weight:600;margin:0}.card p{font-size:14px;margin:0;color:var(--ink-2);flex:1}
.card .buy{display:flex;justify-content:space-between;align-items:baseline;margin-top:6px;font-size:15px}
.card .buy b{font-weight:600}.card .buy span{color:var(--accent);font-weight:500}
.signs{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin:24px 0;padding:0;list-style:none}
.signs a{display:block;padding:14px;border:1px solid var(--rule);border-radius:var(--r-sm);background:var(--surface-solid);color:inherit;text-decoration:none}
.signs a:hover{border-color:var(--rule-2)}.signs b{display:block;font-size:16px;font-weight:600}.signs small{font-size:13px;color:var(--ink-2)}
.finder{display:flex;flex-wrap:wrap;align-items:flex-end;gap:10px;margin:20px 0}
.finder label{display:flex;flex-direction:column;gap:4px;font-size:14px}
.finder select,.finder button{font:inherit;min-height:44px;padding:8px 12px;border-radius:var(--r-sm);border:1px solid var(--rule-2);background:var(--surface-solid);color:var(--ink)}
.finder button{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);cursor:pointer}
.note{font-size:15px;color:var(--ink-2);max-width:640px}.note a,.lede a{color:var(--accent)}
.more{display:flex;flex-wrap:wrap;gap:8px 14px;margin:0 0 32px;padding:0;list-style:none;font-size:15px}.more a{color:var(--accent)}
footer{margin-top:56px;padding:24px 0 40px;border-top:1px solid var(--rule);font-size:13px;color:var(--ink-2)}
footer nav{display:flex;flex-wrap:wrap;gap:6px 14px;margin-bottom:10px}footer a{color:inherit}
"""


def shell(title, description, path, image, body, ld, track):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{BASE}{path}">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#090B17">
<meta property="og:type" content="website">
<meta property="og:site_name" content="mySign">
<meta property="og:url" content="{BASE}{path}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:image" content="{BASE}{image}">
<meta name="twitter:card" content="summary_large_image">
<link rel="stylesheet" href="/fonts/fonts.css?v=2">
<link rel="stylesheet" href="/brand.css?v=5">
<style>{STYLE}</style>
<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>
</head>
<body data-gift-guide="{esc(track)}">
<div class="wrap">
<header class="g-top">
  <a class="home" href="/">mySign</a>
  <nav aria-label="Main"><a href="/gifts">All signs</a> &nbsp; <a href="/shop">Shop</a></nav>
</header>
<main>
{body}
</main>
<footer>
  <nav aria-label="Footer"><a href="/shop">Shop</a><a href="/refunds">Refunds</a><a href="/support">Support</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="/cookies">Cookies</a></nav>
  <p>© 2026 XII Studios LLC. Prices and stock are confirmed at checkout on Shopify. Astrology here is for fun and reflection.</p>
</footer>
</div>
<script src="/consent.js?v=2" defer></script>
<script src="/track.js?v=2" defer></script>
</body>
</html>
"""


def sign_page(sign, cards):
    s = SIGNS[sign]
    low = min(c["price"] for c in cards)
    items = "\n".join(
        f"""<li><a class="card" href="/shop/products/{esc(c['handle'])}?variant={c['variant']}&utm_source=mysign&utm_medium=guide&utm_campaign=gifts-{sign}">
  <img src="{esc(c['image'])}{'&' if '?' in c['image'] else '?'}width=600" alt="{esc(c['name'])} for {s['name']}, by {esc(c['vendor'])}" loading="lazy" decoding="async" width="600" height="600">
  <span class="body"><span class="maker">{esc(c['vendor'])}</span><h3>{esc(c['name'])}</h3><p>{esc(c['blurb'])}</p>
  <span class="buy"><b>{'from ' if c['more'] else ''}{money(c['price'])}</b><span>See it →</span></span></span>
</a></li>""" for c in cards)
    others = "\n".join(f'<li><a href="/gifts/{k}">{v["name"]}</a></li>' for k, v in SIGNS.items() if k != sign)
    title = f"Gifts for {for_a(s['name'])}: {len(cards)} picks from {money(low)} | mySign"
    desc = (f"Zodiac gifts for {for_a(s['name'])} ({s['dates']}) from independent US makers, starting at {money(low)}. "
            f"Each one comes in {s['name']}, ready to give.")
    body = f"""<p class="eyebrow">{s['glyph']}&#xFE0E; {s['dates']} · {s['element']} sign, ruled by {s['ruler']}</p>
<h1>Gifts for {for_a(s['name'])}</h1>
<p class="lede">{esc(s['who'])} {esc(s['pick'])}</p>
<ul class="grid">
{items}
</ul>
<p class="note">Each piece comes from the maker named on its card and ships from the US. You’ll see the delivery estimate at checkout, so check it if you need this by a certain date.</p>
<p class="note">Buying for two people? <a href="/check">Check how their signs get along</a>, or <a href="/shop#pair">find a pair of gifts</a>.</p>
<h2>Another sign</h2>
<ul class="more">
{others}
</ul>"""
    ld = {"@context": "https://schema.org", "@type": "ItemList", "name": f"Gifts for {for_a(s['name'])}",
          "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": c["name"],
                               "url": f"https://shop.mysignapp.com/products/{c['handle']}?variant={c['variant']}"}
                              for i, c in enumerate(cards)]}
    return shell(title, desc, f"/gifts/{sign}", f"/gifts/pin-{sign}.jpg", body, ld, sign)


def hub_page(low):
    items = "\n".join(f'<li><a href="/gifts/{k}"><b>{v["glyph"]}&#xFE0E; {v["name"]}</b><small>{v["dates"]}</small></a></li>'
                      for k, v in SIGNS.items())
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    month_opts = "".join(f'<option value="{i + 1}">{m}</option>' for i, m in enumerate(months))
    day_opts = "".join(f'<option value="{d}">{d}</option>' for d in range(1, 32))
    starts = json.dumps([[v["start"][0], v["start"][1], k] for k, v in SIGNS.items()])
    title = "Zodiac gifts by sign, from independent makers | mySign"
    desc = f"Find a gift by star sign. Jewelry, candles, perfume oils and ritual kits for all 12 signs from independent US makers, from {money(low)}."
    body = f"""<p class="eyebrow">Gift guide</p>
<h1>Zodiac gifts by sign</h1>
<p class="lede">Pick their sign and see what comes in it, from independent US makers. Don’t know their sign? Their birthday will do.</p>
<form class="finder" id="finder">
  <label>Month<select id="m">{month_opts}</select></label>
  <label>Day<select id="d">{day_opts}</select></label>
  <button type="submit">Find their sign</button>
</form>
<ul class="signs">
{items}
</ul>
<p class="note">Everything here is also in <a href="/shop">the shop</a>.</p>
<script>
document.getElementById("finder").addEventListener("submit",function(e){{
  e.preventDefault();
  var m=+document.getElementById("m").value,d=+document.getElementById("d").value,s={starts},sign="capricorn";
  s.sort(function(a,b){{return a[0]-b[0]||a[1]-b[1]}});
  s.forEach(function(x){{if(m>x[0]||(m===x[0]&&d>=x[1]))sign=x[2]}});
  location.href="/gifts/"+sign;
}});
</script>"""
    ld = {"@context": "https://schema.org", "@type": "ItemList", "name": "Zodiac gifts by sign",
          "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": f"Gifts for {for_a(v['name'])}", "url": f"{BASE}/gifts/{k}"}
                              for i, (k, v) in enumerate(SIGNS.items())]}
    return shell(title, desc, "/gifts", "/gifts/pin-sagittarius.jpg", body, ld, "hub")


def build(products=None):
    """Every generated page as {path relative to site/gifts: html}."""
    products = products if products is not None else catalog()
    pages, low = {}, None
    for sign in SIGNS:
        cards = picks(sign, products)
        if not cards:
            raise SystemExit(f"no gifts in stock for {sign}")
        low = min(low or cards[0]["price"], cards[0]["price"])
        pages[f"{sign}.html"] = sign_page(sign, cards)
    pages["index.html"] = hub_page(low)
    return pages


def sitemap_block():
    lines = [f"  <url><loc>{BASE}/gifts</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>"]
    lines += [f"  <url><loc>{BASE}/gifts/{s}</loc><changefreq>weekly</changefreq><priority>0.7</priority></url>" for s in SIGNS]
    return "  <!-- gifts:begin -->\n" + "\n".join(lines) + "\n  <!-- gifts:end -->\n"


def main():
    OUT.mkdir(exist_ok=True)
    for name, text in build().items():
        (OUT / name).write_text(text)
    path = SITE / "sitemap.xml"
    xml = re.sub(r"  <!-- gifts:begin -->.*?<!-- gifts:end -->\n", "", path.read_text(), flags=re.S)
    path.write_text(xml.replace("</urlset>", sitemap_block() + "</urlset>"))
    print(f"wrote {len(SIGNS) + 1} pages to {OUT}")


if __name__ == "__main__":
    main()
