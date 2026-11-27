"""
The part that makes this a test and not just an app.

Test 2 asks one question: when My Sign recalls something from week one, does she
light up or recoil? A rig that only runs the loop cannot answer that. So every
outbound message is tagged with whether it contained a memory callback, and every
inbound reply is measured against the message it answered.

Second question, free of charge: how much does the operator have to fix? If edit
distance falls over fourteen days, the prompt is close to shippable. If it
doesn't, the voice is not there yet regardless of what participants say.
"""
import datetime as dt
import difflib
import re
import statistics

from engine.store import load, save

DATE_RE = re.compile(r"\b(\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*"
                     r"|last (?:week|month|Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday))\b", re.I)


def tag_outbound(user_id, msg_id, draft_text, sent_text, context_block, verdict):
    """Called at send time. Everything needed for analysis, captured once."""
    callback = bool(DATE_RE.search(sent_text)) or "you told me" in sent_text.lower() \
        or "you logged" in sent_text.lower() or "you said" in sent_text.lower()
    quoted = [q for q in re.findall(r'"([^"]{6,120})"', sent_text)
              if q.lower() in context_block.lower()]
    rec = {
        "msg_id": msg_id,
        "sent_at": dt.datetime.now().isoformat(timespec="seconds"),
        "salience_band": verdict["band"],
        "salience_score": verdict["score"],
        "has_callback": callback,
        "quoted_her_words": quoted,
        "edit_distance": round(1 - difflib.SequenceMatcher(None, draft_text, sent_text).ratio(), 3),
        "was_edited": draft_text.strip() != sent_text.strip(),
        "reply": None,
    }
    log = load(user_id, "metrics", [])
    log.append(rec)
    save(user_id, "metrics", log)
    return rec


def tag_reply(user_id, msg_id, reply_text, received_at=None):
    """Attach her response to the message it answered. Latency and length are the
    crude proxies for 'lit up'; the qualitative read comes from the transcript."""
    received_at = received_at or dt.datetime.now()
    log = load(user_id, "metrics", [])
    for rec in reversed(log):
        if rec["msg_id"] == msg_id:
            sent = dt.datetime.fromisoformat(rec["sent_at"])
            rec["reply"] = {
                "text": reply_text,
                "latency_s": int((received_at - sent).total_seconds()),
                "chars": len(reply_text),
                "words": len(reply_text.split()),
            }
            break
    save(user_id, "metrics", log)


def report(user_ids):
    """Run at the end of the fourteen days. This is the deliverable."""
    cb, no_cb, edits_by_week = [], [], {}
    for uid in user_ids:
        for r in load(uid, "metrics", []):
            wk = (dt.datetime.fromisoformat(r["sent_at"]).date().isocalendar()[1])
            edits_by_week.setdefault(wk, []).append(r["edit_distance"])
            if r.get("reply"):
                (cb if r["has_callback"] else no_cb).append(r["reply"])

    def summarize(rs, label):
        if not rs:
            return f"  {label:22} no data"
        lat = statistics.median(r["latency_s"] for r in rs)
        wds = statistics.median(r["words"] for r in rs)
        return (f"  {label:22} n={len(rs):3}  median reply {lat//60:4}m  "
                f"{wds:3.0f} words")

    out = ["MEMORY CALLBACK EFFECT",
           summarize(cb, "with callback"),
           summarize(no_cb, "without callback"),
           "",
           "OPERATOR EDIT DISTANCE BY WEEK  (falling = voice converging)"]
    for wk in sorted(edits_by_week):
        vals = edits_by_week[wk]
        out.append(f"  week {wk}   n={len(vals):3}  mean {statistics.mean(vals):.3f}")
    out += ["",
            "Latency and word count are proxies. Read the actual transcripts for",
            "the recoil cases — a short fast reply can be delight or alarm, and the",
            "difference is the whole finding."]
    return "\n".join(out)
