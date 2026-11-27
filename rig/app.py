"""
Test 2 rig — SMS in, approval queue out.

Fourteen days, ten participants, no app. Twilio handles transport; every outbound
message waits in a queue for a human. Drafting and sending are separate functions
on purpose: nothing this system writes reaches a real person without someone
reading it first.

  uvicorn rig.app:app --reload
  # then, in another terminal:  python -m rig.operator
"""
import datetime as dt
import os
import uuid

from fastapi import FastAPI, Form, Response
from twilio.rest import Client as Twilio

from engine.store import load, save
from rig import agent, metrics, safety
from rig.sky import sky_for

app = FastAPI(title="My Sign — Test 2 rig")

_twilio = None


def twilio():
    """Lazy — so the module imports (and the queue is inspectable) without creds."""
    global _twilio
    if _twilio is None:
        _twilio = Twilio(os.environ["TWILIO_SID"], os.environ["TWILIO_TOKEN"])
    return _twilio


def sms(to, body):
    twilio().messages.create(to=to, from_=os.environ["TWILIO_FROM"], body=body)


def _participant(phone):
    for p in load("_study", "participants", []):
        if p["phone"] == phone:
            return p
    return None


def enqueue(user_id, text, kind, context_block="", verdict=None, auto=False):
    q = load("_study", "queue", [])
    item = {"id": uuid.uuid4().hex[:8], "user_id": user_id, "kind": kind,
            "draft": text, "context_block": context_block,
            "verdict": verdict or {"band": "n/a", "score": 0},
            "queued_at": dt.datetime.now().isoformat(timespec="seconds"),
            "status": "auto-sent" if auto else "pending"}
    q.append(item)
    save("_study", "queue", q)
    return item


def send(user_id, phone, text, item=None):
    sms(phone, text)
    thread = load(user_id, "thread", [])
    mid = (item or {}).get("id") or uuid.uuid4().hex[:8]
    thread.append({"id": mid, "role": "assistant", "text": text,
                   "at": dt.datetime.now().isoformat(timespec="seconds")})
    save(user_id, "thread", thread)
    if item:
        metrics.tag_outbound(user_id, mid, item["draft"], text,
                             item.get("context_block", ""), item["verdict"])
    save(user_id, "last_outbound", mid)
    return mid


def alert_operator(body):
    """Best effort. A pager that raises would take the webhook down with it."""
    pager = os.environ.get("OPERATOR_SMS")
    if not pager:
        return False
    try:
        sms(pager, body)
        return True
    except Exception:
        return False


@app.post("/sms")
def inbound(From: str = Form(...), Body: str = Form(...)):
    p = _participant(From)
    if not p:
        return Response(status_code=204)                 # not enrolled, stay silent
    uid, text = p["user_id"], Body.strip()

    thread = load(uid, "thread", [])
    thread.append({"role": "user", "text": text,
                   "at": dt.datetime.now().isoformat(timespec="seconds")})
    save(uid, "thread", thread)

    last = load(uid, "last_outbound", None)
    if last:
        metrics.tag_reply(uid, last, text)

    # Safety runs first. Always. Before any astrology touches the message.
    # Safety runs first, always, and cannot fail: engine.safety pre-filters
    # locally and only consults the model on top of that floor.
    tier = safety.classify(text)
    if safety.is_hard_stop(tier.tier):
        try:
            send(uid, From, safety.CRISIS_REPLY)         # no queue, no delay
            sent = True
        except Exception as e:
            sent = False
            enqueue(uid, safety.CRISIS_REPLY, "crisis/SEND-FAILED",
                    context_block=f"transport error: {type(e).__name__}: {e}")
        alert_operator(
            f"RED TIER — {p['name']} ({From}). "
            + ("Canned reply sent. Go look now."
               if sent else "SEND FAILED — reply by hand NOW."))
        if sent:
            enqueue(uid, safety.CRISIS_REPLY, "crisis", auto=True)
        return Response(status_code=204)

    # Check-in of the day if we haven't got one, otherwise a chat turn.
    checkin = load(uid, f"checkin-{dt.date.today()}", None) or {"text": text}
    if "mood" not in checkin and text[:2].strip().isdigit():
        checkin = {"mood": int(text[:2].strip()), "text": text,
                   "time": dt.datetime.now().strftime("%H:%M")}
        save(uid, f"checkin-{dt.date.today()}", checkin)

    try:
        d = agent.draft(uid, p["natal"], sky_for(p), checkin,
                        history=[{"role": m["role"], "content": m["text"]}
                                 for m in thread[-8:] if m["role"] in ("user", "assistant")][:-1],
                        tier=tier.tier)
    except Exception as e:
        # Twilio retries a 5xx, which would re-run everything above and double
        # the queue. The operator gets an empty draft to write over instead —
        # a human was always going to read this one anyway.
        enqueue(uid, "", f"reply/{tier.tier}/DRAFT-FAILED",
                context_block=f"{type(e).__name__}: {e}\n\nthey said: {text}")
        alert_operator(f"Draft failed for {p['name']} ({From}): {type(e).__name__}. "
                       f"Queue has an empty item to write over.")
        return Response(status_code=204)

    enqueue(uid, d["text"], f"reply/{tier.tier}", d["context_block"], d["verdict"])
    return Response(status_code=204)


@app.post("/approve/{item_id}")
def approve(item_id: str, text: str = Form(None)):
    q = load("_study", "queue", [])
    item = next(i for i in q if i["id"] == item_id)
    p = next(x for x in load("_study", "participants", []) if x["user_id"] == item["user_id"])
    send(item["user_id"], p["phone"], text or item["draft"], item)
    item["status"] = "sent"
    item["sent_text"] = text or item["draft"]
    save("_study", "queue", q)
    return {"ok": True}
