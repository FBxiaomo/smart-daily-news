"""Serializable domain objects. Remote text is always data, never executable instructions."""
from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json


def fingerprint(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class Article:
    id: str
    title: str
    url: str
    source_id: str
    source_name: str
    category: str
    published_at: str | None
    excerpt: str
    weight: float = 1.0


@dataclass
class Story:
    id: str
    title: str
    category: str
    articles: list[Article]
    bullets: list[dict] = field(default_factory=list)
    mode: str = "source_excerpt"


@dataclass
class Digest:
    edition: str
    title: str
    created_at: str
    timezone: str
    stories: list[Story]
    source_reports: list[dict]
    warnings: list[str] = field(default_factory=list)
    ai_usage: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Digest":
        values = dict(data)
        values["stories"] = [
            Story(**{**story, "articles": [Article(**a) for a in story["articles"]]})
            for story in data["stories"]
        ]
        return cls(**values)

    def article_ids(self) -> list[str]:
        return list(dict.fromkeys(a.id for s in self.stories for a in s.articles))


def utc_now() -> datetime:
    from datetime import timezone
    return datetime.now(timezone.utc)
