"""The three independent choices used by all digest renderers."""

LAYOUTS = {
    "editorial": "纵向要闻",
    "cards": "卡片精选",
    "columns": "双栏报纸",
}
THEMES = {
    "light": "清晨纸白",
    "dark": "夜读深色",
    "accent": "暖调杂志",
}
DENSITIES = {
    "brief": "精简速览",
    "standard": "标准阅读",
    "detailed": "深度阅读",
}
DEFAULT_PRESENTATION = {"layout": "editorial", "theme": "light", "density": "standard"}

# Values are fixed application constants, never user-supplied CSS.
PALETTES = {
    "light": {
        "background": "#f1f3ef", "surface": "#ffffff", "surface_alt": "#f5f8f5",
        "text": "#172f30", "muted": "#526966", "accent": "#17695e",
        "border": "#d8e4de", "header": "#173c3a", "header_text": "#ffffff",
        "header_muted": "#d0e7df", "chip": "#e6f2ea",
        "warning_bg": "#fff4da", "warning_text": "#69430a",
    },
    "dark": {
        "background": "#0d1920", "surface": "#1a2c32", "surface_alt": "#21383d",
        "text": "#eef5ef", "muted": "#bed3ca", "accent": "#ade8d3",
        "border": "#41615d", "header": "#102a2b", "header_text": "#f2faf5",
        "header_muted": "#c2ded1", "chip": "#294b42",
        "warning_bg": "#4c3a1c", "warning_text": "#fff0c8",
    },
    "accent": {
        "background": "#f8f0ea", "surface": "#fffdf9", "surface_alt": "#fdf1e9",
        "text": "#392a26", "muted": "#765b50", "accent": "#9b3f2c",
        "border": "#efd9cb", "header": "#8e4233", "header_text": "#ffffff",
        "header_muted": "#ffe6d7", "chip": "#fae9de",
        "warning_bg": "#fff1ce", "warning_text": "#70400a",
    },
}


def resolve_presentation(value=None):
    """Normalize partial/legacy settings and reject unknown styles."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("presentation must be a table")
    invalid = set(value) - set(DEFAULT_PRESENTATION)
    if invalid:
        raise ValueError("unknown presentation setting: " + ", ".join(sorted(invalid)))
    result = {**DEFAULT_PRESENTATION, **value}
    for key, choices in (("layout", LAYOUTS), ("theme", THEMES), ("density", DENSITIES)):
        if not isinstance(result[key], str) or result[key] not in choices:
            raise ValueError(f"unsupported presentation {key}: {result[key]}")
    return result


def _shorten(text, length):
    text = str(text).strip()
    if len(text) <= length:
        return text
    excerpt = text[:length].rstrip()
    last_end = max(excerpt.rfind(mark) for mark in ("。", "！", "？", ". ", "! ", "? "))
    if last_end >= length // 2:
        excerpt = excerpt[:last_end + 1].rstrip()
    return excerpt.rstrip("…") + "…"


def visible_bullets(story, density):
    """Return display-only copies; never mutate the persisted source material."""
    # Validate density even when this helper is called directly.
    resolve_presentation({"density": density})
    limits = {"brief": (1, 90), "standard": (2, 260), "detailed": (3, 720)}
    count, length = limits[density]
    articles = {article.id: article for article in story.articles}
    result = []
    for bullet in story.bullets:
        refs = bullet.get("sources", [])
        # Refuse to display unsupported facts without valid source citations.
        if (not isinstance(refs, list) or not refs
                or any(not isinstance(source, str) or source not in articles for source in refs)):
            continue
        text = bullet.get("text", "")
        if not isinstance(text, str) or not text.strip():
            continue
        if density == "detailed" and story.mode == "source_excerpt" and len(refs) == 1:
            # The stored source excerpt is bounded at ingestion; use it only when it
            # actually contains the displayed excerpt, never substitute AI output.
            candidate = articles[refs[0]].excerpt
            prefix = text.rstrip("…").strip()
            if isinstance(candidate, str) and prefix and candidate.startswith(prefix) and len(candidate) > len(text):
                text = candidate
        result.append({"text": _shorten(text, length), "sources": list(refs)})
        if len(result) == count:
            break
    return result
