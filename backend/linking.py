from .config import now_iso
from .util import normalize_title


def _variants(candidate):
    values = []
    if candidate.title:
        values.append((candidate.title, "main", ""))
    for key, value in (candidate.titles or {}).items():
        if value:
            values.append((value, "alt", key))
    seen = set()
    result = []
    for title, kind, lang in values:
        norm = normalize_title(title)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        result.append((title.strip(), norm, kind, lang))
    return result


def find_existing(conn, candidate, allow_year_gap=1):
    existing = conn.execute(
        "SELECT w.id FROM work_sources ws JOIN works w ON w.id=ws.work_id WHERE ws.source=? AND ws.source_id=?",
        (candidate.source, str(candidate.source_id)),
    ).fetchone()
    if existing:
        return int(existing["id"])
    variants = _variants(candidate)
    if not variants:
        return None
    norms = [v[1] for v in variants]
    placeholders = ",".join("?" for _ in norms)
    rows = conn.execute(
        "SELECT DISTINCT w.id, w.year, w.review_count, w.title FROM work_titles wt JOIN works w ON w.id=wt.work_id "
        "WHERE wt.normalized IN (" + placeholders + ") AND w.type=?",
        tuple(norms) + (candidate.type,),
    ).fetchall()
    matches = []
    for row in rows:
        if candidate.year and row["year"]:
            if abs(int(row["year"]) - int(candidate.year)) > allow_year_gap:
                continue
        matches.append(row)
    if len(matches) == 1:
        return int(matches[0]["id"])
    if len(matches) > 1:
        # 只在所有命中的作品同属一个标题且评论数明显领先时才自动合并，否则不猜。
        matches.sort(key=lambda row: (row["review_count"] or 0), reverse=True)
        if len(matches) == 1:
            return int(matches[0]["id"])
    return None


def create_work(conn, candidate):
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO works(type, title, year, synopsis, cover_url, nsfw, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (candidate.type, candidate.title, candidate.year, candidate.synopsis, candidate.cover_url, 1 if candidate.nsfw else 0, now, now),
    )
    work_id = int(cur.lastrowid)
    save_work_details(conn, work_id, candidate)
    return work_id


def save_work_details(conn, work_id, candidate):
    work = conn.execute("SELECT title, year, synopsis, cover_url, nsfw FROM works WHERE id=?", (work_id,)).fetchone()
    if not work:
        return
    title = work["title"] or candidate.title
    year = work["year"] if work["year"] is not None else candidate.year
    synopsis = work["synopsis"] or candidate.synopsis
    cover = work["cover_url"] or candidate.cover_url
    conn.execute(
        "UPDATE works SET title=?, year=?, synopsis=?, cover_url=?, nsfw=?, updated_at=? WHERE id=?",
        (title, year, synopsis, cover, 1 if (candidate.nsfw or work["nsfw"]) else 0, now_iso(), work_id),
    )
    for title_text, norm, kind, lang in _variants(candidate):
        conn.execute(
            "INSERT OR IGNORE INTO work_titles(work_id, title, normalized, kind, lang) VALUES(?,?,?,?,?)",
            (work_id, title_text, norm, kind, lang),
        )
    conn.execute("DELETE FROM work_tags WHERE work_id=? AND source=?", (work_id, candidate.source))
    for tag in candidate.tags or []:
        name = (tag.get("name") if isinstance(tag, dict) else str(tag)) or ""
        norm = normalize_title(name)
        if not name or not norm:
            continue
        weight = tag.get("weight", 1) if isinstance(tag, dict) else 1
        try:
            weight = float(weight)
        except Exception:
            weight = 1.0
        conn.execute(
            "INSERT OR REPLACE INTO work_tags(work_id, tag, normalized, weight, source) VALUES(?,?,?,?,?)",
            (work_id, name, norm, weight, candidate.source),
        )
    if candidate.rating is not None:
        conn.execute(
            "INSERT INTO ratings(work_id, source, score, scale, vote_count, summary, url) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(work_id, source) DO UPDATE SET score=excluded.score, scale=excluded.scale, vote_count=excluded.vote_count, "
            "summary=excluded.summary, url=excluded.url",
            (work_id, candidate.source, candidate.rating, candidate.rating_scale or 10, candidate.rating_votes or 0, "", candidate.url),
        )
    conn.execute(
        "INSERT INTO work_sources(work_id, source, source_id, url, title) VALUES(?,?,?,?,?) "
        "ON CONFLICT(source, source_id) DO UPDATE SET work_id=excluded.work_id, url=excluded.url, title=excluded.title",
        (work_id, candidate.source, str(candidate.source_id), candidate.url, candidate.title),
    )


