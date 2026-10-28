"""Topic catalog with source-aware, conservative article matching.

RSS is not a content classifier. Broad feeds are matched on the title and excerpt;
only truly vertical feeds can assign a category without a keyword match.
"""
from collections.abc import Mapping, Sequence
import re


def _source(name: str, url: str, category: str) -> dict:
    return {"name": name, "url": url, "category": category, "weight": 1.0, "enabled": True}


# IDs for the original three feeds are intentionally preserved.
SOURCES = {
    "ithome": _source("IT之家", "https://www.ithome.com/rss/", "科技综合"),
    "solidot": _source("Solidot", "https://www.solidot.org/index.rss", "科技综合"),
    "sspai": _source("少数派", "https://sspai.com/feed", "数字生活"),
    "36kr": _source("36氪", "https://www.36kr.com/feed", "商业科技"),
    "cn-china": _source("中新网·国内", "https://www.chinanews.com.cn/rss/china.xml", "国内"),
    "cn-world": _source("中新网·国际", "https://www.chinanews.com.cn/rss/world.xml", "国际"),
    "cn-society": _source("中新网·社会", "https://www.chinanews.com.cn/rss/society.xml", "社会"),
    "cn-finance": _source("中新网·财经", "https://www.chinanews.com.cn/rss/finance.xml", "财经"),
    "cn-life": _source("中新网·生活", "https://www.chinanews.com.cn/rss/life.xml", "生活"),
    "cn-culture": _source("中新网·文娱", "https://www.chinanews.com.cn/rss/culture.xml", "文娱"),
    "cn-sports": _source("中新网·体育", "https://www.chinanews.com.cn/rss/sports.xml", "体育"),
    "cn-education": _source("中新网·教育", "https://www.chinanews.com.cn/rss/edu.xml", "教育"),
    "cn-law": _source("中新网·法治", "https://www.chinanews.com.cn/rss/fz.xml", "法治"),
    "bbc-world": _source("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "国际"),
    "bbc-business": _source("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", "商业财经"),
    "bbc-tech": _source("BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml", "科技"),
    "bbc-science": _source(
        "BBC Science & Environment", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "科学与环境"
    ),
    "bbc-health": _source("BBC Health", "https://feeds.bbci.co.uk/news/health/rss.xml", "健康"),
    "bbc-sports": _source("BBC Sport", "https://feeds.bbci.co.uk/sport/rss.xml", "体育"),
    "bbc-culture": _source(
        "BBC Entertainment & Arts", "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml", "文娱"
    ),
    "nasa": _source("NASA 新闻", "https://www.nasa.gov/news-release/feed/", "航天"),
}


def _topic(id: str, name: str, group: str, description: str,
           source_ids: tuple[str, ...], keywords: tuple[str, ...],
           direct_source_ids: tuple[str, ...] = ()) -> dict:
    return {
        "id": id, "name": name, "group": group, "description": description,
        "source_ids": list(source_ids), "keywords": list(keywords),
        "direct_source_ids": list(direct_source_ids),
    }


TOPICS = (
    _topic("domestic", "国内要闻", "要闻与社会", "国内政策、公共事务及重要动态",
           ("cn-china",), ("中国", "国内", "政策", "china"), ("cn-china",)),
    _topic("international", "国际与世界", "要闻与社会", "国际局势与世界各地新闻",
           ("cn-world", "bbc-world"), ("国际", "全球", "世界", "global", "world"),
           ("cn-world", "bbc-world")),
    _topic("society", "社会与民生", "要闻与社会", "社会事件、公共服务与民生",
           ("cn-society",), ("社会", "民生", "社区", "生活", "society"), ("cn-society",)),
    _topic("law", "法治与安全", "要闻与社会", "法治、司法及公共安全",
           ("cn-law", "cn-society", "bbc-world"),
           ("法治", "司法", "法院", "法律", "犯罪", "公安", "诈骗", "court", "law", "crime"),
           ("cn-law",)),
    _topic("local", "城市与地方", "要闻与社会", "城市发展和地方新闻；可用关键词关注具体地区",
           ("cn-china", "cn-society", "cn-life"),
           ("城市", "地方", "县城", "街道", "乡村", "城镇", "重庆", "上海", "北京", "深圳", "city")),

    _topic("technology", "科技综合", "科技与数码", "科技产品、技术发展及数字产业",
           ("ithome", "solidot", "sspai", "36kr", "bbc-tech"),
           ("科技", "技术", "智能", "数码", "芯片", "手机", "电脑", "软件", "硬件", "互联网", "网络",
            "technology", "tech", "digital", "computer", "chip", "smartphone", "software", "internet"),
           ("bbc-tech",)),
    _topic("ai", "人工智能", "科技与数码", "大模型、AI 应用、机器学习与机器人",
           ("ithome", "solidot", "36kr", "bbc-tech"),
           ("人工智能", "大模型", "机器学习", "深度学习", "神经网络", "机器人", "智算", "生成式",
            "ai", "llm", "artificial intelligence", "machine learning", "deep learning", "chatgpt",
            "deepseek", "qwen", "openai", "anthropic", "gemini", "robotics")),
    _topic("mobile", "手机与智能硬件", "科技与数码", "手机、电脑、芯片及消费硬件",
           ("ithome", "sspai", "36kr", "bbc-tech"),
           ("手机", "电脑", "平板", "笔记本", "芯片", "处理器", "显卡", "耳机", "显示器", "平板电脑",
            "智能硬件", "iphone", "ipad", "macbook", "android", "harmonyos", "processor", "gpu", "chip",
            "smartphone", "tablet", "laptop")),
    _topic("software", "软件与开发", "科技与数码", "应用、操作系统、开源与开发工具",
           ("ithome", "solidot", "sspai", "36kr", "bbc-tech"),
           ("软件", "开源", "操作系统", "开发者", "编程", "数据库", "浏览器", "应用程序", "软件开发",
            "windows", "linux", "macos", "android", "github", "python", "javascript", "open source",
            "software", "developer", "programming", "database")),
    _topic("cybersecurity", "网络安全", "科技与数码", "网络攻击、漏洞、隐私与信息安全",
           ("ithome", "solidot", "bbc-tech"),
           ("网络安全", "信息安全", "漏洞", "勒索", "隐私", "数据泄露", "黑客", "安全补丁",
            "cybersecurity", "cyberattack", "security breach", "privacy", "malware", "ransomware", "vulnerability")),
    _topic("games", "游戏与电竞", "科技与数码", "游戏、主机、电竞与互动娱乐",
           ("ithome", "sspai", "36kr", "bbc-tech", "cn-culture"),
           ("游戏", "电竞", "主机", "玩家", "playstation", "xbox", "nintendo", "esports", "video game",
            "game studio", "steam")),

    _topic("automotive", "汽车与出行", "汽车与产业", "汽车、新能源车、交通和移动出行",
           ("ithome", "36kr", "cn-finance", "cn-life", "bbc-business"),
           ("汽车", "新能源车", "电动车", "电动汽车", "轿车", "车企", "车市", "电池车", "出行",
            "automotive", "electric vehicle", "electric car", "automaker", "vehicle", "car")),
    _topic("smart_driving", "智能驾驶", "汽车与产业", "辅助驾驶、自动驾驶和车载技术",
           ("ithome", "36kr", "cn-finance", "bbc-tech"),
           ("自动驾驶", "智能驾驶", "辅助驾驶", "无人驾驶", "智驾", "车载芯片", "车机",
            "autonomous driving", "self-driving", "driver assistance", "adas")),
    _topic("energy", "能源与新能源", "汽车与产业", "电池、电力、清洁能源及产业转型",
           ("ithome", "solidot", "36kr", "cn-finance", "bbc-science", "bbc-business"),
           ("能源", "电力", "电网", "电池", "储能", "光伏", "太阳能", "风电", "清洁能源", "核能",
            "energy", "battery", "electricity", "solar power", "wind power", "renewable", "nuclear power")),

    _topic("finance", "财经与金融", "商业与经济", "经济、金融、行业与市场综合资讯",
           ("cn-finance", "36kr", "bbc-business"),
           ("财经", "金融", "经济", "资本", "银行", "finance", "financial", "economy", "market"),
           ("cn-finance", "bbc-business")),
    _topic("markets", "股票与市场", "商业与经济", "股票、证券、基金和交易市场",
           ("cn-finance", "36kr", "bbc-business"),
           ("股市", "股票", "证券", "基金", "债券", "上市", "融资", "大盘", "a股", "港股", "美股",
            "stock", "stocks", "shares", "bond", "nasdaq", "wall street")),
    _topic("business", "企业与商业", "商业与经济", "公司动态、商业模式、企业与产业",
           ("36kr", "cn-finance", "bbc-business"),
           ("企业", "公司", "商业", "营收", "盈利", "并购", "供应链", "品牌", "business", "company",
            "corporate", "revenue", "merger", "acquisition")),
    _topic("startups", "创业与投资", "商业与经济", "创业公司、融资、风投与创新项目",
           ("36kr", "cn-finance", "bbc-business"),
           ("创业", "初创", "融资", "风投", "投资", "孵化", "创业公司", "startup", "start-up",
            "venture capital", "funding round", "investment")),
    _topic("property", "房产与住房", "商业与经济", "住房、房地产和城市居住",
           ("cn-finance", "cn-life", "bbc-business"),
           ("房产", "房地产", "楼市", "房价", "租房", "住房", "购房", "地产", "real estate", "housing",
            "property market", "mortgage")),

    _topic("science", "科学与研究", "科学与环境", "自然科学、研究成果与科学发现",
           ("solidot", "bbc-science", "nasa", "cn-education"),
           ("科学", "科研", "研究", "实验", "物理", "生物", "化学", "数学", "量子", "天文", "发现",
            "science", "scientist", "research", "study", "physics", "biology", "chemistry", "discovery")),
    _topic("space", "航天与天文", "科学与环境", "航天、太空探索、卫星与天文",
           ("nasa", "ithome", "solidot", "bbc-science", "cn-china"),
           ("航天", "航天员", "太空", "卫星", "火箭", "探月", "月球", "宇宙", "天文", "space", "nasa",
            "astronaut", "satellite", "rocket", "moon", "astronomy", "telescope"),
           ("nasa",)),
    _topic("environment", "环境与气候", "科学与环境", "环境保护、气候、自然与可持续发展",
           ("bbc-science", "solidot", "cn-society", "cn-finance"),
           ("环境", "气候", "碳排放", "低碳", "生态", "污染", "气温", "可持续", "全球变暖",
            "environment", "climate", "carbon emissions", "pollution", "sustainability", "global warming")),

    _topic("health", "健康与医学", "生活与兴趣", "健康、医学研究与公共卫生",
           ("bbc-health", "cn-life", "cn-society", "solidot"),
           ("健康", "医学", "医疗", "医院", "疾病", "疫苗", "公共卫生", "营养", "心理", "药物",
            "health", "medical", "medicine", "hospital", "disease", "vaccine", "nutrition"),
           ("bbc-health",)),
    _topic("education", "教育与学习", "生活与兴趣", "教育政策、学校、考试及学习",
           ("cn-education", "cn-society", "36kr"),
           ("教育", "学校", "学生", "大学", "教师", "考试", "学习", "招生", "高考", "education",
            "university", "school", "student", "learning"), ("cn-education",)),
    _topic("culture", "文化与艺术", "生活与兴趣", "文化、艺术、历史、文学与展览",
           ("cn-culture", "bbc-culture", "cn-life"),
           ("文化", "艺术", "历史", "文学", "博物馆", "展览", "非遗", "戏剧", "阅读",
            "culture", "art", "history", "literature", "museum", "exhibition")),
    _topic("entertainment", "影视与娱乐", "生活与兴趣", "电影、电视剧、音乐与娱乐产业",
           ("cn-culture", "bbc-culture", "ithome", "cn-life"),
           ("电影", "电视剧", "影视", "演员", "导演", "音乐", "演唱会", "票房", "综艺",
            "film", "movie", "television", "music", "cinema", "box office")),
    _topic("sports", "体育与赛事", "生活与兴趣", "体育新闻、赛事与运动员",
           ("cn-sports", "bbc-sports"),
           ("体育", "赛事", "足球", "篮球", "网球", "运动员", "奥运", "sport", "football", "basketball",
            "tennis", "olympics"), ("cn-sports", "bbc-sports")),
    _topic("travel", "旅行与旅游", "生活与兴趣", "旅游目的地、旅行服务和文旅",
           ("cn-life", "cn-society", "cn-culture"),
           ("旅游", "旅行", "景区", "游客", "度假", "航班", "铁路", "文旅", "travel", "tourism",
            "tourist", "hotel", "flight")),
    _topic("lifestyle", "生活与消费", "生活与兴趣", "日常生活、消费服务与生活方式",
           ("cn-life", "sspai", "cn-society", "36kr"),
           ("生活", "消费", "购物", "饮食", "美食", "家居", "家庭", "居住", "生活方式",
            "lifestyle", "consumer", "shopping", "food", "home"), ("cn-life",)),
)

_TOPIC_BY_ID = {topic["id"]: topic for topic in TOPICS}


def _selected(topic_ids: Sequence[str]) -> list[dict]:
    if isinstance(topic_ids, (str, bytes)) or not isinstance(topic_ids, Sequence):
        raise ValueError("topic_ids 必须是主题 ID 数组")
    result = []
    seen = set()
    for topic_id in topic_ids:
        if not isinstance(topic_id, str) or topic_id not in _TOPIC_BY_ID:
            raise ValueError(f"未知新闻类型: {topic_id}")
        if topic_id not in seen:
            result.append(_TOPIC_BY_ID[topic_id])
            seen.add(topic_id)
    return result


def build_sources(topic_ids: Sequence[str]) -> list[dict]:
    """Return fresh source dicts for the selected topics, with duplicate feeds removed."""
    selected = _selected(topic_ids)
    result = []
    seen = set()
    for topic in selected:
        for source_id in topic["source_ids"]:
            if source_id not in seen:
                seen.add(source_id)
                result.append({"id": source_id, **SOURCES[source_id]})
    return result


def _compile(term: str):
    term = term.strip().casefold()
    if not term:
        raise ValueError("关键词不能空白")
    if re.search(r"[\u3400-\u9fff]", term):
        return term
    # ASCII boundary prevents 'AI' from matching 'said', while allowing 'AI芯片'.
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")


_COMPILED = {topic["id"]: tuple(_compile(word) for word in topic["keywords"]) for topic in TOPICS}


def _contains(term, text: str) -> bool:
    if isinstance(term, str):
        return term in text
    return term.search(text) is not None


def match_topics(title: str, excerpt: str, source_id: str, topic_ids: Sequence[str], *,
                 extra_keywords: Mapping[str, Sequence[str]] | None = None) -> list[str]:
    """Return matching selected IDs; custom feed IDs use keywords only.

    Extra per-topic keywords are additive. Matching is intentionally lexical rather
    than an AI semantic claim; a topic can have zero hits.
    """
    selected = _selected(topic_ids)
    if not isinstance(title, str) or not isinstance(excerpt, str) or not isinstance(source_id, str):
        raise ValueError("标题、摘要和来源 ID 必须是字符串")
    extras = extra_keywords or {}
    if not isinstance(extras, Mapping):
        raise ValueError("extra_keywords 必须按主题 ID 映射到关键词数组")
    for topic_id, terms in extras.items():
        if topic_id not in _TOPIC_BY_ID or isinstance(terms, (str, bytes)) or not isinstance(terms, Sequence):
            raise ValueError("extra_keywords 包含未知主题或无效关键词数组")
        if any(not isinstance(term, str) or not term.strip() for term in terms):
            raise ValueError("自定义关键词必须是非空字符串")
    text = (title + " " + excerpt).casefold()
    result = []
    for topic in selected:
        if source_id in SOURCES and source_id not in topic["source_ids"]:
            continue
        if source_id in topic["direct_source_ids"]:
            result.append(topic["id"])
            continue
        terms = _COMPILED[topic["id"]] + tuple(_compile(term) for term in extras.get(topic["id"], ()))
        if any(_contains(term, text) for term in terms):
            result.append(topic["id"])
    return result
