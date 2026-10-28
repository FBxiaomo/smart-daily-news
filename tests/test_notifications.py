from dataclasses import replace
import base64
import hashlib
import hmac
import json

import pytest
import requests

from daily_news.delivery import DeliveryError
from daily_news.notifications import compact_text, send_feishu, send_serverchan, send_wecom
from conftest import FakeHttp


@pytest.fixture
def notification_cfg(cfg, monkeypatch):
    cfg.data["wecom"] = {"enabled": True, "url_env": "TEST_WECOM_URL", "timeout_seconds": 12}
    cfg.data["feishu"] = {"enabled": True, "url_env": "TEST_FEISHU_URL",
                          "secret_env": "TEST_FEISHU_SECRET", "keyword": "", "timeout_seconds": 13}
    cfg.data["serverchan"] = {"enabled": True, "sendkey_env": "TEST_SERVERCHAN_KEY", "timeout_seconds": 14}
    monkeypatch.setenv("TEST_WECOM_URL", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fake_key")
    monkeypatch.setenv("TEST_FEISHU_URL", "https://open.feishu.cn/open-apis/bot/v2/hook/fake-bot-id")
    monkeypatch.setenv("TEST_FEISHU_SECRET", "")
    monkeypatch.setenv("TEST_SERVERCHAN_KEY", "SCTfaketestkey12345")
    return cfg


def body(client):
    return json.loads(client.post_calls[0][1]["body"])


def test_wecom_text_payload_with_sources(notification_cfg, digest):
    client = FakeHttp(post_response=(200, {}, b'{"errcode":0,"errmsg":"ok"}'))
    send_wecom(digest, notification_cfg, client)
    payload = body(client)
    assert payload["msgtype"] == "markdown"
    assert digest.stories[0].title in payload["markdown"]["content"]
    assert digest.stories[0].articles[0].url in payload["markdown"]["content"]
    assert len(payload["markdown"]["content"].encode("utf-8")) <= 4096
    assert client.post_calls[0][1]["timeout"] == 12


def test_wecom_max_length_and_omission_are_explicit(digest):
    digest.stories *= 40
    result = compact_text(digest, max_bytes=2048)
    assert len(result.encode("utf-8")) <= 2048
    assert "部分事件或要点已省略" in result and "完整日报" in result


def test_density_and_platform_mentions_are_safe(digest):
    digest.title = '<at user_id="all">你好</at> <@all>'
    digest.stories[0].title = '<@all>关注'
    digest.presentation = {"density": "brief"}
    result = compact_text(digest, max_bytes=2048)
    assert "<@all>" not in result and '<at user_id=' not in result
    assert "＜@all＞" in result
    assert digest.stories[0].bullets[0]["text"] not in result
    assert digest.stories[0].articles[0].url in result


def test_detailed_each_bullet_has_its_cited_source(digest):
    second = replace(digest.stories[0].articles[0], id="two",
                     source_name="Source Two", url="https://other.example/a")
    digest.stories[0].articles.append(second)
    digest.stories[0].bullets.append({"text": "第二条结果", "sources": ["two"]})
    digest.presentation = {"density": "detailed"}
    result = compact_text(digest, max_bytes=2048)
    assert "第二条结果\n来源：Source Two https://other.example/a" in result


def test_unknown_bullet_source_stops_delivery_without_misattribution(notification_cfg, digest):
    digest.stories[0].bullets[0]["sources"] = ["missing-source"]
    client = FakeHttp()
    with pytest.raises(DeliveryError, match="来源引用"):
        send_wecom(digest, notification_cfg, client)
    assert not client.post_calls


def test_feishu_signing_and_keyword(notification_cfg, digest, monkeypatch):
    notification_cfg.data["feishu"]["keyword"] = "日报"
    monkeypatch.setenv("TEST_FEISHU_SECRET", "fake-signing-secret")
    monkeypatch.setattr("daily_news.notifications.time.time", lambda: 1599360473)
    client = FakeHttp(post_response=(200, {}, b'{"code":0,"msg":"success"}'))
    send_feishu(digest, notification_cfg, client)
    payload = body(client)
    expected = base64.b64encode(hmac.new(b"1599360473\nfake-signing-secret", b"", hashlib.sha256).digest())
    assert payload["timestamp"] == "1599360473"
    assert payload["sign"] == expected.decode("ascii")
    assert payload["msg_type"] == "text"
    assert payload["content"]["text"].startswith("日报\n")
    assert len(client.post_calls[0][1]["body"]) <= 20000


def test_feishu_secret_optional(notification_cfg, digest):
    client = FakeHttp(post_response=(200, {}, b'{"code":0}'))
    send_feishu(digest, notification_cfg, client)
    assert "sign" not in body(client) and "timestamp" not in body(client)


@pytest.mark.parametrize("service,env_name,bad_url", [
    ("wecom", "TEST_WECOM_URL", "https://evil.example/cgi-bin/webhook/send?key=secret"),
    ("wecom", "TEST_WECOM_URL", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=a&key=b"),
    ("feishu", "TEST_FEISHU_URL", "http://open.feishu.cn/open-apis/bot/v2/hook/abc"),
    ("feishu", "TEST_FEISHU_URL", "https://open.feishu.cn.evil.example/open-apis/bot/v2/hook/abc"),
    ("feishu", "TEST_FEISHU_URL", "https://open.feishu.cn/open-apis/bot/v2/hook/abc?secret=bad"),
])
def test_wrong_platform_urls_rejected_without_leaking(notification_cfg, digest, monkeypatch,
                                                       service, env_name, bad_url):
    monkeypatch.setenv(env_name, bad_url)
    client = FakeHttp()
    sender = send_wecom if service == "wecom" else send_feishu
    with pytest.raises(DeliveryError) as error:
        sender(digest, notification_cfg, client)
    assert "secret" not in str(error.value) and not client.post_calls


def test_serverchan_turbo_request_and_real_markdown(notification_cfg, digest):
    digest.title = "中文日报标题" * 20
    client = FakeHttp(post_response=(200, {}, b'{"code":0,"message":""}'))
    send_serverchan(digest, notification_cfg, client)
    url, args = client.post_calls[0]
    assert url == "https://sctapi.ftqq.com/SCTfaketestkey12345.send"
    assert args["timeout"] == 14
    payload = body(client)
    assert payload["title"] and "\n" not in payload["title"] and len(payload["title"]) <= 32
    assert digest.stories[0].articles[0].url in payload["desp"]


@pytest.mark.parametrize("value", [
    "sctp123tkeyforkey123",
    "SCTkey/pathattacker",
    "SCTsomething?access_token=private",
])
def test_serverchan_wrong_key_type_or_path_rejected(notification_cfg, digest, monkeypatch, value):
    monkeypatch.setenv("TEST_SERVERCHAN_KEY", value)
    client = FakeHttp()
    with pytest.raises(DeliveryError) as error:
        send_serverchan(digest, notification_cfg, client)
    assert value not in str(error.value) and not client.post_calls


@pytest.mark.parametrize("service,payload,retryable", [
    ("wecom", b'{"errcode":45009,"errmsg":"private-token-should-not-appear"}', True),
    ("wecom", b'{"errcode":40001}', False),
    ("feishu", b'{"code":11232,"msg":"private-token-should-not-appear"}', True),
    ("feishu", b'{"code":19021}', False),
    ("serverchan", b'{"code":900,"message":"private-token-should-not-appear"}', False),
])
def test_http_200_business_failure_not_misreported(notification_cfg, digest, service, payload, retryable):
    client = FakeHttp(post_response=(200, {}, payload))
    sender = {"wecom": send_wecom, "feishu": send_feishu, "serverchan": send_serverchan}[service]
    with pytest.raises(DeliveryError) as error:
        sender(digest, notification_cfg, client)
    assert error.value.retryable is retryable
    assert not error.value.uncertain
    assert "private-token" not in str(error.value)


@pytest.mark.parametrize("response", [
    (200, {}, b'{"msg":"missing code"}'),
    (200, {}, b'{"code":false}'),
    (200, {}, b"not json"),
    (500, {}, b"internal error"),
    (408, {}, b"request timeout"),
])
def test_unconfirmed_response_becomes_uncertain(notification_cfg, digest, response):
    with pytest.raises(DeliveryError) as error:
        send_feishu(digest, notification_cfg, FakeHttp(post_response=response))
    assert error.value.uncertain and not error.value.retryable


def test_connection_timeout_is_known_safe_to_retry(notification_cfg, digest):
    with pytest.raises(DeliveryError) as error:
        send_wecom(digest, notification_cfg, FakeHttp(post_response=requests.ConnectTimeout("fake_key")))
    assert error.value.retryable and not error.value.uncertain
    assert "fake_key" not in str(error.value)


def test_read_timeout_is_uncertain_and_secret_not_exposed(notification_cfg, digest):
    with pytest.raises(DeliveryError) as error:
        send_serverchan(digest, notification_cfg, FakeHttp(post_response=requests.ReadTimeout("SCTfaketestkey12345")))
    assert error.value.uncertain and not error.value.retryable
    assert "SCTfaketestkey12345" not in str(error.value)


@pytest.mark.parametrize("channel", ["wecom", "feishu", "serverchan"])
@pytest.mark.parametrize("mode", ["full", "summary", "notice"])
def test_push_content_modes_apply_to_all_three_channels(notification_cfg, digest, channel, mode):
    notification_cfg.data["push"] = {"content": mode}
    digest.stories[0].mode = "ai_synthesis"
    source = digest.stories[0].articles[0].id
    digest.stories[0].bullets = [
        {"text": "第一条内容", "sources": [source]},
        {"text": "第二条内容", "sources": [source]},
    ]
    responses = {"wecom": b'{"errcode":0}', "feishu": b'{"code":0}', "serverchan": b'{"code":0}'}
    sender = {"wecom": send_wecom, "feishu": send_feishu, "serverchan": send_serverchan}[channel]
    client = FakeHttp(post_response=(200, {}, responses[channel]))
    sender(digest, notification_cfg, client)
    payload = body(client)
    content = (payload["markdown"]["content"] if channel == "wecom"
               else payload["content"]["text"] if channel == "feishu" else payload["desp"])
    if mode == "full":
        assert "第一条内容" in content and "第二条内容" in content
        assert digest.stories[0].articles[0].url in content
    elif mode == "summary":
        assert "第一条内容" in content and "第二条内容" not in content
    else:
        assert "第一条内容" not in content and "第二条内容" not in content
        assert "共 1 个事件" in content


def test_missing_push_section_remains_full_by_default(notification_cfg, digest):
    notification_cfg.data.pop("push", None)
    client = FakeHttp(post_response=(200, {}, b'{"errcode":0}'))
    send_wecom(digest, notification_cfg, client)
    assert digest.stories[0].bullets[0]["text"].rstrip(".") in body(client)["markdown"]["content"]


def test_wecom_full_content_uses_platform_limit_and_marks_omissions(notification_cfg, digest):
    notification_cfg.data["push"] = {"content": "full"}
    digest.stories[0].mode = "ai_synthesis"
    digest.stories[0].bullets[0]["text"] = "甲" * 1000
    digest.stories *= 20
    client = FakeHttp(post_response=(200, {}, b'{"errcode":0}'))
    send_wecom(digest, notification_cfg, client)
    content = body(client)["markdown"]["content"]
    assert len(content.encode("utf-8")) <= 4096
    assert "甲" * 100 in content and "部分事件或要点已省略" in content
    assert digest.stories[0].articles[0].url in content


def test_feishu_json_escaping_cannot_exceed_platform_body_limit(notification_cfg, digest):
    notification_cfg.data["push"] = {"content": "full"}
    digest.stories[0].mode = "ai_synthesis"
    digest.stories[0].bullets[0]["text"] = '"' * 1200
    digest.stories *= 20
    client = FakeHttp(post_response=(200, {}, b'{"code":0}'))
    send_feishu(digest, notification_cfg, client)
    assert len(client.post_calls[0][1]["body"]) <= 20000
    assert "部分事件或要点已省略" in body(client)["content"]["text"]


def test_markdown_in_source_text_cannot_inject_links(notification_cfg, digest):
    digest.stories[0].title = "![伪造](https://evil.example/image)"
    client = FakeHttp(post_response=(200, {}, b'{"errcode":0}'))
    send_wecom(digest, notification_cfg, client)
    value = body(client)["markdown"]["content"]
    assert "![伪造](https://evil.example/image)" not in value
    assert digest.stories[0].articles[0].url in value
