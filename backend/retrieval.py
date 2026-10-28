from .config import SOURCE_LABELS
from .tokenizer import fts_query, query_tokens
from .util import excerpt, normalize_title


def _review_from_row(row):
    return {
        "id": int(row["review_id"]),
        "source": row["source"],
        "source_label": SOURCE_LABELS.get(row["source"], row["source"]),
        "author": row["author"],
        "score": row["score"],
        "score_scale": row["score_scale"],
        "sentiment": row["sentiment"],
        "language": row["language"],
        "spoiler": bool(row["spoiler"]),
        "likes": row["likes"],
        "url": row["url"],
        "posted_at": row["posted_at"],
        "excerpt": excerpt(row["text"], 360),
        "work_id": int(row["work_id"]),
        "work_title": row["work_title"],
        "work_type": row["work_type"],
        "work_year": row["work_year"],
        "cover_url": row["cover_url"],
    }


def search_chunks(conn, query, work_id=None, subject_type=None, limit=40, allow_nsfw=True):
    fts = fts_query(query_tokens(query))
    if not fts:
        return []
    sql = (
        "SELECT c.id AS chunk_id, c.review_id, c.work_id, c.text, "
        "bm25(review_fts) AS rank, r.source, r.author, r.score, r.score_scale, r.sentiment, "
        "r.language, r.spoiler, r.url, r.posted_at, r.likes, w.title AS work_title, "
        "w.type AS work_type, w.year AS work_year, w.cover_url "
        "FROM review_fts JOIN review_chunks c ON c.id=review_fts.rowid "
        "JOIN reviews r ON r.id=c.review_id JOIN works w ON w.id=c.work_id "
        "WHERE review_fts MATCH ?"
    )
    params = [fts]
    if work_id is not None:
        sql += " AND c.work_id=?"
        params.append(int(work_id))
    if subject_type in ("anime", "galgame"):
        sql += " AND w.type=?"
        params.append(subject_type)
    if not allow_nsfw:
        sql += " AND w.nsfw=0"
    sql += " ORDER BY rank LIMIT ?"
    params.append(max(1, int(limit) * 3))
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        return []
    result = []
    seen = set()
    for row in rows:
        review_id = int(row["review_id"])
        if review_id in seen:
            continue
        seen.add(review_id)
        item = _review_from_row(row)
        item["chunk_id"] = int(row["chunk_id"])
        item["rank"] = float(row["rank"] or 0)
        item["text"] = row["text"]
        result.append(item)
        if len(result) >= limit:
            break
    return result


def recent_chunks(conn, work_id, limit=6):
    rows = conn.execute(
        "SELECT c.id AS chunk_id, c.review_id, c.work_id, c.text, 0 AS rank, r.source, r.author, r.score, "
        "r.score_scale, r.sentiment, r.language, r.spoiler, r.url, r.posted_at, r.likes, "
        "w.title AS work_title, w.type AS work_type, w.year AS work_year, w.cover_url "
        "FROM review_chunks c JOIN reviews r ON r.id=c.review_id JOIN works w ON w.id=c.work_id "
        "WHERE c.work_id=? ORDER BY r.likes DESC, r.posted_at DESC LIMIT ?",
        (int(work_id), int(limit)),
    ).fetchall()
    result = []
    seen = set()
    for row in rows:
        if int(row["review_id"]) in seen:
            continue
        seen.add(int(row["review_id"]))
        item = _review_from_row(row)
        item["chunk_id"] = int(row["chunk_id"])
        item["rank"] = 0.0
        item["text"] = row["text"]
        result.append(item)
    return result


def _compact_summary(conn, work):
    work_id = int(work["id"])
    stats = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END) AS positive, "
        "SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END) AS negative, "
        "AVG(score) AS avg_score FROM reviews WHERE work_id=?",
        (work_id,),
    ).fetchone()
    tags = [row["tag"] for row in conn.execute(
        "SELECT tag FROM work_tags WHERE work_id=? ORDER BY weight DESC, id ASC LIMIT 4",
        (work_id,),
    ).fetchall()]
    total = int(stats["total"] or 0)
    if total <= 0:
        return "暂无评论，仅元数据。", tags
    positive = int(stats["positive"] or 0)
    negative = int(stats["negative"] or 0)
    snippet = conn.execute(
        "SELECT text FROM reviews WHERE work_id=? ORDER BY likes DESC, posted_at DESC, id DESC LIMIT 1",
        (work_id,),
    ).fetchone()
    text = "评论 " + str(total) + " 条；正面 " + str(positive) + " / 负面 " + str(negative)
    if snippet and snippet["text"]:
        text += "；摘录：" + excerpt(snippet["text"], 64)
    return text, tags


