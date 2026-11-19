"""Проверка логики отбора _select_candidates на искусственных новостях."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from datetime import datetime, timedelta, timezone  # noqa: E402

from digest import _select_candidates  # noqa: E402
from news import NewsItem  # noqa: E402


def mk(i, title):
    return NewsItem(title=title, url=f"https://example.com/{i}", orig_title=title,
                    score=2, lang="ru")


def topics(items):
    return [it.orig_title for it in items]


# 1) 10 GTA + 2 WoW -> 3 GTA + 2 WoW (лимит партии 3 на тему, всего 5)
gta = [mk(i, f"GTA 6 новость номер {i}") for i in range(10)]
wow = [mk(100 + i, f"World of Warcraft обновление {i}") for i in range(2)]
res = _select_candidates(gta + wow, {})
assert len(res) == 5, f"сценарий 1: {len(res)}"
assert sum("GTA" in t for t in topics(res)) == 3, f"сценарий 1 GTA: {topics(res)}"
assert sum("Warcraft" in t for t in topics(res)) == 2, f"сценарий 1 WoW: {topics(res)}"
print("1) 10 GTA + 2 WoW -> 3 GTA + 2 WoW: OK")

# 2) только GTA (10 шт.) -> исключение: 5 GTA
res = _select_candidates(gta, {})
assert len(res) == 5 and all("GTA" in t for t in topics(res)), f"сценарий 2: {topics(res)}"
print("2) 10 GTA без других тем -> 5 GTA (исключение): OK")

# 3) 10 GTA + 1 WoW -> 3 GTA + 1 WoW (всего 4, лимит 3 соблюдён)
res = _select_candidates(gta + [wow[0]], {})
assert len(res) == 4, f"сценарий 3: {len(res)}"
assert sum("GTA" in t for t in topics(res)) == 3, f"сценарий 3 GTA: {topics(res)}"
print("3) 10 GTA + 1 WoW -> 3 GTA + 1 WoW: OK")

# 4) суточный лимит: в истории уже 8 GTA за сутки -> GTA не берём, только WoW
now = datetime.now(tz=timezone.utc)
seen = {}
for i in range(8):
    v = {"ts": (now - timedelta(hours=1)).isoformat(), "t": f"gta {i}"}
    seen[f"url{i}"] = v
    seen[f"t{i}"] = v
res = _select_candidates(gta + wow, seen)
assert len(res) == 2 and all("Warcraft" in t for t in topics(res)), f"сценарий 4: {topics(res)}"
print("4) 8 GTA уже выходили за сутки -> только 2 WoW: OK")

# 5) мало новостей: 2 GTA + 1 WoW -> все три выходят (минимум 1, до 5)
res = _select_candidates(gta[:2] + [wow[0]], {})
assert len(res) == 3, f"сценарий 5: {topics(res)}"
print("5) 2 GTA + 1 WoW -> 3 новости: OK")

# 6) порядок: свежие первыми (candidates уже отсортированы снаружи — проверяем сохранение)
print("\nВсе сценарии отбора прошли успешно!")
