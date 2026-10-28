"""Explicitly configured WeCom, Feishu and ServerChan delivery adapters.

Each adapter sends a single request. Delivery acknowledgement can be ambiguous:
never include secrets, response bodies or configured webhook URLs in errors.
"""
import base64
import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qs, quote, urlsplit

import requests

from .config import ConfigError, secret
from .delivery import DeliveryError
from .network import NetworkError
from .render import safe_url


def _secret(options, name, *, required=True):
    try:
        return secret(options, name, required=required)
    except (KeyError, ConfigError):
        raise DeliveryError("推送凭据缺失，请检查本地环境变量配置") from None


def _robot_url(value, provider):
    """Reject credential disclosure through malformed or non-provider endpoints."""
    try:
        parts = urlsplit(value)
        hostname = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        raise DeliveryError("机器人地址格式无效") from None
    if (parts.scheme != "https" or parts.username is not None or parts.password is not None
            or port not in (None, 443) or parts.fragment or any(ord(ch) < 33 for ch in value)):
        raise DeliveryError("机器人地址格式无效，请使用平台提供的 HTTPS 地址")
    if provider == "wecom":
        keys = parse_qs(parts.query).get("key", [])
        valid = (hostname == "qyapi.weixin.qq.com" and parts.path == "/cgi-bin/webhook/send"
                 and len(keys) == 1 and bool(keys[0]))
    else:
        valid = (hostname in ("open.feishu.cn", "open.larksuite.com")
                 and re.fullmatch(r"/open-apis/bot/v2/hook/[A-Za-z0-9_-]{1,200}", parts.path)
                 and not parts.query)
    if not valid:
        raise DeliveryError("机器人地址不属于所选平台，请重新复制平台提供的地址")
    return value


def _clean_text(value, max_chars=250):
    """Remove control characters and prevent platform inline @-mention markup."""
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("<", "＜").replace(">", "＞")
    if len(text) > max_chars:
        text = text[:max_chars - 1].rstrip() + "…"
    return text


def _display_text(value, max_chars, *, markdown=False):
    text = _clean_text(value, max_chars)
    if markdown:
        text = re.sub(r"([\\\x60*_{}\[\]()#+.!|>~-])", r"\\\1", text)
    return text


def _source_lines(story, bullet=None, *, markdown=False):
    if not story.articles:
        raise DeliveryError("日报缺少来源，已停止投递")
    by_id = {a.id: a for a in story.articles}
    if bullet is None:
        articles = story.articles[:1]
    else:
        ids = bullet.get("sources", [])
        if (not isinstance(ids, list) or not ids
                or any(not isinstance(source, str) or source not in by_id for source in ids)):
            raise DeliveryError("日报存在无效来源引用，已停止投递")
        articles = [by_id[source] for source in ids]
    lines = []
    for article in articles:
        name = _display_text(article.source_name, 50, markdown=markdown)
        url = safe_url(article.url)
        if url == "#":
            lines.append("来源：" + name + "（无有效链接）")
            continue
        if len(url.encode("utf-8")) > 800:
            lines.append("来源：" + name + "（链接过长，请看完整日报）")
        else:
            url = quote(url, safe=":/?&=%#-._~")
            lines.append(f"来源：[{name}]({url})" if markdown else "来源：" + name + " " + url)
    return lines


def _bullet_text(story, bullet, mode):
    text = bullet.get("text", "")
    if not isinstance(text, str):
        raise DeliveryError("日报要点格式无效，已停止投递")
    refs = bullet.get("sources", [])
    # Full mode may use the feed's longer excerpt, but only if it is the
    # actual source of this excerpt and cannot replace AI-written content.
    if mode == "full" and story.mode == "source_excerpt" and isinstance(refs, list) and len(refs) == 1:
        article = next((a for a in story.articles if a.id == refs[0]), None)
        prefix = text.rstrip("…").strip()
        if article and prefix and article.excerpt.startswith(prefix) and len(article.excerpt) > len(text):
            text = article.excerpt
    return text


def compact_text(digest, max_bytes, keyword="", *, content=None, markdown=False):
    """Build one bounded message; disclose all truncation and cite visible facts."""
    if content is None:
        # Preserve direct use by older callers that choose a display density.
        presentation = getattr(digest, "presentation", None) or {}
        density = presentation.get("density", "standard") if isinstance(presentation, dict) else "standard"
        content = "titles" if density == "brief" else "full" if density == "detailed" else "summary"
    if content not in ("full", "summary", "notice", "titles"):
        raise DeliveryError("推送内容模式无效")
    def render(value, length):
        return _display_text(value, length, markdown=markdown)
    prefix = render(keyword, 80)
    head = "\n".join(filter(None, [prefix, render(digest.title, 100),
                                    "期号：" + render(digest.edition, 100)]))
    if content == "notice":
        result = head + f"\n日报已生成，共 {len(digest.stories)} 个事件；请查看完整日报。"
        if len(result.encode("utf-8")) > max_bytes:
            raise DeliveryError("日报标题或提示过长，无法放入推送消息")
        return result

    for warning in digest.warnings[:3]:
        head += "\n运行提示：" + render(warning, 120)
    total = len(digest.stories)
    footer = f"\n… 受平台长度限制，部分事件或要点已省略；完整日报共 {total} 个事件。"
    reserve = len(footer.encode("utf-8"))
    chunks = [head]
    shortened = False
    shown = 0
    for index, story in enumerate(digest.stories, 1):
        title = f"{index}. {render(story.translated_title or story.title, 300)}"
        mode_label = "AI 整合" if story.mode == "ai_synthesis" else "来源摘要"
        title += " · " + mode_label
        bullets = story.bullets if content == "full" else story.bullets[:1]
        pieces = []
        for bullet in bullets:
            raw_text = _bullet_text(story, bullet, content)
            text = render(raw_text, 1200 if content == "full" else 120)
            if text:
                pieces.append("\n".join([text, *_source_lines(story, bullet, markdown=markdown)]))
        if content == "titles" or not pieces:
            pieces = ["\n".join(_source_lines(story, markdown=markdown))]
        full = "\n".join([title, *pieces])
        variants = [full]
        if len(pieces) > 1:
            variants.append("\n".join([title, pieces[0]]))
        variants.append("\n".join([title, *_source_lines(story, markdown=markdown)]))
        selected = None
        for candidate in variants:
            tentative = "\n\n".join([*chunks, candidate])
            if len(tentative.encode("utf-8")) + reserve <= max_bytes:
                selected = candidate
                break
        if selected is None:
            shortened = True
            break
        if selected != full:
            shortened = True
        chunks.append(selected)
        shown += 1
    result = "\n\n".join(chunks)
    if shortened or shown < total:
        result += footer
    elif content == "summary":
        result += "\n本通知展示简要摘要。"
    if len(result.encode("utf-8")) > max_bytes:
        raise DeliveryError("日报标题或提示过长，无法放入推送消息")
    return result