def catalog_entry(conn, work):
    summary, tags = _compact_summary(conn, work)
    type_label = "番剧" if work["type"] == "anime" else "Galgame"
    return {
        "id": int(work["id"]),
        "type": work["type"],
        "type_label": type_label,
        "title": str(work["title"] or ""),
        "year": work["year"],
        "rating": work["rating_score"],
        "reviews": int(work["review_count"] or 0),
        "tags": tags,
        "summary": summary,
    }


def build_catalog(conn, allow_nsfw=True):
    clause = "" if allow_nsfw else " WHERE nsfw=0"
    rows = conn.execute("SELECT * FROM works" + clause + " ORDER BY id ASC").fetchall()
    entries = [catalog_entry(conn, row) for row in rows]
    galgames = sum(1 for entry in entries if entry["type"] == "galgame")
    return {
        "entries": entries,
        "works_total": len(entries),
        "galgame_total": galgames,
        "anime_total": len(entries) - galgames,
    }


def format_catalog(entries, scores=None, summary_chars=None):
    blocks = []
    for entry in entries:
        attrs = (
            ' id="' + str(entry["id"]) + '"'
            + ' type="' + _sanitize(str(entry["type"])) + '"'
            + ' title="' + _sanitize(str(entry["title"])) + '"'
        )
        if entry.get("year"):
            attrs += ' year="' + str(entry["year"]) + '"'
        if entry.get("rating") is not None:
            attrs += ' rating="' + ("%.1f" % float(entry["rating"])) + '"'
        attrs += ' reviews="' + str(entry.get("reviews") or 0) + '"'
        if entry.get("tags"):
            attrs += ' tags="' + _sanitize("、".join(entry["tags"])) + '"'
        if scores is not None and int(entry["id"]) in scores:
            attrs += ' score="' + str(int(scores[int(entry["id"])])) + '"'
        summary = _sanitize(str(entry.get("summary") or ""))
        if summary_chars is not None:
            summary = summary[:max(0, int(summary_chars))]
        blocks.append("<work" + attrs + ">\n" + summary + "\n</work>")
    return "\n\n".join(blocks)


def plan_catalog_batches(entries, budget_chars=16000, max_works=0):
    budget_chars = max(2000, int(budget_chars or 16000))
    try:
        max_works = max(0, int(max_works or 0))
    except Exception:
        max_works = 0
    batches = []
    current = []
    total = 0
    for entry in entries:
        size = len(format_catalog([entry]))
        if current and (total + size > budget_chars or (max_works and len(current) >= max_works)):
            batches.append(current)
            current = []
            total = 0
        current.append(entry)
        total += size
    if current:
        batches.append(current)
    return batches


def local_catalog_scores(conn, query, entries, allow_nsfw=True):
    scores = {int(entry["id"]): 0 for entry in entries}
    if not query:
        return scores
    items = search_chunks(conn, query, limit=max(200, len(entries) * 4), allow_nsfw=allow_nsfw)
    seen = {}
    for index, item in enumerate(items):
        wid = int(item["work_id"])
        if wid not in seen:
            seen[wid] = index
            scores[wid] = max(scores.get(wid, 0), max(20, 90 - index))
    tokens = [normalize_title(token) for token in query_tokens(query)[:8] if normalize_title(token)]
    for entry in entries:
        haystack = normalize_title(entry["title"] + " " + " ".join(entry["tags"]))
        bonus = sum(4 for token in tokens if len(token) >= 2 and token in haystack)
        scores[entry["id"]] = min(100, scores.get(entry["id"], 0) + bonus)
    return scores


def _work_chunks(conn, work_id, limit=20, sentiment=None, allow_nsfw=True):
    sql = (
        "SELECT c.id AS chunk_id, c.review_id, c.work_id, c.text, 0 AS rank, r.source, r.author, r.score, "
        "r.score_scale, r.sentiment, r.language, r.spoiler, r.url, r.posted_at, r.likes, "
        "w.title AS work_title, w.type AS work_type, w.year AS work_year, w.cover_url "
        "FROM review_chunks c JOIN reviews r ON r.id=c.review_id JOIN works w ON w.id=c.work_id "
        "WHERE c.work_id=?"
    )
    params = [int(work_id)]
    if sentiment:
        sql += " AND r.sentiment=?"
        params.append(sentiment)
    if not allow_nsfw:
        sql += " AND w.nsfw=0"
    sql += " ORDER BY r.likes DESC, r.posted_at DESC LIMIT ?"
    params.append(max(1, int(limit)))
    rows = conn.execute(sql, params).fetchall()
    result = []
    seen = set()
    for row in rows:
        if int(row["review_id"]) in seen:
            continue
        seen.add(int(row["review_id"]))
        item = _review_from_row(row)
        item["chunk_id"] = int(row["chunk_id"])
        item["rank"] = 0.0
        item["text"] = row["text"]
        result.append(item)
    return result


