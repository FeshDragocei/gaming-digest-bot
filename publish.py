"""Публикация дайджеста в Telegram-канал."""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiogram import Bot
from aiogram.types import BufferedInputFile, InputMediaPhoto

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


async def _send_album(bot: Bot, chat_id: str, items: list[NewsItem]) -> bool:
    """Отправляет альбом (до 10 фото) с индивидуальными подписями."""
    media = [
        InputMediaPhoto(media=it.image, caption=digest.caption_for(it), parse_mode="HTML")
        for it in items
    ]
    try:
        await bot.send_media_group(chat_id, media=media)
        return True
    except Exception as e:
        log.warning("Альбом не отправился (%s), пробую по одной", e)
        return False


async def publish_digest(bot: Bot, chat_id: str, items: list[NewsItem]) -> None:
    """Публикует шапку дайджеста и все новости. Пропущенные помечает всё равно."""
    if not items:
        log.info("Дайджест пуст — нечего публиковать")
        return

    # Шапка
    try:
        await bot.send_message(chat_id, digest.header_text(len(items)), parse_mode="HTML")
    except Exception as e:
        log.error("Не удалось отправить шапку в %s: %s", chat_id, e)

    with_photo = [it for it in items if it.image]
    without_photo = [it for it in items if not it.image]

    # Альбом, если новостей с фото несколько (иначе — по одной)
    sent_ok: set[int] = set()
    failed_photo: list[NewsItem] = []
    if len(with_photo) >= 3:
        if await _send_album(bot, chat_id, with_photo[:10]):
            sent_ok.update(id(it) for it in with_photo[:10])
        else:
            for it in with_photo[:10]:
                if await _send_one_with_photo(bot, chat_id, it):
                    sent_ok.add(id(it))
                else:
                    failed_photo.append(it)
    else:
        for it in with_photo:
            if await _send_one_with_photo(bot, chat_id, it):
                sent_ok.add(id(it))
            else:
                failed_photo.append(it)

    # Новости без фото + те, чьё фото не отправилось ни одним способом — текстом
    for it in failed_photo + without_photo:
        try:
            await bot.send_message(chat_id, digest.caption_for(it), parse_mode="HTML")
            sent_ok.add(id(it))
        except Exception as e:
            log.warning("Текстовая новость не отправилась: %s", e)

    # Помечаем ВСЕ новости выпуска как показанные, чтобы не всплывали в следующем
    mark_published(items)
    log.info("Дайджест опубликован: %d новостей", len(items))


async def make_and_publish(bot: Bot, chat_id: str) -> list[NewsItem]:
    """Полный цикл: собрать -> сформировать -> опубликовать. Возвращает список новостей."""
    items = await digest.build_digest()
    await publish_digest(bot, chat_id, items)
    return items
