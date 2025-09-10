import asyncio
import logging
import sys
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from conf import settings
from database import db
from handlers import register_handlers
from user_client import user_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

storage = MemoryStorage()
bot = Bot(
    token=settings.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher(storage=storage)


async def on_startup():
    logger.info("🚀 Запуск ИИ-Судьи...")
    try:
        await db.connect()
        await db.create_additional_tables()
        logger.info("✅ Подключение к базе данных успешно")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к базе данных: {e}")
        raise e

    try:
        client_initialized = await user_client.initialize()
        if client_initialized:
            logger.info("✅ Пользовательский клиент инициализирован")
        else:
            logger.warning("⚠️ Пользовательский клиент требует авторизации")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации пользовательского клиента: {e}")

    os.makedirs("documents", exist_ok=True)
    logger.info("📁 Папка для документов создана")

    register_handlers(dp)
    logger.info("✅ Хендлеры зарегистрированы")
    logger.info("✅ Инициализация завершена")


async def on_shutdown():
    logger.info("🛑 Остановка ИИ-Судьи...")

    await user_client.disconnect()

    if db.pool:
        await db.pool.close()
        logger.info("✅ Соединение с базой данных закрыто")
    await bot.session.close()
    logger.info("✅ Бот остановлен")


async def main():
    if not settings.BOT_TOKEN:
        logger.error("❌ Не указан BOT_TOKEN")
        return
    if not settings.DATABASE_URL:
        logger.error("❌ Не указан DATABASE_URL")
        return
    if not settings.API_ID or not settings.API_HASH:
        logger.error("❌ Не указаны API_ID или API_HASH для Telegram API")
        return

    try:
        await on_startup()
        logger.info("🔄 Начинается поллинг бота...")

        await dp.start_polling(bot, skip_updates=True)

    except KeyboardInterrupt:
        logger.info("⌨️ Получен сигнал остановки")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        raise e
    finally:
        await on_shutdown()


if __name__ == "__main__":
    if sys.version_info < (3, 8):
        logger.error("❌ Требуется Python 3.8 или новее")
        sys.exit(1)
    asyncio.run(main())