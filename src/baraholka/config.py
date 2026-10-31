"""Настройки из .env. Всё, что меняется без правки кода, живёт здесь."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    telegram_chat_id: str
    llm_model: str
    poll_interval_min: int
    request_delay_sec: float
    db_path: Path
    max_price_ron: int


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        llm_model=os.getenv("LLM_MODEL", "anthropic/claude-haiku-5-5"),
        poll_interval_min=int(os.getenv("POLL_INTERVAL_MIN", "12")),
        request_delay_sec=float(os.getenv("REQUEST_DELAY_SEC", "3")),
        db_path=Path(os.getenv("DB_PATH", "data/baraholka.db")),
        max_price_ron=int(os.getenv("MAX_PRICE_RON", "1500")),
    )
