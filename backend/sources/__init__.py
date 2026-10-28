import threading
import time

from ..http_client import HttpClient
from .bangumi import BangumiAdapter


class SourceRegistry:
    def __init__(self, settings, logger=None, secrets=None):
        crawler = settings.get("crawler", {})
        proxy = (settings.get("network", {}) or {}).get("proxy", "")
        self.logger = logger
        self.allow_nsfw = bool(crawler.get("allow_nsfw"))
        token = secrets.get_bangumi_token() if secrets else ""
        cookie = secrets.get_bangumi_cookie() if secrets else ""
        interval = max(0.5, min(10.0, float(crawler.get("request_interval") or 0.5)))
        client = HttpClient(proxy=proxy, interval=interval, logger=logger)
        self.adapter = BangumiAdapter(client, allow_nsfw=self.allow_nsfw, logger=logger, token=token, cookie=cookie)
        self.adapters = {"bangumi": self.adapter}
        self.cache = {}
        self.lock = threading.Lock()

    def enabled(self):
        return ["bangumi"]

    def get(self, source):
        return self.adapter if source == "bangumi" else None

    def search_page(self, query, subject_type="all", limit=20, filters=None, page=1):
        query = (query or "").strip()
        filters = filters or {}
        page = max(1, int(page or 1))
        if not query and not filters:
            return {"results": [], "errors": [], "cached": False, "page": page, "has_more": False}
        filter_key = (
            tuple(str(tag) for tag in (filters.get("tags") or [])),
            filters.get("year_from"),
            filters.get("year_to"),
            filters.get("min_rating"),
        )
        key = (query, subject_type, filter_key, page, limit)
        with self.lock:
            hit = self.cache.get(key)
            if hit and time.monotonic() - hit[0] < 600:
                return {"results": hit[1], "errors": hit[2], "cached": True, "page": page, "has_more": hit[3]}
        try:
            payload = self.adapter.search_page(query, subject_type, limit, filters, page)
            results = [item.to_dict() for item in (payload.get("items") or [])]
            errors = []
            has_more = bool(payload.get("has_more"))
        except Exception as exc:
            results = []
            errors = [{"source": "bangumi", "message": str(exc)}]
            has_more = False
            if self.logger:
                self.logger.warning("Bangumi search failed: %s", exc)
        with self.lock:
            self.cache[key] = (time.monotonic(), results, errors, has_more)
        return {"results": results, "errors": errors, "cached": False, "page": page, "has_more": has_more}

    def search(self, query, subject_type="all", limit=20, filters=None):
        return self.search_page(query, subject_type, limit, filters, 1)

    def resolve(self, value):
        return "bangumi", self.adapter.resolve(value)
