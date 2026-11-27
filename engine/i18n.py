"""
Her language, for the words the server writes itself.

The model already answers in whatever language she writes in. What it does not
write — the free-cap message, the "lost my thread" fallback, the reading's
starter questions, the compatibility report's theme titles and notes — was
English for everyone, so Lucía, who wrote "no hablo ingles", got excellent
Spanish from the model and an English wall from the app around it (audit
MEDIUM-6).

Scope, on purpose: detect the language from her own messages, keep it on her
profile, and localise the server-generated strings that appear in the chat
flow and the compatibility report. Spanish is the one non-English language the
audit saw; adding another is a column in each table below. What stays English
is listed in `ENGLISH_ONLY`, so it is a known list rather than a surprise.

Detection is a word count, not a model: a message counts only when it is
clearly one language (two markers, twice the other side), so "ok" or "jake"
never flips anyone.
"""
import re

from engine.store import load, save

SUPPORTED = ("en", "es")

ENGLISH_ONLY = [
    "app chrome in app/static/index.html (buttons, headings, tab names)",
    "the natal reading's opening, cards and placement narratives (written at signup, before "
    "any message shows her language)",
    "compatibility contact lines and their meanings (engine/compat.PAIR), house overlays, "
    "the balance notes",
    "the daily line and push notifications",
    "the grounding check reads English planet and sign names only, so a Spanish reply's "
    "claims are not scored",
]

_MARKERS = {
    "es": {"que", "qué", "el", "la", "los", "las", "de", "del", "y", "es", "mi", "mis", "tu",
           "por", "para", "como", "cómo", "pero", "se", "una", "un", "esta", "está", "estoy",
           "hola", "gracias", "porque", "muy", "más", "cuál", "cual", "dime", "somos", "tengo",
           "quiero", "novio", "novia", "hoy", "ayer", "mañana", "también", "eso", "esto", "yo",
           "él", "ella", "signo", "astros", "pregunta", "buenas", "noches", "hablo", "peleamos",
           "explicame", "explícame", "siento", "contesta", "dice", "verdad", "mucho", "semana"},
    "en": {"the", "is", "and", "my", "i", "you", "what", "how", "why", "are", "was", "it", "to",
           "of", "that", "this", "me", "with", "for", "im", "i'm", "about", "do", "does",
           "should", "he", "she", "we", "today", "just", "like", "so", "but", "have", "been",
           "his", "her", "him", "they", "can", "if", "when", "really", "think"},
}


def detect(text):
    """'es', 'en' or None when the message doesn't clearly say."""
    words = re.findall(r"[a-záéíóúñü']+", (text or "").lower())
    hits = {k: sum(w in m for w in words) for k, m in _MARKERS.items()}
    best = max(hits, key=hits.get)
    other = min(hits.values()) if len(hits) == 2 else 0
    if hits[best] >= 2 and hits[best] >= 2 * other:
        return best
    return None


def observe(uid, text):
    """Record her language from a message she sent. Never fails a message."""
    got = detect(text)
    if not got:
        return None
    prof = load(uid, "profile", None)
    if prof and prof.get("lang") != got:
        prof["lang"] = got
        save(uid, "profile", prof)
    return got


def lang_of(uid):
    lang = ((load(uid, "profile", {}) or {}).get("lang")) or "en"
    return lang if lang in SUPPORTED else "en"


TEXT = {
    "cap": {
        "en": ("That's three for today. Tomorrow's reading and check-in are yours either way, "
               "and the conversation opens again in the morning."),
        "es": ("Son tres por hoy. La lectura y el check-in de mañana son tuyos de todas formas, "
               "y la conversación se abre otra vez por la mañana."),
    },
    "fallback": {
        "en": ("I lost my thread there and didn't get an answer out. Ask me again? That one "
               "didn't count against today."),
        "es": ("Perdí el hilo y no me salió la respuesta. ¿Me preguntas otra vez? Esa no contó "
               "para hoy."),
    },
}


def text(key, lang):
    row = TEXT[key]
    return row.get(lang) or row["en"]


# --------------------------------------------------------- reading starters

