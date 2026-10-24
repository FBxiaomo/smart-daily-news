"""Escaped HTML, Markdown, plain text, and JSON from the same immutable digest."""
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo
import json
import os
import re
import tempfile
from .feeds import canonical_url
from .models import Digest


def source_refs(digest):
    refs = {}
    for story in digest.stories:
        for article in story.articles:
            if article.id not in refs:
                refs[article.id] = (len(refs) + 1, article)
    return refs


def display_time(value, zone):
    if not value:
        return "发布时间未知"
    return datetime.fromisoformat(value).astimezone(ZoneInfo(zone)).strftime("%m-%d %H:%M")


def safe_url(url):
    return canonical_url(url) or "#"


def text_content(digest: Digest) -> str:
    refs = source_refs(digest)
    lines = [digest.title, f"期号：{digest.edition}",
             f"生成时间：{display_time(digest.created_at, digest.timezone)} ({digest.timezone})",
             f"{len(digest.stories)} 个事件 · {len(refs)} 篇来源文章", ""]
    for warning in digest.warnings:
        lines += ["运行提示：" + warning]
    if digest.warnings:
        lines.append("")
    for index, story in enumerate(digest.stories, 1):
        mode = "AI 整合" if story.mode == "ai_synthesis" else "来源摘要"
        lines += [f"{index}. [{story.category}] {story.title}", f"   {mode}"]
        for bullet in story.bullets:
            cites = " ".join(f"[{refs[s][0]}]" for s in bullet["sources"])
            lines.append("   • " + bullet["text"] + " " + cites)
        lines.append("")
    lines.append("原始来源")
    for number, article in refs.values():
        lines += [f"[{number}] {article.source_name} · {display_time(article.published_at, digest.timezone)}",
                  article.title, safe_url(article.url), ""]
    lines.append("采集状态：" + "；".join(
        f"{r['name']} {r['status']} ({r['accepted']} 条)" for r in digest.source_reports))
    lines.append("摘要仅基于订阅源提供的材料；完整事实和后续更新请核对原文。")
    return "\n".join(lines) + "\n"


def md_escape(value):
    return re.sub(r"([\\\x60*_{}\[\]()#+.!|>~-])", r"\\\1", str(value))


def markdown_content(digest: Digest) -> str:
    refs = source_refs(digest)
    lines = [f"# {md_escape(digest.title)}", "",
             f"{md_escape(digest.edition)} · {display_time(digest.created_at, digest.timezone)}",
             "", f"{len(digest.stories)} 个事件 · {len(refs)} 篇来源文章", ""]
    for warning in digest.warnings:
        lines += ["> " + md_escape(warning), ""]
    for index, story in enumerate(digest.stories, 1):
        mode = "AI 整合" if story.mode == "ai_synthesis" else "来源摘要"
        lines += [f"## {index}. {md_escape(story.title)}", "", f"{md_escape(story.category)} · {mode}", ""]
        for bullet in story.bullets:
            links = " ".join(
                f"[{refs[s][0]}](<{quote(safe_url(refs[s][1].url), safe=':/?&=%#-._~')}>)"
                for s in bullet["sources"]
            )
            lines.append("- " + md_escape(bullet["text"]) + " " + links)
        lines.append("")
    lines += ["## 原始来源", ""]
    for number, article in refs.values():
        url = quote(safe_url(article.url), safe=":/?&=%#-._~")
        lines.append(f"- [{number}. {md_escape(article.title)}](<{url}>)"
                     f" — {md_escape(article.source_name)} · {display_time(article.published_at, digest.timezone)}")
    lines += ["", "摘要仅基于订阅源提供的材料；完整事实和后续更新请核对原文。", ""]
    return "\n".join(lines)


