from urllib.parse import urlsplit

import pytest

from daily_news.topics import SOURCES, TOPICS, build_sources, match_topics


def test_catalog_covers_distinct_user_interests_and_references_known_sources():
    by_id = {topic["id"]: topic for topic in TOPICS}
    assert len(by_id) == len(TOPICS) >= 20
    assert {"technology", "ai", "automotive", "finance", "international", "domestic",
            "science", "health", "culture", "sports", "education"} <= set(by_id)
    for topic in TOPICS:
        assert topic["name"] and topic["description"] and topic["group"]
        assert topic["keywords"] and topic["source_ids"]
        assert set(topic["direct_source_ids"]) <= set(topic["source_ids"]) <= set(SOURCES)


def test_selected_sources_are_valid_deduplicated_and_preserve_existing_ids():
    sources = build_sources(["technology", "ai", "automotive", "science", "technology"])
    ids = [source["id"] for source in sources]
    assert len(ids) == len(set(ids))
    assert {"ithome", "solidot"} <= set(ids)
    for source in sources:
        assert set(source) == {"id", "name", "url", "category", "weight", "enabled"}
        assert urlsplit(source["url"]).scheme == "https"
        assert source["enabled"] and source["weight"] > 0


def test_returned_sources_are_independent_from_catalog():
    first = build_sources(["technology"])
    first[0]["name"] = "modified"
    assert build_sources(["technology"])[0]["name"] != "modified"


def test_unknown_or_invalid_topic_fails_without_silently_expanding_sources():
    assert build_sources([]) == []
    with pytest.raises(ValueError, match="未知"):
        build_sources(["technology", "not-a-topic"])
    with pytest.raises(ValueError):
        build_sources("technology")


def test_auto_selection_does_not_include_an_unrelated_phone_article():
    assert match_topics("苹果发布新款手机", "芯片和显示器升级", "ithome", ["automotive"]) == []
    assert match_topics("小米汽车新款电动车发布", "新款车型开始交付", "ithome", ["automotive"]) == [
        "automotive"
    ]


def test_multiple_selected_topics_use_union_and_keep_each_matching_category():
    result = match_topics("新款汽车搭载人工智能芯片", "", "ithome", ["ai", "automotive"])
    assert result == ["ai", "automotive"]
    assert match_topics("某人工智能模型更新", "", "ithome", ["automotive"]) == []


def test_ai_and_car_terms_respect_ascii_word_boundaries():
    assert match_topics("The company said results improved", "", "bbc-tech", ["ai"]) == []
    assert match_topics("AI芯片发布", "", "ithome", ["ai"]) == ["ai"]
    assert match_topics("Carnegie opens a campus", "", "bbc-business", ["automotive"]) == []
    assert match_topics("Electric car deliveries rise", "", "bbc-business", ["automotive"]) == ["automotive"]


def test_direct_vertical_source_but_not_unrelated_known_source():
    assert match_topics("中国队晋级决赛", "", "cn-sports", ["sports"]) == ["sports"]
    assert match_topics("AI 机器人赢得比赛", "", "cn-sports", ["ai"]) == []
    assert match_topics("无关键词标题", "", "ithome", ["ai", "automotive"]) == []


def test_custom_source_gets_selected_keyword_matching_without_unconditional_classification():
    assert match_topics("芯片研发的新突破", "", "user-custom", ["mobile"]) == ["mobile"]
    assert match_topics("无关键词标题", "", "user-custom", ["mobile"]) == []
    assert match_topics("小米 SU7 动态", "", "user-custom", ["automotive"],
                        extra_keywords={"automotive": ["SU7"]}) == ["automotive"]


def test_extra_keywords_are_validated_and_apply_only_to_their_topic():
    assert match_topics("哪吒", "", "ithome", ["automotive", "entertainment"],
                        extra_keywords={"automotive": ["哪吒"]}) == ["automotive"]
    assert match_topics("无关键词", "", "ithome", []) == []
    with pytest.raises(ValueError):
        match_topics("x", "", "ithome", ["ai"], extra_keywords={"unknown": ["x"]})
    with pytest.raises(ValueError):
        match_topics("x", "", "ithome", ["ai"], extra_keywords={"ai": [""]})
    with pytest.raises(ValueError):
        match_topics("x", "", "ithome", ["ai"], extra_keywords={"ai": "x"})


def test_direct_source_has_no_entries_without_choosing_that_topic():
    assert match_topics("中国队晋级", "", "cn-sports", []) == []
    assert match_topics("中国队晋级", "", "cn-sports", ["international"]) == []
