"""
Crisis resources by country, and the canned red replies.

988 is a US number and it was hard-coded. The geocoder accepts thirty-four
thousand cities in every country, so a woman in Manchester who says she does not
want to be here any more was being told to text a line that does not exist
where she lives.

The tables are deliberately short. A wrong crisis number is worse than a
directory link, so only lines I am confident of are listed by number; everywhere
else gets the emergency number and the international directory, and says
plainly that it is a directory. Verify each entry again before allowing signups
from that country — item 39 on the road map. Checked for this table (Oct 2026):
the national lines below are the long-standing, government- or charity-run
numbers; Argentina, Colombia and Peru have no single national crisis line I am
sure of, so they get their emergency number plus the directory.

Red is not one thing. A friend's overdose needs "call 911 now", a stalking
victim needs a domestic-abuse line and a safety plan, a 15-year-old needs to be
told kindly that the app is for adults — and every one of them used to get the
same suicide check. The model writes the red reply now (engine/redreply.py);
`fallback(kind, country)` below is what she gets when the model cannot be
reached, so it has to be right on its own.
"""

# country code -> (line label, how to reach it, extra channel or None)
LINES = {
    "US": ("988 Suicide and Crisis Lifeline", "call or text 988",
           "or text HOME to 741741"),
    "CA": ("988 Suicide Crisis Helpline", "call or text 988", None),
    "GB": ("Samaritans", "call 116 123, free, any time",
           "or text SHOUT to 85258"),
    "IE": ("Samaritans", "call 116 123, free, any time", "or text HELLO to 50808"),
    "AU": ("Lifeline", "call 13 11 14", "or text 0477 13 11 14"),
    "NZ": ("Need to talk?", "call or text 1737", None),
    "MX": ("Línea de la Vida", "call 800 911 2000, free", None),
    "ES": ("Línea 024", "call 024, free", None),
    "CL": ("Línea *4141", "call *4141 from a mobile, free", None),
    "DE": ("Telefonseelsorge", "call 0800 111 0 111, free", None),
    "FR": ("Le 3114", "call 3114, free", None),
    "NL": ("113 Zelfmoordpreventie", "call 113, or 0800-0113 free", None),
    "IN": ("Tele-MANAS", "call 14416, free", None),
    "BR": ("CVV", "call 188, free", None),
    "ZA": ("SADAG", "call 0800 567 567", None),
    "PH": ("NCMH Crisis Hotline", "call 1553 (landline, toll-free) or 0917 899 8727", None),
    "JP": ("よりそいホットライン", "call 0120 279 338, free", None),
}

# The number for an ambulance or the police, right now. Every reply that can
# mean "someone may die tonight" needs it, and the old reply had none.
EMERGENCY = {
    "US": "911", "CA": "911", "MX": "911", "AR": "911", "PH": "911",
    "GB": "999", "IE": "112 or 999", "AU": "000", "NZ": "111",
    "ES": "112", "DE": "112", "FR": "112", "NL": "112", "IN": "112",
    "CO": "123", "CL": "131 for an ambulance, 133 for the police",
    "PE": "106 for an ambulance, 105 for the police",
    "ZA": "112 from a mobile, or 10177 for an ambulance",
    "BR": "192 for an ambulance, 190 for the police",
    "JP": "119 for an ambulance, 110 for the police",
}
EMERGENCY_UNKNOWN = "your local emergency number"

# Domestic abuse and stalking. National, free lines only.
ABUSE = {
    "US": ("National Domestic Violence Hotline", "call 1-800-799-7233 or text START to 88788"),
    "GB": ("National Domestic Abuse Helpline", "call 0808 2000 247, free, any time"),
    "IE": ("Women's Aid", "call 1800 341 900, free, any time"),
    "AU": ("1800RESPECT", "call 1800 737 732, any time"),
    "NZ": ("Women's Refuge", "call 0800 733 843, any time"),
    "ES": ("016", "call 016, free"),
    "AR": ("Línea 144", "call 144, free, any time"),
    "DE": ("Hilfetelefon Gewalt gegen Frauen", "call 116 016, free, any time"),
    "FR": ("3919", "call 3919, free"),
    "BR": ("Ligue 180", "call 180, free"),
    "IN": ("Women Helpline", "call 181"),
    "ZA": ("GBV Command Centre", "call 0800 428 428, free, any time"),
}

