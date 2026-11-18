"""Публикация дайджеста в Telegram-канал."""
from __future__ import annotations

import asyncio
import logging

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
    """Публикует шапку дайджеста и каждую новость отдельным постом.

    Формат «картинка + небольшой пост»: у каждого фото видна подпись с
    заголовком, кратким описанием и ссылкой. Новости без картинки (или
    с нескачиваемой) уходят текстом, чтобы ничего не потерялось.
    """
    if not items:
        log.info("Дайджест пуст — нечего публиковать")
        return

    # Шапка
    try:
        await bot.send_message(chat_id, digest.header_text(len(items)), parse_mode="HTML")
    except Exception as e:
        log.error("Не удалось отправить шапку в %s: %s", chat_id, e)

    for it in items:
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

    # Помечаем ВСЕ новости выпуска как показанные, чтобы не всплывали в следующем
    mark_published(items)
    log.info("Дайджест опубликован: %d новостей", len(items))


async def make_and_publish(bot: Bot, chat_id: str) -> list[NewsItem]:
    """Полный цикл: собрать -> сформировать -> опубликовать. Возвращает список новостей."""
    items = await digest.build_digest()
    await publish_digest(bot, chat_id, items)
    return items
