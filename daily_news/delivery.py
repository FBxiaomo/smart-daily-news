"""SMTP and generic Webhook delivery; no external messages without an explicit send command."""
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime
import hashlib
import hmac
import json
import smtplib
import ssl
import requests
from .config import ConfigError, recipients, secret
from .models import fingerprint
from .network import NetworkError
from .render import html_content, markdown_content, text_content


class DeliveryError(RuntimeError):
    def __init__(self, message, *, retryable=False, uncertain=False):
        super().__init__(message)
        self.retryable = retryable
        self.uncertain = uncertain


def send_email(digest, cfg, client=None):
    options = cfg.section("email")
    sender = secret(options, "from_env")
    to = recipients(secret(options, "to_env"))
    message = EmailMessage()
    message["Subject"] = f"{digest.title} · {digest.edition}"
    message["From"] = sender
    message["To"] = ", ".join(to)
    message["Date"] = format_datetime(datetime.fromisoformat(digest.created_at))
    message_id = fingerprint({"edition": digest.edition, "from": sender, "to": sorted(to)})[:32]
    message["Message-ID"] = f"<{message_id}@{sender.rsplit('@', 1)[1]}>"
    message.set_content(text_content(digest))
    message.add_alternative(html_content(digest), subtype="html")
    server, submitting = None, False
    try:
        context = ssl.create_default_context()
        if options["security"] == "ssl":
            server = smtplib.SMTP_SSL(options["host"], options["port"],
                                     timeout=cfg.section("network")["timeout_seconds"], context=context)
        else:
            server = smtplib.SMTP(options["host"], options["port"],
                                 timeout=cfg.section("network")["timeout_seconds"])
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
        server.login(secret(options, "username_env"), secret(options, "password_env"))
        submitting = True
        refused = server.send_message(message, from_addr=sender, to_addrs=to)
        if refused:
            raise DeliveryError("部分收件人被拒绝；请核对已收到的收件人，避免重复补发", uncertain=True)
    except DeliveryError:
        raise
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused):
        raise DeliveryError("SMTP 拒绝发件人或全部收件人") from None
    except smtplib.SMTPAuthenticationError:
        raise DeliveryError("SMTP 认证失败，请检查账号与授权码") from None
    except smtplib.SMTPDataError as exc:
        raise DeliveryError(f"SMTP 明确拒绝邮件内容（{exc.smtp_code}）",
                            retryable=400 <= exc.smtp_code < 500) from None
    except (OSError, smtplib.SMTPException) as exc:
        raise DeliveryError(f"SMTP {type(exc).__name__}",
                            retryable=not submitting, uncertain=submitting) from None
    finally:
        if server is not None:
            try:
                server.quit()
            except (OSError, smtplib.SMTPException):
                server.close()


def webhook_payload(digest) -> bytes:
    return json.dumps({
        "version": 1, "event": "daily_news.digest", "id": digest.edition,
        "text": text_content(digest), "markdown": markdown_content(digest),
        "html": html_content(digest), "digest": digest.to_dict(),
    }, ensure_ascii=False, separators=(",", ":")).encode()


def send_webhook(digest, cfg, client):
    options = cfg.section("webhook")
    url = secret(options, "url_env")
    body = webhook_payload(digest)
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Idempotency-Key": fingerprint({"edition": digest.edition, "target": url}),
    }
    token = secret(options, "bearer_token_env", required=False)
    if token:
        headers["Authorization"] = "Bearer " + token
    signing = secret(options, "signing_secret_env", required=False)
    if signing:
        from .models import utc_now
        timestamp = str(int(utc_now().timestamp()))
        headers["X-Daily-News-Timestamp"] = timestamp
        headers["X-Daily-News-Signature"] = "sha256=" + hmac.new(
            signing.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    try:
        status, _, _ = client.post(url, headers=headers, body=body,
                                  timeout=cfg.section("network")["timeout_seconds"])
    except requests.ConnectTimeout:
        raise DeliveryError("Webhook 连接超时（尚未建立连接）", retryable=True) from None
    except ConfigError:
        raise DeliveryError("Webhook 地址不符合网络配置") from None
    except (requests.RequestException, NetworkError):
        raise DeliveryError("Webhook 响应未确认，请核对接收端再决定是否补发", uncertain=True) from None
    if 200 <= status < 300:
        return
    if status == 429:
        raise DeliveryError("Webhook 限流（HTTP 429）", retryable=True)
    if status >= 500:
        raise DeliveryError(f"Webhook 返回 HTTP {status}，接收结果需核对", uncertain=True)
    raise DeliveryError(f"Webhook 拒绝请求（HTTP {status}）")


def deliver(channel, digest, cfg, client):
    if channel == "email":
        send_email(digest, cfg, client)
    elif channel == "webhook":
        send_webhook(digest, cfg, client)
    else:
        raise DeliveryError("未支持的投递渠道")
