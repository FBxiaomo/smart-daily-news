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
from .presentation import DENSITIES, PALETTES, resolve_presentation, visible_bullets


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
    options = resolve_presentation(getattr(digest, "presentation", None))
    lines = [digest.title, f"期号：{digest.edition}",
             f"生成时间：{display_time(digest.created_at, digest.timezone)} ({digest.timezone})",
             f"{len(digest.stories)} 个事件 · {len(refs)} 篇来源文章", ""]
    for warning in digest.warnings:
        lines += ["运行提示：" + warning]
    if digest.warnings:
        lines.append("")
    for index, story in enumerate(digest.stories, 1):
        mode = "AI 整合" if story.mode == "ai_synthesis" else "来源摘要"
        lines += [f"{index}. [{story.category}] {story.translated_title or story.title}", f"   {mode}"]
        for bullet in visible_bullets(story, options["density"]):
            cites = " ".join(f"[{refs[s][0]}]" for s in bullet["sources"])
            lines.append("   • " + bullet["text"] + " " + cites)
        lines.append("")
    lines.append("原始来源")
    for number, article in refs.values():
        lines += [f"[{number}] {article.source_name} · {display_time(article.published_at, digest.timezone)}",
                  article.translated_title or article.title, safe_url(article.url), ""]
    lines.append("采集状态：" + "；".join(
        f"{r['name']} {r['status']} ({r['accepted']} 条)" for r in digest.source_reports))
    lines.append("摘要仅基于订阅源提供的材料；完整事实和后续更新请核对原文。")
    return "\n".join(lines) + "\n"


def md_escape(value):
    return re.sub(r"([\\\x60*_{}\[\]()#+.!|>~-])", r"\\\1", str(value))


def markdown_content(digest: Digest) -> str:
    refs = source_refs(digest)
    options = resolve_presentation(getattr(digest, "presentation", None))
    lines = [f"# {md_escape(digest.title)}", "",
             f"{md_escape(digest.edition)} · {display_time(digest.created_at, digest.timezone)}",
             "", f"{len(digest.stories)} 个事件 · {len(refs)} 篇来源文章", ""]
    for warning in digest.warnings:
        lines += ["> " + md_escape(warning), ""]
    for index, story in enumerate(digest.stories, 1):
        mode = "AI 整合" if story.mode == "ai_synthesis" else "来源摘要"
        lines += [f"## {index}. {md_escape(story.translated_title or story.title)}", "", f"{md_escape(story.category)} · {mode}", ""]
        for bullet in visible_bullets(story, options["density"]):
            links = " ".join(
                f"[{refs[s][0]}](<{quote(safe_url(refs[s][1].url), safe=':/?&=%#-._~')}>)"
                for s in bullet["sources"]
            )
            lines.append("- " + md_escape(bullet["text"]) + " " + links)
        lines.append("")
    lines += ["## 原始来源", ""]
    for number, article in refs.values():
        url = quote(safe_url(article.url), safe=":/?&=%#-._~")
        lines.append(f"- [{number}. {md_escape(article.translated_title or article.title)}](<{url}>)"
                     f" — {md_escape(article.source_name)} · {display_time(article.published_at, digest.timezone)}")
    lines += ["", "摘要仅基于订阅源提供的材料；完整事实和后续更新请核对原文。", ""]
    return "\n".join(lines)


def _bullet_html(story, refs, options, palette):
    paragraphs = []
    for bullet in visible_bullets(story, options["density"]):
        cites = " ".join(
            f'<a href="{escape(safe_url(refs[source][1].url), quote=True)}" '
            f'style="color:{palette["accent"]};text-decoration:none;font-weight:600">[{refs[source][0]}]</a>'
            for source in bullet["sources"]
        )
        paragraphs.append(
            f'<p style="margin:10px 0 0;font-size:15px;line-height:1.75;'
            f'color:{palette["text"]};overflow-wrap:anywhere">{escape(bullet["text"])} {cites}</p>'
        )
    return "".join(paragraphs)


def _story_body(story, index, refs, options, palette):
    mode = "AI 整合" if story.mode == "ai_synthesis" else "来源摘要"
    title_size = "20px" if options["layout"] == "columns" else "22px"
    return (
        f'<p style="margin:0 0 12px;color:{palette["muted"]};font-size:12px;'
        f'line-height:1.5;font-weight:600;letter-spacing:.3px">'
        f'{index:02d} &nbsp;/&nbsp; {escape(story.category)} &nbsp;·&nbsp; {mode}</p>'
        f'<h2 style="margin:0 0 14px;color:{palette["text"]};font-size:{title_size};'
        f'line-height:1.45;overflow-wrap:anywhere">{escape(story.translated_title or story.title)}</h2>'
        + _bullet_html(story, refs, options, palette)
    )