def representative_reviews(conn, work_id, query="", limit=15, allow_nsfw=True):
    limit = max(1, int(limit or 15))
    result = []
    seen = set()

    def add(items):
        for item in items:
            rid = int(item["id"])
            if rid in seen:
                continue
            seen.add(rid)
            result.append(item)
            if len(result) >= limit:
                return True
        return False

    if query:
        if add(search_chunks(conn, query, work_id=work_id, limit=max(8, limit), allow_nsfw=allow_nsfw)):
            return result[:limit]
    if add(recent_chunks(conn, work_id, limit=max(20, limit * 2))):
        return result[:limit]
    for sentiment in ("positive", "negative", "neutral"):
        if add(_work_chunks(conn, work_id, limit=2, sentiment=sentiment, allow_nsfw=allow_nsfw)):
            break
    return result[:limit]


def candidate_works(conn, query, max_works=8, allow_nsfw=True):
    max_works = max(1, min(20, int(max_works or 8)))
    ranked = []
    seen = set()
    if query:
        for item in search_chunks(conn, query, limit=max(80, max_works * 20), allow_nsfw=allow_nsfw):
            wid = int(item["work_id"])
            if wid not in seen:
                seen.add(wid)
                ranked.append(wid)
    tokens = [normalize_title(token) for token in query_tokens(query)[:8] if normalize_title(token)]
    tokens = [token for token in tokens if len(token) >= 2][:8]
    if tokens:
        like = ["%" + token + "%" for token in tokens]
        nsfw_clause = "" if allow_nsfw else " AND w.nsfw=0"
        title_rows = conn.execute(
            "SELECT DISTINCT w.id FROM works w JOIN work_titles wt ON wt.work_id=w.id "
            "WHERE (" + " OR ".join("wt.normalized LIKE ?" for _ in like) + ")" + nsfw_clause,
            tuple(like),
        ).fetchall()
        tag_rows = conn.execute(
            "SELECT DISTINCT w.id FROM works w JOIN work_tags wt ON wt.work_id=w.id "
            "WHERE (" + " OR ".join("wt.normalized LIKE ?" for _ in like) + ")" + nsfw_clause,
            tuple(like),
        ).fetchall()
        for row in list(title_rows) + list(tag_rows):
            wid = int(row["id"])
            if wid not in seen:
                seen.add(wid)
                ranked.append(wid)
    nsfw_clause = "" if allow_nsfw else " AND nsfw=0"
    fallback = conn.execute(
        "SELECT id FROM works WHERE review_count>0" + nsfw_clause +
        " ORDER BY COALESCE(rating_score,0) DESC, review_count DESC LIMIT ?",
        (max_works,),
    ).fetchall()
    for row in fallback:
        wid = int(row["id"])
        if wid not in seen:
            seen.add(wid)
            ranked.append(wid)
    return ranked[:max_works]


def _work_summary(conn, work, reviews):
    work_id = int(work["id"])
    stats = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END) AS positive, "
        "SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END) AS negative, "
        "SUM(CASE WHEN sentiment='neutral' THEN 1 ELSE 0 END) AS neutral, "
        "SUM(CASE WHEN kind='吐槽' THEN 1 ELSE 0 END) AS short_reviews, "
        "SUM(CASE WHEN kind='评论' THEN 1 ELSE 0 END) AS long_reviews, "
        "AVG(score) AS avg_score FROM reviews WHERE work_id=?",
        (work_id,),
    ).fetchone()
    tags = [row["tag"] for row in conn.execute(
        "SELECT tag FROM work_tags WHERE work_id=? ORDER BY weight DESC, id ASC LIMIT 6",
        (work_id,),
    ).fetchall()]
    type_label = "番剧" if work["type"] == "anime" else "Galgame"
    pieces = ["《" + str(work["title"]) + "》", type_label]
    if work["year"]:
        pieces.append(str(work["year"]))
    if work["rating_score"] is not None:
        pieces.append("评分 " + ("%.1f" % float(work["rating_score"])))
    pieces.append(str(stats["total"] or 0) + " 条评论（吐槽 " + str(stats["short_reviews"] or 0)
                  + " / 长评 " + str(stats["long_reviews"] or 0)
                  + "；正面 " + str(stats["positive"] or 0)
                  + " / 中性 " + str(stats["neutral"] or 0)
                  + " / 负面 " + str(stats["negative"] or 0) + "）")
    if tags:
        pieces.append("高频标签：" + "、".join(tags))
    snippets = []
    positive = next((item for item in reviews if item.get("sentiment") == "positive" and item.get("text")), None)
    negative = next((item for item in reviews if item.get("sentiment") == "negative" and item.get("text")), None)
    if positive:
        snippets.append("正面“" + excerpt(positive["text"], 70) + "”")
    if negative:
        snippets.append("负面“" + excerpt(negative["text"], 70) + "”")
    if snippets:
        pieces.append("代表观点：" + "；".join(snippets))
    return " · ".join(pieces)[:650]


