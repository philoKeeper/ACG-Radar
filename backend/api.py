import json
import re
import time
import urllib.parse

from .ai import AIError, AIClient, extract_json
from .config import (
    SOURCE_LABELS,
    ensure_dirs,
    get_settings,
    public_settings,
    save_settings,
)
from .crawler import candidate_from_dict
from .db import connect, db_counts, recompute_work
from .linking import merge_works
from .prompts import build_catalog_score_messages, build_chat_messages
from .retrieval import build_catalog, build_work_evidence, format_catalog, local_catalog_scores, pack_chat_evidence, plan_catalog_batches, review_full, review_list, search_chunks, work_digest
from .sources.base import Candidate
from .tokenizer import chunk_text, tokenize
from .util import clean_text, excerpt, normalize_title, now_iso, safe_float, safe_int


class APIError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


DEFAULT_PRECISION = {
    "screen_summary_chars": 80,
    "screen_batch_works": 20,
    "screen_batch_chars": 16000,
    "screen_passes": 1,
    "shortlist_works": 12,
    "refine_reviews_per_work": 5,
    "detail_works": 8,
    "detail_reviews_per_work": 10,
    "detail_review_chars": 360,
    "max_context_chars": 30000,
    "weights": {"relevance": 10, "story": 5, "characters": 5, "atmosphere": 5, "gameplay": 5, "accessibility": 5, "confidence": 3},
}


