"""Отбор и подготовка новостей: интересность, дедупликация, пересказ, перевод."""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import html
import json
import logging
import re
from datetime import datetime, timezone

import aiohttp
from deep_translator import GoogleTranslator

from config import CFG
from news import NewsItem, collect_news, enrich_item

log = logging.getLogger(__name__)

# Минимальная «интересность» для публикации (1 совпадение ключевого слова = 2 балла)
MIN_SCORE = 2
# Если столько новостей ждёт публикации — выпускаем за раз две, чтобы разгрести очередь
BACKLOG_BATCH_AT = 10

# Ключевые слова, повышающие интересность новости: релизы, анонсы, события
# индустрии, необычные факты, а также скидки, распродажи и раздачи
SCORE_WORDS = {
    "ru": [
        "релиз", "вышла", "вышел", "выходит", "анонс", "анонсирован", "представлен",
        "трейлер", "геймплей", "обзор", "продаж", "рекорд", "миллион", "миллиард",
        "банкрот", "закрыл", "закрыт", "уволен", "уволил", "сокращ", "ремейк",
        "ремастер", "перезапуск", "объяви", "показ", "игра года", "наград", "суд",
        "иск ", "скандал", "дата выхода", "обновлен", "патч", "переиздан", "сиквел",
        "первый", "подтверд", "отмен", "перенес", "задерж", "слух", "утеч", "взлом",
        "рейтинг", "метакритик", "интервью", "игрок", "сделк", "слиян", "поглощ",
        "купил", "прода", "штраф",
        # скидки и раздачи
        "скидк", "распрода", "дешев", "бесплат", "разда", "подар", "цен", "хайвл",
        "разыгр",
    ],
    "en": [
        "release", "released", "launch", "announc", "reveal", "trailer", "gameplay",
        "review", "sales", "record", "million", "billion", "bankrupt", "shut down",
        "layoff", "remake", "remaster", "reboot", "unveil", "showcase", "award",
        "lawsuit", "controversy", "release date", "update", "patch", "sequel",
        "first look", "hands-on", "demo", "surprise", "acquire", "acquisition",
        "confirmed", "cancelled", "canceled", "delayed", "postponed", "rumor",
        "rumour", "leak", "hack", "rating", "metacritic", "interview", "players",
        # скидки и раздачи
        "discount", "sale", "deal", "free", "giveaway", "price", "cheap",
        "bargain", "claim", "redeem",
    ],
}

_tl = GoogleTranslator(source="auto", target="ru")
_my = None  # MyMemoryTranslator создаётся лениво (запасной вариант)
_translate_cache: dict[str, str] = {}
_tl_lock = asyncio.Lock()  # бесплатные переводчики не любят параллельные запросы

_URL_CLIENTS5 = "https://clients5.google.com/translate_a/t"
_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def _clients5_translate(text: str, src: str) -> str:
    """Перевод через эндпоинт Google, который использует встроенный переводчик Chrome."""
    params = {"client": "dict-chrome-ex", "sl": src, "tl": "ru", "q": text}
    async with _get_session().get(
        _URL_CLIENTS5, params=params, timeout=aiohttp.ClientTimeout(total=15)
    ) as r:
        data = await r.json(content_type=None)

    if isinstance(data, dict):  # запасной формат ответа
        sentences = data.get("sentences") or []
        return "".join(s.get("trans", "") for s in sentences if isinstance(s, dict)).strip()

    parts: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, list) and item and isinstance(item[0], str):
                parts.append(item[0])
    return "".join(parts).strip()


def _google_deep_translate(text: str) -> str:
    return _tl.translate(text)


def _mymemory_translate(text: str) -> str:
    global _my
    if _my is None:
        from deep_translator import MyMemoryTranslator
        _my = MyMemoryTranslator(source="en-US", target="ru-RU")
    return _my.translate(text)


async def close_session() -> None:
    """Корректно закрыть HTTP-сессию переводчика (вызывать при завершении бота)."""
    global _session
    if _session and not _session.closed:
        await _session.close()
    _session = None


async def _translate(text: str, src: str = "en") -> str:
    """
    Перевод на русский с кэшем. Цепочка: clients5.google.com ->
    deep-translator (Google) -> MyMemory. Если всё не удалось,
    возвращаем исходный текст, чтобы пост не сорвался.
    """
    if not text:
        return text
    if text in _translate_cache:
        return _translate_cache[text]

    async with _tl_lock:
        # 1) основной бесплатный эндпоинт с повторами
        for attempt in range(1, 4):
            try:
                translated = await _clients5_translate(text, src)
                if translated:
                    _translate_cache[text] = translated
                    await asyncio.sleep(0.3)
                    return translated
            except Exception as e:
                log.warning("Перевод #1 не удался (попытка %d/3, %s…): %s", attempt, text[:40], e)
                await asyncio.sleep(1.5 * attempt)

        # 2) запасные переводчики
        for name, backend in (("Google", _google_deep_translate), ("MyMemory", _mymemory_translate)):
            try:
                translated = await asyncio.to_thread(backend, text)
                if translated:
                    log.info("Перевод через запасной сервис: %s", name)
                    _translate_cache[text] = translated
                    return translated
            except Exception as e:
                log.warning("Запасной перевод %s не удался: %s", name, e)

    log.warning("Перевод полностью не удался — публикую исходный текст")
    return text