STARTERS_ES = {
    "Why do I need so much time on my own?": "¿Por qué necesito tanto tiempo a solas?",
    "Why do I always overexplain myself?": "¿Por qué siempre me explico de más?",
    "Why do I lose myself in relationships?": "¿Por qué me pierdo en las relaciones?",
    "Why am I so weird about money?": "¿Por qué soy tan rara con el dinero?",
    "Why does work feel like my whole identity?": "¿Por qué siento que el trabajo es toda mi identidad?",
    "Why is it so hard for me to let something go?": "¿Por qué me cuesta tanto soltar algo?",
    "Why do I start more than I finish?": "¿Por qué empiezo más cosas de las que termino?",
    "How do I tell my feelings apart from everyone else's?":
        "¿Cómo distingo mis emociones de las de los demás?",
    "What should I know about today?": "¿Qué debería saber sobre hoy?",
}
PLANETS_ES = {"Sun": "Sol", "Moon": "Luna", "Mercury": "Mercurio", "Venus": "Venus",
              "Mars": "Marte", "Jupiter": "Júpiter", "Saturn": "Saturno", "Uranus": "Urano",
              "Neptune": "Neptuno", "Pluto": "Plutón", "Chiron": "Quirón",
              "North Node": "Nodo Norte"}
_TENSION = re.compile(r"^What is the tension between my (.+) and my (.+) actually about\?$")


_LEGACY = re.compile(r"^What is the (\w+)–(\w+) tension actually about\?$")


def _modern(items):
    """Readings cached before the starters were reworded still say
    'Mars–TrueNode tension' or offer the Ascendant–Midheaven square."""
    out = []
    for s in items:
        m = _LEGACY.match(s)
        if m:
            a, b = ("North Node" if x == "TrueNode" else x for x in m.groups())
            if {a, b} <= {"Ascendant", "Midheaven"}:
                continue
            s = f"What is the tension between my {a} and my {b} actually about?"
        out.append(s)
    return out


def starters(items, lang):
    items = _modern(items)
    if lang != "es":
        return items
    out = []
    for s in items:
        m = _TENSION.match(s)
        if m:
            a, b = (PLANETS_ES.get(x, x) for x in m.groups())
            out.append(f"¿De qué se trata de verdad la tensión entre mi {a} y mi {b}?")
        else:
            out.append(STARTERS_ES.get(s, s))
    return out


# ------------------------------------------------------ compatibility report

THEMES_ES = {
    "fate": ("Destino y rumbo", "si esto se siente como que va a algún lado"),
    "healing": ("Heridas viejas", "dónde se pueden lastimar o sanar"),
    "intensity": ("Intensidad", "profundidad, atracción y poder"),
    "commitment": ("Compromiso", "si está hecho para durar"),
    "excitement": ("Emoción", "frescura e imprevisibilidad"),
    "idealism": ("Romance e ilusión", "el romance, y qué tan claro se ven"),
    "growth": ("Crecimiento y suerte", "cuánto se expanden el uno al otro"),
    "attraction": ("Atracción", "química y deseo"),
    "affection": ("Afecto", "cómo se da y se recibe el cariño"),
    "emotional": ("Vínculo emocional", "qué tan seguros están sus sentimientos el uno con el otro"),
    "communication": ("Comunicación", "cómo se hablan y se entienden"),
    "identity": ("Quiénes son juntos", "cómo encajan en lo básico"),
}
ABSENT_ES = {
    "communication": "Nada une sus mentes de forma directa. Hablar será algo que hagan a propósito, no por inercia.",
    "emotional": "No hay contactos emocionales directos. La cercanía vendrá del tiempo y de elegirse, no del instinto.",
    "attraction": "No hay contactos fuertes de química. No es un impedimento: algunas de las mejores parejas se encienden despacio.",
    "commitment": "Nada en las cartas ancla esto a largo plazo. Que dure será una decisión de los dos.",
    "affection": "No hay contactos directos de Venus. Puede que el cariño necesite decirse más de lo normal.",
}
PRESENCE_ES = {"strong": "fuerte", "present": "presente", "faint": "leve", "absent": "ausente"}
TONE_ES = {"flowing": "fluido", "charged": "cargado", "mixed": "mixto"}
SIGNS_ES = {"Aries": "Aries", "Taurus": "Tauro", "Gemini": "Géminis", "Cancer": "Cáncer",
            "Leo": "Leo", "Virgo": "Virgo", "Libra": "Libra", "Scorpio": "Escorpio",
            "Sagittarius": "Sagitario", "Capricorn": "Capricornio", "Aquarius": "Acuario",
            "Pisces": "Piscis"}
