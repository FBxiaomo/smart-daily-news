import hashlib
import hmac
import json
import smtplib
import requests
import pytest
from daily_news.config import ConfigError
from daily_news.delivery import DeliveryError, send_email, send_webhook
from daily_news.network import HttpClient, NetworkError, validate_url
from conftest import FakeHttp


@pytest.mark.parametrize("url", ["http://example.com/feed", "https://localhost/feed",
                                "https://127.0.0.1/feed", "https://[::1]/feed",
                                "https://169.254.169.254/latest", "https://user:pass@example.com",
                                "file:///etc/passwd"])
def test_unsafe_urls_rejected(cfg, url):
    with pytest.raises(ConfigError):
        validate_url(url, cfg.section("network"), resolve=False)


def test_private_dns_is_rejected(cfg, monkeypatch):
    monkeypatch.setattr("daily_news.network.socket.getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("10.0.0.1", 443))])
    with pytest.raises(ConfigError):
        validate_url("https://public-looking.example/feed", cfg.section("network"))


class Response:
    def __init__(self, status, body=b"", headers=None):
        self.status_code, self.body, self.headers = status, body, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.headers, self.calls = {}, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)

    def close(self):
        pass


def test_redirect_to_private_host_blocked(cfg, monkeypatch):
    monkeypatch.setattr("daily_news.network.socket.getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443))])
    session = Session([Response(302, headers={"Location": "https://127.0.0.1/secret"})])
    client = HttpClient(cfg.section("network"), session=session, sleeper=lambda *a: None)
    with pytest.raises(ConfigError):
        client.get("https://public.example/feed")
    assert len(session.calls) == 1


def test_feed_response_limit(cfg, monkeypatch):
    monkeypatch.setattr("daily_news.network.validate_url", lambda *a, **k: None)
    policy = {**cfg.section("network"), "get_retries": 0, "max_feed_bytes": 10}
    client = HttpClient(policy, Session([Response(200, b"x" * 11)]))
    with pytest.raises(NetworkError):
        client.get("https://news.example/feed")


def test_post_does_not_follow_redirects_or_repeat(cfg, monkeypatch):
    monkeypatch.setattr("daily_news.network.validate_url", lambda *a, **k: None)
    session = Session([Response(302, headers={"Location": "https://evil.example"})])
    client = HttpClient(cfg.section("network"), session)
    assert client.post("https://receiver.example", headers={"Authorization": "secret"},
                       body=b"body", timeout=10)[0] == 302
    assert len(session.calls) == 1
    assert session.calls[0][1]["allow_redirects"] is False


@pytest.mark.parametrize("status,expected", [(200, "success"), (204, "success"), (401, "failed"),
                                          (429, "retry"), (503, "uncertain")])
def test_webhook_http_outcomes(sending_cfg, digest, status, expected):
    client = FakeHttp(post_response=(status, {}, b""))
    if expected == "success":
        send_webhook(digest, sending_cfg, client)
    else:
        with pytest.raises(DeliveryError) as error:
            send_webhook(digest, sending_cfg, client)
        assert error.value.uncertain is (expected == "uncertain")
        assert error.value.retryable is (expected == "retry")


def test_webhook_signature_idempotency_and_payload(sending_cfg, digest, monkeypatch):
    monkeypatch.setenv("DAILY_NEWS_WEBHOOK_SECRET", "signing-secret")
    monkeypatch.setenv("DAILY_NEWS_WEBHOOK_TOKEN", "bearer-secret")
    client = FakeHttp()
    send_webhook(digest, sending_cfg, client)
    send_webhook(digest, sending_cfg, client)
    _, args = client.post_calls[0]
    headers, body = args["headers"], args["body"]
    signature = hmac.new(b"signing-secret",
                         headers["X-Daily-News-Timestamp"].encode() + b"." + body, hashlib.sha256).hexdigest()
    assert headers["X-Daily-News-Signature"] == "sha256=" + signature
    assert headers["Authorization"] == "Bearer bearer-secret"
    assert headers["Idempotency-Key"] == client.post_calls[1][1]["headers"]["Idempotency-Key"]
    payload = json.loads(body)
    assert payload["id"] == digest.edition and payload["digest"]["stories"]
    assert "signing-secret" not in body.decode() and "bearer-secret" not in body.decode()


def test_read_timeout_not_blindly_retried(sending_cfg, digest):
    with pytest.raises(DeliveryError) as error:
        send_webhook(digest, sending_cfg, FakeHttp(post_response=requests.ReadTimeout("secret-url")))
    assert error.value.uncertain and "secret-url" not in str(error.value)


class SMTP:
    def __init__(self, error=None, refused=None):
        self.error, self.refused = error, refused
        self.calls, self.messages = [], []

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, context):
        self.calls.append("starttls")

    def login(self, *args):
        self.calls.append("login")

    def send_message(self, message, **kwargs):
        self.calls.append("send")
        self.messages.append(message)
        if self.error:
            raise self.error
        return self.refused or {}

    def quit(self):
        self.calls.append("quit")

    def close(self):
        pass


def test_email_contains_multipart_stable_id_and_tls(sending_cfg, digest, monkeypatch):
    sending_cfg.data["email"].update(security="starttls", port=587)
    smtp = SMTP()
    monkeypatch.setattr("daily_news.delivery.smtplib.SMTP", lambda *a, **k: smtp)
    send_email(digest, sending_cfg)
    send_email(digest, sending_cfg)
    assert smtp.calls[:4] == ["ehlo", "starttls", "ehlo", "login"]
    assert smtp.messages[0].is_multipart()
    assert [part.get_content_type() for part in smtp.messages[0].iter_parts()] == ["text/plain", "text/html"]
    assert smtp.messages[0]["Message-ID"] == smtp.messages[1]["Message-ID"]


def test_partial_smtp_refusal_requires_review(sending_cfg, digest, monkeypatch):
    monkeypatch.setattr("daily_news.delivery.smtplib.SMTP_SSL",
                        lambda *a, **k: SMTP(refused={"one@example.com": (550, b"refused")}))
    with pytest.raises(DeliveryError) as error:
        send_email(digest, sending_cfg)
    assert error.value.uncertain


def test_smtp_disconnect_after_submission_is_uncertain(sending_cfg, digest, monkeypatch):
    monkeypatch.setattr("daily_news.delivery.smtplib.SMTP_SSL",
                        lambda *a, **k: SMTP(error=smtplib.SMTPServerDisconnected("lost")))
    with pytest.raises(DeliveryError) as error:
        send_email(digest, sending_cfg)
    assert error.value.uncertain


def test_smtp_explicit_temporary_rejection_can_retry(sending_cfg, digest, monkeypatch):
    monkeypatch.setattr("daily_news.delivery.smtplib.SMTP_SSL",
                        lambda *a, **k: SMTP(error=smtplib.SMTPDataError(451, b"temporary")))
    with pytest.raises(DeliveryError) as error:
        send_email(digest, sending_cfg)
    assert error.value.retryable and not error.value.uncertain
