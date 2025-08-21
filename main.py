# main.py
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

# ----------------- Логирование -----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ----------------- FSM и бот -----------------
storage = MemoryStorage()
bot = Bot(
    token=settings.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")  # задаём parse_mode для всего бота
)
dp = Dispatcher(storage=storage)


# ----------------- СТАРТ/СТОП -----------------
async def on_startup():
    logger.info("🚀 Запуск ИИ-Судьи...")
    try:
        await db.connect()
        logger.info("✅ Подключение к базе данных успешно")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к базе данных: {e}")
        raise e

    os.makedirs("documents", exist_ok=True)
    logger.info("📁 Папка для документов создана")

    # Регистрируем хендлеры
    register_handlers(dp)
    logger.info("✅ Хендлеры зарегистрированы")
    logger.info("✅ Инициализация завершена")


async def on_shutdown():
    logger.info("🛑 Остановка ИИ-Судьи...")
    if db.pool:
        await db.pool.close()
        logger.info("✅ Соединение с базой данных закрыто")
    await bot.session.close()
    logger.info("✅ Бот остановлен")


# ----------------- ОСНОВНАЯ ФУНКЦИЯ -----------------
async def main():
    if not settings.BOT_TOKEN:
        logger.error("❌ Не указан BOT_TOKEN")
        return
    if not settings.DATABASE_URL:
        logger.error("❌ Не указан DATABASE_URL")
        return

    try:
        await on_startup()
        logger.info("🔄 Начинается поллинг бота...")

        # Запуск поллинга
        await dp.start_polling(bot, skip_updates=True)

    except KeyboardInterrupt:
        logger.info("⌨️ Получен сигнал остановки")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        raise e
    finally:
        await on_shutdown()


# ----------------- ЗАПУСК -----------------
if __name__ == "__main__":
    if sys.version_info < (3, 8):
        logger.error("❌ Требуется Python 3.8 или новее")
        sys.exit(1)
    asyncio.run(main())