async def _translate_item(item: NewsItem) -> None:
    if item.lang != "en":
        return
    item.title = await _translate(item.title, src="en")
    item.summary = await _translate(item.summary, src="en")


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-zа-яё0-9 ]", "", t.lower()).strip()


def _hash(title: str, url: str) -> str:
    return hashlib.md5(_norm_title(title).encode()).hexdigest()[:12]


def score_item(item: NewsItem) -> int:
    text = f"{item.title} {item.summary}".lower()
    words = SCORE_WORDS.get("ru", []) + SCORE_WORDS.get("en", [])
    return sum(2 for w in words if w in text)


# ---------------------------------------------------------------- дедупликация

def _load_seen() -> dict[str, str]:
    try:
        CFG.seen_file.parent.mkdir(parents=True, exist_ok=True)
        return json.loads(CFG.seen_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_seen(seen: dict[str, str]) -> None:
    try:
        CFG.seen_file.parent.mkdir(parents=True, exist_ok=True)
        CFG.seen_file.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log.warning("Не удалось сохранить историю: %s", e)


def mark_published(items: list[NewsItem]) -> None:
    seen = _load_seen()
    now = datetime.now(tz=timezone.utc).isoformat()
    for it in items:
        seen[_hash(it.title, it.url)] = now
    _save_seen(seen)


def _drop_seen_and_dupes(items: list[NewsItem], seen: dict[str, str]) -> list[NewsItem]:
    """Убирает уже публиковавшиеся и очень похожие друг на друга новости."""
    result: list[NewsItem] = []
    kept_titles: list[str] = []
    for it in items:
        h = _hash(it.title, it.url)
        if h in seen:
            continue
        norm = _norm_title(it.title)
        if any(difflib.SequenceMatcher(None, norm, kept).ratio() > 0.7 for kept in kept_titles):
            continue  # одна и та же новость на разных сайтах
        kept_titles.append(norm)
        result.append(it)
    return result


# ---------------------------------------------------------------- отбор

async def _prepare_item(item: NewsItem) -> None:
    """Обогащает новость перед публикацией: пересказ из статьи, картинка, перевод."""
    await enrich_item(item)       # пересказ и картинка со страницы статьи
    await _translate_item(item)   # перевод зарубежных новостей


async def build_digest(fresh_hours: int | None = None) -> list[NewsItem]:
    """
    Отбирает ОДНУ самую интересную новую новость (или две, если накопилась
    большая очередь) и готовит её к публикации: пересказ, картинка, перевод.
    Остальные новости остаются на следующие проверки — так публикации
    выходят постепенно, по мере поступления.
    """
    hours = fresh_hours if fresh_hours is not None else CFG.fresh_hours
    items = await collect_news(fresh_hours=hours)

    seen = _load_seen()
    items = _drop_seen_and_dupes(items, seen)

    for it in items:
        it.score = score_item(it)
    candidates = sorted(
        (it for it in items if it.score >= MIN_SCORE),
        key=lambda i: (i.score, i.published or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )
    if not candidates:
        log.info("Интересных новых новостей не нашлось (%d всего было)", len(items))
        return []

    batch = 2 if len(candidates) >= BACKLOG_BATCH_AT else 1
    chosen = candidates[:batch]
    await asyncio.gather(*[_prepare_item(it) for it in chosen])
    return chosen


def caption_for(item: NewsItem) -> str:
    """HTML-подпись к одной новости (лимит Telegram для фото — 1024 символа)."""
    title = html.escape(item.title)
    source_line = f"📰 Источник: {html.escape(item.source)}"
    link_line = f"🔗 <a href=\"{item.url}\">Читать полностью</a>"

    summary = html.escape(item.summary) if item.summary else ""
    if summary:
        # Резервируем место под заголовок, источник, ссылку и разметку
        fixed_len = len(title) + len(source_line) + len(link_line) + 30
        room = max(0, 1024 - fixed_len)
        if len(summary) > room:
            if room > 40:
                summary = summary[:room].rsplit(" ", 1)[0] + "…"
            else:
                summary = ""  # места нет — описание опускаем

    parts = [f"🎮 <b>{title}</b>"]
    if summary:
        parts.append(summary)
    parts.append(source_line)
    parts.append(link_line)
    return "\n\n".join(parts)
