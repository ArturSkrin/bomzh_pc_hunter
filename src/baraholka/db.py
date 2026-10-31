"""SQLite-хранилище. Схема простая, чтобы потом без боли переехать на Postgres."""

import json
import sqlite3
from pathlib import Path

from .models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id           INTEGER PRIMARY KEY,
    source       TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    title        TEXT NOT NULL,
    url          TEXT NOT NULL,
    price_ron    REAL,
    is_free      INTEGER NOT NULL DEFAULT 0,
    description  TEXT NOT NULL DEFAULT '',
    city         TEXT NOT NULL DEFAULT '',
    photos       TEXT NOT NULL DEFAULT '[]',
    posted_at    TEXT,
    seen_at      TEXT NOT NULL DEFAULT (datetime('now')),
    -- new -> rejected | stale | sent | send_failed -> skipped | negotiating
    status       TEXT NOT NULL DEFAULT 'new',
    llm_score    REAL,
    llm_comment  TEXT,
    UNIQUE (source, external_id)
);

-- Переписка с продавцом: черновики бота и пересланные мной ответы
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY,
    listing_id  INTEGER NOT NULL REFERENCES listings(id),
    direction   TEXT NOT NULL CHECK (direction IN ('draft', 'seller')),
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_if_new(conn: sqlite3.Connection, lot: Listing) -> int | None:
    """Сохраняет объявление. Возвращает id новой строки или None, если уже видели."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO listings
           (source, external_id, title, url, price_ron, is_free, description, city, photos, posted_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            lot.source,
            lot.external_id,
            lot.title,
            lot.url,
            lot.price_ron,
            int(lot.is_free),
            lot.description,
            lot.city,
            json.dumps(lot.photos),
            lot.posted_at.isoformat() if lot.posted_at else None,
        ),
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount else None
