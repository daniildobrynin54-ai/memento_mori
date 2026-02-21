"""
debug_csrf2.py — финальная диагностика с явными куками
"""
import sys, os, urllib.parse
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import LOGIN_EMAIL, LOGIN_PASSWORD, BASE_URL
from auth import login
from proxy_manager import ProxyManager

BOOST_URL = f"{BASE_URL}/clubs/memento-mori/boost"
AJAX_URL  = f"{BASE_URL}/clubs/getTopUsers?period=week"

print("1. Авторизация...")
session = login(LOGIN_EMAIL, LOGIN_PASSWORD, ProxyManager(enabled=False))
if not session:
    print("FAIL"); sys.exit(1)
print("   OK")

# Достаём реальную внутреннюю сессию
inner = session._session if hasattr(session, '_session') else session

print(f"\n2. Куки в inner._session:")
for k, v in inner.cookies.items():
    print(f"   {k} = {str(v)[:60]}")

print(f"\n3. Куки в session (обёртка):")
try:
    for k, v in session.cookies.items():
        print(f"   {k} = {str(v)[:60]}")
except Exception as e:
    print(f"   Ошибка: {e}")

print("\n4. GET буста через inner напрямую...")
resp = inner.get(BOOST_URL, timeout=15)
print(f"   Статус: {resp.status_code}")

soup = BeautifulSoup(resp.text, "html.parser")
meta = soup.find("meta", {"name": "csrf-token"})
meta_token = meta.get("content", "") if meta else ""
print(f"   meta csrf: {meta_token[:50] if meta_token else 'НЕ НАЙДЕН'}")

print(f"\n5. POST через inner с meta токеном...")
r = inner.post(AJAX_URL, headers={
    "X-CSRF-TOKEN": meta_token,
    "X-Requested-With": "XMLHttpRequest",
    "Referer": BOOST_URL,
    "Accept": "*/*",
}, data=None, timeout=15)
print(f"   Статус: {r.status_code}")
if r.status_code == 200:
    try:
        d = r.json()
        content = d.get("content","")
        print(f"   OK! content len={len(content)}, items={content.count('club-boost__top-item')}")
        if content:
            from weekly_stats import parse_weekly_contributions
            rows = parse_weekly_contributions(content)
            print(f"   Участников: {len(rows)}")
            for c in rows[:3]:
                print(f"     {c['nick']} -- {c['contribution']}")
    except Exception as e:
        print(f"   JSON ошибка: {e}, текст: {r.text[:200]}")
else:
    print(f"   Ответ: {r.text[:200]}")

print("\nГотово!")