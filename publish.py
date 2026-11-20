"""Публикация новостей в Telegram-канал."""
from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timezone

import aiohttp
from aiogram import Bot
from aiogram.types import BufferedInputFile

import digest
from digest import mark_published
from news import NewsItem

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# ------------------------------------------------- пауза между публикациями

def _hours_since_last_publish() -> float | None:
    """Сколько часов прошло с последней публикации (None — никогда)."""
    try:
        state = json.loads(CFG.state_file.read_text(encoding="utf-8"))
        last = datetime.fromisoformat(state["last_publish"])
        return (datetime.now(timezone.utc) - last).total_seconds() / 3600
    except Exception:
        return None


def _save_last_publish_time() -> None:
    """Запоминает время публикации (файл коммитится workflow'ом)."""
    try:
        CFG.state_file.parent.mkdir(parents=True, exist_ok=True)
        CFG.state_file.write_text(
            json.dumps(
                {"last_publish": datetime.now(timezone.utc).isoformat()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning("Не удалось сохранить время публикации: %s", e)


async def _download_image(url: str) -> bytes | None:
    """Скачивает картинку в память (на случай, если Telegram не примет ссылку)."""
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    if len(data) > 64:  # не пустышка
                        return data
    except Exception as e:
        log.debug("Картинка %s не скачалась: %s", url[:80], e)
    return None


async def _send_one_with_photo(bot: Bot, chat_id: str, item: NewsItem) -> bool:
    """Пытается отправить новость с фото; возвращает успех."""
    caption = digest.caption_for(item)
    assert item.image
    try:
        await bot.send_photo(chat_id, photo=item.image, caption=caption, parse_mode="HTML")
        return True
    except Exception:
        pass
    # Telegram иногда отказывается качать ссылку сам — качаем и шлём файлом
    data = await _download_image(item.image)
    if data:
        try:
            await bot.send_photo(chat_id, photo=BufferedInputFile(data, "news.jpg"),
                                 caption=caption, parse_mode="HTML")
            return True
        except Exception as e:
            log.debug("Отправка фото файлом не удалась: %s", e)
    return False


async def publish_digest(bot: Bot, chat_id: str, items: list[NewsItem]) -> None:
    """Публикует отобранные новости: каждая — отдельный пост «фото + пересказ».

    Новости без картинки (или с нескачиваемой) уходят текстом, чтобы
    ничего не потерялось.
    """
    if not items:
        log.info("Нечего публиковать")
        return

    for idx, it in enumerate(items):
        sent = False
        if it.image:
            sent = await _send_one_with_photo(bot, chat_id, it)
        if not sent:
            try:
                await bot.send_message(chat_id, digest.caption_for(it), parse_mode="HTML")
                sent = True
            except Exception as e:
                log.warning("Текстовая новость не отправилась: %s", e)
        if not sent:
            log.error("Новость не опубликована ни фото, ни текстом: %s", it.title[:60])

        # Пауза 3–5 минут между постами, чтобы лента не выглядела потоком
        if idx < len(items) - 1:
            pause = random.uniform(180, 300)
            log.info("Пауза %.0f сек перед следующей новостью", pause)
            await asyncio.sleep(pause)

    # Помечаем опубликованное, чтобы новости не всплывали повторно
    mark_published(items)
    log.info("Опубликовано новостей: %d", len(items))


async def make_and_publish(bot: Bot, chat_id: str, force: bool = False) -> list[NewsItem]:
    """
    Полный цикл: собрать -> сформировать -> опубликовать. Возвращает список новостей.

    force=True обходит паузу PUBLISH_EVERY_HOURS (для команды /digest).
    """
    if not force:
        elapsed = _hours_since_last_publish()
        if elapsed is not None and elapsed < CFG.publish_every_hours:
            log.info(
                "С последней публикации прошло %.1f ч (< %d ч) — пропускаю проверку",
                elapsed, CFG.publish_every_hours,
            )
            return []
    items = await digest.build_digest()
    if items:
        _save_last_publish_time()
    await publish_digest(bot, chat_id, items)
    return items
