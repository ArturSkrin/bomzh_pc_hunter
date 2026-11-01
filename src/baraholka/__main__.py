"""Сборщик: раз в N минут обходит все источники, сохраняет новое, прогоняет через фильтр."""

import logging
import random
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import agent, db, web
from .config import load_settings
from .filters import passes_rules
from .sources import SOURCES
from .tg import Bot

MAX_CARDS_PER_CYCLE = 20  # чтобы первый запуск не завалил чат
JITTER_MIN = 10  # каждое время сбора ± столько минут случайно — без ровного ритма

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
    return new


def evaluate(settings, conn) -> None:
    try:
        n = agent.evaluate_new(conn, settings.llm_model, db.get_setting(conn, "llm_eval_prompt"),
                               float(db.get_setting(conn, "llm_min_score")))
        log.info("Оценено нейронкой: %d", n)
    except Exception:
        log.exception("Оценка нейронкой упала — лоты подождут следующего сбора")


def send_new(bot: Bot, conn) -> None:
    day_ago = datetime.now(timezone.utc) - timedelta(days=1)
    sent = 0
    for row in conn.execute("SELECT * FROM listings WHERE status = 'scored' ORDER BY llm_score DESC, id DESC").fetchall():
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


def next_run(times, now: datetime) -> datetime:
    """Ближайшее время сбора после now со случайным сдвигом; сегодня всё прошло — первое завтрашнее."""
    for day in (0, 1):
        for t in times:
            at = datetime.combine(now.date() + timedelta(days=day), t)
            at += timedelta(minutes=random.uniform(-JITTER_MIN, JITTER_MIN))
            if at > now:
                return at
    raise AssertionError("недостижимо: завтрашние времена всегда позже now")


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
        evaluate(settings, conn)
        if bot:
            send_new(bot, conn)
        at = next_run(db.parse_times(db.get_setting(conn, "collect_times")), datetime.now())
        log.info("Следующий сбор: %s", at.strftime("%d.%m %H:%M"))
        while (left := (at - datetime.now()).total_seconds()) > 0:
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
