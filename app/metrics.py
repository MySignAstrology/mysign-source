"""
Instrumentation for the question Test 2 asks.

A rig that only runs the loop cannot tell you whether week-two recall lands as
magic or as surveillance. So every outbound message records whether it contained
a memory callback, and every reply is measured against the message it answered.
"""
import datetime as dt
import re
import statistics

from engine.store import load, save

DATE_RE = re.compile(
    r"\b(\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*"
    r"|last (?:week|month|Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday))\b", re.I)
PHRASES = ("you told me", "you logged", "you said", "you've said", "both times",
           "every time you", "last time you")


def looks_like_callback(text, context_block=""):
    low = text.lower()
    if DATE_RE.search(text) or any(p in low for p in PHRASES):
        return True
    quoted = re.findall(r'"([^"]{6,120})"', text)
    return any(q.lower() in context_block.lower() for q in quoted)


def tag_outbound(uid, msg_id, text, context_block, verdict, tier="green"):
    rec = {"msg_id": msg_id,
           "sent_at": dt.datetime.now().isoformat(timespec="seconds"),
           "band": verdict.get("band"), "score": verdict.get("score"), "tier": tier,
           "has_callback": looks_like_callback(text, context_block),
           "chars": len(text), "reply": None}
    # The last 500 only. Unbounded, this crossed the item size ceiling after
    # about 1,900 replies — and since it is written before every reply, that
    # user would have got a 500 on every message from then on.
    log = load(uid, "metrics", [])[-499:]
    log.append(rec)
    save(uid, "metrics", log)
    return rec


def tag_reply(uid, msg_id, reply_text):
    log = load(uid, "metrics", [])
    for rec in reversed(log):
        if rec["msg_id"] == msg_id and rec["reply"] is None:
            sent = dt.datetime.fromisoformat(rec["sent_at"])
            rec["reply"] = {
                "latency_s": max(0, int((dt.datetime.now() - sent).total_seconds())),
                "words": len(reply_text.split()), "chars": len(reply_text)}
            break
    save(uid, "metrics", log)


def report(uids):
    cb, no_cb = [], []
    for uid in uids:
        for r in load(uid, "metrics", []):
            if r.get("reply"):
                (cb if r["has_callback"] else no_cb).append(r["reply"])

    def line(rs, label):
        if not rs:
            return f"  {label:22} no data"
        return (f"  {label:22} n={len(rs):3}  "
                f"median reply {statistics.median(r['latency_s'] for r in rs)//60:4}m  "
                f"{statistics.median(r['words'] for r in rs):3.0f} words")

    return "\n".join(["MEMORY CALLBACK EFFECT",
                      line(cb, "with callback"), line(no_cb, "without callback"), "",
                      "Latency and word count are proxies. Read the transcripts for",
                      "the recoil cases — a fast short reply can be delight or alarm."])