def upsert_candidate(conn, candidate, force_new=False, link_to=None):
    existing = conn.execute(
        "SELECT work_id FROM work_sources WHERE source=? AND source_id=?",
        (candidate.source, str(candidate.source_id)),
    ).fetchone()
    if existing:
        work_id = int(existing["work_id"])
        save_work_details(conn, work_id, candidate)
        return work_id, "existing"
    if link_to:
        work_id = int(link_to)
        save_work_details(conn, work_id, candidate)
        return work_id, "linked"
    if not force_new:
        matched = find_existing(conn, candidate)
        if matched:
            save_work_details(conn, matched, candidate)
            return matched, "linked"
    work_id = create_work(conn, candidate)
    return work_id, "created"


def merge_works(conn, source_work_id, target_work_id):
    source_work_id = int(source_work_id)
    target_work_id = int(target_work_id)
    if source_work_id == target_work_id:
        raise ValueError("不能合并同一个作品")
    source = conn.execute("SELECT * FROM works WHERE id=?", (source_work_id,)).fetchone()
    target = conn.execute("SELECT * FROM works WHERE id=?", (target_work_id,)).fetchone()
    if not source or not target:
        raise ValueError("作品不存在")
    if source["type"] != target["type"]:
        raise ValueError("不同类型作品不能合并")
    conn.execute("UPDATE OR IGNORE work_sources SET work_id=? WHERE work_id=?", (target_work_id, source_work_id))
    conn.execute("DELETE FROM work_sources WHERE work_id=?", (source_work_id,))
    conn.execute("UPDATE OR IGNORE work_titles SET work_id=? WHERE work_id=?", (target_work_id, source_work_id))
    conn.execute("DELETE FROM work_titles WHERE work_id=?", (source_work_id,))
    # 评论按 source+source_id 去重；目标已有同评论时删除源评论。
    conn.execute(
        "DELETE FROM reviews WHERE work_id=? AND EXISTS (SELECT 1 FROM reviews r2 WHERE r2.work_id=? AND r2.source=reviews.source AND r2.source_id=reviews.source_id)",
        (source_work_id, target_work_id),
    )
    conn.execute("UPDATE reviews SET work_id=? WHERE work_id=?", (target_work_id, source_work_id))
    conn.execute("UPDATE review_chunks SET work_id=? WHERE work_id=?", (target_work_id, source_work_id))
    for row in conn.execute("SELECT * FROM work_tags WHERE work_id=?", (source_work_id,)).fetchall():
        conn.execute(
            "INSERT OR IGNORE INTO work_tags(work_id, tag, normalized, weight, source) VALUES(?,?,?,?,?)",
            (target_work_id, row["tag"], row["normalized"], row["weight"], row["source"]),
        )
    conn.execute("DELETE FROM work_tags WHERE work_id=?", (source_work_id,))
    for row in conn.execute("SELECT * FROM ratings WHERE work_id=?", (source_work_id,)).fetchall():
        conn.execute(
            "INSERT OR IGNORE INTO ratings(work_id, source, score, scale, vote_count, summary, url) VALUES(?,?,?,?,?,?,?)",
            (target_work_id, row["source"], row["score"], row["scale"], row["vote_count"], row["summary"], row["url"]),
        )
    conn.execute("UPDATE chat_sessions SET work_id=? WHERE work_id=?", (target_work_id, source_work_id))
    conn.execute("DELETE FROM works WHERE id=?", (source_work_id,))
    from .db import recompute_work
    recompute_work(conn, target_work_id)
    conn.commit()
    return target_work_id
