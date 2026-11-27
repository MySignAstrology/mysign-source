"""
Forward mail sent to support@, hello@ and dmarc@ to a person.

SES receives it and drops the raw message in S3; this re-sends it. The From
has to change: SES only sends as an identity we own, and re-sending someone
else's From would fail their DMARC anyway. So it goes out as
"Jane via My Sign <forwarder@domain>" with Reply-To set to Jane — hitting
reply in the inbox still reaches her.

Their DKIM signature no longer matches once headers change, and Return-Path
and Sender describe the original hop, so all three are dropped. SES re-signs.

boto3 only: it ships with the runtime, so this deploys without a build step.
"""
import contextlib
import email
import email.policy
import json
import os
from email.header import decode_header, make_header
from email.utils import formataddr, parseaddr

import boto3

BUCKET = os.environ["BUCKET"]
PREFIX = os.environ.get("PREFIX", "inbound/")
FORWARD_TO = os.environ["FORWARD_TO"]
SENDER = os.environ["SENDER"]                 # forwarder@domain
CONFIG_SET = os.environ.get("CONFIG_SET")

DROP = ("DKIM-Signature", "Return-Path", "Sender", "Reply-To", "From",
        "Message-ID", "Errors-To")
SES_LIMIT = 10 * 1024 * 1024                  # SendRawEmail's ceiling

s3 = boto3.client("s3")
ses = boto3.client("ses")


def _display(raw_from):
    with contextlib.suppress(Exception):      # undecodable: use it as it came
        raw_from = str(make_header(decode_header(raw_from)))
    name, addr = parseaddr(raw_from)
    return (name or addr or "Someone").strip(), addr


def rewrite(raw: bytes) -> bytes:
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    name, addr = _display(msg.get("From", ""))
    for h in DROP:
        del msg[h]
    msg["From"] = formataddr((f"{name} via My Sign", SENDER))
    if addr:
        msg["Reply-To"] = addr
    return msg.as_bytes()


def _notice(mail, reason):
    """When the original cannot be forwarded, at least say that it arrived."""
    src = mail.get("source", "unknown")
    subject = (mail.get("commonHeaders") or {}).get("subject", "")
    body = (f"A message from {src} (subject: {subject!r}) could not be forwarded: "
            f"{reason}.\nThe raw message is in s3://{BUCKET}/{PREFIX}{mail['messageId']}.")
    return (f"From: {formataddr(('My Sign forwarder', SENDER))}\r\nTo: {FORWARD_TO}\r\n"
            f"Subject: [not forwarded] {subject}\r\nContent-Type: text/plain; charset=utf-8\r\n"
            f"\r\n{body}\r\n").encode()


def refused(receipt):
    """
    The verdict that stops a forward, or None. SES scans every inbound message;
    re-sending one it marked as spam or a virus puts it out under our domain,
    and the complaints count against the same sending reputation sign-in codes
    depend on (audit L5). The raw message stays in S3 either way.
    """
    for key in ("virusVerdict", "spamVerdict"):
        if ((receipt or {}).get(key) or {}).get("status") == "FAIL":
            return key
    return None


def handler(event, _ctx):
    sent = 0
    for rec in event.get("Records", []):
        mail = rec["ses"]["mail"]
        why = refused(rec["ses"].get("receipt"))
        if why:
            # The id only: the subject and sender are someone else's words.
            print(json.dumps({"event": "mailforward.dropped", "verdict": why,
                              "message_id": mail.get("messageId")}))
            continue
        raw = s3.get_object(Bucket=BUCKET, Key=PREFIX + mail["messageId"])["Body"].read()
        try:
            out = rewrite(raw)
            if len(out) > SES_LIMIT:
                out = _notice(mail, "it is larger than 10 MB")
        except Exception as e:                # a malformed message should not vanish silently
            out = _notice(mail, f"{type(e).__name__}")
        kw = {"ConfigurationSetName": CONFIG_SET} if CONFIG_SET else {}
        ses.send_raw_email(Source=SENDER, Destinations=[FORWARD_TO],
                           RawMessage={"Data": out}, **kw)
        sent += 1
    return {"forwarded": sent}
