"""Конфигурация бота: читается из файла .env"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


def _parse_times(raw: str) -> list[tuple[int, int]]:
    """'10:00,19:30' -> [(10, 0), (19, 30)]"""
    result = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        h, m = part.split(":")
        result.append((int(h), int(m)))
    return result or [(10, 0), (19, 0)]


@dataclass
class Config:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    channel_id: str = os.getenv("CHANNEL_ID", "")

    digest_times: list[tuple[int, int]] = field(
        default_factory=lambda: _parse_times(os.getenv("DIGEST_TIMES", "10:00,19:00"))
    )
    timezone: str = os.getenv("TIMEZONE", "Europe/Moscow")

    news_per_digest: int = int(os.getenv("NEWS_PER_DIGEST", "5"))
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
