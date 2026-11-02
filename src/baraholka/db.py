"""SQLite-хранилище. Схема простая, чтобы потом без боли переехать на Postgres."""

import json
import os
import sqlite3
from datetime import datetime, time as dtime
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
    max_offer_ron REAL,  -- «торгуюсь до» для этого лота; пусто — до цены продавца
    UNIQUE (source, external_id)
);

-- Переписка с продавцом: черновики бота и пересланные мной ответы
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY,
    listing_id  INTEGER NOT NULL REFERENCES listings(id),
    direction   TEXT NOT NULL CHECK (direction IN ('draft', 'seller')),
    text        TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',  -- для черновика: перевод и пояснение по-русски
    flags       TEXT NOT NULL DEFAULT '[]',  -- нарушения жёстких правил (JSON), для продавца — красные флаги
    sent        INTEGER NOT NULL DEFAULT 0,  -- черновик я скопировал и отправил; нейронка видит только отправленное
    prompt      TEXT NOT NULL DEFAULT '',  -- о чём я просил бота, когда он писал этот черновик
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

MIGRATIONS = [
    ("listings", "max_offer_ron", "REAL"),
    ("messages", "note", "TEXT NOT NULL DEFAULT ''"),
    ("messages", "flags", "TEXT NOT NULL DEFAULT '[]'"),
    ("messages", "sent", "INTEGER NOT NULL DEFAULT 0"),
    ("messages", "prompt", "TEXT NOT NULL DEFAULT ''"),
]

# Как пишут продавцы на OLX; «calculator» в одиночку ловит и калькуляторы, поэтому только в связках
DEFAULT_QUERIES = [
    "unitate pc", "calculator gaming", "pc gaming", "sistem pc", "mini pc", "optiplex",
    "placa video", "procesor", "placa de baza", "kit placa de baza", "memorie ram", "ssd", "hard disk", "sursa pc",
    "lot componente", "laptop",
]

DEFAULT_SETTINGS = {
    "max_price_ron": "1500",
    "collect_times": "09:00, 14:00, 20:00",  # местное время, через запятую
    "llm_min_score": "5",  # ниже — «отсеяно нейронкой», в Telegram не идёт
    "llm_eval_prompt": """Ты — опытный перекупщик б/у компьютерного железа в Румынии. База — Rădăuți (жудец Suceava).
Ничего не ремонтируем и не восстанавливаем. Лот выгоден, если подходит хотя бы под один сценарий:
1) Сборка: из дешёвых рабочих комплектующих собрать готовый ПК и продать дороже суммы деталей
   (офисные Dell OptiPlex, Lenovo ThinkCentre, HP EliteDesk + видеокарта — классика).
2) Перепродажа: продавец отдаёт ниже рынка (не знает цену, срочно, «отдам за невостребованностью») —
   купить и перепродать на OLX как есть.
3) Хоумлаб: забрать себе — серверы, мини-ПК, Xeon, серверная/ECC память, HDD/SSD, NAS, сетевые карты,
   маленькие тихие системы под виртуалки. Тут ценность — дешевизна для себя, даже без маржи на перепродаже.

Интересно: готовые ПК и системные блоки, десктопные процессоры, материнские платы, связки «мать + проц + память»,
оперативка, видеокарты, SSD/HDD, блоки питания, лоты и пачки комплектующих, всё из хоумлаба.
Ноутбуки — почти не занимаюсь: оценивай честно, проходят только 10/10 (явный подарок под перепродажу).

Не интересно (score 0): сумки, рюкзаки, чехлы, калькуляторы, игрушки, мыши, клавиатуры, кабели, зарядки, телефоны,
консоли, мониторы и ТВ, объявления «куплю» и «обмен», магазины с новым товаром по рыночной цене,
ломбарды (amanet) — продают по рынку, запчасти ноутбуков (экраны, клавиатуры, батареи, корпуса, матплаты от ноутов).
Неисправное, «на запчасти», «не проверено» — score 0 на любом языке (pentru piese, defect, za djelove, na części,
for parts): ремонтом не занимаемся. Ретро-коллекционное и лабораторное (регулируемые БП и т.п.) — не выше 3.

score 0–10 — насколько выгодно купить. Прикинь реальную цену б/у в Румынии на OLX (resale_ron),
вычти цену покупки и расходы на дорогу или доставку:
- Rădăuți и до ~30 км (Siret, Vicovu de Sus, Marginea, Sucevița, Milișăuți, Arbore, Dornești, Frătăuți, Volovăț,
  Horodnic, Putna, Solca) — забираю сам, без расходов, +1–2 к оценке. Suceava (~50 км) — почти без штрафа.
- Остальная Румыния — закладывай доставку ~25–35 lei и риск, что приедет не то.
- vinted — город неизвестен, продавец может быть в другой стране ЕС; доставка через Vinted с их комиссией.
Шкала: 10 — ниже рынка в 2+ раза; 7 — хорошая маржа или отличная цена для хоумлаба; 5 — небольшая маржа,
стоит спросить; ≤3 — рынок или дороже. В комментарии к готовому ПК назови сценарий: сборка / перепродажа / хоумлаб.
Подозрительно дешёвая дорогая вещь (свежая RTX за копейки) — снижай: часто мошенники.""",
    "llm_bargain_prompt": "Пиши на румынском, коротко и вежливо, как обычный покупатель с OLX, без смайлов.",
}


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # веб читает, пока сборщик пишет
    conn.executescript(SCHEMA)
    # Колонки, добавленные после первого запуска: CREATE TABLE IF NOT EXISTS их в старую базу не добавит
    for table, col, decl in MIGRATIONS:
        if col not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    # Первый запуск: старые MAX_PRICE_RON / POLL_INTERVAL_MIN из .env становятся начальными значениями
    seeds = {k: os.getenv(k.upper(), v) for k, v in DEFAULT_SETTINGS.items()}
    conn.executemany("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", seeds.items())
    if not conn.execute("SELECT 1 FROM queries").fetchone():
        conn.executemany("INSERT INTO queries (text) VALUES (?)", [(q,) for q in DEFAULT_QUERIES])
    conn.commit()
    return conn


def get_setting(conn: sqlite3.Connection, key: str) -> str:
    return conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()[0]


def parse_times(text: str) -> list[dtime]:
    """«09:00, 14:00» -> [time(9), time(14)]; кривой формат — ValueError."""
    times = sorted(datetime.strptime(t.strip(), "%H:%M").time() for t in text.split(",") if t.strip())
    if not times:
        raise ValueError("нужно хотя бы одно время")
    return times


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