EATING = {
    "US": ("ANAD Helpline", "call 1-888-375-7820"),
    "GB": ("Beat", "call 0808 801 0677, free"),
    "AU": ("Butterfly Foundation", "call 1800 33 4673"),
}

POISON = {
    "US": ("Poison Control", "call 1-800-222-1222, any time"),
    "AU": ("Poisons Information Centre", "call 13 11 26, any time"),
    "NZ": ("National Poisons Centre", "call 0800 764 766, any time"),
}

CHILD = {
    "US": ("Childhelp", "call or text 1-800-422-4453"),
    "CA": ("Kids Help Phone", "call 1-800-668-6868 or text CONNECT to 686868"),
    "GB": ("Childline", "call 0800 1111, free"),
    "IE": ("Childline", "call 1800 66 66 66, free"),
    "AU": ("Kids Helpline", "call 1800 55 1800, free"),
    "NZ": ("Youthline", "call 0800 376 633, free"),
    "DE": ("Nummer gegen Kummer", "call 116 111, free"),
    "FR": ("Allô Enfance en Danger", "call 119, free"),
}

DIRECTORY = ("findahelpline.com", "lists free, confidential lines for your country")

# The red sub-types. engine/safety.py classifies into these; the model reply,
# the fallback and the page severity all key off them.
KINDS = ("self_harm", "self_harm_means", "other_emergency", "abuse",
         "eating_disorder", "medical", "minor")


def _cc(country):
    return (country or "").upper()


def _named(entry):
    name, how = entry
    return f"{name}: {how}"


def resource_line(country):
    """One sentence naming the right crisis line for where she is."""
    entry = LINES.get(_cc(country))
    if entry:
        name, how, extra = entry
        s = f"{name}: {how}"
        if extra:
            s += f", {extra}"
        return s + ". They're there all day, every day."
    name, what = DIRECTORY
    # No US number for someone who is not in the US. The emergency number is
    # the one thing that works everywhere.
    return (f"{name} {what}. It's a directory, not a line — pick the one for "
            f"where you are. In an emergency, call {emergency(country)}.")


def emergency(country):
    return EMERGENCY.get(_cc(country), EMERGENCY_UNKNOWN)


def specific(kind, country):
    """The line for this kind of red in her country, or None."""
    table = {"abuse": ABUSE, "eating_disorder": EATING, "other_emergency": POISON,
             "minor": CHILD}.get(kind)
    entry = (table or {}).get(_cc(country))
    return _named(entry) if entry else None


def context_lines(country):
    """
    Where she is and what to give her, for the model's context block. The
    system prompt used to say "in the US, call or text 988" to everyone, and
    the model, never told her country, guessed — a woman in Mexico City was
    told about 988 "if you're in the US".
    """
    cc = _cc(country)
    L = [f"COUNTRY: {cc or 'unknown'}",
         f"  CRISIS LINE: {resource_line(country)}",
         f"  EMERGENCY: {emergency(country)}"]
    for kind, label in (("abuse", "ABUSE LINE"), ("eating_disorder", "EATING DISORDER LINE"),
                        ("other_emergency", "POISON LINE"), ("minor", "CHILD LINE")):
        line = specific(kind, country)
        if line:
            L.append(f"  {label}: {line}")
    if cc not in LINES:
        L.append("  (no national crisis line on file — give the emergency number and "
                 "findahelpline.com; never guess a number)")
    return L


def footer(country):
    """The few words the app footer has room for. It used to say 988 to
    everyone, under a crisis reply that had been localized."""
    entry = LINES.get(_cc(country))
    if entry:
        return entry[1]
    return "find a line at findahelpline.com"


# ------------------------------------------------------- canned fallbacks
#
# What she gets when the model cannot write the red reply: the API is down, it
# timed out, it refused. Each one has to stand on its own, so each leads with
# the thing that matters for that kind of red. None of them asks a person
# whose friend has overdosed whether she is thinking of hurting herself.

