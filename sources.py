"""
Список источников новостей (RSS-ленты игровых сайтов).

lang  — язык ленты: "ru" (публикуем как есть) или "en" (автоперевод на русский).
enabled — можно временно отключить источник, не удаляя его.

Проверить, какие ленты живы прямо сейчас:
    python scripts/check_sources.py
"""

SOURCES: list[dict] = [
    # ================= Русскоязычные =================
    {
        "name": "StopGame",
        "url": "https://rss.stopgame.ru/rss_all.xml",
        "lang": "ru",
        "enabled": True,
    },
    {
        "name": "Игромания",
        "url": "https://www.igromania.ru/rss/news.rss",
        "lang": "ru",
        "enabled": True,
    },
    {
        "name": "DTF",
        "url": "https://dtf.ru/rss",
        "lang": "ru",
        "enabled": True,
    },
    {
        "name": "3DNews — Игры",
        "url": "https://3dnews.ru/games/rss/",
        "lang": "ru",
        "enabled": True,
    },
    # ================= Зарубежные (переводятся автоматически) =================
    {
        "name": "IGN",
        "url": "https://feeds.ign.com/ign/games-all",
        "lang": "en",
        "enabled": True,
    },
    {
        "name": "PC Gamer",
        "url": "https://www.pcgamer.com/rss/",
        "lang": "en",
        "enabled": True,
    },
    {
        "name": "GameSpot",
        "url": "https://www.gamespot.com/feeds/game-news/",
        "lang": "en",
        "enabled": True,
    },
    {
        "name": "Eurogamer",
        "url": "https://www.eurogamer.net/feed",
        "lang": "en",
        "enabled": True,
    },
    {
        "name": "Rock Paper Shotgun",
        "url": "https://www.rockpapershotgun.com/feed",
        "lang": "en",
        "enabled": True,
    },
    {
        "name": "Polygon",
        "url": "https://www.polygon.com/rss/index.xml",
        "lang": "en",
        "enabled": True,
    },
]