def _clamp_int(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except Exception:
        return default


def _add_usage(total, usage):
    if not isinstance(usage, dict):
        return total
    result = dict(total or {})
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        try:
            value = int(usage.get(key) or 0)
        except Exception:
            value = 0
        result[key] = int(result.get(key) or 0) + value
    return result


SCORE_DIMENSIONS = ("relevance", "story", "characters", "atmosphere", "gameplay", "accessibility", "confidence")


def _weighted_score(dimensions, weights):
    dims = dimensions or {}
    weights = weights or {}
    weighted = 0.0
    weight_sum = 0.0
    for key in ("relevance", "story", "characters", "atmosphere", "gameplay", "accessibility"):
        try:
            value = max(0.0, min(10.0, float(dims.get(key, 0) or 0)))
        except Exception:
            value = 0.0
        try:
            weight = max(0.0, min(10.0, float(weights.get(key, 5) or 0)))
        except Exception:
            weight = 5.0
        if key == "relevance":
            weight = max(weight, 1.0)
        weighted += value * weight
        weight_sum += weight
    base = (weighted / weight_sum * 10.0) if weight_sum else 0.0
    try:
        confidence = max(0.0, min(10.0, float(dims.get("confidence", 5) or 5)))
    except Exception:
        confidence = 5.0
    return max(0, min(100, round(base * (0.7 + 0.3 * confidence / 10.0))))


def normalize_precision(raw, default_context=30000):
    raw = raw if isinstance(raw, dict) else {}
    weights_raw = raw.get("weights") if isinstance(raw.get("weights"), dict) else DEFAULT_PRECISION["weights"]
    weights = {}
    for key, default in DEFAULT_PRECISION["weights"].items():
        weights[key] = _clamp_int(weights_raw.get(key), 0, 10, default)
    screen_summary = raw.get("screen_summary_chars", raw.get("catalog_summary_chars", 80))
    screen_batch = raw.get("screen_batch_chars", raw.get("catalog_batch_chars", 16000))
    detail_reviews = raw.get("detail_reviews_per_work", raw.get("reviews_per_work", 10))
    detail_chars = raw.get("detail_review_chars", raw.get("review_chars", 360))
    return {
        "screen_summary_chars": _clamp_int(screen_summary, 0, 2000, 80),
        "screen_batch_works": _clamp_int(raw.get("screen_batch_works"), 5, 40, 20),
        "screen_batch_chars": _clamp_int(screen_batch, 4000, 100000, 16000),
        "screen_passes": _clamp_int(raw.get("screen_passes"), 1, 3, 1),
        "shortlist_works": _clamp_int(raw.get("shortlist_works"), 4, 100, 12),
        "refine_reviews_per_work": _clamp_int(raw.get("refine_reviews_per_work"), 0, 20, 5),
        "detail_works": _clamp_int(raw.get("detail_works"), 0, 50, 8),
        "detail_reviews_per_work": _clamp_int(detail_reviews, 0, 100, 10),
        "detail_review_chars": _clamp_int(detail_chars, 100, 5000, 360),
        "max_context_chars": _clamp_int(raw.get("max_context_chars"), 4000, 500000, default_context),
        "weights": weights,
        "catalog_summary_chars": _clamp_int(screen_summary, 0, 2000, 80),
        "catalog_batch_chars": _clamp_int(screen_batch, 4000, 100000, 16000),
        "reviews_per_work": _clamp_int(detail_reviews, 0, 100, 10),
        "review_chars": _clamp_int(detail_chars, 100, 5000, 360),
    }


class API:
    def __init__(self, app):
        self.app = app

    def _conn(self):
        return connect(self.app.db_path)

    def _work_tags(self, conn, work_id):
        rows = conn.execute(
            "SELECT tag, weight, source FROM work_tags WHERE work_id=? ORDER BY weight DESC LIMIT 16",
            (int(work_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def _work_sources(self, conn, work_id):
        rows = conn.execute(
            "SELECT source, source_id, url, title FROM work_sources WHERE work_id=? ORDER BY source",
            (int(work_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def _work_ratings(self, conn, work_id):
        rows = conn.execute(
            "SELECT source, score, scale, vote_count, summary, url FROM ratings WHERE work_id=? ORDER BY source",
            (int(work_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def _work(self, conn, row, full=True):
        work = {
            "id": int(row["id"]),
            "type": row["type"],
            "title": row["title"],
            "year": row["year"],
            "synopsis": row["synopsis"],
            "cover_url": row["cover_url"],
            "nsfw": bool(row["nsfw"]),
            "rating_score": row["rating_score"],
            "rating_count": row["rating_count"],
            "review_count": row["review_count"],
            "source_count": row["source_count"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if full:
            work["tags"] = self._work_tags(conn, row["id"])
            work["sources"] = self._work_sources(conn, row["id"])
            work["ratings"] = self._work_ratings(conn, row["id"])
            work["titles"] = [
                dict(item) for item in conn.execute(
                    "SELECT title, kind, lang FROM work_titles WHERE work_id=? ORDER BY id",
                    (row["id"],),
                ).fetchall()
            ]
        return work

    def _get_work_or_404(self, conn, work_id):
        row = conn.execute("SELECT * FROM works WHERE id=?", (int(work_id),)).fetchone()
        if not row:
            raise APIError("NOT_FOUND", "作品不存在", 404)
        return row

    def _first_run(self, conn):
        return conn.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 0

    def _has_demo(self, conn):
        return conn.execute("SELECT COUNT(*) FROM work_sources WHERE source='demo'").fetchone()[0] > 0

    def bootstrap(self):
        conn = self._conn()
        try:
            return {
                "version": self.app.version,
                "csrf_token": self.app.csrf,
                "settings": public_settings(conn, self.app.secrets),
                "sources": [{"id": "bangumi", "label": "Bangumi"}],
                "first_run": self._first_run(conn),
                "has_demo": self._has_demo(conn),
                "counts": db_counts(conn),
            }
        finally:
            conn.close()

    def health(self):
        conn = self._conn()
        try:
            counts = db_counts(conn)
            running = conn.execute("SELECT COUNT(*) FROM crawl_jobs WHERE status IN ('queued','running','cancelling')").fetchone()[0]
            return {
                "ok": True,
                "version": self.app.version,
                "counts": counts,
                "running_jobs": running,
                "ai_configured": AIClient(self.app.settings, self.app.secrets).configured,
                "sources": ["bangumi"],
            }
        finally:
            conn.close()

    def get_settings(self):
        conn = self._conn()
        try:
            return public_settings(conn, self.app.secrets)
        finally:
            conn.close()

    def _validate_settings(self, patch):
        if not isinstance(patch, dict):
            raise APIError("VALIDATION", "设置格式错误")
        ai = patch.get("ai") or {}
        crawler = patch.get("crawler") or {}
        network = patch.get("network") or {}
        if "base_url" in ai:
            url = str(ai["base_url"] or "").strip()
            if url and not (url.startswith("http://") or url.startswith("https://")):
                raise APIError("VALIDATION", "AI Base URL 必须以 http:// 或 https:// 开头")
            ai["base_url"] = url.rstrip("/")
        if "model" in ai:
            ai["model"] = str(ai["model"] or "").strip()
        if "temperature" in ai:
            ai["temperature"] = max(0.0, min(2.0, safe_float(ai["temperature"], 0.4)))
        if "max_context_chars" in ai:
            ai["max_context_chars"] = max(4000, min(200000, safe_int(ai["max_context_chars"], 30000)))
        if "timeout" in ai:
            ai["timeout"] = max(10, min(300, safe_int(ai["timeout"], 90)))
        if "review_limit" in crawler:
            crawler["review_limit"] = max(1, min(500, safe_int(crawler["review_limit"], 120)))
        if "max_reviews" in crawler:
            crawler["max_reviews"] = max(1, min(500, safe_int(crawler["max_reviews"], 500)))
        if "request_interval" in crawler:
            crawler["request_interval"] = max(0.5, min(10.0, safe_float(crawler["request_interval"], 1.0)))
        if "proxy" in network:
            proxy = str(network["proxy"] or "").strip()
            if proxy and not (proxy.startswith("http://") or proxy.startswith("https://")):
                raise APIError("VALIDATION", "代理地址必须以 http:// 或 https:// 开头")
            network["proxy"] = proxy
        return {"ai": ai, "crawler": crawler, "network": network}

    def update_settings(self, body):
        if not isinstance(body, dict):
            raise APIError("VALIDATION", "请求体必须是 JSON 对象")
        patch = {key: body.get(key) for key in ("ai", "crawler", "network") if isinstance(body.get(key), dict)}
        patch = self._validate_settings(patch)
        conn = self._conn()
        try:
            save_settings(conn, patch)
            if body.get("clear_key"):
                self.app.secrets.clear()
            key = body.get("api_key")
            if isinstance(key, str) and key.strip():
                self.app.secrets.set_key(key.strip())
            if body.get("clear_bangumi_token") or body.get("clear_bangumi_cookie"):
                self.app.secrets.clear_bangumi(token=bool(body.get("clear_bangumi_token")), cookie=bool(body.get("clear_bangumi_cookie")))
            token = body.get("bangumi_token")
            cookie = body.get("bangumi_cookie")
            if isinstance(token, str) and token.strip():
                self.app.secrets.set_bangumi(token=token.strip())
            if isinstance(cookie, str) and cookie.strip():
                self.app.secrets.set_bangumi(cookie=cookie.strip())
            self.app.refresh()
            return public_settings(conn, self.app.secrets)
        finally:
            conn.close()

    def ai_test(self):
        client = AIClient(self.app.settings, self.app.secrets, self.app.logger)
        try:
            return client.test()
        except AIError as exc:
            raise APIError(exc.code, exc.message, 502) from exc

    def search(self, query):
        q = (query.get("q") or [""])[0].strip()
        raw_tags = (query.get("tags") or [""])[0]
        tags = [item.strip() for item in raw_tags.replace("，", ",").replace("、", ",").split(",") if item.strip()][:5]
        year_from = safe_int((query.get("year_from") or [""])[0], 0) or None
        year_to = safe_int((query.get("year_to") or [""])[0], 0) or None
        min_rating = safe_float((query.get("min_rating") or [""])[0], 0) or None
        if year_from and not (1900 <= year_from <= 2100):
            raise APIError("VALIDATION", "年份范围无效")
        if year_to and not (1900 <= year_to <= 2100):
            raise APIError("VALIDATION", "年份范围无效")
        if min_rating is not None and not (0 <= min_rating <= 10):
            raise APIError("VALIDATION", "评分范围应为 0–10")
        if not q and not tags and not year_from and not year_to and min_rating is None:
            raise APIError("VALIDATION", "请输入名称，或至少设置年份/标签/评分来浏览")
        subject_type = (query.get("type") or ["all"])[0]
        if subject_type not in ("all", "anime", "galgame"):
            subject_type = "all"
        filters = {"tags": tags, "year_from": year_from, "year_to": year_to, "min_rating": min_rating}
        page = max(1, safe_int((query.get("page") or ["1"])[0], 1))
        limit = max(1, min(50, safe_int((query.get("limit") or ["20"])[0], 20)))
        return self.app.registry.search_page(q, subject_type, limit=limit, filters=filters, page=page)

    def _candidate_from_body(self, body):
        source = str(body.get("source") or "").strip()
        candidate_data = body.get("candidate")
        raw_url = body.get("url")
        adapter = self.app.registry.get("bangumi")
        if raw_url:
            resolved_source, source_id = self.app.registry.resolve(raw_url)
            if resolved_source != "bangumi" or not source_id:
                raise APIError("BAD_URL", "无法识别这个链接，请使用 Bangumi 条目链接")
            try:
                return adapter.fetch_work(source_id), source_id
            except Exception as exc:
                raise APIError("SOURCE_ERROR", "获取作品失败：" + str(exc), 502) from exc
        if candidate_data:
            try:
                candidate = candidate_from_dict(candidate_data)
            except Exception as exc:
                raise APIError("VALIDATION", "候选作品格式错误") from exc
            if candidate.source != "bangumi":
                raise APIError("VALIDATION", "只支持 Bangumi 作品")
            if not candidate.source_id or not candidate.title:
                raise APIError("VALIDATION", "候选作品缺少 source_id/title")
            return candidate, candidate.source_id
        source_id = str(body.get("source_id") or "").strip()
        if source_id and source in ("", "bangumi"):
            try:
                return adapter.fetch_work(source_id), source_id
            except Exception as exc:
                raise APIError("SOURCE_ERROR", "获取作品失败：" + str(exc), 502) from exc
        raise APIError("VALIDATION", "缺少 Bangumi 链接或条目 ID")

    def import_work(self, body):
        candidate, _ = self._candidate_from_body(body)
        if not self.app.settings.get("crawler", {}).get("allow_nsfw") and candidate.nsfw:
            raise APIError("NSFW", "该作品被标记为 NSFW，当前设置已隐藏")
        result = self.app.crawler.import_candidate(
            candidate,
            force_new=bool(body.get("force_new")),
            link_to=body.get("link_to"),
            review_limit=body.get("review_limit"),
        )
        conn = self._conn()
        try:
            row = self._get_work_or_404(conn, result["work_id"])
            job = self.app.crawler.get_job(result["job_id"]) if result.get("job_id") else None
            return {"work": self._work(conn, row), "job": job, "mode": result["mode"]}
        finally:
            conn.close()

    def works(self, query):
        conn = self._conn()
        try:
            page = max(1, safe_int((query.get("page") or ["1"])[0], 1))
            page_size = max(1, min(50, safe_int((query.get("page_size") or ["24"])[0], 24)))
            where = []
            params = []
            q = (query.get("q") or [""])[0].strip()
            if q:
                norm = normalize_title(q)
                like = "%" + norm + "%"
                where.append("(w.title LIKE ? OR EXISTS (SELECT 1 FROM work_titles wt WHERE wt.work_id=w.id AND wt.normalized LIKE ?))")
                params.extend(["%" + q + "%", like])
            subject_type = (query.get("type") or [""])[0]
            if subject_type in ("anime", "galgame"):
                where.append("w.type=?")
                params.append(subject_type)
            tag = (query.get("tag") or [""])[0].strip()
            if tag:
                where.append("EXISTS (SELECT 1 FROM work_tags wt WHERE wt.work_id=w.id AND wt.normalized=?)")
                params.append(normalize_title(tag))
            year_from = safe_int((query.get("year_from") or [""])[0], 0)
            year_to = safe_int((query.get("year_to") or [""])[0], 0)
            if year_from:
                where.append("w.year >= ?")
                params.append(year_from)
            if year_to:
                where.append("w.year <= ?")
                params.append(year_to)
            min_rating = safe_float((query.get("min_rating") or [""])[0], 0)
            if min_rating:
                where.append("COALESCE(w.rating_score, 0) >= ?")
                params.append(min_rating)
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            sort = (query.get("sort") or ["rating"])[0]
            ordering = {
                "rating": "COALESCE(w.rating_score, 0) DESC, w.review_count DESC",
                "reviews": "w.review_count DESC, COALESCE(w.rating_score, 0) DESC",
                "newest": "w.updated_at DESC",
                "year": "w.year DESC",
                "title": "w.title COLLATE NOCASE ASC",
            }.get(sort, "COALESCE(w.rating_score, 0) DESC, w.review_count DESC")
            total = conn.execute("SELECT COUNT(*) FROM works w" + clause, tuple(params)).fetchone()[0]
            rows = conn.execute(
                "SELECT w.* FROM works w" + clause + " ORDER BY " + ordering + " LIMIT ? OFFSET ?",
                tuple(params) + (page_size, (page - 1) * page_size),
            ).fetchall()
            items = []
            for row in rows:
                item = self._work(conn, row, full=False)
                item["tags"] = [tag["tag"] for tag in self._work_tags(conn, row["id"])[:6]]
                items.append(item)
            return {"items": items, "total": total, "page": page, "page_size": page_size}
        finally:
            conn.close()

    def work_detail(self, work_id):
        conn = self._conn()
        try:
            row = self._get_work_or_404(conn, work_id)
            return {"work": self._work(conn, row), "reviews": review_list(conn, work_id, page_size=6)}
        finally:
            conn.close()

    def delete_work(self, work_id):
        conn = self._conn()
        try:
            self._get_work_or_404(conn, work_id)
            conn.execute("DELETE FROM works WHERE id=?", (int(work_id),))
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    def reviews(self, work_id, query):
        conn = self._conn()
        try:
            self._get_work_or_404(conn, work_id)
            source = (query.get("source") or [""])[0] or None
            language = (query.get("language") or [""])[0] or None
            sentiment = (query.get("sentiment") or [""])[0] or None
            spoiler = query.get("spoiler")
            spoiler_value = None
            if spoiler and spoiler[0] in ("0", "1"):
                spoiler_value = spoiler[0] == "1"
            page = max(1, safe_int((query.get("page") or ["1"])[0], 1))
            page_size = max(1, min(50, safe_int((query.get("page_size") or ["20"])[0], 20)))
            sort = (query.get("sort") or ["newest"])[0]
            kind = (query.get("kind") or [""])[0] or None
            return review_list(conn, work_id, source, language, sentiment, spoiler_value, page, page_size, sort, kind)
        finally:
            conn.close()

    def review_detail(self, review_id):
        conn = self._conn()
        try:
            item = review_full(conn, review_id)
            if not item:
                raise APIError("NOT_FOUND", "评论不存在", 404)
            return item
        finally:
            conn.close()

    def recrawl(self, work_id, body):
        conn = self._conn()
        try:
            self._get_work_or_404(conn, work_id)
            job_id = self.app.crawler.recrawl(work_id, "bangumi")
            return {"job_id": job_id}
        finally:
            conn.close()

    def merge(self, body):
        source_id = body.get("source_work_id")
        target_id = body.get("target_work_id")
        if not source_id or not target_id:
            raise APIError("VALIDATION", "缺少要合并的作品")
        conn = self._conn()
        try:
            merge_works(conn, source_id, target_id)
            row = self._get_work_or_404(conn, target_id)
            return {"work": self._work(conn, row)}
        finally:
            conn.close()

    def facets(self):
        conn = self._conn()
        try:
            tags = conn.execute(
                "SELECT tag, COUNT(DISTINCT work_id) AS count FROM work_tags GROUP BY normalized, tag ORDER BY count DESC LIMIT 30"
            ).fetchall()
            years = conn.execute("SELECT MIN(year) AS min_year, MAX(year) AS max_year FROM works WHERE year IS NOT NULL").fetchone()
            sources = conn.execute("SELECT source, COUNT(DISTINCT work_id) AS count FROM work_sources GROUP BY source ORDER BY count DESC").fetchall()
            return {
                "tags": [dict(row) for row in tags],
                "min_year": years["min_year"] if years else None,
                "max_year": years["max_year"] if years else None,
                "sources": [dict(row) for row in sources],
            }
        finally:
            conn.close()

    def jobs(self):
        return {"items": self.app.crawler.list_jobs()}

    def job(self, job_id):
        item = self.app.crawler.get_job(job_id)
        if not item:
            raise APIError("NOT_FOUND", "任务不存在", 404)
        return item

    def cancel_job(self, job_id):
        self.job(job_id)
        return {"ok": self.app.crawler.cancel(job_id)}

    def retry_job(self, job_id):
        self.job(job_id)
        return {"job_id": self.app.crawler.retry(job_id)}

    def delete_job(self, job_id):
        item = self.job(job_id)
        if item.get("status") in ("queued", "running", "cancelling"):
            raise APIError("VALIDATION", "任务仍在运行，请先取消或等待完成")
        conn = self._conn()
        try:
            conn.execute("DELETE FROM crawl_jobs WHERE id=?", (int(job_id),))
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    def chat_sessions(self):
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id=s.id) AS message_count, "
                "w.title AS work_title FROM chat_sessions s LEFT JOIN works w ON w.id=s.work_id ORDER BY s.updated_at DESC LIMIT 60"
            ).fetchall()
            return {"items": [dict(row) for row in rows]}
        finally:
            conn.close()

    def create_session(self, body):
        conn = self._conn()
        try:
            work_id = body.get("work_id") if body else None
            if work_id:
                self._get_work_or_404(conn, work_id)
            now = now_iso()
            cur = conn.execute(
                "INSERT INTO chat_sessions(title, work_id, created_at, updated_at) VALUES(?,?,?,?)",
                (str((body or {}).get("title") or "新对话")[:80], int(work_id) if work_id else None, now, now),
            )
            conn.commit()
            session_id = int(cur.lastrowid)
            return {"session": dict(conn.execute("SELECT * FROM chat_sessions WHERE id=?", (session_id,)).fetchone())}
        finally:
            conn.close()

    def get_session(self, session_id):
        conn = self._conn()
        try:
            session = conn.execute(
                "SELECT s.*, w.title AS work_title FROM chat_sessions s LEFT JOIN works w ON w.id=s.work_id WHERE s.id=?",
                (int(session_id),),
            ).fetchone()
            if not session:
                raise APIError("NOT_FOUND", "会话不存在", 404)
            messages = conn.execute(
                "SELECT id, role, content, citations_json, usage_json, created_at FROM chat_messages WHERE session_id=? ORDER BY id",
                (int(session_id),),
            ).fetchall()
            result = []
            for message in messages:
                item = dict(message)
                try:
                    item["citations"] = json.loads(item.pop("citations_json") or "[]")
                except Exception:
                    item["citations"] = []
                try:
                    item["usage"] = json.loads(item.pop("usage_json") or "{}")
                except Exception:
                    item["usage"] = {}
                result.append(item)
            return {"session": dict(session), "messages": result}
        finally:
            conn.close()

    def delete_session(self, session_id):
        conn = self._conn()
        try:
            exists = conn.execute("SELECT 1 FROM chat_sessions WHERE id=?", (int(session_id),)).fetchone()
            if not exists:
                raise APIError("NOT_FOUND", "对话不存在", 404)
            conn.execute("DELETE FROM chat_sessions WHERE id=?", (int(session_id),))
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    def _session_for_chat(self, conn, body):
        work_id = body.get("work_id")
        if work_id:
            self._get_work_or_404(conn, work_id)
            work_id = int(work_id)
        session_id = body.get("session_id")
        if session_id:
            session = conn.execute("SELECT * FROM chat_sessions WHERE id=?", (int(session_id),)).fetchone()
            if not session:
                raise APIError("NOT_FOUND", "会话不存在", 404)
            if work_id and session["work_id"] and int(session["work_id"]) != work_id:
                raise APIError("VALIDATION", "该会话已绑定其他作品，请新建对话")
            if work_id and not session["work_id"]:
                conn.execute("UPDATE chat_sessions SET work_id=?, updated_at=? WHERE id=?", (work_id, now_iso(), session_id))
                session = conn.execute("SELECT * FROM chat_sessions WHERE id=?", (int(session_id),)).fetchone()
            return dict(session)
        now = now_iso()
        title = str(body.get("message") or "新对话")[:28]
        cur = conn.execute(
            "INSERT INTO chat_sessions(title, work_id, created_at, updated_at) VALUES(?,?,?,?)",
            (title, work_id, now, now),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM chat_sessions WHERE id=?", (int(cur.lastrowid),)).fetchone())

    def _citations(self, items):
        result = []
        for item in items:
            result.append({
                "id": item["id"],
                "work_id": item["work_id"],
                "work_title": item["work_title"],
                "source": item["source"],
                "source_label": item["source_label"],
                "excerpt": excerpt(item.get("text") or item.get("excerpt"), 280),
                "url": item.get("url", ""),
                "spoiler": bool(item.get("spoiler")),
            })
        return result

    def _score_catalog(self, client, question, entries, max_context, batch_chars=16000, batch_works=20, passes=1, weights=None):
        local_conn = self._conn()
        try:
            local_scores = local_catalog_scores(local_conn, question, entries)
        finally:
            local_conn.close()
        budget = max(4000, min(int(batch_chars or 16000), int(max_context * 0.7)))
        batches = plan_catalog_batches(entries, budget_chars=budget, max_works=batch_works)
        collected = {}
        failed_batches = 0
        usage_total = {}
        try:
            passes = max(1, min(3, int(passes or 1)))
        except Exception:
            passes = 1
        for pass_index in range(passes):
            for batch in batches:
                try:
                    raw = client.chat(
                        build_catalog_score_messages(question, format_catalog(batch)),
                        max_tokens=max(600, min(4000, 300 + len(batch) * 110)),
                    )
                    usage_total = _add_usage(usage_total, client.last_usage)
                    data = extract_json(raw)
                    rows = data.get("scores") if isinstance(data, dict) else data
                    for item in rows or []:
                        try:
                            work_id = int(item.get("work_id"))
                        except Exception:
                            continue
                        dims = {}
                        for key in SCORE_DIMENSIONS:
                            try:
                                dims[key] = max(0.0, min(10.0, float(item.get(key, 0) or 0)))
                            except Exception:
                                dims[key] = 0.0
                        if not any(dims.get(key) for key in ("relevance", "story", "characters", "atmosphere", "gameplay")):
                            try:
                                dims["relevance"] = max(0.0, min(10.0, float(item.get("score", 0) or 0) / 10.0))
                            except Exception:
                                dims["relevance"] = 0.0
                        if not any(dims.values()):
                            continue
                        collected.setdefault(work_id, []).append(dims)
                except Exception as exc:
                    failed_batches += 1
                    if self.app.logger:
                        self.app.logger.warning("catalog score batch failed: %s", exc)
        dimensions = {}
        for work_id, samples in collected.items():
            averaged = {}
            for key in SCORE_DIMENSIONS:
                averaged[key] = round(sum(sample.get(key, 0.0) for sample in samples) / len(samples), 3)
            dimensions[work_id] = averaged
        scores = {}
        fallback = 0
        for entry in entries:
            wid = int(entry["id"])
            if wid in dimensions:
                scores[wid] = _weighted_score(dimensions[wid], weights)
            else:
                local = int(local_scores.get(wid, 0))
                scores[wid] = local
                dimensions[wid] = {"relevance": local / 10.0, "confidence": 0.0}
                fallback += 1
        return {
            "scores": scores,
            "dimensions": dimensions,
            "batches": len(batches) * passes,
            "passes": passes,
            "failed_batches": failed_batches,
            "fallback_works": fallback,
            "usage": usage_total,
        }

    def _rescore_shortlist(self, client, conn, question, entries, max_context, batch_chars, batch_works, reviews_per_work, weights, allow_nsfw):
        blocks = []
        for entry in entries:
            digest = work_digest(conn, entry["id"], query=question, max_reviews=max(1, reviews_per_work), allow_nsfw=allow_nsfw)
            if not digest:
                continue
            work = digest["work"]
            title = str(work.get("title") or "").replace('"', "").replace("<", "＜").replace(">", "＞")
            text = '<work id="' + str(work.get("id")) + '" title="' + title + '">\n' + digest["summary"] + "\n"
            for item in digest.get("reviews") or []:
                snippet = str(item.get("text") or "")[:600].replace("<", "＜").replace(">", "＞")
                if snippet:
                    text += "- " + snippet + "\n"
            text += "</work>"
            blocks.append(text)
        budget = max(4000, min(int(batch_chars or 16000), int(max_context * 0.6)))
        batches = []
        current = []
        total = 0
        for block in blocks:
            if current and (total + len(block) > budget or (batch_works and len(current) >= batch_works)):
                batches.append(current)
                current = []
                total = 0
            current.append(block)
            total += len(block)
        if current:
            batches.append(current)
        collected = {}
        usage_total = {}
        failed = 0
        for batch in batches:
            try:
                raw = client.chat(
                    build_catalog_score_messages(question, "\n\n".join(batch)),
                    max_tokens=max(600, min(4000, 300 + len(batch) * 110)),
                )
                usage_total = _add_usage(usage_total, client.last_usage)
                data = extract_json(raw)
                rows = data.get("scores") if isinstance(data, dict) else data
                for item in rows or []:
                    try:
                        wid = int(item.get("work_id"))
                    except Exception:
                        continue
                    dims = {}
                    for key in SCORE_DIMENSIONS:
                        try:
                            dims[key] = max(0.0, min(10.0, float(item.get(key, 0) or 0)))
                        except Exception:
                            dims[key] = 0.0
                    if not any(dims.values()):
                        continue
                    collected[wid] = dims
            except Exception as exc:
                failed += 1
                if self.app.logger:
                    self.app.logger.warning("shortlist score batch failed: %s", exc)
        scores = {wid: _weighted_score(dims, weights) for wid, dims in collected.items()}
        return {"scores": scores, "dimensions": collected, "batches": len(batches), "failed_batches": failed, "usage": usage_total, "chars": sum(len(block) for block in blocks)}

    def bangumi_check(self):
        adapter = self.app.registry.get("bangumi")
        result = {
            "api": {"ok": False, "code": "NOT_RUN", "message": "未执行"},
            "r18_api": {"ok": False, "code": "NOT_RUN", "message": "未执行", "token_configured": bool(adapter.token)},
            "r18_html": {"ok": False, "code": "NOT_RUN", "message": "未执行", "cookie_configured": bool(adapter.cookie)},
            "overall": False,
            "checked_at": now_iso(),
        }
        old_timeout = adapter.client.timeout
        old_retries = adapter.client.retries
        adapter.client.timeout = 8
        adapter.client.retries = 1
        try:
            start = time.perf_counter()
            try:
                items = adapter.search("凉宫春日", "anime", 1)
                result["api"] = {
                    "ok": True,
                    "code": "OK",
                    "message": "Bangumi API 正常，返回 " + str(len(items)) + " 条",
                    "latency_ms": round((time.perf_counter() - start) * 1000),
                }
            except Exception as exc:
                result["api"] = {"ok": False, "code": "NETWORK", "message": str(exc)[:240]}

            if not adapter.token:
                result["r18_api"] = {"ok": False, "code": "NO_TOKEN", "message": "未配置 Bangumi Access Token，无法验证 R18 API", "token_configured": False}
            else:
                start = time.perf_counter()
                try:
                    data = adapter.client.get_json(adapter.BASE + "/v0/subjects/1660", adapter._headers())
                    if bool(data.get("nsfw")):
                        result["r18_api"] = {"ok": True, "code": "OK", "message": "Token 可访问 R18 API", "subject_id": "1660", "token_configured": True, "latency_ms": round((time.perf_counter() - start) * 1000)}
                    else:
                        result["r18_api"] = {"ok": False, "code": "INCONCLUSIVE", "message": "探测条目不是 NSFW，无法确认权限", "subject_id": "1660", "token_configured": True}
                except Exception as exc:
                    status = getattr(exc, "status", None)
                    code = "NO_PERMISSION" if status in (401, 403, 404) else "NETWORK"
                    result["r18_api"] = {"ok": False, "code": code, "message": str(exc)[:240], "token_configured": True}

            if not adapter.cookie:
                result["r18_html"] = {"ok": False, "code": "NO_COOKIE", "message": "未配置 Bangumi Cookie，无法验证 R18 评论页面", "cookie_configured": False}
            else:
                start = time.perf_counter()
                try:
                    html = adapter._comment_page("1660", 1)
                    comments = adapter._parse_comments("1660", html)
                    if comments:
                        result["r18_html"] = {"ok": True, "code": "OK", "message": "Cookie 可读取 R18 评论", "comments": len(comments), "subject_id": "1660", "cookie_configured": True, "latency_ms": round((time.perf_counter() - start) * 1000)}
                    else:
                        result["r18_html"] = {"ok": False, "code": "COOKIE_EXPIRED", "message": "Cookie 已失效或页面无评论，请重新复制", "comments": 0, "subject_id": "1660", "cookie_configured": True}
                except Exception as exc:
                    result["r18_html"] = {"ok": False, "code": "NETWORK", "message": str(exc)[:240], "cookie_configured": True}
            result["overall"] = bool(result["api"]["ok"] and result["r18_api"]["ok"] and result["r18_html"]["ok"])
            return result
        finally:
            adapter.client.timeout = old_timeout
            adapter.client.retries = old_retries

    def chat_estimate(self, body):
        if not isinstance(body, dict):
            raise APIError("VALIDATION", "请求体必须是 JSON 对象")
        conn = self._conn()
        try:
            default_context = int(self.app.settings.get("ai", {}).get("max_context_chars", 30000))
            precision = normalize_precision(body.get("precision"), default_context)
            allow_nsfw = bool(self.app.settings.get("crawler", {}).get("allow_nsfw"))
            catalog = build_catalog(conn, allow_nsfw=allow_nsfw)
            entries = catalog["entries"]
            max_context = precision["max_context_chars"]
            catalog_text = format_catalog(entries, summary_chars=precision["screen_summary_chars"])
            if len(catalog_text) > max_context - 1000:
                catalog_text = format_catalog(entries, summary_chars=0)
            shortlist = sorted(entries, key=lambda item: (-(item.get("rating") or 0), -item.get("reviews", 0), item["id"]))[:precision["shortlist_works"]]
            top = shortlist[:precision["detail_works"]]
            digests = []
            for entry in top:
                if precision["detail_reviews_per_work"] <= 0:
                    break
                digest = work_digest(conn, entry["id"], max_reviews=precision["detail_reviews_per_work"], allow_nsfw=allow_nsfw)
                if digest:
                    digests.append(digest)
            detail_budget = max(1000, max_context - len(catalog_text) - 1200)
            detail_text, _, _ = pack_chat_evidence(
                digests, max_chars=detail_budget, per_review_chars=precision["detail_review_chars"], mode="global"
            )
            batches = plan_catalog_batches(entries, budget_chars=precision["screen_batch_chars"], max_works=precision["screen_batch_works"])
            stage2_chars = 0
            stage2_calls = 0
            if shortlist and precision["refine_reviews_per_work"] > 0:
                stage2_chars = len(format_catalog(shortlist)) + len(shortlist) * precision["refine_reviews_per_work"] * 260
                stage2_calls = len(plan_catalog_batches(shortlist, budget_chars=precision["screen_batch_chars"], max_works=precision["screen_batch_works"]))
            scoring_tokens = (round((len(catalog_text) + 600) / 1.7) + len(entries) * 25) * precision["screen_passes"]
            stage2_tokens = round(stage2_chars / 1.7) + len(shortlist) * 25 if stage2_calls else 0
            final_tokens = round((len(catalog_text) + len(detail_text) + 1400) / 1.7) + 1000
            return {
                "precision": precision,
                "works_total": catalog["works_total"],
                "galgame_total": catalog["galgame_total"],
                "catalog_chars": len(catalog_text),
                "detail_chars": len(detail_text),
                "stage1_calls": len(batches) * precision["screen_passes"],
                "stage2_calls": stage2_calls,
                "batches": len(batches),
                "estimated_calls": len(batches) * precision["screen_passes"] + stage2_calls + 1,
                "estimated_input_tokens": round((len(catalog_text) + len(detail_text) + 2000 + stage2_chars) / 1.7),
                "estimated_output_tokens": 1000,
                "estimated_total_tokens": scoring_tokens + stage2_tokens + final_tokens,
            }
        finally:
            conn.close()

    def chat_events(self, body):
        if not isinstance(body, dict):
            raise APIError("VALIDATION", "请求体必须是 JSON 对象")
        message = str(body.get("message") or "").strip()
        if not message:
            raise APIError("VALIDATION", "请输入问题")
        if len(message) > 4000:
            raise APIError("VALIDATION", "问题过长，请控制在 4000 字以内")
        client = AIClient(self.app.settings, self.app.secrets, self.app.logger)
        if not client.configured:
            raise APIError("AI_NOT_CONFIGURED", "请先在设置中配置自己的 AI API（Base URL、模型和 Key）。", 409)
        conn = self._conn()
        try:
            session = self._session_for_chat(conn, body)
            work_id = session.get("work_id")
            if work_id:
                work = self._get_work_or_404(conn, work_id)
                if int(work["review_count"] or 0) <= 0:
                    raise APIError("NO_EVIDENCE", "这部作品还没有评论，请先在任务页抓取评论。", 409)
            else:
                work = None
            allow_nsfw = bool(self.app.settings.get("crawler", {}).get("allow_nsfw"))
            default_context = int(self.app.settings.get("ai", {}).get("max_context_chars", 30000))
            precision = normalize_precision(body.get("precision"), default_context)
            max_context = default_context if work_id is not None else precision["max_context_chars"]
            scoring_usage = {}
            if work_id is not None:
                evidence_text, packed, coverage = build_work_evidence(
                    conn, work_id, message, max_reviews=120, max_chars=max_context, allow_nsfw=allow_nsfw
                )
                if not evidence_text or not packed:
                    raise APIError("NO_EVIDENCE", "没有检索到相关评论。请先导入作品，或换一个与作品口碑相关的问题。", 409)
            else:
                catalog = build_catalog(conn, allow_nsfw=allow_nsfw)
                entries = catalog["entries"]
                if not entries:
                    raise APIError("NO_EVIDENCE", "库里还没有作品，请先导入作品。", 409)
                score_result = self._score_catalog(
                    client, message, entries, max_context,
                    batch_chars=precision["screen_batch_chars"],
                    batch_works=precision["screen_batch_works"],
                    passes=precision["screen_passes"],
                    weights=precision["weights"],
                )
                scores = score_result["scores"]
                scoring_usage = score_result.get("usage") or {}
                stage2 = {"scores": {}, "batches": 0, "failed_batches": 0, "usage": {}}
                shortlist = sorted(entries, key=lambda item: (-scores.get(item["id"], 0), item["id"]))[:precision["shortlist_works"]]
                if shortlist and precision["refine_reviews_per_work"] > 0:
                    stage2 = self._rescore_shortlist(
                        client, conn, message, shortlist, max_context,
                        batch_chars=precision["screen_batch_chars"],
                        batch_works=precision["screen_batch_works"],
                        reviews_per_work=precision["refine_reviews_per_work"],
                        weights=precision["weights"],
                        allow_nsfw=allow_nsfw,
                    )
                    for wid, refined in stage2.get("scores", {}).items():
                        scores[wid] = max(0, min(100, round(scores.get(wid, 0) * 0.4 + refined * 0.6)))
                    scoring_usage = _add_usage(scoring_usage, stage2.get("usage"))
                catalog_text = format_catalog(entries, scores, summary_chars=precision["screen_summary_chars"])
                catalog_in_prompt = True
                if len(catalog_text) > max_context - 1500:
                    catalog_text = format_catalog(entries, scores, summary_chars=0)
                if len(catalog_text) > max_context - 1000:
                    top_for_prompt = sorted(entries, key=lambda item: (-scores.get(item["id"], 0), item["id"]))[:20]
                    catalog_text = format_catalog(top_for_prompt, scores, summary_chars=40)
                    catalog_in_prompt = False
                top_entries = sorted(entries, key=lambda item: (-scores.get(item["id"], 0), item["id"]))[:precision["detail_works"]]
                digests = []
                for entry in top_entries:
                    if precision["detail_reviews_per_work"] <= 0:
                        break
                    digest = work_digest(
                        conn, entry["id"], query=message,
                        max_reviews=precision["detail_reviews_per_work"], allow_nsfw=allow_nsfw,
                    )
                    if digest:
                        digests.append(digest)
                detail_budget = max(1000, max_context - len(catalog_text) - 1200)
                detail_text, packed, detail_coverage = pack_chat_evidence(
                    digests, max_chars=detail_budget,
                    per_review_chars=precision["detail_review_chars"], mode="global",
                )
                evidence_text = "<catalog>\n" + catalog_text + "\n</catalog>\n\n" + detail_text
                scoring_tokens = round((len(catalog_text) + 600) / 1.7) + len(entries) * 25 * precision["screen_passes"]
                stage2_tokens = round(stage2.get("chars", 0) / 1.7) if stage2.get("batches") else 0
                final_tokens = round((len(evidence_text) + 1400) / 1.7) + 1000
                coverage = {
                    "mode": "batched" if (score_result["batches"] + stage2.get("batches", 0)) > 1 else "catalog",
                    "works_total": catalog["works_total"],
                    "galgame_total": catalog["galgame_total"],
                    "catalog_works": len(entries),
                    "ai_scored_works": len(entries) - score_result["fallback_works"],
                    "detail_works": len(digests),
                    "reviews": len(packed),
                    "batches": score_result["batches"] + stage2.get("batches", 0),
                    "passes": score_result.get("passes", 1),
                    "shortlist_works": len(shortlist),
                    "refined_works": len(stage2.get("scores", {})),
                    "failed_batches": score_result["failed_batches"] + stage2.get("failed_batches", 0),
                    "fallback_works": score_result["fallback_works"],
                    "truncated_details": detail_coverage["truncated"],
                    "complete_catalog": True,
                    "catalog_in_prompt": catalog_in_prompt,
                    "precision": precision,
                    "estimated_tokens": scoring_tokens + stage2_tokens + final_tokens,
                }
            if work:
                safe_title = str(work["title"]).replace('"', "").replace("<", "＜").replace(">", "＞")
                safe_synopsis = (work["synopsis"] or "").replace("<", "＜").replace(">", "＞")[:1200]
                work_context = (
                    '<work id="' + str(work["id"]) + '" title="' + safe_title + '" type="' + str(work["type"]) + '"'
                    + (' year="' + str(work["year"]) + '"' if work["year"] else "")
                    + ' rating="' + str(work["rating_score"] or "NA") + '" reviews="' + str(work["review_count"]) + '">'
                    + "\n" + safe_synopsis + "\n</work>\n\n"
                )
                evidence_text = work_context + evidence_text
            history = []
            if session.get("id"):
                rows = conn.execute(
                    "SELECT role, content FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT 6",
                    (session["id"],),
                ).fetchall()
                history = [dict(row) for row in reversed(rows)]
            conn.execute(
                "INSERT INTO chat_messages(session_id, role, content, citations_json, created_at) VALUES(?,?,?,?,?)",
                (session["id"], "user", message, "[]", now_iso()),
            )
            conn.execute("UPDATE chat_sessions SET updated_at=? WHERE id=?", (now_iso(), session["id"]))
            conn.commit()
            user_message_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            citations = self._citations(packed)
            messages = build_chat_messages(history, evidence_text, message)
            session_id = int(session["id"])
        finally:
            conn.close()

        def generate():
            text = ""
            try:
                yield "meta", {"session_id": session_id, "user_message_id": user_message_id, "citations": citations, "coverage": coverage}
                for piece in client.stream_chat(messages, max_tokens=1600):
                    if not piece:
                        continue
                    text += piece
                    yield "delta", {"text": piece}
                used = []
                seen = set()
                for match in re.finditer(r"\[R(\d+)\]", text):
                    rid = int(match.group(1))
                    if rid in seen:
                        continue
                    seen.add(rid)
                    for item in packed:
                        if int(item["id"]) == rid:
                            used.append(item)
                            break
                usage = _add_usage(scoring_usage, client.last_usage)
                conn2 = self._conn()
                try:
                    cur = conn2.execute(
                        "INSERT INTO chat_messages(session_id, role, content, citations_json, usage_json, created_at) VALUES(?,?,?,?,?,?)",
                        (session_id, "assistant", text, _json(self._citations(used)), _json(usage), now_iso()),
                    )
                    message_id = int(cur.lastrowid)
                    conn2.execute("UPDATE chat_sessions SET updated_at=? WHERE id=?", (now_iso(), session_id))
                    conn2.commit()
                finally:
                    conn2.close()
                yield "done", {"session_id": session_id, "message_id": message_id, "char_count": len(text), "citations": self._citations(used), "usage": usage}
            except AIError as exc:
                yield "error", {"code": exc.code, "message": exc.message}
            except GeneratorExit:
                raise
            except Exception as exc:
                if self.app.logger:
                    self.app.logger.exception("chat failed")
                yield "error", {"code": "CHAT_ERROR", "message": "对话生成失败：" + str(exc)}

        return generate()

    def export_data(self):
        conn = self._conn()
        try:
            works = [dict(row) for row in conn.execute("SELECT * FROM works ORDER BY id").fetchall()]
            tables = {}
            for table in ("work_titles", "work_sources", "work_tags", "ratings", "reviews", "crawl_jobs", "chat_sessions", "chat_messages"):
                tables[table] = [dict(row) for row in conn.execute("SELECT * FROM " + table).fetchall()]
            return {
                "version": self.app.version,
                "exported_at": now_iso(),
                "works": works,
                "tables": tables,
            }
        finally:
            conn.close()
