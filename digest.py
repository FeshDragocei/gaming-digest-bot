"""Отбор и подготовка новостей: интересность, дедупликация, пересказ, перевод."""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import html
import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

import aiohttp
from deep_translator import GoogleTranslator

from config import CFG
from news import NewsItem, collect_news, enrich_item

log = logging.getLogger(__name__)

# Минимальная «интересность» для публикации (1 совпадение ключевого слова = 2 балла)
MIN_SCORE = 2
# Лимиты тем и размера партии — в config.py (TOPIC_BATCH_CAP, TOPIC_DAILY_CAP, NEWS_PER_RUN)
SEEN_TTL_DAYS = 7      # сколько дней помнить опубликованные новости
DUP_SIMILARITY = 0.5   # порог похожести заголовков (0..1) — ниже = строже

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
    if not item.orig_title:
        item.orig_title = item.title
    item.title = await _translate(item.title, src="en")
    item.summary = await _translate(item.summary, src="en")


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-zа-яё0-9 ]", "", t.lower()).strip()


def _norm_url(url: str) -> str:
    """Нормализует ссылку: убирает мусорные параметры вроде utm_."""
    url = (url or "").strip().lower().split("#", 1)[0]
    if "?" in url:
        base, query = url.split("?", 1)
        keep = [
            kv for kv in query.split("&")
            if kv and not kv.split("=", 1)[0].startswith(("utm_", "fbclid", "gclid", "yclid"))
        ]
        url = base + ("?" + "&".join(keep) if keep else "")
    return url.rstrip("/")


def _hash(url: str) -> str:
    """Отпечаток новости по ссылке — не меняется от перевода заголовка."""
    return hashlib.md5(_norm_url(url).encode()).hexdigest()[:12]


def _title_hash(title: str) -> str:
    return hashlib.md5(_norm_title(title).encode()).hexdigest()[:12]


# Слова, которые не считаются «темой» новости (слишком общие)
_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "have", "has", "had",
    "are", "was", "were", "will", "would", "its", "his", "her", "their", "our",
    "new", "how", "why", "who", "what", "when", "out", "get", "got", "top",
    "best", "first", "after", "over", "into", "about", "says", "said", "here",
    "all", "more", "most", "than", "then", "now", "day", "year", "years",
    "game", "games", "gaming", "video", "off", "per", "via", "one", "two",
    "you", "your", "they", "them", "she", "him", "not", "but", "yet", "can",
    "для", "как", "что", "все", "это", "еще", "уже", "при", "его", "ее", "их",
    "или", "так", "там", "чем", "нее", "ней", "они", "оно", "мой", "свой",
    "новый", "новая", "новое", "под", "над", "про", "сам", "два", "три",
    "игра", "игры", "игре", "игр", "игровой", "новости", "новость",
}


def _topic_tokens(title: str) -> set[str]:
    """«Тема» новости — значимые слова заголовка (для лимита на тему)."""
    text = (title or "").lower().replace("grand theft auto", "gta")
    words = re.findall(r"[a-zа-яё0-9]+", text)
    return {w for w in words if len(w) >= 3 and w not in _STOPWORDS}


# Слова-признаки игровой тематики: без хотя бы одного из них новость не публикуется
GAMING_WORDS = [
    # универсальные
    "игр", "гейм", "геймер", "трейлер", "геймплей", "релиз", "анонс", "патч",
    "рогалик", "шутер", "стратег", "rpg", "ммо", "mmo", "mmorpg",
    "dlc", "модиф", "моддинг", "mods", "modding", "early access", "демоверс",
    "инди", "киберспорт", "esports", "спидран", "стример", "twitch",
    "ремейк", "ремастер", "сиквел", "персонаж", "прохожден", "гайд",
    "steam", "playstation", "плейстейшн", "ps5", "ps4", "psn",
    "xbox", "нинтенд", "nintendo", "switch", "консол", "геймпад", "gog",
    "gamescom", "game awards", "tga", "handheld", "controller", "console",
    "beta", "demo", "speedrun", "indie", "open world", "boss", "quest", "loot",
    # названия игр и серий
    "gta", "wow", "pokemon", "покемон", "zelda", "зельда", "mario", "марио",
    "minecraft", "майнкрафт", "fortnite", "фортнайт", "valorant", "валорант",
    "dota", "дота", "counter-strike", "cs2", "baldur", "балдур", "hogwarts",
    "хогвартс", "skyrim", "скайрим", "fallout", "фоллаут", "roblox", "роблокс",
    "genshin", "геншин", "witcher", "ведьмак", "overwatch", "elden",
    "cyberpunk", "сайкерпанк", "assassin", "resident evil", "hearthstone",
    "atelier", "monopoly", "europa universalis", "lego",
]


