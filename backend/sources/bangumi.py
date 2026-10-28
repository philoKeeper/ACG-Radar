import hashlib
import html
import re
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

from ..util import clean_text, guess_language, parse_date, parse_year, sentiment_from_score
from .base import Candidate, ReviewItem, ReviewPage, SourceAdapter


BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8,en;q=0.6",
}

BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "section", "article", "tr", "table", "hr",
}


class _TextExtractor(HTMLParser):
    def __init__(self, target_id=None):
        super().__init__(convert_charrefs=True)
        self.target_id = target_id
        self.active = target_id is None
        self.depth = 0
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.target_id and not self.active:
            if attrs.get("id") == self.target_id:
                self.active = True
                self.depth = 1
            return
        if not self.active:
            return
        if tag in ("script", "style"):
            self.skip += 1
            return
        if self.target_id and tag == "div":
            if self.depth > 0:
                self.depth += 1
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if self.active and tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if not self.active:
            return
        if tag in ("script", "style"):
            if self.skip:
                self.skip -= 1
            return
        if tag in BLOCK_TAGS:
            self.parts.append("\n")
        if self.target_id and tag == "div":
            self.depth -= 1
            if self.depth <= 0:
                self.active = False

    def handle_data(self, data):
        if self.active and not self.skip:
            self.parts.append(data)

    def text(self):
        value = "".join(self.parts)
        value = re.sub(r"[ \t\f\v]+", " ", value)
        value = re.sub(r" *\n *", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


def _to_text(fragment):
    parser = _TextExtractor()
    try:
        parser.feed(fragment or "")
        parser.close()
    except Exception:
        return html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).strip()
    return parser.text()


def _extract_by_id(fragment, element_id):
    parser = _TextExtractor(element_id)
    try:
        parser.feed(fragment or "")
        parser.close()
    except Exception:
        return ""
    return parser.text()


def _parse_bgm_date(value):
    text = re.sub(r"\s+", " ", value or "").strip()
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", text)
    if not match:
        return ""
    year, month, day = match.group(1), int(match.group(2)), int(match.group(3))
    hour = int(match.group(4) or 0)
    minute = int(match.group(5) or 0)
    return "%04d-%02d-%02dT%02d:%02d:00+08:00" % (int(year), month, day, hour, minute)


