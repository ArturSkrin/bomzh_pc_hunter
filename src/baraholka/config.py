"""Секреты и пути из .env. Потолок цены, интервал, запросы и промпты — в базе, правятся из веб-морды."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    telegram_chat_id: str
    llm_model: str
    request_delay_sec: float
    db_path: Path
    web_port: int


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        llm_model=os.getenv("LLM_MODEL", "anthropic/claude-haiku-5-5"),
        request_delay_sec=float(os.getenv("REQUEST_DELAY_SEC", "3")),
        db_path=Path(os.getenv("DB_PATH", "data/baraholka.db")),
        web_port=int(os.getenv("WEB_PORT", "8080")),
    )
