import unittest

from base import TempApp
from backend.retrieval import build_catalog, build_global_evidence, build_work_evidence, pack_evidence, plan_catalog_batches, search_chunks
from backend.tokenizer import chunk_text, fts_query, query_tokens, tokenize
from backend.util import normalize_title


class TokenizerTests(unittest.TestCase):
    def test_cjk_bigrams(self):
        tokens = tokenize("这部番的结局非常感人")
        self.assertIn("结局", tokens)
        self.assertIn("感人", tokens)
        self.assertNotIn("的了", tokens)

    def test_query_and_fts(self):
        tokens = query_tokens("结局怎么样")
        self.assertTrue("结局" in tokens)
        query = fts_query(tokens)
        self.assertIn('"结局"', query)
        self.assertEqual(fts_query([]), "")

    def test_chunking(self):
        text = "。".join(["这是很长的评论内容"] * 200)
        chunks = chunk_text(text, max_chars=200, overlap=20, max_chunks=5)
        self.assertLessEqual(len(chunks), 5)
        self.assertTrue(all(len(chunk) <= 220 for chunk in chunks))


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.t = TempApp()
        self.work_id = self.t.seed_work(reviews=[
            "叙事诡计和 UI 结合得很好，通关后回头看标题会有新的理解。",
            "一周目节奏偏闷，第二条线开始才真正进入状态。",
            "结局很感人，音乐也很出色。",
        ])

    def tearDown(self):
        self.t.close()

    def test_search_chinese(self):
        items = search_chunks(self.t.conn, "叙事诡计", work_id=self.work_id)
        self.assertTrue(items)
        self.assertIn("叙事诡计", items[0]["text"])

    def test_pack_evidence(self):
        items = search_chunks(self.t.conn, "叙事诡计", work_id=self.work_id, limit=2)
        text, citations, total = pack_evidence(items, max_chars=5000)
        self.assertIn("id=\"R", text)
        self.assertTrue(citations)
        self.assertGreater(total, 0)
        self.assertNotIn("tester", text)

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.t = TempApp()

    def tearDown(self):
        self.t.close()

    def test_catalog_includes_all_works_and_zero_reviews(self):
        self.t.seed_work(source_id="c1", title="有评论作品", reviews=["剧情不错。"])
        from backend.linking import create_work
        from backend.sources.base import Candidate
        create_work(self.t.conn, Candidate(source="demo", source_id="c2", type="galgame", title="零评论作品"))
        self.t.conn.commit()
        catalog = build_catalog(self.t.conn, allow_nsfw=True)
        self.assertEqual(catalog["works_total"], 2)
        self.assertEqual(catalog["galgame_total"], 1)
        self.assertTrue(any(entry["reviews"] == 0 for entry in catalog["entries"]))

    def test_catalog_batches_cover_every_entry(self):
        for index in range(25):
            self.t.seed_work(source_id="b%d" % index, title="批次作品%d" % index, reviews=["测试评论。"])
        catalog = build_catalog(self.t.conn, allow_nsfw=True)
        batches = plan_catalog_batches(catalog["entries"], budget_chars=1000)
        ids = [entry["id"] for batch in batches for entry in batch]
        self.assertEqual(len(ids), len(catalog["entries"]))
        self.assertEqual(len(set(ids)), len(catalog["entries"]))
        self.assertGreater(len(batches), 1)


class ChatEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.t = TempApp()

    def tearDown(self):
        self.t.close()

    def test_global_evidence_has_summary_and_representative_reviews(self):
        for index in range(10):
            reviews = [
                "第%d部作品的剧情节奏不错，治愈感很强，适合周末看。" % index,
                "第%d部作品的音乐和画面是加分项，但结局稍显仓促。" % index,
            ] * 13
            self.t.seed_work(source_id="g%d" % index, title="作品%d" % index, tags=["治愈"], reviews=reviews)
        text, citations, coverage = build_global_evidence(
            self.t.conn, "剧情 治愈", max_works=8, per_work=15, max_chars=30000, allow_nsfw=True
        )
        self.assertEqual(coverage["mode"], "global")
        self.assertGreater(coverage["works"], 0)
        self.assertLessEqual(coverage["works"], 8)
        self.assertEqual(text.count("<work_summary"), coverage["works"])
        self.assertTrue(citations)
        counts = {}
        for item in citations:
            counts[item["work_id"]] = counts.get(item["work_id"], 0) + 1
        self.assertTrue(all(10 <= count <= 20 for count in counts.values()))
        self.assertIn("高频标签", text)
        self.assertNotIn("tester", text)

    def test_work_evidence_expands_to_120(self):
        reviews = ["第%d条评论：剧情推进自然，人物塑造很扎实。" % index for index in range(130)]
        work_id = self.t.seed_work(source_id="deep", title="深挖作品", reviews=reviews)
        text, citations, coverage = build_work_evidence(
            self.t.conn, work_id, "剧情", max_reviews=120, max_chars=30000, allow_nsfw=True
        )
        self.assertEqual(coverage["mode"], "work")
        self.assertLessEqual(len(citations), 120)
        self.assertGreaterEqual(len(citations), 100)
        self.assertIn("<work_summary", text)
        self.assertIn("130 条评论", text)

    def test_global_evidence_falls_back_when_query_does_not_match(self):
        self.t.seed_work(source_id="fallback", title="回退作品", reviews=["这是一条与查询无关的评论。"])
        text, citations, coverage = build_global_evidence(
            self.t.conn, "完全不存在的关键词组合", max_works=8, per_work=15, max_chars=30000, allow_nsfw=True
        )
        self.assertTrue(text)
        self.assertTrue(citations)
        self.assertEqual(coverage["mode"], "global")


class LinkingTests(unittest.TestCase):
    def test_title_normalization(self):
        self.assertEqual(normalize_title(" 星海 邮差！ "), normalize_title("星海邮差"))

    def test_duplicate_source_and_title_link(self):
        t = TempApp()
        try:
            first = t.seed_work(source="demo", source_id="a", title="同名作品", year=2020)
            from backend.linking import upsert_candidate
            from backend.sources.base import Candidate
            candidate = Candidate(source="other", source_id="b", type="anime", title="同名作品", year=2020)
            linked, mode = upsert_candidate(t.conn, candidate)
            t.conn.commit()
            self.assertEqual(first, linked)
            self.assertEqual(mode, "linked")
        finally:
            t.close()


if __name__ == "__main__":
    unittest.main()
