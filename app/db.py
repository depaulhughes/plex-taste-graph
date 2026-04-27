import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.config import get_settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS titles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plex_rating_key TEXT UNIQUE,
    title TEXT NOT NULL,
    year INTEGER,
    type TEXT NOT NULL CHECK(type IN ('movie', 'show')),
    summary TEXT,
    genres TEXT,
    directors TEXT,
    writers TEXT,
    actors TEXT,
    poster_url TEXT,
    plex_url TEXT,
    source TEXT NOT NULL DEFAULT 'manual' CHECK(source IN ('demo', 'plex', 'manual')),
    enrichment_status TEXT NOT NULL DEFAULT 'pending' CHECK(enrichment_status IN ('pending', 'enriched', 'failed')),
    is_anchor INTEGER NOT NULL DEFAULT 0,
    primary_cluster TEXT,
    last_enriched_at TEXT,
    added_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS taste_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id INTEGER NOT NULL UNIQUE,
    tone_tags TEXT NOT NULL DEFAULT '[]',
    theme_tags TEXT NOT NULL DEFAULT '[]',
    style_tags TEXT NOT NULL DEFAULT '[]',
    mood_tags TEXT NOT NULL DEFAULT '[]',
    intensity_score INTEGER NOT NULL CHECK(intensity_score BETWEEN 1 AND 10),
    weirdness_score INTEGER NOT NULL CHECK(weirdness_score BETWEEN 1 AND 10),
    emotional_weight_score INTEGER NOT NULL CHECK(emotional_weight_score BETWEEN 1 AND 10),
    pacing_score INTEGER NOT NULL CHECK(pacing_score BETWEEN 1 AND 10),
    johnny_core_score INTEGER NOT NULL CHECK(johnny_core_score BETWEEN 1 AND 10),
    ai_summary TEXT,
    recommendation_hooks TEXT NOT NULL DEFAULT '[]',
    closest_viewing_context TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(title_id) REFERENCES titles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_title_id INTEGER NOT NULL,
    target_title_id INTEGER NOT NULL,
    weight REAL NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    edge_type TEXT NOT NULL DEFAULT 'strong' CHECK(edge_type IN ('strong', 'soft', 'bridge')),
    shared_traits TEXT NOT NULL DEFAULT '[]',
    explanation TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(source_title_id, target_title_id),
    FOREIGN KEY(source_title_id) REFERENCES titles(id) ON DELETE CASCADE,
    FOREIGN KEY(target_title_id) REFERENCES titles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ask_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key TEXT NOT NULL UNIQUE,
    graph_version TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_titles_rating_key ON titles(plex_rating_key);
CREATE INDEX IF NOT EXISTS idx_profiles_title_id ON taste_profiles(title_id);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_title_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_title_id);
CREATE INDEX IF NOT EXISTS idx_ask_cache_key ON ask_cache(cache_key);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db_path() -> Path:
    return get_settings().sqlite_path


def init_db() -> None:
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        migrate_db(conn)
        conn.commit()


def migrate_db(conn: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(titles)").fetchall()
    }
    migrations = {
        "source": "ALTER TABLE titles ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'",
        "enrichment_status": "ALTER TABLE titles ADD COLUMN enrichment_status TEXT NOT NULL DEFAULT 'pending'",
        "is_anchor": "ALTER TABLE titles ADD COLUMN is_anchor INTEGER NOT NULL DEFAULT 0",
        "primary_cluster": "ALTER TABLE titles ADD COLUMN primary_cluster TEXT",
        "last_enriched_at": "ALTER TABLE titles ADD COLUMN last_enriched_at TEXT",
    }
    for column, statement in migrations.items():
        if column not in columns:
            conn.execute(statement)

    edge_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(edges)").fetchall()
    }
    edge_migrations = {
        "confidence": "ALTER TABLE edges ADD COLUMN confidence REAL NOT NULL DEFAULT 0",
        "edge_type": "ALTER TABLE edges ADD COLUMN edge_type TEXT NOT NULL DEFAULT 'strong'",
    }
    for column, statement in edge_migrations.items():
        if column not in edge_columns:
            conn.execute(statement)
    conn.execute("UPDATE edges SET confidence = weight WHERE confidence = 0")
    conn.execute("UPDATE edges SET edge_type = 'strong' WHERE edge_type IS NULL OR edge_type = ''")

    profile_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(taste_profiles)").fetchall()
    }
    profile_migrations = {
        "recommendation_hooks": "ALTER TABLE taste_profiles ADD COLUMN recommendation_hooks TEXT NOT NULL DEFAULT '[]'",
        "closest_viewing_context": "ALTER TABLE taste_profiles ADD COLUMN closest_viewing_context TEXT",
    }
    for column, statement in profile_migrations.items():
        if column not in profile_columns:
            conn.execute(statement)

    conn.execute(
        """
        UPDATE titles
        SET source = 'demo'
        WHERE plex_rating_key LIKE 'DEMO:%'
          AND (source IS NULL OR source = 'manual')
        """
    )
    conn.execute(
        """
        UPDATE titles
        SET enrichment_status = 'enriched'
        WHERE id IN (SELECT title_id FROM taste_profiles)
          AND enrichment_status = 'pending'
        """
    )


def dict_factory(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    init_db()
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