COMPOSITE_SUN_ES = {
    "Aries": "Esta relación quiere empezar cosas. Audaz, rápida, un poco impaciente.",
    "Taurus": "Esta relación quiere seguridad y placer, construidos despacio.",
    "Gemini": "Esta relación vive de la conversación, la curiosidad y la variedad.",
    "Cancer": "Esta relación es sobre el hogar, el cuidado y sentirse a salvo.",
    "Leo": "Esta relación quiere ser vista: cálida, orgullosa, un poco dramática.",
    "Virgo": "Esta relación funciona mejor cuando es útil: ayudar, arreglar, mejorar.",
    "Libra": "Esta relación es sobre la pareja en sí: justicia, belleza, equilibrio.",
    "Scorpio": "Esta relación va a fondo o no va. Privada e intensa.",
    "Sagittarius": "Esta relación quiere aventura, sentido y espacio para moverse.",
    "Capricorn": "Esta relación se toma en serio: quiere metas, estructura y futuro.",
    "Aquarius": "Esta relación valora la amistad y la libertad por encima de lo convencional.",
    "Pisces": "Esta relación es tierna, intuitiva y un poco soñadora.",
}
COMPOSITE_MOON_ES = {
    "Aries": "Juntos sienten rápido y lo dicen más rápido.",
    "Taurus": "Juntos necesitan comodidad, rutina y contacto.",
    "Gemini": "Juntos procesan lo que sienten hablándolo.",
    "Cancer": "Juntos hacen nido. La seguridad emocional va primero.",
    "Leo": "Juntos necesitan calidez, juego y aprecio.",
    "Virgo": "Juntos muestran cariño ocupándose de las cosas.",
    "Libra": "Juntos necesitan armonía, a veces a costa de decir lo que está mal.",
    "Scorpio": "Juntos sienten todo a fondo.",
    "Sagittarius": "Juntos necesitan espacio y algo que esperar con ganas.",
    "Capricorn": "Juntos mantienen los sentimientos estables y un poco guardados.",
    "Aquarius": "Juntos se dan espacio: cálidos, pero sin apegarse.",
    "Pisces": "Juntos absorben el ánimo del otro. Suaves, porosos.",
}
_STAY = re.compile(r"Staying power scores (\d+), though")
_IN_PLAY = re.compile(r"^(\w+) is in play, but through contacts read under (.+?): (.+)\.$")


def compat_report(rep, lang):
    """A copy of the deep report with its own words in her language. Contact
    lines stay English (ENGLISH_ONLY)."""
    if lang != "es":
        return rep
    titles = {k: v[0] for k, v in THEMES_ES.items()}
    themes = []
    for t in rep.get("themes", []):
        t = dict(t)
        title, means = THEMES_ES.get(t.get("key"), (t["title"], t.get("means")))
        t.update(title=title, means=means,
                 presence=PRESENCE_ES.get(t.get("presence"), t.get("presence")),
                 tone=TONE_ES.get(t.get("tone"), t.get("tone")))
        note = t.get("note")
        m = _IN_PLAY.match(note or "")
        if m:
            planet = PLANETS_ES.get(m.group(1), m.group(1))
            where = m.group(2)
            for key, en_title in _theme_titles():
                where = where.replace(en_title, titles.get(key, en_title))
            t["note"] = f"{planet} está presente, pero por contactos que se leen en {where.replace(' and ', ' y ')}: {m.group(3)}."
        elif note:
            t["note"] = ABSENT_ES.get(t.get("key"), note)
        themes.append(t)
    comp = dict(rep.get("composite") or {})
    sun = comp.get("signs", {}).get("Sun")
    if sun in COMPOSITE_SUN_ES:
        stay = _STAY.search(comp.get("sun") or "")
        comp["sun"] = COMPOSITE_SUN_ES[sun] + (
            f" Aun así, la permanencia marca {stay.group(1)}: durar es algo que construirían "
            "a propósito, no algo dado." if stay else "")
    moon = comp.get("signs", {}).get("Moon")
    if comp.get("moon") and moon in COMPOSITE_MOON_ES:
        comp["moon"] = COMPOSITE_MOON_ES[moon]
    if comp.get("signs"):
        comp["signs"] = {k: SIGNS_ES.get(v, v) for k, v in comp["signs"].items()}
    unc = [("Sin hora de nacimiento no hay Ascendente ni casas en esa carta, y los contactos "
            "de la Luna se revisan a lo largo de todo el día: solo se muestran los que se "
            "sostienen la mayor parte.") if u.startswith("Without") else
           ("La hora de nacimiento es aproximada, así que el Ascendente y las casas solo se "
            "muestran donde se sostienen en toda la ventana.") if "approximate" in u else u
           for u in rep.get("uncertainty", [])]
    return {**rep, "themes": themes, "composite": comp, "uncertainty": unc, "lang": "es"}


def _theme_titles():
    from engine.compat import THEMES
    return [(k, t) for k, t, _r in THEMES]
