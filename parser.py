"""Парсер страницы boost клуба."""

import logging
import asyncio
import re
from typing import Optional, Dict, Any, List
from bs4 import BeautifulSoup
import requests

from config import (
    BASE_URL, CLUB_BOOST_PATH, PARSE_INTERVAL_SECONDS,
    LOGIN_EMAIL, LOGIN_PASSWORD,
)
from timezone_utils import ts_for_db, now_msk
from rank_detector import RankDetectorImproved
from weekly_stats import (
    parse_weekly_contributions,
    compute_stats_hash,
    save_weekly_contributions,
    send_or_update_weekly_pinned,
    get_week_start,
    archive_weekly_stats,
    send_weekly_archive_message,
)

logger = logging.getLogger(__name__)

# Количество подряд идущих ошибок, после которых пробуем переавторизацию
_RELOGIN_AFTER_FAILURES = 5

# Признаки того, что HTML — страница логина, а не буст
_LOGIN_MARKERS = ("login-button", "form-login", "/login")


class BoostPageParser:
    """Парсер страницы boost клуба."""

    def __init__(self, session: requests.Session, rank_detector: RankDetectorImproved):
        self.session = session
        self.rank_detector = rank_detector
        self.url = f"{BASE_URL}{CLUB_BOOST_PATH}"
        self._consecutive_errors = 0
        self._max_consecutive_errors = 5

    def _is_login_page(self, response) -> bool:
        """Возвращает True если ответ — страница авторизации, а не контент."""
        url_str = str(getattr(response, "url", ""))
        if "/login" in url_str:
            return True
        snippet = response.text[:3000]
        return any(m in snippet for m in _LOGIN_MARKERS)

    def parse(self) -> Optional[Dict[str, Any]]:
        """Парсит страницу boost. Возвращает None также при смерти сессии."""
        try:
            response = self.session.get(self.url)

            if self._is_login_page(response):
                logger.warning("⚠️  Сессия истекла — получена страница логина")
                self._mark_error()
                return None

            if response.status_code == 403:
                logger.warning("⚠️  HTTP 403 — сессия вероятно недействительна")
                self._mark_error()
                return None

            if response.status_code != 200:
                logger.error(f"Ошибка загрузки страницы: {response.status_code}")
                self._mark_error()
                return None

            soup = BeautifulSoup(response.text, "html.parser")

            card_id = self._extract_card_id(soup)
            if not card_id:
                logger.error("Не удалось извлечь card_id")
                self._mark_error()
                return None

            card_image_url = self._extract_card_image(soup)
            replacements   = self._extract_replacements(soup)
            daily_donated  = self._extract_daily_donated(soup)
            club_owners    = self._extract_club_owners(soup)

            self._mark_success()

            return {
                "card_id":        card_id,
                "card_rank":      "?",
                "card_image_url": card_image_url,
                "replacements":   replacements,
                "daily_donated":  daily_donated,
                "club_owners":    club_owners,
                "discovered_at":  ts_for_db(now_msk()),
            }

        except (requests.exceptions.ProxyError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            self._mark_error()
            logger.error(f"Ошибка сети при парсинге: {type(e).__name__}")
            return None

        except Exception as e:
            self._mark_error()
            logger.error(f"Ошибка парсинга: {e}", exc_info=True)
            return None

    def fetch_weekly_ajax(self) -> Optional[str]:
        """
        Запрашивает AJAX-эндпоинт недельной статистики клуба.
        """
        inner    = self.session._session if hasattr(self.session, '_session') else self.session
        ajax_url = f"{BASE_URL}/clubs/getTopUsers?period=week"

        try:
            resp = inner.get(self.url, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"[Weekly AJAX] GET буста вернул {resp.status_code}")
                return None

            if self._is_login_page(resp):
                logger.warning("[Weekly AJAX] Сессия мертва — страница логина")
                return None

            soup = BeautifulSoup(resp.text, "html.parser")
            meta = soup.find("meta", {"name": "csrf-token"})
            if not meta:
                logger.warning("[Weekly AJAX] meta[name=csrf-token] не найден")
                return None

            meta_token = meta.get("content", "")
            if not meta_token:
                logger.warning("[Weekly AJAX] meta csrf-token пустой")
                return None

            ajax_resp = inner.post(
                ajax_url,
                headers={
                    "X-CSRF-TOKEN":     meta_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer":          self.url,
                    "Accept":           "*/*",
                },
                data=None,
                timeout=15,
            )

            logger.info(f"[Weekly AJAX] POST {ajax_url} → HTTP {ajax_resp.status_code}")

            if ajax_resp.status_code != 200:
                logger.warning(f"[Weekly AJAX] Неуспешный статус: {ajax_resp.status_code}")
                return None

            try:
                data    = ajax_resp.json()
                content = data.get("content", "")
                if content:
                    items = content.count("club-boost__top-item")
                    logger.info(
                        f"[Weekly AJAX] Получен контент ({len(content)} байт, "
                        f"~{items // 2} участников)"
                    )
                    return content
                logger.warning(f"[Weekly AJAX] JSON без поля 'content'. Ключи: {list(data.keys())}")
                return None
            except ValueError:
                if "club-boost__top" in ajax_resp.text:
                    return ajax_resp.text
                logger.warning(
                    f"[Weekly AJAX] Ответ не содержит нужных данных: {ajax_resp.text[:200]}"
                )
                return None

        except Exception as e:
            logger.error(f"[Weekly AJAX] Ошибка: {e}", exc_info=True)
            return None

    def _mark_success(self):
        self._consecutive_errors = 0

    def _mark_error(self):
        self._consecutive_errors += 1
        if self._consecutive_errors >= self._max_consecutive_errors:
            logger.warning(f"⚠️ {self._consecutive_errors} ошибок парсинга подряд")

    def _extract_card_id(self, soup: BeautifulSoup) -> Optional[int]:
        link = soup.select_one('a[href*="/cards/"][href*="/users"]')
        if link:
            href  = link.get("href", "")
            match = re.search(r'/cards/(\d+)/users', href)
            if match:
                return int(match.group(1))
        return None

    def _extract_card_image(self, soup: BeautifulSoup) -> str:
        img = soup.select_one('.club-boost__image img')
        if img:
            src = img.get("src", "")
            if src:
                return f"{BASE_URL}{src}" if src.startswith("/") else src
        return ""

    def _extract_replacements(self, soup: BeautifulSoup) -> str:
        text  = soup.get_text()
        match = re.search(r'(\d+)\s*/\s*(\d+)', text)
        if match:
            return f"{match.group(1)}/{match.group(2)}"
        return "0/10"

    def _extract_daily_donated(self, soup: BeautifulSoup) -> str:
        text    = soup.get_text()
        matches = re.findall(r'(\d+)\s*/\s*(\d+)', text)
        if len(matches) >= 2:
            return f"{matches[1][0]}/{matches[1][1]}"
        return "0/50"

    def _extract_club_owners(self, soup: BeautifulSoup) -> List[int]:
        owner_ids    = []
        owners_block = soup.select_one('.club-boost__owners-list')
        if owners_block:
            links = owners_block.select('a[href*="/users/"]')
            for link in links:
                href  = link.get("href", "")
                match = re.search(r'/users/(\d{1,7})', href)
                if match:
                    owner_ids.append(int(match.group(1)))
        return owner_ids


# ══════════════════════════════════════════════════════════════
# ПЕРЕАВТОРИЗАЦИЯ
# ══════════════════════════════════════════════════════════════


async def _try_relogin(session, loop) -> bool:
    """Пытается переавторизоваться, возвращает True при успехе."""
    from auth import relogin
    from proxy_manager import ProxyManager

    logger.warning("[Parser] Попытка переавторизации...")
    try:
        result = await loop.run_in_executor(
            None,
            lambda: relogin(session, LOGIN_EMAIL, LOGIN_PASSWORD, ProxyManager(enabled=False))
        )
        if result:
            logger.info("[Parser] ✅ Переавторизация успешна")
        else:
            logger.error("[Parser] ❌ Переавторизация не удалась")
        return result
    except Exception as e:
        logger.error(f"[Parser] Ошибка переавторизации: {e}", exc_info=True)
        return False


# ══════════════════════════════════════════════════════════════
# ОСНОВНОЙ ЦИКЛ
# ══════════════════════════════════════════════════════════════


async def parse_loop(session: requests.Session, bot, rank_detector: RankDetectorImproved):
    """
    Основной цикл парсинга с мониторингом недельной статистики вкладов.

    Изменения:
    - Автоматическая переавторизация при смерти сессии.
    - При смене недели архивирует старую и отправляет итоговое сообщение.
    """
    from database import get_current_card, archive_card, insert_card
    from notifier import notify_owners, notify_group_new_card
    from card_info_parser import get_card_name, get_owners_nicknames
    from weekly_stats import get_week_contributions_from_db, ensure_weekly_tables

    parser = BoostPageParser(session, rank_detector)
    logger.info("🔄 Запущен цикл парсинга страницы boost")

    consecutive_failures    = 0
    last_weekly_hash: Optional[str] = None
    last_week_start: str            = get_week_start()
    weekly_check_counter: int       = 0
    WEEKLY_CHECK_EVERY              = 10

    await ensure_weekly_tables()
    startup_contribs = await get_week_contributions_from_db(last_week_start)
    if startup_contribs:
        await send_or_update_weekly_pinned(bot, startup_contribs, last_week_start)
        last_weekly_hash = compute_stats_hash([
            {"mangabuff_id": c["mangabuff_id"], "contribution": c["contribution"]}
            for c in startup_contribs
        ])
        logger.info(
            f"🚀 Старт: восстановлена недельная статистика из БД "
            f"({len(startup_contribs)} участников)"
        )
    else:
        logger.info("🚀 Старт: в БД нет данных — AJAX запрос на первой итерации")
        weekly_check_counter = WEEKLY_CHECK_EVERY - 1

    loop = asyncio.get_event_loop()

    while True:
        try:
            current = await get_current_card()
            data    = parser.parse()

            if data:
                consecutive_failures = 0
                current_week_start   = get_week_start()

                # ── Смена недели ─────────────────────────────
                if current_week_start != last_week_start:
                    logger.info(f"🗓 Новая неделя: {last_week_start} → {current_week_start}")

                    # Архивируем итоги прошлой недели
                    old_contribs = await get_week_contributions_from_db(last_week_start)
                    if old_contribs:
                        await send_weekly_archive_message(bot, last_week_start, old_contribs)
                        logger.info(f"📦 Итоги недели {last_week_start} отправлены в архив")

                    last_weekly_hash     = None
                    last_week_start      = current_week_start
                    weekly_check_counter = WEEKLY_CHECK_EVERY - 1  # запросим AJAX сразу

                # ── Недельная статистика (AJAX) ───────────────
                weekly_check_counter += 1
                if weekly_check_counter >= WEEKLY_CHECK_EVERY:
                    weekly_check_counter = 0
                    weekly_html = await loop.run_in_executor(
                        None, parser.fetch_weekly_ajax
                    )

                    if weekly_html:
                        weekly_contributions = parse_weekly_contributions(weekly_html)

                        if weekly_contributions:
                            current_hash = compute_stats_hash(weekly_contributions)

                            if current_hash != last_weekly_hash:
                                await save_weekly_contributions(
                                    current_week_start, weekly_contributions
                                )
                                await send_or_update_weekly_pinned(
                                    bot, weekly_contributions, current_week_start
                                )
                                last_weekly_hash = current_hash
                                logger.info(
                                    f"📊 Недельная статистика обновлена "
                                    f"({len(weekly_contributions)} участников)"
                                )
                        else:
                            logger.warning("[Weekly AJAX] HTML получен, но участники не распарсены")

                # ── Мониторинг смены карты ───────────────────
                if current is None or current.card_id != data["card_id"]:
                    logger.info(
                        f"🔄 Смена карты: "
                        f"{current.card_id if current else 'None'} → {data['card_id']}"
                    )

                    if data["card_image_url"] and rank_detector.is_ready:
                        data["card_rank"] = rank_detector.detect_from_url(
                            data["card_image_url"], session=session
                        )
                    else:
                        data["card_rank"] = "?"

                    if current:
                        await archive_card(current.id)

                    await insert_card(data)

                    card_name = await loop.run_in_executor(
                        None, get_card_name, session, data["card_id"]
                    )
                    owners_nicks = []
                    if data["club_owners"]:
                        owners_nicks = await loop.run_in_executor(
                            None, get_owners_nicknames, session, data["club_owners"], 10
                        )

                    await notify_owners(bot, data)
                    await notify_group_new_card(bot, data, card_name, owners_nicks)

                    logger.info(
                        f"✅ Новая карта «{card_name}» "
                        f"ID {data['card_id']} (Ранг: {data['card_rank']}), "
                        f"владельцев: {len(owners_nicks)}"
                    )

            else:
                # parse() вернул None
                consecutive_failures += 1
                logger.debug(
                    f"[Parser] Неудача #{consecutive_failures}"
                )

                if consecutive_failures >= _RELOGIN_AFTER_FAILURES:
                    ok = await _try_relogin(session, loop)
                    if ok:
                        consecutive_failures = 0
                    else:
                        logger.error("[Parser] Переавторизация не удалась, ждём 60с")
                        await asyncio.sleep(60)
                    # Сбрасываем внутренний счётчик парсера тоже
                    parser._consecutive_errors = 0

        except Exception as e:
            logger.error(f"Ошибка в цикле парсинга: {e}", exc_info=True)
            consecutive_failures += 1

        await asyncio.sleep(PARSE_INTERVAL_SECONDS)