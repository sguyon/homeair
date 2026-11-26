"""Web push sending: the push service is mocked, no network."""
import logging

import pytest
import requests
from pywebpush import WebPushException

import alert_push

PRIVATE = "SECRETPRIVATEKEYVALUE0123456789abcdefghijk"
SUB = {"endpoint": "https://fcm.googleapis.com/send/SECRETTOKEN123", "keys": {"p256dh": "x", "auth": "y"}}


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("VAPID_PRIVATE_KEY", PRIVATE)
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "PUBLICKEY")


def failing(status):
    def fake(**kwargs):
        response = requests.Response()
        response.status_code = status
        raise WebPushException(f"Push failed: {status}", response=response)
    return fake


def test_ok_sends_json_payload_with_ttl_urgency_and_timeout(monkeypatch):
    seen = {}
    monkeypatch.setattr(alert_push, "webpush", lambda **kw: seen.update(kw))
    assert alert_push.send(SUB, "Title", "Body") == "ok"
    assert seen["data"] == '{"title": "Title", "body": "Body", "url": "/#summary"}'
    assert seen["ttl"] == alert_push.PUSH_TTL_S and seen["timeout"] == 10
    assert seen["headers"] == {"Urgency": "high"} and seen["vapid_claims"] == {"sub": "https://homeair.app"}
    assert seen["vapid_private_key"] == PRIVATE


@pytest.mark.parametrize("status", [404, 410])
def test_missing_subscription_is_gone(monkeypatch, status):
    monkeypatch.setattr(alert_push, "webpush", failing(status))
    assert alert_push.send(SUB, "t", "b") == "gone"


def test_404_is_gone(monkeypatch):
    monkeypatch.setattr(alert_push, "webpush", failing(404))
    assert alert_push.send(SUB, "t", "b") == "gone"


def test_410_is_gone(monkeypatch):
    monkeypatch.setattr(alert_push, "webpush", failing(410))
    assert alert_push.send(SUB, "t", "b") == "gone"


@pytest.mark.parametrize("status", [403, 500])
def test_403_is_error_and_logs_no_key(monkeypatch, caplog, status):
    monkeypatch.setattr(alert_push, "webpush", failing(status))
    with caplog.at_level(logging.INFO):
        assert alert_push.send(SUB, "t", "b") == "error"
    assert caplog.records and PRIVATE not in caplog.text and "SECRETTOKEN123" not in caplog.text
    assert "fcm.googleapis.com" in caplog.text


def test_timeout_is_error(monkeypatch, caplog):
    def boom(**kwargs):
        raise requests.exceptions.Timeout(f"slow {SUB['endpoint']}")
    monkeypatch.setattr(alert_push, "webpush", boom)
    with caplog.at_level(logging.INFO):
        assert alert_push.send(SUB, "t", "b") == "error"
    assert "SECRETTOKEN123" not in caplog.text


def test_missing_key_is_error_without_sending(monkeypatch):
    monkeypatch.delenv("VAPID_PRIVATE_KEY")
    monkeypatch.setattr(alert_push, "webpush", lambda **kw: pytest.fail("sent"))
    assert alert_push.send(SUB, "t", "b") == "error"


def test_public_key(monkeypatch):
    assert alert_push.public_key() == "PUBLICKEY"
    monkeypatch.delenv("VAPID_PUBLIC_KEY")
    assert alert_push.public_key() is None


def test_made_keys_sign_a_real_push(tmp_path, monkeypatch):
    """The stored private key is one pywebpush takes as is, and the public key matches it."""
    import sys
    sys.path.insert(0, "scripts")
    import setup
    from py_vapid import Vapid
    from config import parse_dotenv
    setup.ensure_push_keys(tmp_path)
    saved = parse_dotenv((tmp_path / ".env").read_text())
    vapid = Vapid.from_string(saved["VAPID_PRIVATE_KEY"])
    assert vapid.public_key.public_numbers() == Vapid.from_raw_public(saved["VAPID_PUBLIC_KEY"].encode()).public_key.public_numbers()


def test_a_redirect_is_not_followed(monkeypatch):
    """A push service that answers 307 must not send the server anywhere else."""
    import requests
    seen = {}
    monkeypatch.setattr(alert_push, "webpush", lambda **kw: seen.update(kw))
    alert_push.send({"endpoint": "https://fcm.googleapis.com/x", "keys": {}}, "t", "b")
    session = seen["requests_session"]
    asked = []

    class Redirecting(requests.adapters.HTTPAdapter):
        def send(self, request, **kw):
            asked.append(request.url)
            r = requests.Response()
            r.status_code, r.url, r.request = 307, request.url, request
            r.headers["Location"] = "http://10.0.0.1/admin"
            r.raw = None
            return r
    session.mount("https://", Redirecting())
    with pytest.raises(requests.TooManyRedirects):
        session.post("https://fcm.googleapis.com/x")
    assert asked == ["https://fcm.googleapis.com/x"]


@pytest.mark.parametrize("endpoint,ok", [
    ("https://web.push.apple.com/a", True), ("https://fcm.googleapis.com/a", True), ("https://updates.push.services.mozilla.com/a", True),
    ("https://x.web.push.apple.com/a", True), ("https://evilweb.push.apple.com/a", False), ("https://127.1/a", False),
    ("http://fcm.googleapis.com/a", False), ("https://fcm.googleapis.com.evil.example/a", False), ("", False),
    ("https://127.0.0.1\\@fcm.googleapis.com/x", False), ("https://evil.com\\@fcm.googleapis.com/x", False),
    ("https://u:p@fcm.googleapis.com/x", False)])
def test_push_host_allowed(endpoint, ok):
    assert alert_push.push_host_allowed(endpoint) is ok


def test_send_refuses_a_stored_address_that_is_not_a_push_service(monkeypatch):
    called = []
    monkeypatch.setattr(alert_push, "webpush", lambda **kw: called.append(kw))
    assert alert_push.send({"endpoint": "https://127.0.0.1\\@fcm.googleapis.com/x", "keys": {}}, "t", "b") == "error"
    assert alert_push.send({"endpoint": "https://push.example/a", "keys": {}}, "t", "b") == "error"
    assert called == []


def test_a_redirect_makes_the_phone_gone(monkeypatch, caplog):
    import requests
    def redirected(**kw):
        raise requests.TooManyRedirects("redirect")
    monkeypatch.setattr(alert_push, "webpush", redirected)
    assert alert_push.send({"endpoint": "https://fcm.googleapis.com/x", "keys": {}}, "t", "b") == "gone"
    assert "redirected" in caplog.text and "/x" not in caplog.text
