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

    # Максимум новостей за одну проверку (если накопилось много)
    news_per_run: int = int(os.getenv("NEWS_PER_RUN", "5"))
    # Не больше N новостей одной темы за одну проверку
    topic_batch_cap: int = int(os.getenv("TOPIC_BATCH_CAP", "3"))
    # Не больше N новостей одной темы за сутки
    topic_daily_cap: int = int(os.getenv("TOPIC_DAILY_CAP", "8"))

    # Минимальная пауза между публикациями (часы) — бот не постит чаще
    publish_every_hours: int = int(os.getenv("PUBLISH_EVERY_HOURS", "3"))

    # Файл с временем последней публикации
    state_file: Path = BASE_DIR / "data" / "state.json"

    # Сколько часов назад искать новости
    fresh_hours: int = int(os.getenv("FRESH_HOURS", "12"))

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
