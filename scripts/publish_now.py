"""
Разовая публикация дайджеста прямо сейчас, не дожидаясь расписания.

Запуск из папки проекта:  python scripts/publish_now.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from aiogram import Bot  # noqa: E402

from config import CFG  # noqa: E402
from digest import close_session  # noqa: E402
from publish import make_and_publish  # noqa: E402


async def main():
    if not CFG.bot_token:
        print("❌ Нет BOT_TOKEN в .env")
        sys.exit(1)
    if not CFG.channel:
        print("❌ Нет CHANNEL_ID в .env")
        sys.exit(1)

    bot = Bot(CFG.bot_token)
    try:
        me = await bot.get_me()
        print(f"✅ Бот подключён: @{me.username}")
        items = await make_and_publish(bot, CFG.channel)
        print(f"✅ Дайджест опубликован в канал: {len(items)} новостей")
    finally:
        await bot.session.close()
        await close_session()


if __name__ == "__main__":
    asyncio.run(main())
