"""
Модуль мониторинга вкладов клуба в альянс.
"""

import hashlib
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import aiosqlite
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.error import TelegramError

from config import BASE_URL, REQUIRED_TG_GROUP_ID, GROUP_ALLIANCE_TOPIC_ID
from timezone_utils import now_msk, ts_for_db

logger = logging.getLogger(__name__)
DB_PATH = "bot_data.db"

CLUB_PAGE_ATTR = "club64"


# ══════════════════════════════════════════════════════════════
# УТИЛИТЫ НЕДЕЛИ
# ══════════════════════════════════════════════════════════════


def get_alliance_week_start(dt: datetime = None) -> str:
    if dt is None:
        dt = now_msk()
    monday = dt.date() - timedelta(days=dt.weekday())
    return monday.isoformat()


def get_alliance_week_end(week_start: str) -> str:
    monday = datetime.strptime(week_start, "%Y-%m-%d").date()
    return (monday + timedelta(days=6)).isoformat()


def format_alliance_week_range(week_start: str) -> str:
    week_end = get_alliance_week_end(week_start)
    s = datetime.strptime(week_start, "%Y-%m-%d")
    e = datetime.strptime(week_end, "%Y-%m-%d")
    return f"{s.day:02d}.{s.month:02d} — {e.day:02d}.{e.month:02d}"


# ══════════════════════════════════════════════════════════════
# ИНИЦИАЛИЗАЦИЯ ТАБЛИЦ
# ══════════════════════════════════════════════════════════════