def _story_rows(digest, refs, options, palette):
    layout = options["layout"]
    rows = []
    if layout == "columns":
        # Table cells degrade to stacked, full-width blocks where media queries
        # are supported, while preserving readable table-based email fallback.
        rows.append('<tr><td class="daily-outer" style="padding:16px 24px 8px">'
                    '<table role="presentation" width="100%" cellspacing="0" cellpadding="0">')
        for offset in range(0, len(digest.stories), 2):
            rows.append("<tr>")
            for index in range(offset, min(offset + 2, len(digest.stories))):
                story = digest.stories[index]
                rows.append(
                    f'<td class="daily-col" width="50%" valign="top" '
                    f'style="width:50%;padding:16px 12px 24px;border-bottom:1px solid {palette["border"]};'
                    f'color:{palette["text"]}">'
                    + _story_body(story, index + 1, refs, options, palette) + "</td>"
                )
            if offset + 1 >= len(digest.stories):
                rows.append('<td class="daily-col" width="50%" aria-hidden="true"></td>')
            rows.append("</tr>")
        rows.append("</table></td></tr>")
        return "".join(rows)

    for index, story in enumerate(digest.stories, 1):
        body = _story_body(story, index, refs, options, palette)
        if layout == "cards":
            rows.append(
                '<tr><td class="daily-outer" style="padding:0 24px 18px">'
                f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
                f'style="border:1px solid {palette["border"]};border-radius:14px;'
                f'background:{palette["surface_alt"]}"><tr><td '
                f'style="padding:24px;border-radius:14px;color:{palette["text"]}">'
                + body + "</td></tr></table></td></tr>"
            )
        else:
            rows.append(
                f'<tr><td class="daily-outer" style="padding:28px 32px;'
                f'border-bottom:1px solid {palette["border"]};color:{palette["text"]}">'
                + body + "</td></tr>"
            )
    return "".join(rows)


def html_content(digest: Digest) -> str:
    refs = source_refs(digest)
    options = resolve_presentation(getattr(digest, "presentation", None))
    palette = PALETTES[options["theme"]]
    layout = options["layout"]
    header_font = ("Georgia,Times New Roman,Microsoft YaHei,serif"
                   if layout == "columns" else "Arial,Microsoft YaHei,sans-serif")
    if layout == "columns":
        header_style = (f'background:{palette["surface"]};color:{palette["text"]};'
                        f'border-bottom:4px double {palette["border"]}')
        eyebrow_color, header_secondary = palette["accent"], palette["muted"]
        header_align = "center"
    else:
        header_style = f'background:{palette["header"]};color:{palette["header_text"]}'
        eyebrow_color, header_secondary = palette["header_muted"], palette["header_muted"]
        header_align = "left"
    pieces = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>{escape(digest.title)}</title>"
        '<style>@media only screen and (max-width:600px){'
        '.daily-col{display:block!important;width:100%!important;box-sizing:border-box!important}'
        '.daily-outer{padding-left:18px!important;padding-right:18px!important}'
        '}</style></head>'
        f'<body data-layout="{layout}" data-theme="{options["theme"]}" data-density="{options["density"]}" '
        f'style="margin:0;background:{palette["background"]};color:{palette["text"]};'
        'font-family:Arial,Microsoft YaHei,sans-serif">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">'
        f'<table role="presentation" width="680" cellspacing="0" cellpadding="0" '
        f'style="width:100%;max-width:680px;margin:24px auto;background:{palette["surface"]}">'
        f'<tr><td class="daily-outer" align="{header_align}" '
        f'style="padding:34px 32px;font-family:{header_font};{header_style}">'
        f'<p style="margin:0 0 15px;color:{eyebrow_color};font-size:11px;'
        'font-weight:600;letter-spacing:2px">SMART DAILY NEWS</p>'
        f'<h1 style="margin:0 0 14px;font-size:30px;line-height:1.35">{escape(digest.title)}</h1>'
        f'<p style="margin:0;color:{header_secondary};font-size:13px;line-height:1.65">'
        f'{escape(digest.edition)} · {escape(display_time(digest.created_at, digest.timezone))} '
        f'({escape(digest.timezone)})</p>'
        f'<p style="margin:12px 0 0;color:{header_secondary};font-size:13px">'
        f'{len(digest.stories)} 个事件 · {len(refs)} 篇来源文章 · {DENSITIES[options["density"]]}</p></td></tr>'
    ]
    for warning in digest.warnings:
        pieces.append(
            f'<tr><td class="daily-outer" style="padding:12px 32px;background:{palette["warning_bg"]};'
            f'color:{palette["warning_text"]};font-size:13px;line-height:1.6">{escape(warning)}</td></tr>'
        )
    if layout == "cards":
        pieces.append('<tr><td style="height:24px;font-size:0;line-height:0">&nbsp;</td></tr>')
    pieces.append(_story_rows(digest, refs, options, palette))
    pieces.append(
        f'<tr><td class="daily-outer" style="padding:28px 32px;background:{palette["surface_alt"]};'
        f'color:{palette["text"]}"><h2 style="font-size:15px;margin:0 0 18px">原始来源</h2>'
    )
    for number, article in refs.values():
        pieces.append(
            f'<p style="font-size:13px;line-height:1.7;margin:0 0 14px">'
            f'[{number}] <a href="{escape(safe_url(article.url), quote=True)}" '
            f'style="color:{palette["accent"]}" title="{escape(article.title, quote=True)}">'
            f'{escape(article.translated_title or article.title)}</a><br>'
            f'<span style="color:{palette["muted"]}">{escape(article.source_name)} · '
            f'{escape(display_time(article.published_at, digest.timezone))}</span></p>'
        )
    report = "；".join(f"{r['name']} {r['status']} ({r['accepted']} 条)" for r in digest.source_reports)
    pieces.append(
        f'<p style="font-size:11px;color:{palette["muted"]};line-height:1.7">{escape(report)}</p>'
        f'<p style="font-size:11px;color:{palette["muted"]};line-height:1.7">'
        '摘要仅基于订阅源提供的材料；完整事实和后续更新请核对原文。</p>'
        '</td></tr></table></td></tr></table></body></html>'
    )
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
