CREATE TABLE IF NOT EXISTS works (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK(type IN ('anime','galgame')),
    title TEXT NOT NULL,
    year INTEGER,
    synopsis TEXT DEFAULT '',
    cover_url TEXT DEFAULT '',
    nsfw INTEGER DEFAULT 0,
    rating_score REAL,
    rating_count INTEGER DEFAULT 0,
    review_count INTEGER DEFAULT 0,
    source_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_titles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    normalized TEXT NOT NULL,
    kind TEXT DEFAULT 'main',
    lang TEXT DEFAULT '',
    UNIQUE(work_id, normalized)
);
CREATE INDEX IF NOT EXISTS idx_work_titles_norm ON work_titles(normalized);

CREATE TABLE IF NOT EXISTS work_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    url TEXT DEFAULT '',
    title TEXT DEFAULT '',
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_work_sources_work ON work_sources(work_id);

CREATE TABLE IF NOT EXISTS work_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    normalized TEXT NOT NULL,
    weight REAL DEFAULT 1,
    source TEXT DEFAULT '',
    UNIQUE(work_id, normalized, source)
);
CREATE INDEX IF NOT EXISTS idx_work_tags_norm ON work_tags(normalized);

CREATE TABLE IF NOT EXISTS ratings (
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    score REAL NOT NULL,
    scale REAL DEFAULT 10,
    vote_count INTEGER DEFAULT 0,
    summary TEXT DEFAULT '',
    url TEXT DEFAULT '',
    PRIMARY KEY(work_id, source)
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    author TEXT DEFAULT '',
    score REAL,
    score_scale REAL DEFAULT 10,
    sentiment TEXT DEFAULT 'neutral',
    kind TEXT DEFAULT '',
    text TEXT NOT NULL,
    language TEXT DEFAULT '',
    spoiler INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    url TEXT DEFAULT '',
    posted_at TEXT DEFAULT '',
    fetched_at TEXT NOT NULL,
    content_hash TEXT DEFAULT '',
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_reviews_work ON reviews(work_id);
CREATE INDEX IF NOT EXISTS idx_reviews_source ON reviews(source);

CREATE TABLE IF NOT EXISTS review_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    ord INTEGER DEFAULT 0,
    text TEXT NOT NULL,
    tokens TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_chunks_work ON review_chunks(work_id);
CREATE INDEX IF NOT EXISTS idx_review_chunks_review ON review_chunks(review_id);

CREATE VIRTUAL TABLE IF NOT EXISTS review_fts USING fts5(
    tokens,
    content='review_chunks',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS review_chunks_ai AFTER INSERT ON review_chunks BEGIN
    INSERT INTO review_fts(rowid, tokens) VALUES (new.id, new.tokens);
END;
CREATE TRIGGER IF NOT EXISTS review_chunks_ad AFTER DELETE ON review_chunks BEGIN
    INSERT INTO review_fts(review_fts, rowid, tokens) VALUES('delete', old.id, old.tokens);
END;
CREATE TRIGGER IF NOT EXISTS review_chunks_au AFTER UPDATE ON review_chunks BEGIN
    INSERT INTO review_fts(review_fts, rowid, tokens) VALUES('delete', old.id, old.tokens);
    INSERT INTO review_fts(rowid, tokens) VALUES (new.id, new.tokens);
END;

CREATE TABLE IF NOT EXISTS crawl_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    progress_json TEXT DEFAULT '{}',
    total INTEGER DEFAULT 0,
    review_limit INTEGER DEFAULT 0,
    message TEXT DEFAULT '',
    error TEXT DEFAULT '',
    cancel_requested INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT DEFAULT '',
    finished_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_crawl_jobs_status ON crawl_jobs(status);
CREATE INDEX IF NOT EXISTS idx_crawl_jobs_work ON crawl_jobs(work_id);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '新对话',
    work_id INTEGER REFERENCES works(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content TEXT NOT NULL,
    citations_json TEXT DEFAULT '[]',
    usage_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
