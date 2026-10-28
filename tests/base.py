import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.context import AppContext
from backend.crawler import Crawler
from backend.db import connect
from backend.linking import create_work
from backend.sources.base import Candidate, ReviewItem


class TempApp:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = AppContext(Path(self.tmp.name))
        self.conn = connect(self.app.db_path)

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
        try:
            self.app.crawler.pool.shutdown(wait=False)
        except Exception:
            pass
        for handler in list(self.app.logger.handlers):
            try:
                handler.close()
            except Exception:
                pass
        self.tmp.cleanup()

    def seed_work(self, source="demo", source_id="w1", title="测试作品", year=2022, tags=None, reviews=None, rating=8.0):
        candidate = Candidate(
            source=source,
            source_id=source_id,
            type="anime",
            title=title,
            titles={"zh": title},
            year=year,
            synopsis="测试用简介",
            cover_url="",
            tags=[{"name": tag, "weight": 1} for tag in (tags or ["治愈", "奇幻"])],
            rating=rating,
            rating_scale=10,
            rating_votes=100,
            url="",
        )
        work_id = create_work(self.conn, candidate)
        crawler = Crawler(self.app.db_path, self.app.settings, self.app.registry, self.app.logger)
        items = []
        for index, text in enumerate(reviews or ["这是一条测试评论，整体观感不错。", "节奏有点慢，但结局很好。"]):
            items.append(ReviewItem(
                source=source,
                source_id=source_id + "-r" + str(index),
                author="tester",
                text=text,
                score=8,
                score_scale=10,
                language="zh",
                posted_at="2024-01-01T00:00:00+00:00",
                sentiment="positive",
            ))
        crawler.upsert_reviews(self.conn, work_id, items)
        from backend.db import recompute_work
        recompute_work(self.conn, work_id)
        self.conn.commit()
        return work_id
