"""Утилиты работы с часовым поясом МСК."""

import pytz
from datetime import datetime, timedelta
from config import TZ

# ══════════════════════════════════════════════════════════════
# КОНСТАНТЫ
# ══════════════════════════════════════════════════════════════

MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта",
    4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября",
    10: "октября", 11: "ноября", 12: "декабря"
}

WEEKDAYS_RU = {
    0: "понедельник", 1: "вторник", 2: "среда",
    3: "четверг", 4: "пятница", 5: "суббота", 6: "воскресенье"
}

# ══════════════════════════════════════════════════════════════
# ОСНОВНЫЕ ФУНКЦИИ
# ══════════════════════════════════════════════════════════════


def now_msk() -> datetime:
    """Текущее время в МСК."""
    return datetime.now(TZ)


def to_msk(dt: datetime) -> datetime:
    """Конвертирует datetime в МСК."""
    if dt.tzinfo is None:
        return TZ.localize(dt)
    return dt.astimezone(TZ)


def ts_for_db(dt: datetime) -> str:
    """ISO-строка для хранения в БД."""
    return to_msk(dt).isoformat()


def parse_booking_dt(date: str, time: str) -> datetime:
    """
    Парсит дату и время брони в datetime МСК.
    
    Args:
        date: "2026-02-15"
        time: "14:30"
    
    Returns:
        datetime с timezone МСК
    """
    dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    return TZ.localize(dt)


# ══════════════════════════════════════════════════════════════
# ФОРМАТИРОВАНИЕ
# ══════════════════════════════════════════════════════════════


def format_date_ru(date_str: str) -> str:
    """
    Форматирует дату в русский формат.
    
    Args:
        date_str: "2026-02-15"
    
    Returns:
        "15 февраля"
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.day} {MONTHS_RU[dt.month]}"


def format_date_with_weekday(date_str: str) -> str:
    """
    Форматирует дату с днём недели.
    
    Args:
        date_str: "2026-02-15"
    
    Returns:
        "15 февраля, воскресенье"
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    weekday = WEEKDAYS_RU[dt.weekday()]
    return f"{dt.day} {MONTHS_RU[dt.month]}, {weekday}"


def format_duration(hours: float) -> str:
    """
    Форматирует длительность в читаемый вид.
    
    Args:
        hours: 1.5
    
    Returns:
        "1 ч 30 мин" / "1 ч" / "30 мин"
    """
    total_min = int(hours * 60)
    h, m = divmod(total_min, 60)
    
    if h and m:
        return f"{h} ч {m} мин"
    elif h:
        return f"{h} ч"
    return f"{m} мин"


def format_time_range(start: str, end: str) -> str:
    """
    Форматирует временной диапазон.
    
    Args:
        start: "14:30"
        end: "16:00"
    
    Returns:
        "14:30 — 16:00 МСК"
    """
    return f"{start} — {end} МСК"


# ══════════════════════════════════════════════════════════════
# ВЫЧИСЛЕНИЯ
# ══════════════════════════════════════════════════════════════


def get_today_date() -> str:
    """Возвращает сегодняшнюю дату в формате YYYY-MM-DD."""
    return now_msk().date().isoformat()


def get_tomorrow_date() -> str:
    """Возвращает завтрашнюю дату в формате YYYY-MM-DD."""
    return (now_msk().date() + timedelta(days=1)).isoformat()


def calculate_duration_hours(start_time: str, end_time: str) -> float:
    """
    Вычисляет длительность в часах.
    
    Args:
        start_time: "14:30"
        end_time: "16:00"
    
    Returns:
        1.5
    """
    start_h, start_m = map(int, start_time.split(":"))
    end_h, end_m = map(int, end_time.split(":"))
    
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    
    return (end_minutes - start_minutes) / 60


def minutes_until(target_dt: datetime) -> int:
    """
    Вычисляет количество минут до целевого времени.
    
    Args:
        target_dt: целевое время (с timezone)
    
    Returns:
        количество минут (может быть отрицательным)
    """
    now = now_msk()
    delta = target_dt - now
    return int(delta.total_seconds() / 60)


def is_past(dt: datetime) -> bool:
    """Проверяет, прошло ли время."""
    return dt < now_msk()


def is_future(dt: datetime) -> bool:
    """Проверяет, в будущем ли время."""
    return dt > now_msk()
