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

    try:
        client_initialized = await user_client.initialize()
        if client_initialized:
            logger.info("✅ Пользовательский клиент инициализирован")
        else:
            logger.warning("⚠️ Пользовательский клиент требует авторизации")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации пользовательского клиента: {e}")

    try:
        os.makedirs("documents", exist_ok=True)
        logger.info("📁 Папка для документов создана")
    except Exception as e:
        logger.error(f"❌ Ошибка при создании папки documents: {e}")
    try:
        register_handlers(dp)
        logger.info("✅ Хендлеры зарегистрированы")
    except Exception as e:
        logger.error(f"❌ Ошибка при регистрации хендлеров: {e}")

    logger.info("✅ Инициализация завершена")


async def on_shutdown():
    logger.info("🛑 Остановка ИИ-Судьи...")

    try:
        await user_client.disconnect()
    except Exception as e:
        logger.error(f"❌ Ошибка при отключении user_client: {e}")

    if db.pool:
        try:
            await db.pool.close()
            logger.info("✅ Соединение с базой данных закрыто")
        except Exception as e:
            logger.error(f"❌ Ошибка при закрытии БД: {e}")

    try:
        await bot.session.close()
        logger.info("✅ Бот остановлен")
    except Exception as e:
        logger.error(f"❌ Ошибка при закрытии сессии бота: {e}")


async def run_bot():
    try:
        await on_startup()
        logger.info("🔄 Начинается поллинг бота...")
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"❌ Критическая ошибка во время работы бота: {e}", exc_info=True)
    finally:
        await on_shutdown()


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

    while True:
        try:
            await run_bot()
        except KeyboardInterrupt:
            logger.info("⌨️ Получен сигнал остановки")
            break
        except Exception as e:
            logger.error(f"🔥 Бот упал с ошибкой: {e}, перезапуск через 5 секунд...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    if sys.version_info < (3, 8):
        logger.error("❌ Требуется Python 3.8 или новее")
        sys.exit(1)
    asyncio.run(main())
