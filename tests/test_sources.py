import unittest

from backend.sources.bangumi import BangumiAdapter


class FakeClient:
    def __init__(self, get_payload=None, post_payload=None):
        self.get_payload = get_payload or {}
        self.post_payload = post_payload or {}

    def get_json(self, url, headers=None):
        for key, value in self.get_payload.items():
            if key in url:
                return value
        raise AssertionError("unexpected GET " + url)

    def post_json(self, url, payload, headers=None):
        return self.post_payload

    def get_text(self, url, headers=None):
        for key, value in self.get_payload.items():
            if key in url:
                return value
        raise AssertionError("unexpected GET text " + url)


class SourceTests(unittest.TestCase):
    def test_nsfw_filter_and_token_header(self):
        adapter = BangumiAdapter(FakeClient(), allow_nsfw=True, token="abc")
        payload = adapter._search_payload("", "all", {})
        self.assertNotIn("nsfw", payload["filter"])
        self.assertEqual(adapter._headers()["Authorization"], "Bearer abc")
        blocked = BangumiAdapter(FakeClient(), allow_nsfw=False)
        self.assertIs(blocked._search_payload("", "all", {})["filter"]["nsfw"], False)
        cookie_adapter = BangumiAdapter(FakeClient(), cookie="chii_auth=test; chii_sid=test")
        self.assertEqual(cookie_adapter._html_headers()["Cookie"], "chii_auth=test; chii_sid=test")

    def test_bangumi_search_and_comments(self):
        search = {"data": [{
            "id": 253, "type": 2, "name": "涼宮ハルヒ", "name_cn": "凉宫春日的忧郁",
            "date": "2006-04-02", "summary": "简介", "images": {"large": "https://lain.bgm.tv/a.jpg"},
            "rating": {"score": 8.5, "total": 1000}, "tags": [{"name": "科幻", "count": 20}], "nsfw": False,
        }]}
        comments_html = """
        <div class="item clearit" data-item-user="u1">
        <a href="/user/u1" class="l">alice</a>
        <span class="starstop-s"><span class="starlight stars9"></span></span>
        <small class="grey"> 看过 </small><small class="grey">@ 2h ago</small>
        <p class="comment">很好看</p><div class="likes_grid" id="likes_grid_123"></div>
        </div>
        """
        reviews_html = """
        <div class="item clearit" data-item-user="u2">
        <h2 class="title"><a href="/blog/999" class="l">长评标题</a></h2>
        <div class="content"><a href="/blog/999">摘要内容</a></div>
        <div class="time"><a href="/user/u2" class="l">bob</a> · 2024-1-2 03:04</div>
        </div>
        """
        blog_html = """
        <div class="author user-card"><a href="/user/u2" class="l">bob</a></div>
        <div class="header"><h1 class="title">长评标题</h1><div class="time">2024-1-2 03:04</div></div>
        <div id="entry_content" class="content">正文<br>更多</div>
        """
        client = FakeClient({"comments": comments_html, "reviews": reviews_html, "blog/999": blog_html}, post_payload=search)
        adapter = BangumiAdapter(client)
        items = adapter.search("凉宫", "anime", 5)
        self.assertEqual(items[0].source_id, "253")
        self.assertEqual(items[0].rating, 8.5)
        pages = list(adapter.iter_reviews("253", 4))
        all_items = [item for page in pages for item in page.items]
        kinds = {item.kind for item in all_items}
        self.assertEqual(kinds, {"吐槽", "评论"})
        review = [item for item in all_items if item.kind == "评论"][0]
        self.assertEqual(review.source_id, "bgm-r-999")
        self.assertIn("正文", review.text)
        comment = [item for item in all_items if item.kind == "吐槽"][0]
        self.assertEqual(comment.source_id, "bgm-c-123")
        self.assertEqual(comment.score, 9.0)


if __name__ == "__main__":
    unittest.main()