class BangumiAdapter(SourceAdapter):
    source = "bangumi"
    label = "Bangumi"
    interval = 0.5
    BASE = "https://api.bgm.tv"
    WEB = "https://bgm.tv"

    def __init__(self, client, allow_nsfw=False, logger=None, token="", cookie=""):
        super().__init__(client, allow_nsfw=allow_nsfw, logger=logger)
        self.token = (token or "").strip()
        self.cookie = (cookie or "").strip()

    def _headers(self):
        headers = {"User-Agent": "ACGRadar/0.1 (personal comment analysis tool; local use)"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        return headers

    def _html_headers(self):
        headers = dict(BROWSER_HEADERS)
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    @staticmethod
    def _is_galgame(item):
        hints = ("galgame", "视觉小说", "美少女游戏", "eroge", "avg", "adv", "18禁")
        for tag in item.get("tags") or []:
            name = str(tag.get("name") or "").strip().casefold().replace(" ", "")
            if any(hint in name for hint in hints):
                return True
        return False

    def _subject(self, item, forced_type=None):
        type_code = item.get("type")
        if type_code not in (2, 4) and not forced_type:
            return None
        if type_code == 4 and not forced_type and not self._is_galgame(item):
            return None
        subject_type = forced_type or ("anime" if type_code == 2 else "galgame")
        name = item.get("name") or ""
        name_cn = item.get("name_cn") or ""
        titles = {}
        if name_cn:
            titles["zh"] = name_cn
        if name:
            titles["ja"] = name
        images = item.get("images") or {}
        rating = item.get("rating") or {}
        score = rating.get("score")
        if not score or float(score) <= 0:
            score = None
        tags = []
        for tag in (item.get("tags") or [])[:20]:
            tags.append({"name": tag.get("name", ""), "weight": tag.get("count", 1)})
        return Candidate(
            source=self.source,
            source_id=str(item.get("id")),
            type=subject_type,
            title=name_cn or name or str(item.get("id")),
            titles=titles,
            year=parse_year(item.get("date") or item.get("air_date")),
            synopsis=clean_text(item.get("summary", ""), 6000),
            cover_url=images.get("large") or images.get("common") or "",
            tags=[t for t in tags if t["name"]],
            rating=float(score) if score is not None else None,
            rating_scale=10,
            rating_votes=int(rating.get("total") or 0),
            url=self.WEB + "/subject/" + str(item.get("id")),
            nsfw=bool(item.get("nsfw") or False),
        )

    def _search_payload(self, query, subject_type, filters):
        filters = filters or {}
        types = [2] if subject_type == "anime" else [4] if subject_type == "galgame" else [2, 4]
        filter_obj = {"type": types}
        if not self.allow_nsfw:
            filter_obj["nsfw"] = False
        tags = [str(tag).strip() for tag in (filters.get("tags") or []) if str(tag).strip()]
        if subject_type == "galgame":
            filter_obj["tag"] = ["Galgame"] + tags[:3]
        elif tags and not query:
            filter_obj["tag"] = tags[:3]
        year_from = filters.get("year_from")
        year_to = filters.get("year_to")
        if not query and (year_from or year_to):
            dates = []
            if year_from:
                dates.append(">=" + str(int(year_from)) + "-01-01")
            if year_to:
                dates.append("<=" + str(int(year_to)) + "-12-31")
            if dates:
                filter_obj["air_date"] = dates
        min_rating = filters.get("min_rating")
        if not query and min_rating is not None:
            try:
                filter_obj["rating"] = [">=" + ("%.1f" % float(min_rating))]
            except Exception:
                pass
        return {
            "keyword": query or "",
            "sort": "heat" if not query else "match",
            "filter": filter_obj,
        }

    def search_page(self, query, subject_type="anime", limit=8, filters=None, page=1):
        limit = max(1, min(20, int(limit)))
        page = max(1, int(page or 1))
        offset = (page - 1) * limit
        payload = self._search_payload(query, subject_type, filters)
        data = self.client.post_json(
            self.BASE + "/v0/search/subjects?limit=" + str(limit) + "&offset=" + str(offset),
            payload,
            self._headers(),
        )
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = []
        total = 0
        if isinstance(data, dict):
            try:
                total = int(data.get("total") or 0)
            except Exception:
                total = 0
        result = []
        for item in items:
            candidate = self._subject(item)
            if candidate:
                result.append(candidate)
        has_more = (offset + len(items)) < total if total else len(items) >= limit
        return {"items": result, "has_more": has_more, "page": page}

    def search(self, query, subject_type="anime", limit=8, filters=None):
        return self.search_page(query, subject_type, limit, filters, 1)["items"]

    def resolve(self, value):
        text = str(value or "").strip()
        match = re.search(r"(?:bgm\.tv|bangumi\.tv)/subject/(\d+)", text)
        if not match:
            match = re.match(r"^(?:bgm|bangumi)[:\s]*(\d+)$", text, re.I)
        if match:
            return match.group(1)
        return None

    def fetch_work(self, source_id):
        data = self.client.get_json(self.BASE + "/v0/subjects/" + str(source_id), self._headers())
        candidate = self._subject(data)
        if not candidate:
            raise ValueError("Bangumi 条目类型不受支持")
        if candidate.nsfw and not self.allow_nsfw:
            raise ValueError("该条目被标记为 NSFW，已按当前设置跳过")
        return candidate

    def _comment_page(self, source_id, page):
        url = self.WEB + "/subject/" + str(source_id) + "/comments?page=" + str(page)
        return self.client.get_text(url, self._html_headers())

    def _review_list_page(self, source_id, page):
        if page <= 1:
            url = self.WEB + "/subject/" + str(source_id) + "/reviews"
        else:
            url = self.WEB + "/subject/" + str(source_id) + "/reviews/" + str(page) + ".html"
        return self.client.get_text(url, self._html_headers())

    def _parse_comments(self, source_id, page_html):
        items = []
        blocks = re.split(r'<div class="item clearit"', page_html)[1:]
        for block in blocks:
            body = re.search(r'<p class="comment">(.*?)</p>', block, re.S)
            if not body:
                continue
            text = _to_text(body.group(1))
            if not text:
                continue
            author_match = re.search(r'<a href="/user/[^"]+" class="l">(.*?)</a>', block, re.S)
            author = _to_text(author_match.group(1)) if author_match else ""
            if not author:
                user = re.search(r'data-item-user="([^"]+)"', block)
                author = user.group(1) if user else ""
            stars = re.search(r'class="starlight stars(\d+)"', block)
            score = float(stars.group(1)) if stars else None
            likes_match = re.search(r'likes_grid_(\d+)', block)
            if likes_match:
                source_review_id = "bgm-c-" + likes_match.group(1)
            else:
                source_review_id = "bgm-c-" + hashlib.sha256((author + "|" + text).encode("utf-8")).hexdigest()[:20]
            grey = re.findall(r'<small class="grey">(.*?)</small>', block, re.S)
            date_text = ""
            for value in reversed(grey):
                date_text = _to_text(value).replace("@", "").strip()
                if _parse_bgm_date(date_text):
                    break
            posted = _parse_bgm_date(date_text)
            items.append(ReviewItem(
                source=self.source,
                source_id=source_review_id,
                author=author,
                text=text,
                score=score,
                score_scale=10,
                language=guess_language(text, "zh"),
                kind="吐槽",
                spoiler=False,
                likes=0,
                url=self.WEB + "/subject/" + str(source_id) + "/comments",
                posted_at=posted,
                sentiment=sentiment_from_score(score, 10),
            ))
        return items

    def _parse_review_entries(self, source_id, page_html):
        entries = []
        blocks = re.split(r'<div class="item clearit"', page_html)[1:]
        for block in blocks:
            title_match = re.search(r'<h2 class="title"><a href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
            if not title_match:
                continue
            href = title_match.group(1)
            title = _to_text(title_match.group(2))
            content_match = re.search(r'<div class="content"><a[^>]*>(.*?)</a></div>', block, re.S)
            excerpt = _to_text(content_match.group(1)) if content_match else ""
            author_match = re.search(r'<div class="time">.*?<a href="/user/[^"]+" class="l">(.*?)</a>', block, re.S)
            author = _to_text(author_match.group(1)) if author_match else ""
            time_match = re.search(r'<div class="time">(.*?)</div>', block, re.S)
            date_text = _to_text(time_match.group(1)) if time_match else ""
            entries.append({
                "href": href,
                "title": title,
                "excerpt": excerpt,
                "author": author,
                "posted_at": _parse_bgm_date(date_text),
            })
        return entries

    def _parse_blog(self, entry):
        url = entry["href"]
        if url.startswith("/"):
            url = self.WEB + url
        blog_id_match = re.search(r"/blog/(\d+)", url)
        blog_id = blog_id_match.group(1) if blog_id_match else hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        title = entry["title"]
        author = entry["author"]
        posted = entry["posted_at"]
        content = ""
        try:
            page_html = self.client.get_text(url, self._html_headers())
            content = _extract_by_id(page_html, "entry_content")
            title_match = re.search(r'<h1 class="title">(.*?)</h1>', page_html, re.S)
            if title_match:
                title = _to_text(title_match.group(1)) or title
            author_match = re.search(r'<div class="title">\s*<p><a href="/user/[^"]+"[^>]*>(.*?)</a>', page_html, re.S)
            if author_match:
                author = _to_text(author_match.group(1)) or author
            time_match = re.search(r'<div class="time">(.*?)</div>', page_html, re.S)
            if time_match:
                posted = _parse_bgm_date(_to_text(time_match.group(1))) or posted
        except Exception as exc:
            if self.logger:
                self.logger.warning("Bangumi blog %s failed: %s", blog_id, exc)
        body = content if content else entry.get("excerpt", "")
        text = (title + "\n\n" + body).strip()
        if not text:
            return None
        return ReviewItem(
            source=self.source,
            source_id="bgm-r-" + blog_id,
            author=author,
            text=clean_text(text, 20000),
            score=None,
            score_scale=10,
            language=guess_language(text, "zh"),
            kind="评论",
            spoiler=False,
            likes=0,
            url=url,
            posted_at=posted,
            sentiment="neutral",
        )

    def iter_reviews(self, source_id, limit=120):
        try:
            limit = max(1, min(500, int(limit)))
        except Exception:
            limit = 120
        review_target = max(1, min(8, max(1, limit // 10)))
        fetched_reviews = 0
        for page in range(1, 4):
            try:
                page_html = self._review_list_page(source_id, page)
            except Exception as exc:
                if self.logger:
                    self.logger.warning("Bangumi review list %s page %s failed: %s", source_id, page, exc)
                break
            entries = self._parse_review_entries(source_id, page_html)
            if not entries:
                break
            selected = entries[:max(0, review_target - fetched_reviews)]
            if selected:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    items = list(pool.map(self._parse_blog, selected))
                page_items = [item for item in items if item]
                fetched_reviews += len(page_items)
                if page_items:
                    yield ReviewPage(page_items, total=0, next_cursor=str(page + 1))
            if fetched_reviews >= review_target or len(entries) < 10:
                break

        remaining = max(0, limit - fetched_reviews)
        if remaining <= 0:
            return
        fetched_comments = 0
        pages = min(30, max(1, (remaining + 19) // 20))
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {page: pool.submit(self._comment_page, source_id, page) for page in range(1, pages + 1)}
            for page in range(1, pages + 1):
                try:
                    page_html = futures[page].result()
                except Exception as exc:
                    if self.logger:
                        self.logger.warning("Bangumi comment page %s failed: %s", page, exc)
                    break
                comments = self._parse_comments(source_id, page_html)
                if not comments:
                    break
                slice_comments = comments[:max(0, remaining - fetched_comments)]
                if slice_comments:
                    fetched_comments += len(slice_comments)
                    yield ReviewPage(slice_comments, total=0, next_cursor=str(page + 1))
                if len(comments) < 20 or fetched_comments >= remaining:
                    break
