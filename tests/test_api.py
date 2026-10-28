import json
import unittest

from base import TempApp
from mock_ai import start_mock
from backend.api import API, APIError


class APITests(unittest.TestCase):
    def setUp(self):
        self.t = TempApp()
        self.api = API(self.t.app)
        self.work_id = self.t.seed_work(title="API 测试作品", reviews=["画面很好，剧情节奏偏慢。", "结局很感人，但中间有点拖。", "配乐是加分项。"])
        self.app = self.t.app

    def tearDown(self):
        self.t.close()

    def test_bootstrap_and_works(self):
        boot = self.api.bootstrap()
        self.assertIn("csrf_token", boot)
        self.assertFalse(boot["first_run"])
        data = self.api.works({"page": ["1"]})
        self.assertGreaterEqual(data["total"], 1)

    def test_settings_do_not_leak_key(self):
        public = self.api.update_settings({"ai": {"base_url": "http://127.0.0.1:9999/v1", "model": "mock"}, "api_key": "sk-secret-123456"})
        self.assertNotIn("api_key", public["ai"])
        self.assertTrue(public["ai"]["key_configured"])
        self.assertNotIn("sk-secret", json.dumps(public, ensure_ascii=False))

    def test_clear_credentials(self):
        self.api.update_settings({
            "api_key": "sk-clear-test-123456",
            "bangumi_token": "token-clear-test",
            "bangumi_cookie": "chii_auth=cookie-clear-test",
        })
        public = self.api.update_settings({
            "clear_key": True,
            "clear_bangumi_token": True,
            "clear_bangumi_cookie": True,
        })
        self.assertFalse(public["ai"]["key_configured"])
        self.assertFalse(public["bangumi"]["token_configured"])
        self.assertFalse(public["bangumi"]["cookie_configured"])
        self.assertNotIn("cookie-clear-test", json.dumps(public, ensure_ascii=False))

    def test_chat_without_ai_returns_error(self):
        with self.assertRaises(APIError) as ctx:
            self.api.chat_events({"work_id": self.work_id, "message": "口碑如何？"})
        self.assertEqual(ctx.exception.code, "AI_NOT_CONFIGURED")

    def test_chat_stream_and_persist(self):
        server, base_url = start_mock()
        try:
            self.api.update_settings({"ai": {"base_url": base_url, "model": "mock", "temperature": 0.2}})
            events = self.api.chat_events({"work_id": self.work_id, "message": "口碑如何？"})
            names = []
            text = ""
            meta = None
            done = None
            for name, data in events:
                names.append(name)
                if name == "meta":
                    meta = data
                elif name == "delta":
                    text += data.get("text", "")
                elif name == "done":
                    done = data
            self.assertIn("meta", names)
            self.assertIn("delta", names)
            self.assertIn("done", names)
            self.assertTrue(text)
            self.assertTrue(meta["citations"])
            self.assertEqual(meta["coverage"]["mode"], "work")
            self.assertGreater(done["usage"]["total_tokens"], 0)
            self.assertTrue(done["message_id"])
            session = self.api.get_session(meta["session_id"])
            self.assertEqual(len(session["messages"]), 2)
            self.assertGreater(session["messages"][-1]["usage"]["total_tokens"], 0)
        finally:
            server.shutdown()
            server.server_close()

    def test_global_chat_scores_full_catalog(self):
        self.t.seed_work(source_id="g1", title="治愈作品一", tags=["治愈"], reviews=["剧情治愈，适合推荐。"] * 15)
        self.t.seed_work(source_id="g2", title="悬疑作品二", tags=["悬疑"], reviews=["剧情反转很多，节奏紧凑。"] * 15)
        server, base_url = start_mock()
        try:
            self.api.update_settings({"ai": {"base_url": base_url, "model": "mock", "max_context_chars": 30000}})
            meta = None
            for name, data in self.api.chat_events({"message": "推荐一部治愈的作品"}):
                if name == "meta":
                    meta = data
            self.assertIsNotNone(meta)
            coverage = meta["coverage"]
            self.assertIn(coverage["mode"], ("catalog", "batched"))
            self.assertTrue(coverage["complete_catalog"])
            self.assertEqual(coverage["catalog_works"], coverage["works_total"])
            self.assertEqual(coverage["ai_scored_works"], coverage["works_total"])
            self.assertGreaterEqual(coverage["detail_works"], 1)
        finally:
            server.shutdown()
            server.server_close()

    def test_chat_estimate_with_precision(self):
        result = self.api.chat_estimate({"precision": {
            "catalog_summary_chars": 0,
            "detail_works": 0,
            "reviews_per_work": 0,
            "review_chars": 360,
            "max_context_chars": 16000,
            "catalog_batch_chars": 8000,
        }})
        self.assertGreater(result["works_total"], 0)
        self.assertGreater(result["catalog_chars"], 0)
        self.assertGreaterEqual(result["estimated_calls"], 1)
        self.assertGreater(result["estimated_total_tokens"], 0)
        self.assertEqual(result["precision"]["detail_works"], 0)

    def test_global_chat_zero_detail_keeps_catalog(self):
        server, base_url = start_mock()
        try:
            self.api.update_settings({"ai": {"base_url": base_url, "model": "mock", "max_context_chars": 30000}})
            meta = None
            for name, data in self.api.chat_events({
                "message": "随便推荐一部",
                "precision": {"catalog_summary_chars": 0, "detail_works": 0, "reviews_per_work": 0},
            }):
                if name == "meta":
                    meta = data
            coverage = meta["coverage"]
            self.assertEqual(coverage["catalog_works"], coverage["works_total"])
            self.assertEqual(coverage["detail_works"], 0)
            self.assertTrue(coverage["complete_catalog"])
        finally:
            server.shutdown()
            server.server_close()

    def test_bangumi_check_statuses(self):
        class FakeClient:
            timeout = 30
            retries = 3
            def get_json(self, url, headers=None):
                return {"nsfw": True}
        class FakeAdapter:
            token = "test-token"
            cookie = "test-cookie"
            BASE = "https://api.bgm.tv"
            client = FakeClient()
            def search(self, query, subject_type="anime", limit=1):
                return [1]
            def _headers(self):
                return {}
            def _comment_page(self, source_id, page):
                return "<html></html>"
            def _parse_comments(self, source_id, html):
                return ["comment"]
        fake = FakeAdapter()
        self.app.registry.get = lambda source: fake
        result = self.api.bangumi_check()
        self.assertTrue(result["api"]["ok"])
        self.assertTrue(result["r18_api"]["ok"])
        self.assertTrue(result["r18_html"]["ok"])
        self.assertTrue(result["overall"])
        self.assertNotIn("test-cookie", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("test-token", json.dumps(result, ensure_ascii=False))

    def test_bangumi_check_without_credentials(self):
        class FakeClient:
            timeout = 30
            retries = 3
            def get_json(self, url, headers=None):
                raise AssertionError("should not request without token")
        class FakeAdapter:
            token = ""
            cookie = ""
            BASE = "https://api.bgm.tv"
            client = FakeClient()
            def search(self, query, subject_type="anime", limit=1):
                return [1]
            def _comment_page(self, source_id, page):
                raise AssertionError("should not request without cookie")
        fake = FakeAdapter()
        self.app.registry.get = lambda source: fake
        result = self.api.bangumi_check()
        self.assertEqual(result["r18_api"]["code"], "NO_TOKEN")
        self.assertEqual(result["r18_html"]["code"], "NO_COOKIE")

    def test_precision_clamps_wide_ranges(self):
        result = self.api.chat_estimate({"precision": {
            "catalog_summary_chars": 9999,
            "detail_works": 999,
            "reviews_per_work": 999,
            "review_chars": 9999,
            "max_context_chars": 999999,
            "catalog_batch_chars": 999999,
        }})
        precision = result["precision"]
        self.assertEqual(precision["screen_summary_chars"], 2000)
        self.assertEqual(precision["detail_works"], 50)
        self.assertEqual(precision["detail_reviews_per_work"], 100)
        self.assertEqual(precision["detail_review_chars"], 5000)
        self.assertEqual(precision["max_context_chars"], 500000)
        self.assertEqual(precision["screen_batch_chars"], 100000)

    def test_search_accepts_filters_without_query(self):
        class DummyRegistry:
            adapters = {"bangumi": object()}

            def __init__(self):
                self.kwargs = None

            def search_page(self, query, subject_type="all", limit=20, filters=None, page=1):
                self.kwargs = {"query": query, "type": subject_type, "filters": filters, "page": page, "limit": limit}
                return {"results": [], "errors": [], "cached": False, "page": page, "has_more": True}

        dummy = DummyRegistry()
        self.app.registry = dummy
        result = self.api.search({"q": [""], "type": ["galgame"], "tags": ["悬疑,科幻"], "year_from": ["2000"], "year_to": ["2005"]})
        self.assertEqual(result["results"], [])
        self.assertEqual(dummy.kwargs["filters"]["tags"], ["悬疑", "科幻"])
        self.assertEqual(dummy.kwargs["filters"]["year_from"], 2000)
        self.assertEqual(dummy.kwargs["page"], 1)
        self.assertEqual(dummy.kwargs["limit"], 20)
        self.assertTrue(result["has_more"])
        with self.assertRaises(APIError):
            self.api.search({"q": [""]})

    def test_delete_chat_session_cascades_messages(self):
        session = self.api.create_session({"title": "待删除"})["session"]
        self.t.conn.execute(
            "INSERT INTO chat_messages(session_id, role, content, citations_json, created_at) VALUES(?,?,?,?,?)",
            (session["id"], "user", "hello", "[]", "2024-01-01T00:00:00+00:00"),
        )
        self.t.conn.commit()
        self.api.delete_session(session["id"])
        count = self.t.conn.execute("SELECT COUNT(*) FROM chat_messages WHERE session_id=?", (session["id"],)).fetchone()[0]
        self.assertEqual(count, 0)

    def test_delete_job_record(self):
        job_id = self.app.crawler.create_job(self.t.conn, self.work_id, "demo")
        self.t.conn.execute("UPDATE crawl_jobs SET status='success' WHERE id=?", (job_id,))
        self.t.conn.commit()
        self.api.delete_job(job_id)
        with self.assertRaises(APIError):
            self.api.job(job_id)

    def test_merge(self):
        other = self.t.seed_work(source="demo", source_id="other", title="另一个", year=2023)
        before = self.api.works({"page": ["1"]})["total"]
        self.api.merge({"source_work_id": other, "target_work_id": self.work_id})
        self.assertEqual(self.api.works({"page": ["1"]})["total"], before - 1)


if __name__ == "__main__":
    unittest.main()
