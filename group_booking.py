"""Бронирование через inline-кнопки в группе."""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from database import (
    get_user,
    get_user_active_bookings,
    create_booking,
    get_bookings_for_schedule,
    add_booking_event
)
from timezone_utils import (
    get_today_date,
    get_tomorrow_date,
    format_date_ru,
    format_duration,
    calculate_duration_hours,
    ts_for_db,
    now_msk
)
from booking_validator import (
    get_available_start_slots,
    get_available_end_slots,
    validate_booking_slot
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# ГЛАВНОЕ МЕНЮ БРОНИРОВАНИЯ
# ══════════════════════════════════════════════════════════════


async def show_booking_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню выбора даты для бронирования."""
    user = update.effective_user
    
    # Проверка верификации
    db_user = await get_user(user.id)
    if not db_user or not db_user.is_verified:
        await update.message.reply_text(
            "❌ Для бронирования нужно привязать аккаунт.\n"
            "Напиши мне в личные сообщения: /start"
        )
        return
    
    # Проверка существующих броней
    today = get_today_date()
    tomorrow = get_tomorrow_date()
    
    existing = await get_user_active_bookings(user.id, [today, tomorrow])
    if existing:
        text = "📋 У тебя уже есть активные брони:\n\n"
        for b in existing:
            status_emoji = "🟢" if b.status == "confirmed" else "🟡"
            text += (
                f"{status_emoji} {format_date_ru(b.date)} | "
                f"🕐 {b.start_time} — {b.end_time} МСК\n"
            )
        text += "\n⚠️ Одна дата — одна бронь."
        
        await update.message.reply_text(text)
        return
    
    # Показываем меню выбора даты
    keyboard = [
        [
            InlineKeyboardButton(
                f"📅 Сегодня, {format_date_ru(today)}",
                callback_data=f"book_date:{today}"
            )
        ],
        [
            InlineKeyboardButton(
                f"📅 Завтра, {format_date_ru(tomorrow)}",
                callback_data=f"book_date:{tomorrow}"
            )
        ]
    ]
    
    await update.message.reply_text(
        "📅 Выбери дату для бронирования:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ══════════════════════════════════════════════════════════════
# ВЫБОР ДАТЫ
# ══════════════════════════════════════════════════════════════


async def handle_date_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора даты."""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    date = query.data.split(":")[1]
    
    # Получаем занятые брони на эту дату
    busy_bookings = await get_bookings_for_schedule([date])
    
    # Получаем доступные слоты начала
    available_slots = get_available_start_slots(date, busy_bookings)
    
    if not available_slots:
        await query.edit_message_text(
            f"😔 На {format_date_ru(date)} все слоты заняты.\n"
            f"Попробуй выбрать другую дату."
        )
        return
    
    # Формируем клавиатуру со слотами (по 4 в ряд)
    keyboard = []
    row = []
    for slot in available_slots[:20]:  # Ограничиваем 20 слотами
        row.append(
            InlineKeyboardButton(
                slot,
                callback_data=f"book_start:{date}:{slot}"
            )
        )
        if len(row) == 4:
            keyboard.append(row)
            row = []
    
    if row:  # Добавляем оставшиеся кнопки
        keyboard.append(row)
    
    # Кнопка "Назад"
    keyboard.append([
        InlineKeyboardButton("◀️ Назад", callback_data="book_menu")
    ])
    
    await query.edit_message_text(
        f"🕐 Дата: {format_date_ru(date)}\n\n"
        f"Выбери время начала брони:\n"
        f"(максимальная длительность — 2 часа)",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ══════════════════════════════════════════════════════════════
# ВЫБОР ВРЕМЕНИ НАЧАЛА
# ══════════════════════════════════════════════════════════════


async def handle_start_time_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора времени начала."""
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split(":")
    date = parts[1]
    start_time = parts[2]
    
    # Получаем занятые брони
    busy_bookings = await get_bookings_for_schedule([date])
    
    # Получаем доступные слоты окончания
    available_slots = get_available_end_slots(date, start_time, busy_bookings)
    
    if not available_slots:
        await query.edit_message_text(
            "😔 Нет доступных слотов окончания для этого времени.\n"
            "Попробуй другое время начала."
        )
        return
    
    # Формируем клавиатуру
    keyboard = []
    row = []
    for slot in available_slots:
        row.append(
            InlineKeyboardButton(
                slot,
                callback_data=f"book_end:{date}:{start_time}:{slot}"
            )
        )
        if len(row) == 4:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    # Кнопка "Назад"
    keyboard.append([
        InlineKeyboardButton("◀️ Назад", callback_data=f"book_date:{date}")
    ])
    
    await query.edit_message_text(
        f"🕐 Дата: {format_date_ru(date)}\n"
        f"⏰ Начало: {start_time}\n\n"
        f"Выбери время окончания:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ══════════════════════════════════════════════════════════════
# СОЗДАНИЕ БРОНИ
# ══════════════════════════════════════════════════════════════


async def handle_end_time_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора времени окончания и создание брони."""
    query = update.callback_query
    await query.answer("⏳ Создаю бронь...")
    
    user = query.from_user
    parts = query.data.split(":")
    date = parts[1]
    start_time = parts[2]
    end_time = parts[3]
    
    # Проверка верификации
    db_user = await get_user(user.id)
    if not db_user or not db_user.is_verified:
        await query.edit_message_text(
            "❌ Для бронирования нужно привязать аккаунт.\n"
            "Напиши мне в личные сообщения: /start"
        )
        return
    
    # Финальная валидация (race condition guard)
    is_valid, error_msg = await validate_booking_slot(date, start_time, end_time)
    
    if not is_valid:
        await query.edit_message_text(
            f"⚠️ {error_msg}\n"
            f"Кто-то успел забронировать этот слот быстрее."
        )
        return
    
    # Вычисляем длительность
    duration_hours = calculate_duration_hours(start_time, end_time)
    
    # Создаём бронь
    booking_id = await create_booking(
        tg_id=db_user.tg_id,
        tg_nickname=db_user.tg_nickname,
        mangabuff_nick=db_user.mangabuff_nick,
        date=date,
        start_time=start_time,
        end_time=end_time,
        duration_hours=duration_hours
    )
    
    # Отправляем подтверждение
    await query.edit_message_text(
        f"✅ Бронь успешно создана!\n\n"
        f"🃏 Назначение: внос карт в клуб Таро\n"
        f"📅 Дата: {format_date_ru(date)}\n"
        f"🕐 Время: {start_time} — {end_time} МСК\n"
        f"⏱ Длительность: {format_duration(duration_hours)}\n"
        f"👤 {db_user.tg_nickname} / {db_user.mangabuff_nick}\n\n"
        f"⚠️ За 5 минут до начала придёт уведомление.\n"
        f"Не подтвердишь в течение 5 минут после начала — бронь отменится."
    )
    
    logger.info(
        f"✅ Создана бронь #{booking_id} из группы: {db_user.tg_nickname} "
        f"на {date} {start_time}-{end_time}"
    )


# ══════════════════════════════════════════════════════════════
# ВОЗВРАТ В ГЛАВНОЕ МЕНЮ
# ══════════════════════════════════════════════════════════════


async def handle_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат в главное меню бронирования."""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    
    # Проверка верификации
    db_user = await get_user(user.id)
    if not db_user or not db_user.is_verified:
        await query.edit_message_text(
            "❌ Для бронирования нужно привязать аккаунт.\n"
            "Напиши мне в личные сообщения: /start"
        )
        return
    
    # Проверка существующих броней
    today = get_today_date()
    tomorrow = get_tomorrow_date()
    
    existing = await get_user_active_bookings(user.id, [today, tomorrow])
    if existing:
        text = "📋 У тебя уже есть активные брони:\n\n"
        for b in existing:
            status_emoji = "🟢" if b.status == "confirmed" else "🟡"
            text += (
                f"{status_emoji} {format_date_ru(b.date)} | "
                f"🕐 {b.start_time} — {b.end_time} МСК\n"
            )
        text += "\n⚠️ Одна дата — одна бронь."
        
        await query.edit_message_text(text)
        return
    
    # Показываем меню выбора даты
    keyboard = [
        [
            InlineKeyboardButton(
                f"📅 Сегодня, {format_date_ru(today)}",
                callback_data=f"book_date:{today}"
            )
        ],
        [
            InlineKeyboardButton(
                f"📅 Завтра, {format_date_ru(tomorrow)}",
                callback_data=f"book_date:{tomorrow}"
            )
        ]
    ]
    
    await query.edit_message_text(
        "📅 Выбери дату для бронирования:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ══════════════════════════════════════════════════════════════
# РЕГИСТРАЦИЯ HANDLERS
# ══════════════════════════════════════════════════════════════


def register_group_booking_handlers(application):
    """Регистрирует handlers для бронирования в группах."""
    
    # Выбор даты
    application.add_handler(
        CallbackQueryHandler(handle_date_selection, pattern=r"^book_date:")
    )
    
    # Выбор времени начала
    application.add_handler(
        CallbackQueryHandler(handle_start_time_selection, pattern=r"^book_start:")
    )
    
    # Выбор времени окончания (создание брони)
    application.add_handler(
        CallbackQueryHandler(handle_end_time_selection, pattern=r"^book_end:")
    )
    
    # Возврат в меню
    application.add_handler(
        CallbackQueryHandler(handle_back_to_menu, pattern=r"^book_menu$")
    )
    
    logger.info("✅ Handlers для группового бронирования зарегистрированы")