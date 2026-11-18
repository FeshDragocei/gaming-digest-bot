"""Конфигурация бота: читается из файла .env"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


@dataclass
class Config:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    channel_id: str = os.getenv("CHANNEL_ID", "")

    # Как часто проверять новые новости (минуты)
    check_interval_minutes: int = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))
    timezone: str = os.getenv("TIMEZONE", "Europe/Moscow")

    # Сколько часов назад искать новости
    fresh_hours: int = int(os.getenv("FRESH_HOURS", "24"))

    # Файл с историей опубликованных новостей (для защиты от повторов)
    seen_file: Path = BASE_DIR / "data" / "seen_news.json"

    @property
    def channel(self) -> str:
        """Канал как строка: либо @username, либо числовой id."""
        cid = self.channel_id.strip()
        if cid.startswith("@") or cid.startswith("-"):
            return cid
        return cid


CFG = Config()