def work_digest(conn, work_id, query="", max_reviews=15, allow_nsfw=True):
    work = conn.execute("SELECT * FROM works WHERE id=?", (int(work_id),)).fetchone()
    if not work or int(work["review_count"] or 0) <= 0:
        return None
    if work["nsfw"] and not allow_nsfw:
        return None
    reviews = representative_reviews(conn, work_id, query=query, limit=max_reviews, allow_nsfw=allow_nsfw)
    if not reviews:
        return None
    return {
        "work": dict(work),
        "summary": _work_summary(conn, work, reviews),
        "reviews": reviews,
    }


def _review_block(item, max_chars=360):
    text = _sanitize(item.get("text") or item.get("excerpt") or "")[:max_chars]
    header = (
        '<review id="R' + str(item["id"]) + '" source="' + str(item.get("source_label") or item.get("source", "")) + '"'
        + (' score="' + str(item.get("score")) + '"' if item.get("score") is not None else "")
        + (' language="' + str(item.get("language")) + '"' if item.get("language") else "")
        + (' spoiler="true"' if item.get("spoiler") else "")
        + ' work="' + _sanitize(str(item.get("work_title") or "")) + '">'
    )
    return header + "\n" + text + "\n</review>"


def _summary_block(digest):
    work = digest["work"]
    title = _sanitize(str(work.get("title") or ""))
    return (
        '<work_summary work_id="' + str(work.get("id")) + '" title="' + title + '"'
        + (' rating="' + ("%.1f" % float(work["rating_score"])) + '"' if work.get("rating_score") is not None else "")
        + ' reviews="' + str(work.get("review_count") or 0) + '">'
        + "\n" + _sanitize(digest["summary"]) + "\n</work_summary>"
    )


def pack_chat_evidence(digests, max_chars=30000, per_review_chars=360, mode="global"):
    max_chars = max(1000, int(max_chars or 30000))
    accepted = []
    summary_blocks = []
    total = 0
    for digest in digests or []:
        block = _summary_block(digest)
        if total + len(block) > max_chars and accepted:
            break
        if len(block) > max_chars:
            block = block[:max_chars]
        accepted.append(digest)
        summary_blocks.append(block)
        total += len(block)
    selected = {int(digest["work"]["id"]): [] for digest in accepted}
    queues = {int(digest["work"]["id"]): list(digest.get("reviews") or []) for digest in accepted}
    review_blocks = []
    citations = []
    truncated = len(accepted) < len(digests or [])
    while queues:
        progress = False
        for digest in accepted:
            wid = int(digest["work"]["id"])
            queue = queues.get(wid) or []
            if not queue:
                continue
            item = queue.pop(0)
            block = _review_block(item, per_review_chars)
            if total + len(block) > max_chars:
                truncated = True
                queues[wid] = []
                continue
            selected[wid].append(item)
            review_blocks.append(block)
            citations.append(item)
            total += len(block)
            progress = True
            if not queue:
                queues[wid] = []
            else:
                queues[wid] = queue
        if not progress:
            break
    if any(queues.get(int(d["work"]["id"])) for d in accepted):
        truncated = True
    text = "\n\n".join(summary_blocks + review_blocks)
    coverage = {
        "mode": mode,
        "works": len(accepted),
        "reviews": len(citations),
        "summaries": len(summary_blocks),
        "truncated": truncated,
    }
    return text, citations, coverage


def build_global_evidence(conn, query, max_works=8, per_work=15, max_chars=30000, allow_nsfw=True):
    try:
        per_work = max(10, min(20, int(per_work or 15)))
    except Exception:
        per_work = 15
    works = candidate_works(conn, query, max_works=max_works, allow_nsfw=allow_nsfw)
    digests = []
    for work_id in works:
        digest = work_digest(conn, work_id, query=query, max_reviews=per_work, allow_nsfw=allow_nsfw)
        if digest:
            digests.append(digest)
    if not digests:
        return "", [], {"mode": "global", "works": 0, "reviews": 0, "summaries": 0, "truncated": False}
    return pack_chat_evidence(digests, max_chars=max_chars, per_review_chars=360, mode="global")