def _push_content(cfg):
    try:
        section = cfg.section("push")
    except (AttributeError, KeyError):
        return "full"
    if not isinstance(section, dict):
        raise DeliveryError("推送内容配置无效")
    value = section.get("content", "full")
    if value not in ("full", "summary", "notice"):
        raise DeliveryError("推送内容模式无效")
    return value


def _post_json(client, url, payload, timeout, service, success_key, retry_codes=()):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        status, _, response = client.post(
            url,
            headers={"Content-Type": "application/json; charset=utf-8"},
            body=body,
            timeout=timeout,
        )
    except requests.ConnectTimeout:
        raise DeliveryError(f"{service} 连接超时，尚未建立连接", retryable=True) from None
    except ConfigError:
        raise DeliveryError(f"{service} 地址不符合网络配置") from None
    except (requests.RequestException, NetworkError):
        raise DeliveryError(f"{service} 响应未确认，请核对接收端后再决定是否补发",
                            uncertain=True) from None
    if status == 429:
        raise DeliveryError(f"{service} 限流（HTTP 429）", retryable=True)
    if status == 408 or status >= 500:
        raise DeliveryError(f"{service} 响应未确认（HTTP {status}），请核对接收端",
                            uncertain=True)
    if not 200 <= status < 300:
        raise DeliveryError(f"{service} 拒绝请求（HTTP {status}）")
    try:
        data = json.loads(response)
        code = data[success_key]
        # bool must not be treated as integer 0.
        if type(code) is not int:
            raise ValueError("invalid code")
    except (ValueError, TypeError, KeyError, UnicodeDecodeError):
        raise DeliveryError(f"{service} 返回无法确认，请核对接收端", uncertain=True) from None
    if code == 0:
        return
    # The numeric provider code is useful to operators; never expose provider message/body.
    if not -(2 ** 31) <= code < 2 ** 31:
        raise DeliveryError(f"{service} 返回无法确认，请核对接收端", uncertain=True)
    raise DeliveryError(f"{service} 明确拒绝请求（业务码 {code}）", retryable=code in retry_codes)


def send_wecom(digest, cfg, client):
    options = cfg.section("wecom")
    url = _robot_url(_secret(options, "url_env"), "wecom")
    text = compact_text(digest, max_bytes=4096, content=_push_content(cfg), markdown=True)
    payload = {"msgtype": "markdown", "markdown": {"content": text}}
    _post_json(client, url, payload, options["timeout_seconds"], "企业微信", "errcode",
               retry_codes=(45009, 45033))


def send_feishu(digest, cfg, client):
    options = cfg.section("feishu")
    url = _robot_url(_secret(options, "url_env"), "feishu")
    keyword = options.get("keyword", "")
    if any(c in keyword for c in "<>\r\n"):
        raise DeliveryError("飞书机器人关键词不能包含换行或尖括号")
    mode = _push_content(cfg)
    payload = {"msg_type": "text", "content": {"text": ""}}
    signing_key = _secret(options, "secret_env", required=False)
    if signing_key:
        timestamp = str(int(time.time()))
        key = (timestamp + "\n" + signing_key).encode("utf-8")
        payload["timestamp"] = timestamp
        payload["sign"] = base64.b64encode(hmac.new(key, digestmod=hashlib.sha256).digest()).decode("ascii")
    for budget in (18000, 14000, 10000, 6000):
        payload["content"]["text"] = compact_text(digest, max_bytes=budget, keyword=keyword, content=mode)
        if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 20000:
            break
    else:
        raise DeliveryError("飞书消息超过平台大小上限")
    _post_json(client, url, payload, options["timeout_seconds"], "飞书", "code",
               retry_codes=(11232,))


def send_serverchan(digest, cfg, client):
    options = cfg.section("serverchan")
    key = _secret(options, "sendkey_env")
    # This adapter targets Turbo's SCT key and its personal WeChat binding.
    if not re.fullmatch(r"SCT[A-Za-z0-9_-]{8,200}", key):
        raise DeliveryError("Server酱需要 Turbo 版 SCT 开头的 SendKey")
    url = "https://sctapi.ftqq.com/" + key + ".send"
    title = _clean_text(f"{digest.title} · {digest.edition}", 32)
    markdown = compact_text(digest, max_bytes=12000, content=_push_content(cfg), markdown=True)
    _post_json(client, url, {"title": title, "desp": markdown}, options["timeout_seconds"],
               "Server酱", "code")
