"""
Парсер мониторинга альянса.

Мониторит:
1. Смену текущей манги (как раньше).
2. Вклады клуба Memento Mori (data-page="club64") с отображением
   прироста за неделю в закреплённом сообщении.

Изменения:
- При рестарте НЕ сбрасывает baseline (прогресс сохраняется).
- При смене недели: архивирует старую неделю → отправляет итоги → начинает новую.
- Восстанавливается после смерти сессии (переавторизация).
"""

import asyncio
import logging
from typing import Optional, Dict, Any

import requests
from bs4 import BeautifulSoup

from config import (
    BASE_URL, ALLIANCE_URL, ALLIANCE_CHECK_INTERVAL,
    LOGIN_EMAIL, LOGIN_PASSWORD,
)
from timezone_utils import ts_for_db, now_msk
from alliance_weekly_stats import (
    CLUB_PAGE_ATTR,
    parse_alliance_club_contributions,
    compute_alliance_hash,
    get_alliance_week_start,
    get_alliance_week_rows,
    upsert_alliance_contributions,
    send_or_update_alliance_pinned,
    send_alliance_week_archive_message,
)

logger = logging.getLogger(__name__)

# Количество подряд идущих ошибок, после которых пробуем переавторизацию
_RELOGIN_AFTER_FAILURES = 5


# ══════════════════════════════════════════════════════════════
# ПАРСЕР АЛЬЯНСА
# ══════════════════════════════════════════════════════════════


class AllianceParser:
    """Парсер страницы буста альянса."""

    MAX_RETRIES = 3
    RETRY_DELAY = 5

    def __init__(self, session: requests.Session):
        self.session = session

    # ── Получение HTML страницы ──────────────────────────────

    def fetch_page(self) -> Optional[str]:
        """
        Загружает HTML страницы альянса.

        Returns:
            HTML-строка или None при ошибке.
        """
        for attempt in range(self.MAX_RETRIES):
            try:
                response = self.session.get(ALLIANCE_URL, timeout=15)

                # Детектируем смерть сессии по редиректу на /login
                if hasattr(response, 'url') and '/login' in str(response.url):
                    logger.warning("[Alliance] Сессия истекла — редирект на /login")
                    return None

                if response.status_code == 500:
                    logger.warning(
                        f"[Alliance] HTTP 500 (попытка {attempt + 1}/{self.MAX_RETRIES})"
                    )
                    if attempt < self.MAX_RETRIES - 1:
                        import time; time.sleep(self.RETRY_DELAY)
                    continue

                if response.status_code == 403:
                    logger.warning("[Alliance] HTTP 403 — сессия вероятно мертва")
                    return None

                if response.status_code != 200:
                    logger.warning(
                        f"[Alliance] HTTP {response.status_code} "
                        f"(попытка {attempt + 1}/{self.MAX_RETRIES})"
                    )
                    if attempt < self.MAX_RETRIES - 1:
                        import time; time.sleep(self.RETRY_DELAY)
                    continue

                # Проверяем, не пришла ли страница логина вместо альянса
                if 'login-button' in response.text and 'alliance' not in response.url:
                    logger.warning("[Alliance] Получена страница логина вместо альянса")
                    return None

                return response.text

            except requests.exceptions.Timeout:
                logger.warning(
                    f"[Alliance] Таймаут (попытка {attempt + 1}/{self.MAX_RETRIES})"
                )
                if attempt < self.MAX_RETRIES - 1:
                    import time; time.sleep(self.RETRY_DELAY)
            except requests.exceptions.ConnectionError:
                logger.warning(
                    f"[Alliance] Ошибка соединения (попытка {attempt + 1}/{self.MAX_RETRIES})"
                )
                if attempt < self.MAX_RETRIES - 1:
                    import time; time.sleep(self.RETRY_DELAY)
            except Exception as e:
                logger.error(f"[Alliance] Ошибка загрузки: {e}", exc_info=True)
                if attempt < self.MAX_RETRIES - 1:
                    import time; time.sleep(self.RETRY_DELAY)

        return None

    # ── Парсинг slug текущей манги ────────────────────────────

    def get_current_manga_slug(self, html: str) -> Optional[str]:
        """Извлекает slug текущей манги из уже загруженного HTML."""
        try:
            soup = BeautifulSoup(html, "html.parser")

            manga_link = soup.find("a", class_="card-show__placeholder")
            if manga_link:
                href = manga_link.get("href", "")
                if href.startswith("/manga/"):
                    return href.replace("/manga/", "")

            poster = soup.find("div", class_="card-show__header")
            if poster:
                style = poster.get("style", "")
                if "background-image: url(" in style:
                    try:
                        img_url = style.split("url('")[1].split("'")[0]
                        return img_url.split("/posters/")[-1].replace(".jpg", "")
                    except IndexError:
                        pass

            return None

        except Exception as e:
            logger.error(f"[Alliance] Ошибка парсинга slug: {e}")
            return None

    # ── Детальные данные о манге ──────────────────────────────

    def get_manga_details(self, manga_slug: str) -> Optional[Dict[str, Any]]:
        """Получает детальную информацию о манге по slug."""
        for attempt in range(self.MAX_RETRIES):
            try:
                url = f"{BASE_URL}/manga/{manga_slug}"
                response = self.session.get(url, timeout=15)

                if response.status_code not in (200,):
                    if attempt < self.MAX_RETRIES - 1:
                        import time; time.sleep(self.RETRY_DELAY)
                    continue

                soup = BeautifulSoup(response.text, "html.parser")

                title = None
                for cls in ("manga-mobile__name", "manga__name"):
                    elem = soup.find("h1", class_=cls)
                    if elem:
                        title = elem.text.strip()
                        break
                if not title:
                    title = manga_slug

                img_src = None
                img_elem = soup.find("img", class_="manga-mobile__image")
                if img_elem:
                    img_src = img_elem.get("src")
                if not img_src:
                    wrapper = soup.find("div", class_="manga__img")
                    if wrapper:
                        img = wrapper.find("img")
                        if img:
                            img_src = img.get("src")

                if img_src and img_src.startswith("/"):
                    img_src = f"{BASE_URL}{img_src}"

                return {
                    "slug":          manga_slug,
                    "title":         title,
                    "image":         img_src,
                    "url":           f"{BASE_URL}/manga/{manga_slug}",
                    "discovered_at": ts_for_db(now_msk()),
                }

            except Exception as e:
                logger.error(
                    f"[Alliance] Ошибка деталей манги {manga_slug}: {e}",
                    exc_info=True
                )
                if attempt < self.MAX_RETRIES - 1:
                    import time; time.sleep(self.RETRY_DELAY)

        return None


