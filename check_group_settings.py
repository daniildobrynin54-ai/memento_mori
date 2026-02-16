"""Диагностический скрипт для проверки работы бота в группе."""

import asyncio
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# Вставьте ваш токен
BOT_TOKEN = "8319843979:AAGM9m3V1IlfBjxl8X-hWSWBMLSXjoYXg80"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


async def log_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Логирует ВСЕ входящие сообщения."""
    if update.message:
        chat = update.message.chat
        user = update.message.from_user
        text = update.message.text or "[без текста]"
        
        logger.info("=" * 60)
        logger.info("📨 ПОЛУЧЕНО СООБЩЕНИЕ:")
        logger.info(f"   Тип чата: {chat.type}")
        logger.info(f"   ID чата: {chat.id}")
        logger.info(f"   Название чата: {chat.title if chat.title else 'N/A'}")
        logger.info(f"   Username чата: @{chat.username if chat.username else 'N/A'}")
        logger.info(f"")
        logger.info(f"   От пользователя: {user.full_name}")
        logger.info(f"   Username: @{user.username if user.username else 'нет'}")
        logger.info(f"   ID пользователя: {user.id}")
        logger.info(f"")
        logger.info(f"   Текст: {text}")
        logger.info("=" * 60)
        
        # Отвечаем в любом чате
        try:
            await update.message.reply_text(
                f"✅ Получил сообщение!\n\n"
                f"Тип чата: {chat.type}\n"
                f"ID чата: {chat.id}\n"
                f"Текст: {text}"
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке ответа: {e}")


async def main():
    """Запуск диагностического бота."""
    logger.info("=" * 60)
    logger.info("🔍 ДИАГНОСТИЧЕСКИЙ РЕЖИМ")
    logger.info("=" * 60)
    logger.info("")
    logger.info("Этот бот будет логировать ВСЕ сообщения, которые он получает.")
    logger.info("Используйте его для проверки:")
    logger.info("  1. Получает ли бот сообщения из группы?")
    logger.info("  2. Какой ID у вашей группы?")
    logger.info("  3. Какой тип у чата (group/supergroup)?")
    logger.info("")
    logger.info("ИНСТРУКЦИЯ:")
    logger.info("  1. Добавьте бота в группу (если еще не добавлен)")
    logger.info("  2. Напишите что-нибудь в группе")
    logger.info("  3. Напишите что-нибудь боту в личные сообщения")
    logger.info("  4. Посмотрите на логи ниже")
    logger.info("")
    logger.info("=" * 60)
    logger.info("")
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Ловим ВСЕ сообщения (текстовые, команды, всё)
    application.add_handler(
        MessageHandler(
            filters.ALL,  # ВСЕ типы сообщений
            log_all_messages
        )
    )
    
    logger.info("🚀 Бот запущен...")
    logger.info("📝 Ожидаю сообщения...")
    logger.info("")
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    
    try:
        # Ждем, пока не прервут
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("\n⏹ Остановка...")
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Программа прервана")