def is_gaming(item: NewsItem) -> bool:
    """True, если новость про игры (по словам в заголовке и описании)."""
    text = f"{item.title} {item.summary}".lower()
    return any(re.search(r"\b" + re.escape(w), text) for w in GAMING_WORDS)


def _unique_values(seen: dict[str, dict]) -> list[dict]:
    """История хранит по два ключа на новость (ссылка + заголовок) — убираем двойников."""
    uniq: list[dict] = []
    seen_keys: set = set()
    for v in seen.values():
        key = (v.get("ts"), v.get("t"))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        uniq.append(v)
    return uniq


def score_item(item: NewsItem) -> int:
    text = f"{item.title} {item.summary}".lower()
    words = SCORE_WORDS.get("ru", []) + SCORE_WORDS.get("en", [])
    return sum(2 for w in words if w in text)


# ---------------------------------------------------------------- дедупликация

def _load_seen() -> dict[str, dict]:
    """История опубликованного: hash -> {ts, t} (t — нормализованный заголовок).

    Поддерживаем и старый формат (hash -> строка-дата), чтобы ничего не потерять.
    """
    try:
        CFG.seen_file.parent.mkdir(parents=True, exist_ok=True)
        raw = json.loads(CFG.seen_file.read_text(encoding="utf-8"))
        return {
            k: v if isinstance(v, dict) else {"ts": None, "t": ""}
            for k, v in raw.items()
        }
    except Exception:
        return {}


