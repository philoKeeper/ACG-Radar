import json
import math
import shutil
import sqlite3
from pathlib import Path

from .config import BASE_DIR, SOURCE_WEIGHTS, ensure_dirs, now_iso


def connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=8, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


def init_db(path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    conn = connect(path)
    old_version = 0
    try:
        old_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    except Exception:
        old_version = 0
    if old_version and old_version < 4:
        backup = path.parent / "backups" / ("app-before-v4-" + now_iso().replace(":", "-") + ".db")
        try:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
        except Exception:
            pass
    conn.executescript(schema)
    review_columns = [row[1] for row in conn.execute("PRAGMA table_info(reviews)").fetchall()]
    if "kind" not in review_columns:
        conn.execute("ALTER TABLE reviews ADD COLUMN kind TEXT DEFAULT ''")
    job_columns = [row[1] for row in conn.execute("PRAGMA table_info(crawl_jobs)").fetchall()]
    if "review_limit" not in job_columns:
        conn.execute("ALTER TABLE crawl_jobs ADD COLUMN review_limit INTEGER DEFAULT 0")
    message_columns = [row[1] for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()]
    if "usage_json" not in message_columns:
        conn.execute("ALTER TABLE chat_messages ADD COLUMN usage_json TEXT DEFAULT '{}'")
    conn.execute("PRAGMA user_version=4")
    conn.commit()
    return conn


def recompute_work(conn, work_id):
    work_id = int(work_id)
    review_count = conn.execute("SELECT COUNT(*) FROM reviews WHERE work_id=?", (work_id,)).fetchone()[0]
    source_count = conn.execute("SELECT COUNT(DISTINCT source) FROM work_sources WHERE work_id=?", (work_id,)).fetchone()[0]
    rows = conn.execute("SELECT source, score, scale, vote_count FROM ratings WHERE work_id=?", (work_id,)).fetchall()
    prior = 6.5
    prior_weight = 0.3
    weighted = 0.0
    weights = 0.0
    total_votes = 0
    for row in rows:
        source = row["source"]
        if source not in SOURCE_WEIGHTS:
            continue
        score = row["score"]
        scale = row["scale"] or 10
        if score is None or not scale:
            continue
        normalized = float(score) / float(scale) * 10
        votes = max(0, int(row["vote_count"] or 0))
        weight = SOURCE_WEIGHTS[source] * min(1.0, math.log10(votes + 1) / 3.0)
        if weight <= 0:
            continue
        weighted += normalized * weight
        weights += weight
        total_votes += votes
    rating = (prior * prior_weight + weighted) / (prior_weight + weights) if weights > 0 else None
    conn.execute(
        "UPDATE works SET rating_score=?, rating_count=?, review_count=?, source_count=?, updated_at=? WHERE id=?",
        (round(rating, 3) if rating is not None else None, total_votes, review_count, source_count, now_iso(), work_id),
    )


def rebuild_fts(conn):
    conn.execute("INSERT INTO review_fts(review_fts) VALUES('rebuild')")
    work_ids = [row[0] for row in conn.execute("SELECT id FROM works").fetchall()]
    for work_id in work_ids:
        recompute_work(conn, work_id)
    conn.commit()


def recover_jobs(conn):
    conn.execute(
        "UPDATE crawl_jobs SET status='interrupted', message='服务重启，任务已中断，可重试', finished_at=? "
        "WHERE status IN ('queued','running','cancelling')",
        (now_iso(),),
    )
    conn.commit()


def db_counts(conn):
    def count(table):
        try:
            return conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
        except Exception:
            return 0
    return {
        "works": count("works"),
        "reviews": count("reviews"),
        "jobs": count("crawl_jobs"),
        "chunks": count("review_chunks"),
    }


def get_work(conn, work_id):
    return conn.execute("SELECT * FROM works WHERE id=?", (int(work_id),)).fetchone()
