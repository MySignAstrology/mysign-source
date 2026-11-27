"""The test notification is throttled (audit M5): each one fans out to every
registered device, synchronously, on the request path."""
import app.server as server
from app import push
from tests.test_invites import person


def test_test_pushes_are_a_few_an_hour(monkeypatch):
    c = person("push-test-limit@x.com", "Eve")
    sent = []
    monkeypatch.setattr(push, "send", lambda uid, *a, **k: sent.append(uid) or {"sent": 1})
    codes = [c.post("/api/push/test").status_code
             for _ in range(server.PUSH_TESTS_PER_HOUR + 2)]
    assert codes == [200] * server.PUSH_TESTS_PER_HOUR + [429, 429]
    assert len(sent) == server.PUSH_TESTS_PER_HOUR
