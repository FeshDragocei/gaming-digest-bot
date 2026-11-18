"""
Телеграм-бот «Игровой дайджест».

Собирает новости с игровых сайтов (RSS), отбирает самое интересное,
переводит зарубежные статьи и публикует дайджест в канал дважды в день.

Запуск:  python bot.py
"""
import asyncio
import logging
import sys
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

# Windows-консоль часто в cp1251 — русские логи могут превращаться в кракозябры
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from config import CFG  # noqa: E402
from digest import close_session  # noqa: E402
from publish import make_and_publish  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bot")

router = Router()


# ----------------------------------------------------------- команды

@router.message(CommandStart())
async def cmd_start(m: Message):
    await m.answer(
        "🎮 <b>Игровые новости</b>\n\n"
        "Я слежу за игровыми сайтами и публикую в канал самые интересные "
        "новости по мере их появления: релизы, анонсы, события индустрии, "
        "необычные факты, скидки и раздачи.\n\n"
        "Команды:\n"
        "/digest — проверить и опубликовать следующую новость\n"
        "/chatid — узнать ID этого чата/канала\n"
        "/help — помощь"
    )


@router.message(Command("help"))
async def cmd_help(m: Message):
    await cmd_start(m)


@router.message(Command("digest"))
async def cmd_digest(m: Message):
    target = CFG.channel or str(m.chat.id)
    await m.answer(f"⏳ Ищу интересную новость и публикую в {target}…")
    try:
        items = await make_and_publish(m.bot, target)
        if items:
            await m.answer(f"✅ Готово! Опубликовано новостей: {len(items)}")
        else:
            await m.answer("📭 Свежих новостей не нашлось — попробуйте позже.")
    except Exception as e:
        log.exception("Ручной дайджест упал")
        await m.answer(f"⚠️ Ошибка: {e}")


@router.message(Command("chatid"))
async def cmd_chatid(m: Message):
    await m.answer(f"ID этого чата: <code>{m.chat.id}</code>\nВпишите его в .env → CHANNEL_ID")


@router.channel_post(Command("chatid"))
async def channel_chatid(m: Message):
    """Тот же /chatid, отправленный прямо в канал (бот должен быть админом)."""
    await m.answer(f"ID канала: <code>{m.chat.id}</code>\nВпишите его в .env → CHANNEL_ID")


# ----------------------------------------------------------- фоновые задачи

async def digest_job(bot: Bot):
    """Проверка новостей по расписанию: публикуем следующую интересную новость."""
    try:
        items = await make_and_publish(bot, CFG.channel)
        log.info("Проверка завершена: опубликовано %d новостей", len(items))
    except Exception:
        log.exception("Фоновая публикация упала")


# ----------------------------------------------------------- запуск

async def main():
    if not CFG.bot_token:
        print(
            "\n❌ Не задан токен бота!\n\n"
            "1) Получите токен у @BotFather (инструкция в README.md)\n"
            "2) Скопируйте .env.example в .env и впишите BOT_TOKEN и CHANNEL_ID\n"
        )
        sys.exit(1)

    if not CFG.channel:
        print("⚠️  CHANNEL_ID не задан — /digest будет постить в текущий чат.\n")

    bot = Bot(CFG.bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    tz = ZoneInfo(CFG.timezone)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(
        digest_job,
        IntervalTrigger(minutes=CFG.check_interval_minutes),
        args=[bot],
    )
    log.info(
        "Проверка новостей каждые %d мин (%s)", CFG.check_interval_minutes, CFG.timezone
    )
    scheduler.start()

    log.info("Бот запущен. Ожидаю обновления…")
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await close_session()


if __name__ == "__main__":
    asyncio.run(main())
