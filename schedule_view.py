"""Форматирование расписания и истории броней."""

import logging
from typing import List
from database import Booking
from timezone_utils import format_date_ru, format_duration, format_time_range

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# РАСПИСАНИЕ БРОНЕЙ
# ══════════════════════════════════════════════════════════════


def format_schedule(bookings: List[Booking], dates: List[str]) -> str:
    """
    Форматирует расписание броней на указанные даты.
    
    Args:
        bookings: список броней
        dates: список дат для отображения
    
    Returns:
        отформатированный текст расписания
    """
    if not bookings:
        return "📋 Расписание броней (МСК)\n\nНет активных броней."
    
    text = "📋 Расписание броней (МСК)\n\n"
    
    for date in dates:
        date_bookings = [b for b in bookings if b.date == date]
        
        text += f"📅 {format_date_ru(date)}:\n"
        text += "─" * 30 + "\n"
        
        if date_bookings:
            for booking in sorted(date_bookings, key=lambda x: x.start_time):
                status_emoji = "🟢" if booking.status == "confirmed" else "🟡"
                status_text = "" if booking.status == "confirmed" else " [ожидает подтв.]"
                
                text += (
                    f"{status_emoji} {booking.start_time} — {booking.end_time} │ "
                    f"{booking.mangabuff_nick}    "
                    f"({format_duration(booking.duration_hours)}){status_text}\n"
                )
            
            # Добавляем информацию о свободных слотах
            text += f"🆓 Остальное время свободно\n"
        else:
            text += "🆓 Весь день свободен\n"
        
        text += "\n"
    
    text += (
        "Легенда:\n"
        "🟢 подтверждена  🟡 ожидает подтв.  🆓 свободно"
    )
    
    return text


# ══════════════════════════════════════════════════════════════
# ИСТОРИЯ БРОНЕЙ
# ══════════════════════════════════════════════════════════════


def format_user_history(bookings: List[Booking]) -> str:
    """
    Форматирует историю броней пользователя.
    
    Args:
        bookings: список броней пользователя
    
    Returns:
        отформатированный текст истории
    """
    if not bookings:
        return "📜 История моих броней:\n\nУ тебя пока нет броней."
    
    text = "📜 История моих броней:\n\n"
    
    for booking in bookings:
        emoji = _get_status_emoji(booking.status)
        status_text = _get_status_text(booking.status, booking.cancelled_by)
        
        text += (
            f"{emoji} {format_date_ru(booking.date)} | "
            f"{booking.start_time} — {booking.end_time} МСК | "
            f"{status_text}\n"
        )
    
    text += f"\nПоказаны последние {len(bookings)} броней."
    
    return text


def format_all_history(bookings: List[Booking]) -> str:
    """
    Форматирует полную историю всех броней.
    
    Args:
        bookings: список всех броней
    
    Returns:
        отформатированный текст истории
    """
    if not bookings:
        return "📜 Полная история броней:\n\nБроней пока нет."
    
    text = "📜 Полная история броней (последние 50):\n\n"
    
    for booking in bookings:
        emoji = _get_status_emoji(booking.status)
        status_text = _get_status_text(booking.status, booking.cancelled_by)
        
        text += (
            f"{emoji} {format_date_ru(booking.date)} "
            f"{booking.start_time}–{booking.end_time} МСК │ "
            f"{booking.mangabuff_nick} │ "
            f"{status_text}\n"
        )
    
    return text


def format_user_bookings(bookings: List[Booking]) -> str:
    """
    Форматирует список активных броней пользователя.
    
    Args:
        bookings: список активных броней
    
    Returns:
        отформатированный текст
    """
    if not bookings:
        return "📋 Мои брони:\n\nУ тебя нет активных броней."
    
    text = "📋 Мои активные брони:\n\n"
    
    for booking in bookings:
        status_emoji = "🟢" if booking.status == "confirmed" else "🟡"
        status_text = "подтверждена" if booking.status == "confirmed" else "ожидает подтверждения"
        
        text += (
            f"{status_emoji} {format_date_ru(booking.date)}\n"
            f"🕐 {format_time_range(booking.start_time, booking.end_time)}\n"
            f"⏱ {format_duration(booking.duration_hours)}\n"
            f"📊 Статус: {status_text}\n\n"
        )
    
    return text


# ══════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ══════════════════════════════════════════════════════════════


def _get_status_emoji(status: str) -> str:
    """Возвращает emoji для статуса брони."""
    emoji_map = {
        "pending": "🟡",
        "confirmed": "✅",
        "completed": "✅",
        "cancelled": "❌",
        "cancelled_by_user": "🚫",
        "cancelled_by_admin": "🔧"
    }
    return emoji_map.get(status, "❓")


def _get_status_text(status: str, cancelled_by: str = None) -> str:
    """Возвращает текстовое описание статуса."""
    status_map = {
        "pending": "ожидает подтв.",
        "confirmed": "подтверждена",
        "completed": "завершена",
        "cancelled": "отменена (таймаут)",
        "cancelled_by_user": "отменена мной",
        "cancelled_by_admin": "отменена админом"
    }
    
    # Для отменённых броней уточняем причину
    if status.startswith("cancelled"):
        if cancelled_by == "system":
            return "отменена (таймаут)"
        elif cancelled_by == "user":
            return "отменена пользователем"
        elif cancelled_by == "admin":
            return "отменена админом"
    
    return status_map.get(status, status)
