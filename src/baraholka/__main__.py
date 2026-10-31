"""Сборщик: раз в N минут обходит все источники, сохраняет новое, прогоняет через фильтр."""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import db, web
from .config import load_settings
from .filters import passes_rules
from .sources import SOURCES
from .tg import Bot

MAX_CARDS_PER_CYCLE = 20  # чтобы первый запуск не завалил чат

log = logging.getLogger("baraholka")


def collect_once(settings, conn) -> int:
    new = 0
    max_price = float(db.get_setting(conn, "max_price_ron"))
    with httpx.Client(timeout=20, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for name, fetch in SOURCES.items():
            for query in db.enabled_queries(conn):
                try:
                    lots = fetch(client, query)
                except Exception:
                    log.exception("%s: %r упал", name, query)
                    continue
                for lot in lots:
                    lot_id = db.insert_if_new(conn, lot)
                    if lot_id is None:
                        continue
                    new += 1
                    status = "new" if passes_rules(lot, max_price) else "rejected"
                    conn.execute("UPDATE listings SET status = ? WHERE id = ?", (status, lot_id))
                conn.commit()
                time.sleep(settings.request_delay_sec)
    # TODO: LLM-оценка статуса 'new' перед отправкой
    return new


def send_new(bot: Bot, conn) -> None:
    day_ago = datetime.now(timezone.utc) - timedelta(days=1)
    sent = 0
    for row in conn.execute("SELECT * FROM listings WHERE status = 'new' ORDER BY id DESC").fetchall():
        if not row["posted_at"] or datetime.fromisoformat(row["posted_at"]) < day_ago:
            status = "stale"  # старьё и поднятые объявления не шлём
        elif sent >= MAX_CARDS_PER_CYCLE:
            continue  # остальные свежие уйдут в следующий проход
        else:
            try:
                bot.send_card(row)
                status, sent = "sent", sent + 1
            except httpx.HTTPError:
                log.exception("Не отправилась карточка %s", row["url"])
                status = "send_failed"
            time.sleep(1)  # лимит Telegram — ~1 сообщение в секунду в чат
        conn.execute("UPDATE listings SET status = ? WHERE id = ?", (status, row["id"]))
        conn.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    conn = db.connect(settings.db_path)
    bot = Bot(settings.telegram_token, settings.telegram_chat_id) if settings.telegram_token else None
    if not bot:
        log.warning("TELEGRAM_BOT_TOKEN не задан — только собираю в базу")
    threading.Thread(target=web.serve, args=(settings,), daemon=True).start()
    log.info("Веб-морда: http://127.0.0.1:%d", settings.web_port)
    while True:
        log.info("Новых объявлений: %d", collect_once(settings, conn))
        if bot:
            send_new(bot, conn)
        deadline = time.monotonic() + int(db.get_setting(conn, "poll_interval_min")) * 60
        while (left := deadline - time.monotonic()) > 0:
            if not bot:
                time.sleep(left)
                continue
            try:
                bot.poll(conn, timeout=int(min(50, max(1, left))))
            except httpx.HTTPError:
                log.exception("Telegram недоступен")
                time.sleep(5)


if __name__ == "__main__":
    main()
