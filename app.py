import argparse
import sys
import time
from pathlib import Path

from backend.config import VERSION, data_dir
from backend.context import AppContext
from backend.db import connect, init_db, rebuild_fts
from backend.sources.base import Candidate, ReviewItem
from backend.linking import create_work, save_work_details
from backend.crawler import Crawler
from backend.db import recompute_work
from backend.server import serve


DEMO_WORKS = [
    {
        "source_id": "demo-anime-1",
        "type": "anime",
        "title": "星海邮差",
        "titles": {"zh": "星海邮差", "ja": "星海のポストマン"},
        "year": 2023,
        "rating": 8.6,
        "votes": 1830,
        "tags": ["科幻", "治愈", "公路片", "原创动画"],
        "reviews": [
            ("把“送达”拍成一种温柔的执念，最后一集没有强行煽情，而是让所有伏笔安静落地。", 9, "positive"),
            ("单元剧质量有起伏，第四集和第七集特别好，但中间两集节奏偏慢。", 7, "mixed"),
            ("如果喜欢《虫师》那种不急着解释世界观的作品，会很喜欢；想看强主线的可能会失望。", 8, "positive"),
            ("作画稳定，配乐是最大加分项。结局有点仓促，不过情绪是完整的。", 8, "positive"),
        ],
    },
    {
        "source_id": "demo-anime-2",
        "type": "anime",
        "title": "夏日回声",
        "titles": {"zh": "夏日回声", "ja": "夏のエコー"},
        "year": 2021,
        "rating": 8.1,
        "votes": 960,
        "tags": ["青春", "校园", "恋爱", "音乐"],
        "reviews": [
            ("青春群像写得很细，主角团不是每个人都圆满，但这才像真正的夏天。", 9, "positive"),
            ("感情线推进偏慢，喜欢快节奏恋爱的观众可能会着急。", 6, "mixed"),
            ("音乐社的排练段落拍得很有生活感，不是只把音乐当背景板。", 8, "positive"),
            ("结尾的合唱场景值得单独回看，前面所有小矛盾都汇到了一起。", 9, "positive"),
        ],
    },
    {
        "source_id": "demo-anime-3",
        "type": "anime",
        "title": "逆光列车",
        "titles": {"zh": "逆光列车", "ja": "逆光列車"},
        "year": 2019,
        "rating": 7.7,
        "votes": 640,
        "tags": ["悬疑", "时间旅行", "剧情", "群像"],
        "reviews": [
            ("时间线设计很野心，前六集信息量很大，但最后一集收得有点太急。", 7, "mixed"),
            ("适合一口气看完，二刷会发现很多前期埋的细节。", 9, "positive"),
            ("角色动机有几次为了推动剧情而服务，逻辑控可能会出戏。", 6, "negative"),
            ("氛围和美术很强，列车这个意象用得漂亮。", 8, "positive"),
        ],
    },
    {
        "source_id": "demo-galgame-1",
        "type": "galgame",
        "title": "白昼梦边界",
        "titles": {"zh": "白昼梦边界", "ja": "白昼夢の境界"},
        "year": 2022,
        "rating": 8.8,
        "votes": 740,
        "tags": ["视觉小说", "悬疑", "心理", "全年龄"],
        "reviews": [
            ("叙事诡计和 UI 结合得很好，通关后回头看标题会有新的理解。", 9, "positive"),
            ("一周目节奏偏闷，第二条线开始才真正进入状态；建议坚持到第二章。", 7, "mixed"),
            ("没有恋爱线也能成立，人物关系写得很克制。喜欢快节奏的人慎入。", 8, "positive"),
            ("真结局的解释略绕，但情绪落点很好，值得重读一遍。", 9, "positive"),
        ],
    },
    {
        "source_id": "demo-galgame-2",
        "type": "galgame",
        "title": "雨巷咖啡馆",
        "titles": {"zh": "雨巷咖啡馆", "ja": "雨巷カフェ"},
        "year": 2020,
        "rating": 7.9,
        "votes": 410,
        "tags": ["视觉小说", "日常", "治愈", "恋爱"],
        "reviews": [
            ("非常安静的日常作品，适合睡前慢慢推，不要期待强冲突。", 8, "positive"),
            ("四条线里两条写得很好，另外两条有点模板化。", 6, "mixed"),
            ("文本量不大，CG 和雨声氛围很舒服，短篇佳作。", 8, "positive"),
            ("结局偏理想化，但作为治愈系可以接受。", 7, "positive"),
        ],
    },
    {
        "source_id": "demo-galgame-3",
        "type": "galgame",
        "title": "星屑回路",
        "titles": {"zh": "星屑回路", "ja": "星屑ループ"},
        "year": 2024,
        "rating": 8.3,
        "votes": 520,
        "tags": ["视觉小说", "科幻", "轮回", "剧情"],
        "reviews": [
            ("轮回设定不算新，但人物成长写得扎实，尤其是配角线。", 8, "positive"),
            ("系统有点老旧，存档和回想功能不够顺手。", 6, "mixed"),
            ("最后一条线的反转有冲击力，前提是能接受前面较慢的铺垫。", 9, "positive"),
            ("喜欢《命运石之门》式科学幻想的可以试试，但本作更偏情感。", 8, "positive"),
        ],
    },
]


