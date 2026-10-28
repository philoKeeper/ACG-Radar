import json
import threading
from concurrent.futures import ThreadPoolExecutor

from .db import connect, recompute_work
from .linking import save_work_details, upsert_candidate
from .sources.base import Candidate, ReviewItem
from .tokenizer import chunk_text, tokenize
from .util import clean_text, hash_text, now_iso, sentiment_from_score


def candidate_from_dict(data) -> Candidate:
    return Candidate(
        source=str(data.get("source", "")),
        source_id=str(data.get("source_id", "")),
        type=str(data.get("type", "anime")),
        title=str(data.get("title", "")).strip(),
        titles=data.get("titles") or {},
        year=data.get("year"),
        synopsis=clean_text(data.get("synopsis", ""), 6000),
        cover_url=data.get("cover_url", ""),
        tags=data.get("tags") or [],
        rating=data.get("rating"),
        rating_scale=data.get("rating_scale", 10),
        rating_votes=data.get("rating_votes", 0),
        url=data.get("url", ""),
        nsfw=bool(data.get("nsfw", False)),
    )


class Crawler:
    def __init__(self, db_path, settings, registry, logger=None):
        self.db_path = db_path
        self.settings = settings
        self.registry = registry
        self.logger = logger
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="crawl")
        self.lock = threading.Lock()

    def _conn(self):
        return connect(self.db_path)

    def create_job(self, conn, work_id, source, review_limit=0):
        now = now_iso()
        try:
            limit = max(0, min(500, int(review_limit or 0)))
        except Exception:
            limit = 0
        cur = conn.execute(
            "INSERT INTO crawl_jobs(work_id, source, status, progress_json, review_limit, created_at) VALUES(?,?,?,?,?,?)",
            (int(work_id), source, "queued", "{}", limit, now),
        )
        conn.commit()
        return int(cur.lastrowid)

    def import_candidate(self, candidate, force_new=False, link_to=None, review_limit=None):
        conn = self._conn()
        try:
            work_id, mode = upsert_candidate(conn, candidate, force_new=force_new, link_to=link_to)
            conn.commit()
            adapter = self.registry.get(candidate.source)
            job_id = None
            if adapter:
                job_id = self.create_job(conn, work_id, candidate.source, review_limit=review_limit)
                self.pool.submit(self._run_job, job_id)
            recompute_work(conn, work_id)
            conn.commit()
            return {"work_id": work_id, "mode": mode, "job_id": job_id}
        finally:
            conn.close()

    def recrawl(self, work_id, source, review_limit=0):
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM work_sources WHERE work_id=? AND source=?", (int(work_id), source)).fetchone()
            if not row:
                raise ValueError("该作品没有此来源")
            job_id = self.create_job(conn, work_id, source, review_limit=review_limit)
            self.pool.submit(self._run_job, job_id)
            return job_id
        finally:
            conn.close()

    def cancel(self, job_id):
        conn = self._conn()
        try:
            conn.execute("UPDATE crawl_jobs SET cancel_requested=1, status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'cancelling' END WHERE id=? AND status IN ('queued','running')", (int(job_id),))
            conn.commit()
            return True
        finally:
            conn.close()

    def retry(self, job_id):
        conn = self._conn()
        try:
            old = conn.execute("SELECT * FROM crawl_jobs WHERE id=?", (int(job_id),)).fetchone()
            if not old:
                raise ValueError("任务不存在")
            try:
                old_limit = int(old["review_limit"] or 0)
            except Exception:
                old_limit = 0
            new_id = self.create_job(conn, old["work_id"], old["source"], review_limit=old_limit)
            self.pool.submit(self._run_job, new_id)
            return new_id
        finally:
            conn.close()

    def _job_update(self, conn, job_id, **fields):
        if not fields:
            return
        cols = ", ".join(key + "=?" for key in fields)
        conn.execute("UPDATE crawl_jobs SET " + cols + " WHERE id=?", tuple(fields.values()) + (int(job_id),))
        conn.commit()

    def _run_job(self, job_id):
        conn = self._conn()
        job = conn.execute("SELECT * FROM crawl_jobs WHERE id=?", (int(job_id),)).fetchone()
        if not job:
            conn.close()
            return
        if job["cancel_requested"]:
            self._job_update(conn, job_id, status="cancelled", finished_at=now_iso(), message="已取消")
            conn.close()
            return
        self._job_update(conn, job_id, status="running", started_at=now_iso(), message="准备抓取")
        try:
            adapter = self.registry.get(job["source"])
            if not adapter:
                raise ValueError("未知来源：" + str(job["source"]))
            link = conn.execute(
                "SELECT * FROM work_sources WHERE work_id=? AND source=?",
                (job["work_id"], job["source"]),
            ).fetchone()
            if not link:
                raise ValueError("缺少来源映射")
            source_id = link["source_id"]
            self._job_update(conn, job_id, message="获取作品详情")
            candidate = adapter.fetch_work(source_id)
            if candidate:
                save_work_details(conn, job["work_id"], candidate)
                conn.commit()
            try:
                job_limit = int(job["review_limit"] or 0)
            except Exception:
                job_limit = 0
            review_limit = job_limit or int(self.settings.get("crawler", {}).get("review_limit", 120))
            review_limit = max(1, min(500, review_limit))
            progress = {"pages": 0, "fetched": 0, "inserted": 0, "updated": 0}
            total = 0
            if adapter.supports_reviews:
                for page in adapter.iter_reviews(source_id, review_limit):
                    fresh = conn.execute("SELECT cancel_requested FROM crawl_jobs WHERE id=?", (int(job_id),)).fetchone()
                    if fresh and fresh["cancel_requested"]:
                        self._job_update(conn, job_id, status="cancelled", finished_at=now_iso(), message="已取消", progress_json=json.dumps(progress))
                        conn.close()
                        return
                    inserted, updated = self.upsert_reviews(conn, job["work_id"], page.items)
                    progress["pages"] += 1
                    progress["fetched"] += len(page.items)
                    progress["inserted"] += inserted
                    progress["updated"] += updated
                    total = max(total, int(page.total or 0))
                    self._job_update(
                        conn,
                        job_id,
                        progress_json=json.dumps(progress, ensure_ascii=False),
                        total=total,
                        message="已抓取 " + str(progress["fetched"]) + " 条评论",
                    )
                    if progress["fetched"] >= review_limit:
                        break
            else:
                self._job_update(conn, job_id, message="该来源仅提供元数据/评分")
            rating = adapter.fetch_rating(source_id) if hasattr(adapter, "fetch_rating") else None
            if rating:
                conn.execute(
                    "INSERT INTO ratings(work_id, source, score, scale, vote_count, summary, url) VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(work_id, source) DO UPDATE SET score=excluded.score, scale=excluded.scale, vote_count=excluded.vote_count, summary=excluded.summary, url=excluded.url",
                    (job["work_id"], job["source"], rating.get("score"), rating.get("scale", 10), rating.get("vote_count", 0), rating.get("summary", ""), rating.get("url", "")),
                )
            recompute_work(conn, job["work_id"])
            conn.commit()
            self._job_update(
                conn,
                job_id,
                status="success",
                finished_at=now_iso(),
                message="完成：抓取 " + str(progress["fetched"]) + " 条，新增 " + str(progress["inserted"]) + " 条",
                progress_json=json.dumps(progress, ensure_ascii=False),
                total=total,
            )
        except Exception as exc:
            if self.logger:
                self.logger.exception("crawl job %s failed", job_id)
            try:
                self._job_update(conn, job_id, status="failed", finished_at=now_iso(), error=str(exc)[:500], message="抓取失败")
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def upsert_reviews(self, conn, work_id, items):
        inserted = 0
        updated = 0
        for item in items:
            text = clean_text(item.text, 20000)
            if not text:
                continue
            source_id = str(item.source_id or "").strip()
            if not source_id:
                source_id = "hash-" + hash_text(item.source + "|" + item.author + "|" + item.posted_at + "|" + text)[:24]
            existing = conn.execute(
                "SELECT id FROM reviews WHERE source=? AND source_id=?",
                (item.source, source_id),
            ).fetchone()
            sentiment = item.sentiment or sentiment_from_score(item.score, item.score_scale)
            conn.execute(
                "INSERT INTO reviews(work_id, source, source_id, author, score, score_scale, sentiment, kind, text, language, spoiler, likes, url, posted_at, fetched_at, content_hash) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(source, source_id) DO UPDATE SET work_id=excluded.work_id, author=excluded.author, score=excluded.score, "
                "score_scale=excluded.score_scale, sentiment=excluded.sentiment, kind=excluded.kind, text=excluded.text, language=excluded.language, "
                "spoiler=excluded.spoiler, likes=excluded.likes, url=excluded.url, posted_at=excluded.posted_at, fetched_at=excluded.fetched_at, content_hash=excluded.content_hash",
                (work_id, item.source, source_id, item.author[:120], item.score, item.score_scale or 10, sentiment, item.kind or "", text, item.language or "", 1 if item.spoiler else 0, int(item.likes or 0), item.url, item.posted_at, now_iso(), hash_text(text)),
            )
            row = conn.execute("SELECT id FROM reviews WHERE source=? AND source_id=?", (item.source, source_id)).fetchone()
            if not row:
                continue
            review_id = int(row["id"])
            if existing:
                updated += 1
            else:
                inserted += 1
            conn.execute("DELETE FROM review_chunks WHERE review_id=?", (review_id,))
            for index, chunk in enumerate(chunk_text(text)):
                tokens = " ".join(tokenize(chunk))
                if not tokens:
                    continue
                conn.execute(
                    "INSERT INTO review_chunks(review_id, work_id, ord, text, tokens) VALUES(?,?,?,?,?)",
                    (review_id, work_id, index, chunk, tokens),
                )
        conn.commit()
        return inserted, updated

    def list_jobs(self, limit=50):
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT j.*, w.title AS work_title FROM crawl_jobs j LEFT JOIN works w ON w.id=j.work_id ORDER BY j.id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_job(self, job_id):
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT j.*, w.title AS work_title FROM crawl_jobs j LEFT JOIN works w ON w.id=j.work_id WHERE j.id=?",
                (int(job_id),),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
