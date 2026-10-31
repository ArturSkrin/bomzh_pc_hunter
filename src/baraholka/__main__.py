"""Сборщик: раз в N минут обходит все источники, сохраняет новое, прогоняет через фильтр."""

import logging
import time

import httpx

from . import db
from .config import load_settings
from .filters import passes_rules
from .sources import SOURCES

QUERIES = ["placa video", "procesor", "calculator", "laptop", "ssd"]  # TODO: вынести, когда устаканится

log = logging.getLogger("baraholka")


def collect_once(settings, conn) -> int:
    new = 0
    with httpx.Client(timeout=20, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for name, fetch in SOURCES.items():
            for query in QUERIES:
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
                    status = "new" if passes_rules(lot, settings.max_price_ron) else "rejected"
                    conn.execute("UPDATE listings SET status = ? WHERE id = ?", (status, lot_id))
                conn.commit()
                time.sleep(settings.request_delay_sec)
    # TODO: LLM-оценка статуса 'new' и отправка карточек в Telegram
    return new


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    conn = db.connect(settings.db_path)
    while True:
        log.info("Новых объявлений: %d", collect_once(settings, conn))
        time.sleep(settings.poll_interval_min * 60)


if __name__ == "__main__":
    main()
