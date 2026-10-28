from dataclasses import replace
from daily_news.config import initialize, load_config
from daily_news.editorial import build_stories
from daily_news.feeds import parse_feed
from conftest import rss


def test_new_config_selects_all_topics_but_old_config_is_unchanged(tmp_path):
    from daily_news.topics import TOPICS
    fresh = tmp_path / "fresh" / "config.toml"
    initialize(fresh)
    cfg = load_config(fresh)
    assert cfg.data["topics"]["enabled"]
    assert set(cfg.data["topics"]["selected"]) == {t["id"] for t in TOPICS}
    legacy = tmp_path / "legacy" / "config.toml"
    legacy.parent.mkdir()
    legacy.write_text("[briefing]\ntitle = '旧日报'\n", encoding="utf-8")
    assert not load_config(legacy).data["topics"]["enabled"]


def test_more_specific_selected_topic_wins_over_broad_one(cfg, now):
    cfg.data["topics"].update(enabled=True, selected=["technology", "automotive"])
    source = {"id": "ithome", "name": "IT之家", "url": "https://www.ithome.com/rss/",
              "category": "科技", "weight": 1.0, "enabled": True}
    items, _ = parse_feed(rss(title="科技企业推出新能源汽车新车型"), source, cfg, now)
    assert items[0].category == "汽车与出行"


def test_balanced_selection_covers_categories_instead_of_one_high_volume_feed(cfg, article, now):
    cfg.data["topics"].update(enabled=True, selected=["technology", "automotive"], balance=True)
    cfg.data["briefing"].update(max_stories=4, max_per_source=30)
    articles = [
        replace(article, id=f"tech-{i:02d}", url=f"https://news.example/tech/{i}",
                title=f"Completely distinct technology story {i}", category="科技综合")
        for i in range(6)
    ]
    articles.extend([
        replace(article, id=f"z-car-{i:02d}", url=f"https://news.example/car/{i}",
                title=f"Different automotive announcement {i}", category="汽车与出行")
        for i in range(2)
    ])
    stories = build_stories(articles, cfg, now)
    assert len(stories) == 4
    assert sum(s.category == "汽车与出行" for s in stories) == 2
