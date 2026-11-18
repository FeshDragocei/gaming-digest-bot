"""
Проверка доступности RSS-лент из sources.py.

Запуск из папки проекта:  python scripts/check_sources.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows-консоль часто работает в cp1251 — переключаем вывод в UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import aiohttp  # noqa: E402
import feedparser  # noqa: E402

from sources import SOURCES  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"


async def check(session, src):
    try:
        async with session.get(src["url"], timeout=aiohttp.ClientTimeout(total=25)) as r:
            if r.status != 200:
                return f"{src['name']:<22} ✗ HTTP {r.status}"
            raw = await r.read()
    except Exception as e:
        return f"{src['name']:<22} ✗ {type(e).__name__}: {e}"

    feed = feedparser.parse(raw)
    n = len(feed.entries)
    if n == 0:
        return f"{src['name']:<22} ✗ лента прочиталась, но записей нет"

    last = feed.entries[0]
    title = (last.get("title") or "")[:60]
    return f"{src['name']:<22} ✓ {n} записей | посл.: {title}"


async def main():
    async with aiohttp.ClientSession(headers={"User-Agent": UA}) as s:
        for line in await asyncio.gather(*[check(s, src) for src in SOURCES if src["enabled"]]):
            print(line)


if __name__ == "__main__":
    asyncio.run(main())