# ══════════════════════════════════════════════════════════════
# ПЕРЕАВТОРИЗАЦИЯ
# ══════════════════════════════════════════════════════════════


async def _try_relogin(session, loop) -> bool:
    """Пытается переавторизоваться, возвращает True при успехе."""
    from auth import relogin
    from proxy_manager import ProxyManager

    logger.warning("[Alliance] Попытка переавторизации...")
    try:
        result = await loop.run_in_executor(
            None,
            lambda: relogin(session, LOGIN_EMAIL, LOGIN_PASSWORD, ProxyManager(enabled=False))
        )
        if result:
            logger.info("[Alliance] ✅ Переавторизация прошла успешно")
        else:
            logger.error("[Alliance] ❌ Переавторизация не удалась")
        return result
    except Exception as e:
        logger.error(f"[Alliance] Ошибка переавторизации: {e}", exc_info=True)
        return False


# ══════════════════════════════════════════════════════════════
# ОСНОВНОЙ ЦИКЛ МОНИТОРИНГА
# ══════════════════════════════════════════════════════════════


async def alliance_monitor_loop(session, bot):
    """
    Фоновый цикл мониторинга альянса.

    Параллельно ведёт:
    1. Детектирование смены манги → уведомление в топик альянса.
    2. Мониторинг вкладов клуба (data-page="club64") →
       закреплённое сообщение с приростом за неделю.

    При смене недели:
    - Архивирует старую неделю (отправляет итоговое сообщение).
    - Начинает новую неделю с текущих значений как baseline.

    При рестарте:
    - НЕ сбрасывает baseline если данные за эту неделю уже есть в БД.
    """
    from database import get_current_alliance_manga, save_alliance_manga
    from notifier import notify_alliance_manga_changed

    parser = AllianceParser(session)
    logger.info("🔄 Запущен мониторинг альянса (манга + вклады клуба)")

    loop = asyncio.get_event_loop()

    # ── Стартовое состояние ──────────────────────────────────

    start_html = await loop.run_in_executor(None, parser.fetch_page)

    current_slug: Optional[str] = None
    if start_html:
        current_slug = parser.get_current_manga_slug(start_html)

    saved = await get_current_alliance_manga()

    if saved is None and current_slug and start_html:
        manga_info = await loop.run_in_executor(
            None, parser.get_manga_details, current_slug
        )
        if manga_info:
            await save_alliance_manga(manga_info)
            await notify_alliance_manga_changed(bot, manga_info, is_startup=True)
            logger.info(f"🚀 Стартовый тайтл альянса: {manga_info['title']}")
    elif saved:
        current_slug = saved["slug"]
        logger.info(f"🔖 Тайтл альянса из БД: {saved['title']}")

    # ── Состояние мониторинга вкладов ────────────────────────

    last_club_hash:  Optional[str] = None
    last_week_start: str           = get_alliance_week_start()
    consecutive_failures: int      = 0

    # ── Инициализация вкладов при старте ────────────────────
    #
    # КЛЮЧЕВОЕ ОТЛИЧИЕ от предыдущей версии:
    # Если данные за эту неделю УЖЕ ЕСТЬ в БД — используем is_new_week=False,
    # чтобы не перезаписать baseline (не сбросить накопленный прогресс).
    # is_new_week=True ставим только если данных нет вообще.

    if start_html:
        contributions = parse_alliance_club_contributions(start_html)
        if contributions:
            existing_rows = await get_alliance_week_rows(last_week_start)
            is_fresh_week = len(existing_rows) == 0

            await upsert_alliance_contributions(
                last_week_start, contributions, is_new_week=is_fresh_week
            )
            rows = await get_alliance_week_rows(last_week_start)
            await send_or_update_alliance_pinned(bot, rows, last_week_start)
            last_club_hash = compute_alliance_hash(contributions)

            if is_fresh_week:
                logger.info(
                    f"🚀 Старт: новая неделя {last_week_start}, "
                    f"baseline установлен ({len(contributions)} участников)"
                )
            else:
                logger.info(
                    f"🚀 Старт: восстановление недели {last_week_start}, "
                    f"baseline сохранён ({len(existing_rows)} участников в БД)"
                )

    # ── Основной цикл ─────────────────────────────────────────

    check_count = 0

    while True:
        try:
            await asyncio.sleep(ALLIANCE_CHECK_INTERVAL)
            check_count += 1

            html = await loop.run_in_executor(None, parser.fetch_page)

            if not html:
                consecutive_failures += 1
                if check_count % 10 == 0:
                    logger.warning(
                        f"[Alliance] Не удалось загрузить страницу "
                        f"(подряд: {consecutive_failures})"
                    )

                # Пробуем переавторизацию после N неудач
                if consecutive_failures >= _RELOGIN_AFTER_FAILURES:
                    ok = await _try_relogin(session, loop)
                    if ok:
                        consecutive_failures = 0
                    else:
                        # Ждём дольше перед следующей попыткой
                        await asyncio.sleep(60)
                continue

            # Успешная загрузка — сбрасываем счётчик ошибок
            consecutive_failures = 0
            current_week_start   = get_alliance_week_start()

            # ══════════════════════════════════════════════════
            # СМЕНА МАНГИ
            # ══════════════════════════════════════════════════

            new_slug = parser.get_current_manga_slug(html)
            if new_slug and new_slug != current_slug:
                logger.info(
                    f"[Alliance] Смена тайтла: {current_slug} → {new_slug}"
                )
                manga_info = await loop.run_in_executor(
                    None, parser.get_manga_details, new_slug
                )
                if manga_info:
                    await save_alliance_manga(manga_info)
                    await notify_alliance_manga_changed(bot, manga_info, is_startup=False)
                    current_slug = new_slug
                    logger.info(
                        f"✅ Уведомление об альянсе отправлено: {manga_info['title']}"
                    )
                else:
                    current_slug = new_slug

            # ══════════════════════════════════════════════════
            # МОНИТОРИНГ ВКЛАДОВ КЛУБА
            # ══════════════════════════════════════════════════

            contributions = parse_alliance_club_contributions(html)
            if not contributions:
                if check_count % 60 == 0:
                    logger.debug("[Alliance] Вклады клуба не найдены")
                continue

            current_hash = compute_alliance_hash(contributions)

            # ── Смена недели ─────────────────────────────────
            if current_week_start != last_week_start:
                logger.info(
                    f"[Alliance] 📅 Смена недели: "
                    f"{last_week_start} → {current_week_start}"
                )

                # 1. Архивируем итоги старой недели
                old_rows = await get_alliance_week_rows(last_week_start)
                if old_rows:
                    await send_alliance_week_archive_message(bot, last_week_start, old_rows)
                    logger.info(f"[Alliance] Итоги недели {last_week_start} отправлены")

                # 2. Начинаем новую неделю: baseline = текущие значения
                await upsert_alliance_contributions(
                    current_week_start, contributions, is_new_week=True
                )
                last_week_start = current_week_start
                last_club_hash  = None   # гарантируем обновление закреплённого

            # ── Данные изменились ────────────────────────────
            if current_hash != last_club_hash:
                await upsert_alliance_contributions(
                    current_week_start,
                    contributions,
                    is_new_week=False,  # baseline уже установлен — не трогаем
                )
                rows = await get_alliance_week_rows(current_week_start)
                await send_or_update_alliance_pinned(bot, rows, current_week_start)
                last_club_hash = current_hash

                top = max(
                    rows,
                    key=lambda r: r["contribution_current"] - r["contribution_baseline"],
                    default=None,
                )
                if top:
                    delta = top["contribution_current"] - top["contribution_baseline"]
                    logger.info(
                        f"[Alliance] Вклады обновлены. "
                        f"Лидер прироста: {top['nick']} (+{delta})"
                    )
            elif check_count % 60 == 0:
                logger.debug(
                    f"[Alliance] Вклады без изменений (проверка #{check_count})"
                )

        except asyncio.CancelledError:
            logger.info("⏹ Мониторинг альянса остановлен")
            break
        except Exception as e:
            logger.error(f"[Alliance] Ошибка в цикле: {e}", exc_info=True)
            await asyncio.sleep(30)