_FALLBACK = {
    "self_harm": (
        "I'm going to put the astrology down for this one, because I'd rather "
        "ask than guess.\n\n"
        "Are you safe right now?\n\n"
        "If you're thinking about ending your life or hurting yourself — {crisis} "
        "If you might act on it soon, call {emergency} now.\n\n"
        "I'm here either way. Tell me what's going on."),
    "self_harm_means": (
        "Thank you for telling me that. It matters.\n\n"
        "Right now, can you put what you'd use somewhere you can't easily get to — "
        "another room, someone else's hands, out of the house — just for tonight?\n\n"
        "If you might act on it, call {emergency} now. {crisis}\n\n"
        "I'm not going anywhere. Is anyone with you?"),
    "other_emergency": (
        "Call {emergency} now. Tell them what happened, what was taken and when, "
        "and where they are.{specific}\n\n"
        "Don't wait to see how they seem — with some overdoses, Tylenol "
        "(paracetamol) especially, a person can look fine for hours and still "
        "need treatment fast. If you can't reach them, they can send someone to "
        "check on them.\n\n"
        "I'm here. Tell me what's happening once you've called."),
    "abuse": (
        "I'm not going to answer this one with the charts. What you're describing "
        "is about your safety, and that comes first.\n\n"
        "If you're in danger right now, call {emergency}.{specific} They can "
        "help you think through a safety plan, at your pace, without you having "
        "to decide anything today.\n\n"
        "If he checks your phone, use one he can't see to call, and clear this "
        "chat if you need to. Do you feel safe where you are right now?"),
    "eating_disorder": (
        "I'm not going to pick a moon phase for this one — your body needs food "
        "whatever the sky is doing, and eating this little can hurt you.\n\n"
        "{specific_or_doctor} If you feel faint, your heart is racing or you have "
        "chest pain, call {emergency}.\n\n"
        "I'm still here to talk. What's been going on?"),
    "medical": (
        "This one's for your doctor, not the chart.\n\n"
        "Please don't stop or change a medication without talking to whoever "
        "prescribed it — stopping some suddenly can make you feel a lot worse. "
        "Your prescriber or a pharmacist can help you do it safely. If you feel "
        "unwell right now, call {emergency}.\n\n"
        "If things feel heavy — {crisis}"),
    "minor": (
        "Thank you for telling me the truth.\n\n"
        "My Sign is only for people 18 and over, so I have to stop our chats here. "
        "You didn't do anything wrong.\n\n"
        "If an adult is pressuring you or wants to meet you alone, please tell a "
        "parent, a teacher or another adult you trust.{specific} If you're in "
        "danger right now, call {emergency}.\n\n"
        "You can delete your account and everything in it from the next screen."),
}

# A second fallback in a row must not be the same words again — that is the
# loop the audit found. The repeat moves to the most urgent thing it can say.
_REPEAT = {
    "self_harm": "self_harm_means",
    "self_harm_means": (
        "I'm still here.\n\n"
        "If you're in danger now, call {emergency}. {crisis}\n\n"
        "If you can, get one person to be with you tonight — a friend, family, "
        "a neighbour. You don't have to explain everything to them."),
}


def fallback(kind, country, repeat=False):
    """The canned red reply for this kind and country. repeat=True when the
    last thing she got was already a canned red reply."""
    kind = kind if kind in _FALLBACK else "self_harm"
    body = _FALLBACK[kind]
    if repeat and kind in _REPEAT:
        r = _REPEAT[kind]
        body = _FALLBACK.get(r, r)
    crisis = resource_line(country)
    spec = specific(kind, country)
    if kind == "abuse" and not spec:
        spec_txt = f" {crisis}"
    else:
        spec_txt = f" {spec}." if spec else ""
    doctor = (f"{spec}. Or talk to a doctor you trust." if spec else
              "Please talk to a doctor you trust about how you've been eating.")
    return body.format(crisis=crisis, emergency=emergency(country),
                       specific=spec_txt, specific_or_doctor=doctor)


def reply_for(country):
    """The self-harm fallback. The SMS rig and older callers use this."""
    return fallback("self_harm", country)


def known(country):
    return _cc(country) in LINES
