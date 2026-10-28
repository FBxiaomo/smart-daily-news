"""Conservative same-event grouping and transparent source-excerpt fallback."""
from datetime import datetime
from difflib import SequenceMatcher
import re
from .config import Config
from .models import Article, Story, fingerprint


def title_tokens(title: str) -> set[str]:
    text = title.casefold()
    words = set(re.findall(r"[a-z0-9]+", text))
    for chunk in re.findall(r"[\u3400-\u9fff]+", text):
        words.update(chunk[i:i + 2] for i in range(max(1, len(chunk) - 1)))
    return words


def same_event(a: Article, b: Article) -> bool:
    if a.url == b.url:
        return True
    ta, tb = title_tokens(a.title), title_tokens(b.title)
    if not ta or not tb:
        return False
    # Distinct version numbers / amounts commonly indicate distinct events.
    na, nb = set(re.findall(r"\d+(?:\.\d+)*", a.title)), set(re.findall(r"\d+(?:\.\d+)*", b.title))
    if na and nb and na != nb:
        return False
    overlap = len(ta & tb) / len(ta | tb)
    ratio = SequenceMatcher(None, a.title.casefold(), b.title.casefold(), autojunk=False).ratio()
    if overlap >= 0.72 and ratio >= 0.68:
        return True
    # A video and a text report often have different verbs but the same named event.
    names_a = set(re.findall(r"\b[A-Z][a-z]{2,}\b", a.title))
    names_b = set(re.findall(r"\b[A-Z][a-z]{2,}\b", b.title))
    return len(names_a & names_b) >= 2 and len(ta & tb) >= 5 and overlap >= 0.5 and ratio >= 0.65


def build_stories(articles: list[Article], cfg: Config, now: datetime,
                  seen: set[str] | None = None) -> list[Story]:
    b = cfg.section("briefing")
    seen = seen or set()
    unique = {}
    for article in articles:
        if article.id in seen:
            continue
        if article.id not in unique or article.weight > unique[article.id].weight:
            unique[article.id] = article

    def score(article):
        age = 36.0
        if article.published_at:
            age = max(0, (now - datetime.fromisoformat(article.published_at)).total_seconds() / 3600)
        matches = sum(k.casefold() in (article.title + " " + article.excerpt).casefold()
                      for k in b["include_keywords"])
        return article.weight + max(0, 1 - age / b["lookback_hours"]) + min(matches, 3) * 0.2

    ordered = sorted(unique.values(), key=lambda a: (-score(a), a.id))
    groups = []
    # Cap candidates to bound O(n²) matching on large feeds.
    for article in ordered[:400]:
        group = next((g for g in groups if same_event(article, g[0])), None)
        if group is not None:
            if len(group) < 4:
                group.append(article)
        else:
            groups.append([article])
    groups.sort(key=lambda g: -(score(g[0]) + min(len({a.source_id for a in g}) - 1, 2) * 0.25))
    topics = cfg.section("topics")
    if topics["enabled"] and topics.get("balance", True):
        # Interleave ranked categories, so a high-volume feed cannot occupy every slot.
        from collections import deque
        categories = {}
        for group in groups:
            categories.setdefault(group[0].category, deque()).append(group)
        mixed = []
        while categories:
            for category in list(categories):
                mixed.append(categories[category].popleft())
                if not categories[category]:
                    del categories[category]
        groups = mixed
    stories, counts = [], {}
    for group in groups:
        lead = group[0]
        if counts.get(lead.source_id, 0) >= b["max_per_source"]:
            continue
        counts[lead.source_id] = counts.get(lead.source_id, 0) + 1
        story = Story(
            id=fingerprint(sorted(a.id for a in group))[:16], title=lead.title,
            category=lead.category, articles=group,
        )
        use_source_excerpts(story)
        stories.append(story)
        if len(stories) >= b["max_stories"]:
            break
    return stories


def use_source_excerpts(story: Story) -> None:
    story.mode = "source_excerpt"
    story.bullets = []
    previous = set()
    for article in story.articles:
        text = article.excerpt if article.excerpt else "该来源未提供摘要，请查看原文。"
        if len(text) > 260:
            shortened = text[:260]
            boundary = max(shortened.rfind(mark) for mark in ("。", ". ", "！", "？", "; "))
            text = shortened[:boundary + 1] if boundary > 140 else shortened.rstrip()
            text += "…"
        if text in previous:
            continue
        previous.add(text)
        story.bullets.append({"text": text, "sources": [article.id]})
        if len(story.bullets) == 3:
            break