def build_work_evidence(conn, work_id, query, max_reviews=120, max_chars=30000, allow_nsfw=True):
    try:
        max_reviews = max(1, min(120, int(max_reviews or 120)))
    except Exception:
        max_reviews = 120
    digest = work_digest(conn, work_id, query=query, max_reviews=max_reviews, allow_nsfw=allow_nsfw)
    if not digest:
        return "", [], {"mode": "work", "works": 0, "reviews": 0, "summaries": 0, "truncated": False}
    return pack_chat_evidence([digest], max_chars=max_chars, per_review_chars=900, mode="work")


def evidence_for_chat(conn, query, work_id=None, max_items=100, allow_nsfw=True):
    if work_id is not None:
        items = search_chunks(conn, query, work_id=work_id, limit=max_items, allow_nsfw=allow_nsfw)
        if len(items) < max_items:
            seen = {item["id"] for item in items}
            for item in recent_chunks(conn, work_id, max_items * 2):
                if item["id"] not in seen:
                    items.append(item)
                    seen.add(item["id"])
                if len(items) >= max_items:
                    break
        return items[:max_items]
    items = search_chunks(conn, query, limit=max_items * 4, allow_nsfw=allow_nsfw)
    if not items:
        return []
    result = []
    per_work = {}
    seen = set()
    for item in items:
        wid = item["work_id"]
        if item["id"] in seen:
            continue
        if per_work.get(wid, 0) >= 10:
            continue
        per_work[wid] = per_work.get(wid, 0) + 1
        seen.add(item["id"])
        result.append(item)
        if len(result) >= max_items:
            break
    return result


def _sanitize(text):
    return (text or "").replace("<", "＜").replace(">", "＞")


def pack_evidence(items, max_chars=30000, per_item_chars=900):
    blocks = []
    citations = []
    used = []
    total = 0
    for item in items:
        text = _sanitize(item.get("text") or item.get("excerpt") or "")
        if not text:
            continue
        text = text[:per_item_chars]
        header = (
            '<review id="R' + str(item["id"]) + '" source="' + str(item.get("source_label") or item.get("source", "")) + '"'
            + (' score="' + str(item.get("score")) + '"' if item.get("score") is not None else "")
            + (' language="' + str(item.get("language")) + '"' if item.get("language") else "")
            + (' spoiler="true"' if item.get("spoiler") else "")
            + ' work="' + _sanitize(str(item.get("work_title") or "")) + '">'
        )
        block = header + "\n" + text + "\n</review>"
        if total + len(block) > max_chars and used:
            break
        used.append(item)
        blocks.append(block)
        citations.append(item)
        total += len(block)
    return "\n\n".join(blocks), citations, total


def review_full(conn, review_id):
    row = conn.execute(
        "SELECT r.*, w.title AS work_title, w.type AS work_type, w.year AS work_year, w.cover_url "
        "FROM reviews r JOIN works w ON w.id=r.work_id WHERE r.id=?",
        (int(review_id),),
    ).fetchone()
    return dict(row) if row else None


def review_list(conn, work_id, source=None, language=None, sentiment=None, spoiler=None, page=1, page_size=20, sort="newest", kind=None):
    where = ["r.work_id=?"]
    params = [int(work_id)]
    if source:
        where.append("r.source=?")
        params.append(source)
    if language:
        where.append("r.language=?")
        params.append(language)
    if sentiment in ("positive", "negative", "mixed", "neutral"):
        where.append("r.sentiment=?")
        params.append(sentiment)
    if spoiler is not None:
        where.append("r.spoiler=?")
        params.append(1 if spoiler else 0)
    if kind:
        where.append("r.kind=?")
        params.append(kind)
    clause = " AND ".join(where)
    order = "r.posted_at DESC, r.id DESC" if sort == "newest" else "r.likes DESC, r.id DESC"
    total = conn.execute("SELECT COUNT(*) FROM reviews r WHERE " + clause, tuple(params)).fetchone()[0]
    offset = max(0, (int(page) - 1) * int(page_size))
    rows = conn.execute(
        "SELECT r.* FROM reviews r WHERE " + clause + " ORDER BY " + order + " LIMIT ? OFFSET ?",
        tuple(params) + (int(page_size), offset),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["excerpt"] = excerpt(item["text"], 460)
        item["char_count"] = len(item["text"] or "")
        item.pop("text", None)
        items.append(item)
    return {"items": items, "total": total, "page": int(page), "page_size": int(page_size)}