def _save_seen(seen: dict[str, dict]) -> None:
    """Сохраняет историю, выкидывая записи старше SEEN_TTL_DAYS."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=SEEN_TTL_DAYS)
    fresh: dict[str, dict] = {}
    for h, v in seen.items():
        ts = v.get("ts")
        try:
            keep = ts is None or datetime.fromisoformat(ts) >= cutoff
        except ValueError:
            keep = False
        if keep:
            fresh[h] = v
    try:
        CFG.seen_file.parent.mkdir(parents=True, exist_ok=True)
        CFG.seen_file.write_text(json.dumps(fresh, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log.warning("Не удалось сохранить историю: %s", e)


def mark_published(items: list[NewsItem]) -> None:
    seen = _load_seen()
    now = datetime.now(tz=timezone.utc).isoformat()
    for it in items:
        value = {"ts": now, "t": _norm_title(it.orig_title or it.title)}
        # отпечаток и по ссылке, и по исходному заголовку — двойная защита
        seen[_hash(it.url)] = value
        seen[_title_hash(it.orig_title or it.title)] = value
    _save_seen(seen)


def _similar(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, a, b).ratio() > DUP_SIMILARITY


def _drop_seen_and_dupes(items: list[NewsItem], seen: dict[str, dict]) -> list[NewsItem]:
    """Убирает уже публиковавшиеся и очень похожие новости (в т.ч. с прошлых
    выпусков — та же история с другого сайта в другой формулировке)."""
    seen_titles = list({v.get("t") for v in seen.values() if v.get("t")})
    result: list[NewsItem] = []
    kept_titles: list[str] = []
    for it in items:
        norm = _norm_title(it.orig_title or it.title)
        if _hash(it.url) in seen or _title_hash(norm) in seen:
            continue
        if any(_similar(norm, other) for other in seen_titles):
            continue  # похожее уже выходило в канал
        if any(_similar(norm, kept) for kept in kept_titles):
            continue  # дубль внутри текущей выборки
        kept_titles.append(norm)
        result.append(it)
    return result


# ---------------------------------------------------------------- отбор

async def _prepare_item(item: NewsItem) -> None:
    """Обогащает новость перед публикацией: пересказ из статьи, картинка, перевод."""
    await enrich_item(item)       # пересказ и картинка со страницы статьи
    await _translate_item(item)   # перевод зарубежных новостей


def _select_candidates(candidates: list[NewsItem], seen: dict[str, dict]) -> list[NewsItem]:
    """Выбирает новости для публикации с лимитами по темам.

    Правила:
    - максимум CFG.news_per_run новостей за проверку;
    - не больше CFG.topic_batch_cap новостей одной темы за проверку;
    - не больше CFG.topic_daily_cap новостей одной темы за сутки;
    - если других тем среди кандидатов нет вовсе, партия добирается
      лучшими из отсеянных по лимиту проверки — канал не должен пустовать.
    """
    now_utc = datetime.now(tz=timezone.utc)
    day_counter: Counter[str] = Counter()
    for v in _unique_values(seen):
        try:
            ts = datetime.fromisoformat(v["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if v.get("t") and now_utc - ts <= timedelta(hours=24):
            day_counter.update(_topic_tokens(v["t"]))

    batch_counter: Counter[str] = Counter()
    chosen: list[NewsItem] = []
    capped_out: list[NewsItem] = []  # отсеяны лимитом «N новостей темы на проверку»
    for it in candidates:
        toks = _topic_tokens(it.orig_title or it.title)
        if any(day_counter[t] + batch_counter[t] >= CFG.topic_daily_cap for t in toks):
            continue  # суточный лимит темы исчерпан
        if any(batch_counter[t] >= CFG.topic_batch_cap for t in toks):
            capped_out.append(it)
            continue
        batch_counter.update(toks)
        chosen.append(it)
        if len(chosen) >= CFG.news_per_run:
            break

    # Исключение: если ни одной новости других тем нет вовсе — добираем из отсеянных
    if len(chosen) < CFG.news_per_run and capped_out:
        capped_tokens: set[str] = set()
        for it in capped_out:
            capped_tokens |= _topic_tokens(it.orig_title or it.title)
        has_other = any(
            _topic_tokens(it.orig_title or it.title) - capped_tokens for it in candidates
        )
        if not has_other:
            for it in capped_out:
                batch_counter.update(_topic_tokens(it.orig_title or it.title))
                chosen.append(it)
                if len(chosen) >= CFG.news_per_run:
                    break

    return chosen


async def build_digest(fresh_hours: int | None = None) -> list[NewsItem]:
    """
    Отбирает новости для публикации: минимум одну (если есть что публиковать),
    до NEWS_PER_RUN — если накопилось много. Следит за разнообразием тем.
    """
    hours = fresh_hours if fresh_hours is not None else CFG.fresh_hours
    items = await collect_news(fresh_hours=hours)

    # только игровые новости
    before = len(items)
    items = [it for it in items if is_gaming(it)]
    log.info("Игровой фильтр: %d из %d новостей прошли", len(items), before)

    seen = _load_seen()
    items = _drop_seen_and_dupes(items, seen)

    for it in items:
        it.score = score_item(it)
    # Сначала самые свежие (канал должен следить за потоком), интересность — как тайбрейк
    candidates = sorted(
        (it for it in items if it.score >= MIN_SCORE),
        key=lambda i: (i.published or datetime.now(tz=timezone.utc), i.score),
        reverse=True,
    )
    if not candidates:
        log.info("Интересных новых новостей не нашлось (%d всего было)", len(items))
        return []

    chosen = _select_candidates(candidates, seen)
    if not chosen:
        log.info("Все кандидаты отсеялись лимитами тем (%d кандидатов)", len(candidates))
        return []

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
