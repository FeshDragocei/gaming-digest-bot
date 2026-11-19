"""Сбор новостей: загрузка RSS-лент, извлечение заголовков, описаний и картинок."""
from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import mktime

import aiohttp
import feedparser

from sources import SOURCES

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

TAG_RE = re.compile(r"<[^>]+>")
SPACES_RE = re.compile(r"\s+")
SCRIPT_RE = re.compile(r"<(script|style|noscript|svg|form)[^>]*>.*?</\1>", re.S | re.I)
P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S | re.I)
JUNK_RE = re.compile(r"cookie|куки|подписыва|реклам|18\+|sign in|subscribe", re.I)
OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
OG_IMAGE_RE2 = re.compile(
    r'content=["\']([^"\']+\.(?:jpg|jpeg|png|webp)[^"\']*)["\'][^>]+(?:property|name)=["\']og:image["\']',
    re.IGNORECASE,
)


@dataclass
class NewsItem:
    title: str
    url: str
    summary: str = ""
    image: str | None = None
    source: str = ""
    lang: str = "ru"
    published: datetime | None = None
    score: int = 0
    orig_title: str = ""  # исходный заголовок до перевода (для защиты от повторов)


def _clean(text: str, limit: int = 500) -> str:
    """Убрать HTML-теги и лишние пробелы из текста."""
    text = html.unescape(text or "")
    text = TAG_RE.sub(" ", text)
    text = SPACES_RE.sub(" ", text).strip()
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0]
        text = cut + "…"
    return text


def _extract_image(entry, page_html: str = "") -> str | None:
    """Пытаемся достать картинку: из ленты, затем из HTML статьи (og:image)."""
    candidates: list[str] = []

    for key in ("media_thumbnail", "media_content"):
        media = getattr(entry, key, None) or []
        for m in media:
            url = m.get("url")
            if url:
                candidates.append(url)

    for enc in getattr(entry, "enclosures", None) or []:
        href = enc.get("href")
        etype = (enc.get("type") or "").lower()
        if href and ("image" in etype or href.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))):
            candidates.append(href)

    summary = getattr(entry, "summary", "") or ""
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary)
    if m:
        candidates.append(m.group(1))

    if page_html:
        m = OG_IMAGE_RE.search(page_html) or OG_IMAGE_RE2.search(page_html)
        if m:
            candidates.append(m.group(1))

    for url in candidates:
        url = html.unescape(url).strip()
        if url.startswith("http") and not re.search(r"\.(svg|gif)(\?|$)", url, re.I):
            return url
    return None


def _parse_entry(entry, source: dict) -> NewsItem:
    published = None
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if struct:
        published = datetime.fromtimestamp(mktime(struct), tz=timezone.utc)

    return NewsItem(
        title=_clean(entry.get("title", ""), 200),
        orig_title=_clean(entry.get("title", ""), 200),
        url=(entry.get("link") or "").strip(),
        summary=_clean(entry.get("summary", "")),
        image=_extract_image(entry),
        source=source["name"],
        lang=source["lang"],
        published=published,
    )


async def _fetch_source(session: aiohttp.ClientSession, source: dict) -> list[NewsItem]:
    """Загружает одну RSS-ленту и возвращает список новостей."""
    try:
        async with session.get(source["url"], timeout=aiohttp.ClientTimeout(total=25)) as resp:
            if resp.status != 200:
                log.warning("Лента %s: HTTP %s", source["name"], resp.status)
                return []
            raw = await resp.read()
    except Exception as e:
        log.warning("Лента %s недоступна: %s", source["name"], e)
        return []

    feed = feedparser.parse(raw)
    items: list[NewsItem] = []
    for entry in feed.entries[:30]:
        try:
            item = _parse_entry(entry, source)
            if item.title and item.url:
                items.append(item)
        except Exception as e:
            log.debug("Пропуск записи из %s: %s", source["name"], e)
    log.info("Лента %s: %d новостей", source["name"], len(items))
    return items


async def _fetch_page_head(session: aiohttp.ClientSession, url: str) -> str:
    """Скачивает HTML страницы статьи (для og:image и пересказа)."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return ""
            data = await resp.content.read(350_000)
            return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _extract_article_text(page_html: str, limit: int = 600) -> str:
    """Достаёт начало текста статьи (первые абзацы) для пересказа."""
    cleaned = SCRIPT_RE.sub(" ", page_html)
    paragraphs: list[str] = []
    total = 0
    for m in P_RE.finditer(cleaned):
        text = _clean(m.group(1), 400)
        if len(text) < 50 or JUNK_RE.search(text):
            continue
        paragraphs.append(text)
        total += len(text)
        if total >= limit:
            break
    text = " ".join(paragraphs)
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


async def enrich_item(item: NewsItem) -> None:
    """Догружает страницу статьи: вытаскивает картинку (если её не было)
    и настоящий пересказ новости из первых абзацев статьи."""
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        page = await _fetch_page_head(session, item.url)
    if not page:
        return
    if not item.image:
        item.image = _extract_image({}, page)
    text = _extract_article_text(page)
    if len(text) >= 150:  # короткие выжимки не лучше описания из ленты
        item.summary = text


def _filter_fresh(items: list[NewsItem], hours: int) -> list[NewsItem]:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    fresh = []
    for it in items:
        # Если время публикации неизвестно — оставляем новость (лента и так свежая)
        if it.published is None or it.published >= cutoff:
            fresh.append(it)
    return fresh


async def collect_news(fresh_hours: int = 24) -> list[NewsItem]:
    """Собирает свежие новости со всех источников (без ранжирования и дедупликации)."""
    connector = aiohttp.TCPConnector(limit=10, ssl=False)
    async with aiohttp.ClientSession(
        headers={"User-Agent": USER_AGENT}, connector=connector
    ) as session:
        results = await asyncio.gather(*[_fetch_source(session, s) for s in SOURCES if s["enabled"]])

        items = [it for sub in results for it in sub]
        items = _filter_fresh(items, fresh_hours)

        # Если за окно почти ничего не накопилось — берём свежие 15 из всех лент,
        # чтобы не остаться без новостей.
        if len(items) < 3:
            all_items = [it for sub in results for it in sub]
            all_items.sort(
                key=lambda i: i.published or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            items = all_items[:15]

    log.info("Итого собрано свежих новостей: %d", len(items))
    return items
