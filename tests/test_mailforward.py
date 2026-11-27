"""The support@ forwarder never re-sends what SES marked spam or a virus
(audit L5): it would go out under our domain and spend the reputation that
sign-in codes depend on."""
import importlib.util
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent


class Client:
    def __init__(self):
        self.sent = []

    def get_object(self, Bucket, Key):
        raw = b"From: Jane <jane@example.org>\r\nSubject: hi\r\n\r\nhello\r\n"
        return {"Body": types.SimpleNamespace(read=lambda: raw)}

    def send_raw_email(self, **k):
        self.sent.append(k)


def load(monkeypatch):
    client = Client()
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda *_a, **_k: client))
    for k, v in {"BUCKET": "inbox", "FORWARD_TO": "me@example.org",
                 "SENDER": "forwarder@mysignapp.com"}.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location(
        "mailforward_handler", ROOT / "deploy" / "mailforward" / "handler.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, client


def rec(spam="PASS", virus="PASS"):
    return {"ses": {"mail": {"messageId": "m1", "source": "jane@example.org",
                             "commonHeaders": {"subject": "hi"}},
                    "receipt": {"spamVerdict": {"status": spam},
                                "virusVerdict": {"status": virus}}}}


def test_clean_mail_is_forwarded(monkeypatch):
    mod, client = load(monkeypatch)
    assert mod.handler({"Records": [rec()]}, None) == {"forwarded": 1}
    assert b"Jane via My Sign" in client.sent[0]["RawMessage"]["Data"]


def test_spam_and_viruses_are_dropped(monkeypatch, capsys):
    mod, client = load(monkeypatch)
    out = mod.handler({"Records": [rec(spam="FAIL"), rec(virus="FAIL"), rec()]}, None)
    assert out == {"forwarded": 1} and len(client.sent) == 1
    log = capsys.readouterr().out
    assert "spamVerdict" in log and "virusVerdict" in log and "jane@" not in log