def html_content(digest: Digest) -> str:
    refs = source_refs(digest)
    esc = escape
    pieces = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(digest.title)}</title></head>"
        '<body style="margin:0;background:#f2f1ed;color:#202b34;font-family:Arial,Microsoft YaHei,sans-serif">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">'
        '<table role="presentation" width="680" cellspacing="0" cellpadding="0" '
        'style="width:100%;max-width:680px;margin:32px auto;background:#ffffff">'
        '<tr><td style="padding:36px 32px;background:#152d35;color:#ffffff">'
        '<p style="margin:0 0 16px;color:#9fd7cd;font-size:12px;letter-spacing:2px">SMART DAILY NEWS</p>'
        f'<h1 style="margin:0 0 16px;font-size:30px;line-height:1.35">{esc(digest.title)}</h1>'
        f'<p style="margin:0;color:#c9d5d7;font-size:13px">{esc(digest.edition)}'
        f' · {esc(display_time(digest.created_at, digest.timezone))} ({esc(digest.timezone)})</p>'
        f'<p style="margin:14px 0 0;color:#c9d5d7;font-size:13px">'
        f'{len(digest.stories)} 个事件 · {len(refs)} 篇来源文章</p></td></tr>'
    ]
    for warning in digest.warnings:
        pieces.append('<tr><td style="padding:12px 32px;background:#fff5df;font-size:13px">'
                      + esc(warning) + "</td></tr>")
    for index, story in enumerate(digest.stories, 1):
        mode = "AI 整合" if story.mode == "ai_synthesis" else "来源摘要"
        pieces.append(
            '<tr><td style="padding:28px 32px;border-bottom:1px solid #e6e9e8">'
            f'<p style="margin:0 0 10px;color:#58716d;font-size:12px">'
            f'{index:02d} / {esc(story.category)} · {mode}</p>'
            f'<h2 style="margin:0 0 16px;font-size:21px;line-height:1.5">{esc(story.title)}</h2>'
        )
        for bullet in story.bullets:
            cites = " ".join(
                f'<a href="{esc(safe_url(refs[s][1].url), quote=True)}" '
                f'style="color:#287a70;text-decoration:none">[{refs[s][0]}]</a>'
                for s in bullet["sources"]
            )
            pieces.append(f'<p style="margin:10px 0;font-size:15px;line-height:1.8">'
                          f'{esc(bullet["text"])} {cites}</p>')
        pieces.append("</td></tr>")
    pieces.append('<tr><td style="padding:28px 32px;background:#f7f8f6">'
                  '<h2 style="font-size:15px;margin:0 0 18px">原始来源</h2>')
    for number, article in refs.values():
        pieces.append(
            f'<p style="font-size:13px;line-height:1.7;margin:0 0 14px">'
            f'[{number}] <a href="{esc(safe_url(article.url), quote=True)}" style="color:#246a62">'
            f'{esc(article.title)}</a><br><span style="color:#6b7979">'
            f'{esc(article.source_name)} · {esc(display_time(article.published_at, digest.timezone))}</span></p>'
        )
    report = "；".join(f"{r['name']} {r['status']} ({r['accepted']} 条)" for r in digest.source_reports)
    pieces.append(f'<p style="font-size:11px;color:#71817e;line-height:1.7">{esc(report)}</p>'
                  '<p style="font-size:11px;color:#71817e;line-height:1.7">'
                  '摘要仅基于订阅源提供的材料；完整事实和后续更新请核对原文。</p>'
                  '</td></tr></table></td></tr></table></body></html>')
    return "".join(pieces)


def write_artifacts(digest: Digest, directory: Path, *, preview=False) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", digest.edition):
        raise ValueError("无效的期号文件名")
    stem = digest.edition + ("-preview" if preview else "")
    contents = {
        "txt": text_content(digest), "md": markdown_content(digest),
        "html": html_content(digest), "json": json.dumps(digest.to_dict(), ensure_ascii=False, indent=2),
    }
    files = {}
    for extension, content in contents.items():
        target = directory / f"{stem}.{extension}"
        descriptor, temporary = tempfile.mkstemp(prefix=".daily-", dir=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        files[extension] = str(target.resolve())
    return files
