from dataclasses import replace
from itertools import product
import re

import pytest

from daily_news.models import Story
from daily_news.presentation import DENSITIES, LAYOUTS, PALETTES, THEMES, resolve_presentation, visible_bullets
from daily_news.render import html_content, markdown_content, text_content


@pytest.fixture
def rich_digest(digest, article):
    # Two stories exercise the newspaper layout; three distinct cited points and
    # long excerpts make the three information densities observable.
    other = replace(article, id="article-two", url="https://news.example/b",
                    title="A second source", source_name="Second source")
    long_text = "原始来源提供了可靠的事实和具体背景。" * 35
    digest.stories = [
        Story("story-a", "第一条要闻", "科技", [article, other], [
            {"text": "事实一。" + long_text, "sources": [article.id]},
            {"text": "事实二。" + long_text, "sources": [other.id]},
            {"text": "事实三。" + long_text, "sources": [article.id, other.id]},
        ], mode="ai_synthesis"),
        Story("story-b", "第二条要闻", "国际", [other], [
            {"text": "这是第二条要闻的内容。", "sources": [other.id]},
        ], mode="source_excerpt"),
    ]
    return digest


def test_default_and_legacy_settings():
    assert resolve_presentation() == {"layout": "editorial", "theme": "light", "density": "standard"}
    assert resolve_presentation({"theme": "dark"}) == {
        "layout": "editorial", "theme": "dark", "density": "standard"
    }


@pytest.mark.parametrize("value", [
    {"layout": "hacked"}, {"theme": 'x";color:red'}, {"density": "full"},
    {"extra": "surprise"}, {"layout": None}, [],
])
def test_unknown_presentation_values_are_rejected(value):
    with pytest.raises(ValueError):
        resolve_presentation(value)


def test_all_27_combinations_render_referenced_content(rich_digest):
    pages = {}
    for layout, theme, density in product(LAYOUTS, THEMES, DENSITIES):
        rich_digest.presentation = {"layout": layout, "theme": theme, "density": density}
        page = html_content(rich_digest)
        assert f'data-layout="{layout}"' in page
        assert f'data-theme="{theme}"' in page
        assert f'data-density="{density}"' in page
        assert "第一条要闻" in page and "第二条要闻" in page
        assert "https://news.example/a" in page and "[1]" in page
        assert "原始来源" in page
        pages[layout, theme, density] = page
    assert len(pages) == 27
    assert len(set(pages.values())) == 27


def test_three_layouts_change_page_structure_and_support_mobile(rich_digest):
    pages = {}
    for layout in LAYOUTS:
        rich_digest.presentation = {"layout": layout}
        pages[layout] = html_content(rich_digest)
    assert 'class="daily-col"' not in pages["editorial"]
    assert 'border-radius:14px' in pages["cards"]
    assert 'class="daily-col"' in pages["columns"]
    assert "@media only screen and (max-width:600px)" in pages["columns"]
    assert "display:block!important;width:100%!important" in pages["columns"]


def test_theme_changes_real_colors(rich_digest):
    pages = {}
    for theme in THEMES:
        rich_digest.presentation = {"theme": theme}
        pages[theme] = html_content(rich_digest)
    assert "background:#f1f3ef" in pages["light"]
    assert "background:#0d1920" in pages["dark"]
    assert "background:#f8f0ea" in pages["accent"]


def test_theme_text_colors_are_readable_on_all_surfaces():
    def luminance(hex_color):
        channels = [int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    for palette in PALETTES.values():
        for foreground, background in [
            ("text", "surface"), ("text", "surface_alt"),
            ("muted", "surface"), ("muted", "surface_alt"),
            ("accent", "surface"), ("accent", "surface_alt"),
            ("header_text", "header"), ("header_muted", "header"),
        ]:
            low, high = sorted([luminance(palette[foreground]), luminance(palette[background])])
            assert (high + 0.05) / (low + 0.05) >= 4.5


def test_density_changes_content_consistently_across_formats(rich_digest):
    rendered = {}
    for density in DENSITIES:
        rich_digest.presentation = {"density": density}
        rendered[density] = (html_content(rich_digest), markdown_content(rich_digest), text_content(rich_digest))
    for index in range(3):
        brief, standard, detailed = [rendered[density][index] for density in ("brief", "standard", "detailed")]
        assert "事实一。" in brief and "事实二。" not in brief
        assert "事实二。" in standard and "事实三。" not in standard
        assert "事实三。" in detailed
        assert len(brief) < len(standard) < len(detailed)


def test_detailed_exposes_longer_source_excerpt_without_mutating_stored_bullet(article):
    long_excerpt = "新闻事实。" * 160
    source = replace(article, excerpt=long_excerpt)
    original = {"text": long_excerpt[:80] + "…", "sources": [source.id]}
    story = Story("s", "标题", "科技", [source], [original], mode="source_excerpt")
    brief = visible_bullets(story, "brief")
    detailed = visible_bullets(story, "detailed")
    assert len(brief[0]["text"]) <= 91
    assert len(detailed[0]["text"]) > len(brief[0]["text"])
    assert original["text"] == long_excerpt[:80] + "…"


def test_uncited_or_foreign_bullets_are_never_rendered(rich_digest):
    story = rich_digest.stories[0]
    story.bullets = [
        {"text": "无依据内容", "sources": []},
        {"text": "伪造引用内容", "sources": ["not-a-source"]},
        {"text": "有依据内容", "sources": [story.articles[0].id]},
    ]
    rich_digest.presentation = {"density": "detailed"}
    for rendered in (html_content(rich_digest), markdown_content(rich_digest), text_content(rich_digest)):
        assert "无依据内容" not in rendered and "伪造引用内容" not in rendered
        assert "有依据内容" in rendered and "[1]" in rendered


def test_untrusted_content_escaped_for_all_layouts(rich_digest):
    article = rich_digest.stories[0].articles[0]
    article.title = '<script>alert("source")</script>'
    article.url = "javascript:alert(1)"
    rich_digest.stories[0].title = '<img src=x onerror="alert(1)">'
    rich_digest.stories[0].bullets[0]["text"] = '<script>alert("bullet")</script>'
    for layout in LAYOUTS:
        rich_digest.presentation = {"layout": layout}
        page = html_content(rich_digest)
        assert "<script>" not in page and "<img src=x" not in page
        assert "&lt;script&gt;" in page and "&lt;img" in page
        assert 'href="javascript:' not in page
        assert re.search(r'\[[0-9]+\]</a>', page)
