"""
Предпросмотр публикаций без отправки в Telegram.

Показывает, какие новости будут опубликованы при ближайшей проверке
(со сбором пересказа, картинок и переводом). Новости при этом НЕ
помечаются как показанные.

Запуск из папки проекта:  python scripts/preview_digest.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

import digest  # noqa: E402
from news import collect_news  # noqa: E402


async def main():
    print("\n=== 1. Сбор свежих новостей (за 24 ч) ===")
    items = await collect_news(fresh_hours=24)
    print(f"Всего свежих: {len(items)}")
    with_img = sum(1 for i in items if i.image)
    print(f"С картинкой: {with_img} ({with_img * 100 // max(1, len(items))}%)")

    print("\n=== 2. Примеры новостей ===")
    for it in items[:5]:
        img = (it.image[:60] + "…") if it.image else "нет"
        print(f"  [{it.source}] {it.title[:70]}")
        print(f"      картинка: {img}")

    print("\n=== 3. Перевод одной зарубежной новости ===")
    en = next((i for i in items if i.lang == "en"), None)
    if en:
        print(f"  До:  {en.title[:80]}")
        await digest._translate_item(en)
        print(f"  После: {en.title[:80]}")

    print("\n=== 4. Что будет опубликовано при ближайшей проверке ===")
    top = await digest.build_digest(fresh_hours=24)
    for i, it in enumerate(top, 1):
        print(f"  {i}. [{it.source}] (score={it.score}) {it.title[:75]}")
    if top:
        cap = digest.caption_for(top[0])
        print(f"\nПример подписи ({len(cap)} симв., лимит 1024):")
        print("-" * 60)
        print(cap)
        print("-" * 60)

    # закрываем HTTP-сессию переводчика, чтобы не было предупреждений
    await digest.close_session()


if __name__ == "__main__":
    asyncio.run(main())
