"""SQLite-хранилище. Схема простая, чтобы потом без боли переехать на Postgres."""

import json
import os
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

-- Поисковые запросы и настройки правятся из веб-морды
CREATE TABLE IF NOT EXISTS queries (
    id       INTEGER PRIMARY KEY,
    text     TEXT NOT NULL UNIQUE,
    enabled  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- История поиска из чата: запрос, план от LLM и отобранные результаты (JSON)
CREATE TABLE IF NOT EXISTS searches (
    id          INTEGER PRIMARY KEY,
    request     TEXT NOT NULL,
    plan        TEXT NOT NULL,
    results     TEXT NOT NULL,
    candidates  INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DEFAULT_QUERIES = ["placa video", "procesor", "calculator", "laptop", "ssd"]

DEFAULT_SETTINGS = {
    "max_price_ron": "1500",
    "poll_interval_min": "12",
    "llm_eval_prompt": (
        "Оцени лот для перепродажи/восстановления б/у железа: рыночная цена в Румынии, "
        "риск (неисправность, «для запчастей»), что можно вытащить ценного. Оценка 0–10 и одна строка почему."
    ),
    "llm_bargain_prompt": (
        "Пиши продавцу на румынском, коротко и вежливо, без сленга. Сначала уточни модель, конфиг и "
        "рабочее ли. Торгуйся только реальными аргументами: состояние, похожие объявления."
    ),
}


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # веб читает, пока сборщик пишет
    conn.executescript(SCHEMA)
    # Первый запуск: старые MAX_PRICE_RON / POLL_INTERVAL_MIN из .env становятся начальными значениями
    seeds = {k: os.getenv(k.upper(), v) for k, v in DEFAULT_SETTINGS.items()}
    conn.executemany("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", seeds.items())
    if not conn.execute("SELECT 1 FROM queries").fetchone():
        conn.executemany("INSERT INTO queries (text) VALUES (?)", [(q,) for q in DEFAULT_QUERIES])
    conn.commit()
    return conn


def get_setting(conn: sqlite3.Connection, key: str) -> str:
    return conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()[0]


def enabled_queries(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT text FROM queries WHERE enabled = 1 ORDER BY id")]


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