async def ensure_alliance_weekly_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS alliance_club_contributions (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start              TEXT NOT NULL,
                mangabuff_id            INTEGER NOT NULL,
                nick                    TEXT NOT NULL,
                profile_url             TEXT,
                contribution_baseline   INTEGER NOT NULL DEFAULT 0,
                contribution_current    INTEGER NOT NULL DEFAULT 0,
                updated_at              TEXT NOT NULL,
                UNIQUE(week_start, mangabuff_id)
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_alliance_club_week
            ON alliance_club_contributions(week_start, contribution_current DESC)
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pinned_alliance_weekly_message (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     INTEGER NOT NULL UNIQUE,
                thread_id   INTEGER,
                message_id  INTEGER NOT NULL,
                week_start  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)
        # ── Архив завершённых недель ─────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS alliance_weekly_archive (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start          TEXT NOT NULL UNIQUE,
                week_end            TEXT NOT NULL,
                message_text        TEXT NOT NULL,
                total_delta         INTEGER NOT NULL DEFAULT 0,
                participants_count  INTEGER NOT NULL DEFAULT 0,
                archived_at         TEXT NOT NULL,
                tg_message_id       INTEGER
            )
        """)
        await db.commit()


# ══════════════════════════════════════════════════════════════
# ПАРСИНГ HTML
# ══════════════════════════════════════════════════════════════


def parse_alliance_club_contributions(html: str, club_page: str = CLUB_PAGE_ATTR) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    club_div = soup.find("div", attrs={"data-page": club_page})

    if not club_div:
        logger.warning(
            f"Блок data-page='{club_page}' не найден. "
            f"Доступные табы: "
            + str([d.get("data-page") for d in soup.find_all(attrs={"data-page": True})])
        )
        return []

    results = []
    import re
    for item in club_div.select(".club-boost__top-item"):
        name_link = item.select_one("a.club-boost__top-name")
        if not name_link:
            continue

        nick = name_link.text.strip()
        href = name_link.get("href", "")

        match = re.search(r"/users/(\d+)", href)
        mangabuff_id = int(match.group(1)) if match else 0
        profile_url = (f"{BASE_URL}{href}" if href.startswith("/") else href)

        contrib_el = item.select_one(".club-boost__top-contribution")
        try:
            contribution = int(contrib_el.text.strip()) if contrib_el else 0
        except ValueError:
            contribution = 0

        results.append({
            "mangabuff_id": mangabuff_id,
            "nick":         nick,
            "profile_url":  profile_url,
            "contribution": contribution,
        })

    logger.debug(f"[Alliance club] Спарсено {len(results)} участников из блока '{club_page}'")
    return results


def compute_alliance_hash(contributions: List[Dict]) -> str:
    data = ",".join(
        f"{c['mangabuff_id']}:{c['contribution']}"
        for c in contributions
    )
    return hashlib.md5(data.encode()).hexdigest()


# ══════════════════════════════════════════════════════════════
# РАБОТА С БД — ТЕКУЩАЯ НЕДЕЛЯ
# ══════════════════════════════════════════════════════════════


async def get_alliance_week_rows(week_start: str) -> List[Dict]:
    await ensure_alliance_weekly_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM alliance_club_contributions
            WHERE week_start = ?
            ORDER BY contribution_current DESC
        """, (week_start,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_alliance_available_weeks() -> List[str]:
    await ensure_alliance_weekly_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT DISTINCT week_start FROM alliance_club_contributions
            ORDER BY week_start DESC
        """) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def upsert_alliance_contributions(
    week_start: str,
    contributions: List[Dict],
    is_new_week: bool,
):
    await ensure_alliance_weekly_tables()
    updated_at = ts_for_db(now_msk())

    async with aiosqlite.connect(DB_PATH) as db:
        for c in contributions:
            if is_new_week:
                # Новая неделя: baseline = текущее значение (прирост начинается с 0)
                await db.execute("""
                    INSERT INTO alliance_club_contributions
                        (week_start, mangabuff_id, nick, profile_url,
                         contribution_baseline, contribution_current, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(week_start, mangabuff_id) DO UPDATE SET
                        nick                   = excluded.nick,
                        contribution_baseline  = excluded.contribution_baseline,
                        contribution_current   = excluded.contribution_current,
                        updated_at             = excluded.updated_at
                """, (
                    week_start, c["mangabuff_id"], c["nick"], c["profile_url"],
                    c["contribution"], c["contribution"], updated_at,
                ))
            else:
                # Обновление текущей недели: baseline НЕ трогаем
                await db.execute("""
                    INSERT INTO alliance_club_contributions
                        (week_start, mangabuff_id, nick, profile_url,
                         contribution_baseline, contribution_current, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(week_start, mangabuff_id) DO UPDATE SET
                        nick                  = excluded.nick,
                        contribution_current  = excluded.contribution_current,
                        updated_at            = excluded.updated_at
                """, (
                    week_start, c["mangabuff_id"], c["nick"], c["profile_url"],
                    c["contribution"], c["contribution"], updated_at,
                ))
        await db.commit()


# ══════════════════════════════════════════════════════════════
# АРХИВ НЕДЕЛЬ
# ══════════════════════════════════════════════════════════════


async def get_alliance_archive_weeks() -> List[Dict]:
    """Возвращает список архивированных недель."""
    await ensure_alliance_weekly_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM alliance_weekly_archive
            ORDER BY week_start DESC
        """) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def archive_alliance_week(week_start: str, rows: List[Dict]) -> Optional[str]:
    """
    Сохраняет итоги недели в архив.
    Возвращает текст итогового сообщения или None если нечего архивировать.
    """
    await ensure_alliance_weekly_tables()

    active_rows = [r for r in rows if r["contribution_current"] - r["contribution_baseline"] > 0]
    if not active_rows:
        logger.info(f"[Alliance archive] Неделя {week_start}: нет активных вкладчиков, пропускаем")
        return None

    active_rows.sort(
        key=lambda r: r["contribution_current"] - r["contribution_baseline"],
        reverse=True
    )

    total_delta = sum(r["contribution_current"] - r["contribution_baseline"] for r in rows)
    week_end    = get_alliance_week_end(week_start)
    date_range  = format_alliance_week_range(week_start)

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines  = [f"🏆 <b>Итоги вкладов в альянс</b> ({date_range})\n"]

    for i, r in enumerate(active_rows, 1):
        url   = r.get("profile_url", "")
        nick  = r["nick"]
        base  = r["contribution_baseline"]
        curr  = r["contribution_current"]
        delta = curr - base
        name  = f'<a href="{url}">{nick}</a>' if url else nick
        lines.append(f"{medals.get(i, f'{i}.')} {name} — +{delta} ({base}→{curr})")

    lines.append(f"\n👥 Вкладчиков: {len(active_rows)}")
    lines.append(f"📈 Общий прирост за неделю: <b>+{total_delta}</b>")
    lines.append(f"\n📦 Данные сохранены в архив.")

    text = "\n".join(lines)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO alliance_weekly_archive
                (week_start, week_end, message_text, total_delta,
                 participants_count, archived_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(week_start) DO UPDATE SET
                message_text       = excluded.message_text,
                total_delta        = excluded.total_delta,
                participants_count = excluded.participants_count,
                archived_at        = excluded.archived_at
        """, (
            week_start, week_end, text, total_delta,
            len(active_rows), ts_for_db(now_msk()),
        ))
        await db.commit()

    logger.info(
        f"[Alliance archive] Неделя {week_start} сохранена: "
        f"{len(active_rows)} вкладчиков, прирост +{total_delta}"
    )
    return text


async def send_alliance_week_archive_message(
    bot: Bot,
    week_start: str,
    rows: List[Dict],
) -> bool:
    """
    Отправляет итоговое сообщение в топик и сохраняет в архив.
    Вызывается при смене недели.
    """
    text = await archive_alliance_week(week_start, rows)
    if not text:
        return False

    try:
        msg = await bot.send_message(
            chat_id=REQUIRED_TG_GROUP_ID,
            text=text,
            parse_mode="HTML",
            message_thread_id=GROUP_ALLIANCE_TOPIC_ID,
            disable_web_page_preview=True,
        )
        # Сохраняем ID сообщения в архиве
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                UPDATE alliance_weekly_archive
                SET tg_message_id = ?
                WHERE week_start = ?
            """, (msg.message_id, week_start))
            await db.commit()

        logger.info(f"[Alliance archive] Итоговое сообщение за {week_start} отправлено")
        return True

    except TelegramError as e:
        logger.error(f"[Alliance archive] Ошибка отправки итогов: {e}")
        return False


# ══════════════════════════════════════════════════════════════
# ЗАКРЕПЛЁННОЕ СООБЩЕНИЕ — БД
# ══════════════════════════════════════════════════════════════


async def get_pinned_alliance_message(chat_id: int) -> Optional[Dict]:
    await ensure_alliance_weekly_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM pinned_alliance_weekly_message WHERE chat_id = ?",
            (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def save_pinned_alliance_message(
    chat_id: int,
    thread_id: Optional[int],
    message_id: int,
    week_start: str,
):
    await ensure_alliance_weekly_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO pinned_alliance_weekly_message
                (chat_id, thread_id, message_id, week_start, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                thread_id  = excluded.thread_id,
                message_id = excluded.message_id,
                week_start = excluded.week_start,
                updated_at = excluded.updated_at
        """, (chat_id, thread_id, message_id, week_start, ts_for_db(now_msk())))
        await db.commit()


async def clear_pinned_alliance_message(chat_id: int):
    await ensure_alliance_weekly_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM pinned_alliance_weekly_message WHERE chat_id = ?",
            (chat_id,)
        )
        await db.commit()


# ══════════════════════════════════════════════════════════════
# ФОРМАТИРОВАНИЕ ТЕКУЩЕГО СООБЩЕНИЯ
# ══════════════════════════════════════════════════════════════


def format_alliance_weekly_message(rows: list, week_start: str) -> str:
    """
    Показывает только тех, кто реально вкладывал за неделю (delta > 0),
    отсортированных по приросту по убыванию.
    """
    date_range = format_alliance_week_range(week_start)

    active_rows = [r for r in rows if r["contribution_current"] - r["contribution_baseline"] > 0]
    active_rows.sort(key=lambda r: r["contribution_current"] - r["contribution_baseline"], reverse=True)

    total_delta = sum(r["contribution_current"] - r["contribution_baseline"] for r in rows)
    updated = now_msk().strftime("%d.%m %H:%M МСК")

    if not active_rows:
        return (
            f"🏰 <b>Вклад клуба в альянс</b> ({date_range})\n\n"
            f"За эту неделю вкладов ещё не было.\n\n"
            f"📈 Прирост: <b>+{total_delta}</b>\n"
            f"🕐 <i>Обновлено: {updated}</i>"
        )

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    medal_lines = []
    rest_lines  = []

    for i, r in enumerate(active_rows, 1):
        url   = r.get("profile_url", "")
        nick  = r["nick"]
        base  = r["contribution_baseline"]
        curr  = r["contribution_current"]
        delta = curr - base
        name  = f'<a href="{url}">{nick}</a>' if url else nick
        line  = f"{medals.get(i, f'{i}.')} {name} — {base} → <b>{curr}</b> (+{delta})"

        if i <= 3:
            medal_lines.append(line)
        else:
            rest_lines.append(line)

    body = "\n".join(medal_lines)
    if rest_lines:
        body += "\n\n" + "\n".join(rest_lines)

    return (
        f"🏰 <b>Вклад клуба в альянс</b> ({date_range})\n\n"
        + body
        + f"\n📈 Прирост за неделю: <b>+{total_delta}</b>"
        + f"\n🕐 <i>Обновлено: {updated}</i>"
    )


# ══════════════════════════════════════════════════════════════
# ОТПРАВКА И ОБНОВЛЕНИЕ ЗАКРЕПЛЁННОГО СООБЩЕНИЯ
# ══════════════════════════════════════════════════════════════


async def send_or_update_alliance_pinned(
    bot: Bot,
    rows: List[Dict],
    week_start: str,
):
    chat_id   = REQUIRED_TG_GROUP_ID
    thread_id = GROUP_ALLIANCE_TOPIC_ID
    text      = format_alliance_weekly_message(rows, week_start)

    pinned_info = await get_pinned_alliance_message(chat_id)

    if pinned_info and pinned_info.get("week_start") != week_start:
        logger.info(f"[Alliance] Смена недели → создаём новое сообщение")
        pinned_info = None

    if pinned_info:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=pinned_info["message_id"],
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            await save_pinned_alliance_message(
                chat_id, thread_id, pinned_info["message_id"], week_start
            )
            logger.info("✅ Закреплённое сообщение альянса обновлено")
            return

        except TelegramError as e:
            err = str(e).lower()
            if "message to edit not found" in err or "message_id_invalid" in err:
                logger.warning("[Alliance] Сообщение удалено — создаём новое")
            elif "message is not modified" in err:
                logger.debug("[Alliance] Текст не изменился, пропускаем")
                return
            else:
                logger.error(f"[Alliance] Ошибка edit_message_text: {e}")
                return

    try:
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            message_thread_id=thread_id,
            disable_web_page_preview=True,
        )
        try:
            await bot.pin_chat_message(
                chat_id=chat_id,
                message_id=msg.message_id,
                disable_notification=True,
            )
            logger.info("[Alliance] Сообщение закреплено")
        except TelegramError as e:
            logger.warning(
                f"[Alliance] Не удалось закрепить: {e}\n"
                "Убедись что бот — администратор с правом 'Закреплять сообщения'"
            )

        await save_pinned_alliance_message(chat_id, thread_id, msg.message_id, week_start)
        logger.info("✅ Новое закреплённое сообщение альянса отправлено")

    except TelegramError as e:
        logger.error(f"[Alliance] Ошибка отправки: {e}")