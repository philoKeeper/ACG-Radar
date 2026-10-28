from dataclasses import dataclass, field


@dataclass
class Candidate:
    source: str
    source_id: str
    type: str
    title: str
    titles: dict = field(default_factory=dict)
    year: int | None = None
    synopsis: str = ""
    cover_url: str = ""
    tags: list = field(default_factory=list)
    rating: float | None = None
    rating_scale: float = 10
    rating_votes: int = 0
    url: str = ""
    nsfw: bool = False

    def to_dict(self):
        return {
            "source": self.source,
            "source_id": str(self.source_id),
            "type": self.type,
            "title": self.title,
            "titles": self.titles,
            "year": self.year,
            "synopsis": self.synopsis,
            "cover_url": self.cover_url,
            "tags": self.tags,
            "rating": self.rating,
            "rating_scale": self.rating_scale,
            "rating_votes": self.rating_votes,
            "url": self.url,
            "nsfw": self.nsfw,
        }


@dataclass
class ReviewItem:
    source: str
    source_id: str
    author: str
    text: str
    score: float | None = None
    score_scale: float = 10
    language: str = ""
    kind: str = ""
    spoiler: bool = False
    likes: int = 0
    url: str = ""
    posted_at: str = ""
    sentiment: str = "neutral"


@dataclass
class ReviewPage:
    items: list
    total: int = 0
    next_cursor: str = ""


class SourceAdapter:
    source = ""
    label = ""
    interval = 1.2
    supports_reviews = True

    def __init__(self, client, allow_nsfw=False, logger=None):
        self.client = client
        self.allow_nsfw = allow_nsfw
        self.logger = logger

    def search(self, query, subject_type="anime", limit=8, filters=None):
        raise NotImplementedError

    def search_page(self, query, subject_type="anime", limit=8, filters=None, page=1):
        return {"items": self.search(query, subject_type, limit, filters), "has_more": False, "page": int(page or 1)}

    def resolve(self, value):
        return None

    def fetch_work(self, source_id):
        raise NotImplementedError

    def iter_reviews(self, source_id, limit=120):
        if False:
            yield None
        return

    def fetch_rating(self, source_id):
        return None

    def health(self):
        return True