def seed_demo(app):
    conn = connect(app.db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
        if count:
            print("数据库已有作品，跳过演示数据。")
            return
        crawler = Crawler(app.db_path, app.settings, app.registry, app.logger)
        for index, item in enumerate(DEMO_WORKS):
            candidate = Candidate(
                source="demo",
                source_id=item["source_id"],
                type=item["type"],
                title=item["title"],
                titles=item["titles"],
                year=item["year"],
                synopsis="这是用于离线演示的虚构作品，评论均为本项目编写的合成文本。“" + item["title"] + "”的口碑样本，用于展示抓取、检索和对话流程。",
                cover_url="",
                tags=[{"name": tag, "weight": 1} for tag in item["tags"]],
                rating=item["rating"],
                rating_scale=10,
                rating_votes=item["votes"],
                url="",
                nsfw=False,
            )
            work_id = create_work(conn, candidate)
            reviews = []
            for i, (text, score, sentiment) in enumerate(item["reviews"]):
                reviews.append(ReviewItem(
                    source="demo",
                    source_id=item["source_id"] + "-r" + str(i + 1),
                    author="演示用户" + str(i + 1),
                    text=text,
                    score=float(score),
                    score_scale=10,
                    language="zh",
                    spoiler=False,
                    likes=8 - i,
                    url="",
                    posted_at="2024-0" + str((i % 9) + 1) + "-15T12:00:00+00:00",
                    sentiment=sentiment,
                ))
            crawler.upsert_reviews(conn, work_id, reviews)
            recompute_work(conn, work_id)
            conn.commit()
        print("已写入 " + str(len(DEMO_WORKS)) + " 部演示作品。")
    finally:
        conn.close()


def clear_demo(app):
    conn = connect(app.db_path)
    try:
        ids = [row[0] for row in conn.execute("SELECT DISTINCT work_id FROM work_sources WHERE source='demo'").fetchall()]
        for work_id in ids:
            conn.execute("DELETE FROM works WHERE id=?", (work_id,))
        conn.commit()
        print("已清除 " + str(len(ids)) + " 部演示作品。")
    finally:
        conn.close()


def check_sources(app):
    checks = {
        "bangumi": lambda adapter: adapter.search("凉宫春日", "anime", 1),
    }
    print("检查来源连通性（需要外网）...")
    for name, adapter in app.registry.adapters.items():
        start = time.time()
        try:
            adapter.client.timeout = 8
            adapter.client.retries = 0
            items = checks[name](adapter)
            print("  " + name + ": OK, " + str(len(items)) + " 条, " + format(time.time() - start, ".1f") + "s")
        except Exception as exc:
            print("  " + name + ": FAIL, " + str(exc)[:200])


def main(argv=None):
    parser = argparse.ArgumentParser(description="ACG Radar - Bangumi 评论分析工具")
    parser.add_argument("--version", action="version", version="ACG Radar " + VERSION)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--demo", action="store_true", help="写入离线演示数据（仅当数据库为空）")
    parser.add_argument("--clear-demo", action="store_true")
    parser.add_argument("--check-sources", action="store_true")
    parser.add_argument("--rebuild-index", action="store_true")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost"):
        print("为安全起见只允许绑定 127.0.0.1 / localhost。", file=sys.stderr)
        return 2
    app = AppContext(Path(args.data_dir) if args.data_dir else None, debug=args.debug)
    if args.check_sources:
        check_sources(app)
        return 0
    if args.rebuild_index:
        conn = connect(app.db_path)
        try:
            rebuild_fts(conn)
            print("FTS 索引与统计已重建。")
        finally:
            conn.close()
        return 0
    if args.clear_demo:
        clear_demo(app)
        return 0
    if args.demo:
        seed_demo(app)
    if args.open:
        pass
    serve(app, args.host, args.port, open_browser=args.open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
