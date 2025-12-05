import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from redis.asyncio import Redis

from conf import settings, CLEAN_INTERVAL_DAYS
from database import db
from handlers import register_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


redis = Redis(
    host=settings.REDIS_HOST or "localhost",
    port=settings.REDIS_PORT or 6379,
    password=settings.REDIS_PASSWORD or "38856",
    db=settings.REDIS_DB or 0,
    decode_responses=True
)

storage = RedisStorage(
    redis=redis,
    state_ttl=3600 * 24 * 7,
    data_ttl=3600 * 24 * 7
)

bot = Bot(
    token=settings.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher(storage=storage)


async def on_startup():
    """Инициализация при запуске бота"""
    logger.info("🚀 Запуск ИИ-Судьи...")

    # Проверка подключения к Redis
    try:
        await redis.ping()
        logger.info("✅ Подключение к Redis успешно")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к Redis: {e}")
        raise

    # Подключение к базе данных
    try:
        await db.connect()
        await db.create_additional_tables()
        logger.info("✅ Подключение к базе данных успешно")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к базе данных: {e}")
        raise

    # Создание директории для документов
    try:
        os.makedirs("documents", exist_ok=True)
        logger.info("📁 Папка для документов создана")
    except Exception as e:
        logger.error(f"❌ Ошибка при создании папки documents: {e}")

    # Регистрация хендлеров
    try:
        register_handlers(dp)
        logger.info("✅ Хендлеры зарегистрированы")
    except Exception as e:
        logger.error(f"❌ Ошибка при регистрации хендлеров: {e}")
        raise

    logger.info("✅ Инициализация завершена")


async def on_shutdown():
    """Корректное завершение работы бота"""
    logger.info("🛑 Остановка ИИ-Судьи...")

    # Закрытие подключения к базе данных
    if db.pool:
        try:
            await db.pool.close()
            logger.info("✅ Соединение с базой данных закрыто")
        except Exception as e:
            logger.error(f"❌ Ошибка при закрытии БД: {e}")

    # Закрытие Redis подключения
    try:
        await redis.close()
        logger.info("✅ Redis подключение закрыто")
    except Exception as e:
        logger.error(f"❌ Ошибка при закрытии Redis: {e}")

    # Закрытие сессии бота
    try:
        await bot.session.close()
        logger.info("✅ Бот остановлен")
    except Exception as e:
        logger.error(f"❌ Ошибка при закрытии сессии бота: {e}")


async def run_bot():
    """Основной цикл работы бота"""
    scheduler = None
    try:
        await on_startup()

        # Запуск планировщика задач
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            db.clean_old_records,
            "interval",
            days=CLEAN_INTERVAL_DAYS,
            id="clean_old_records"
        )
        scheduler.start()
        logger.info(f"🕒 Планировщик запущен: очистка каждые {CLEAN_INTERVAL_DAYS} дня")

        # Запуск polling
        logger.info("🔄 Начинается поллинг бота...")
        await dp.start_polling(
            bot,
            skip_updates=True,
            allowed_updates=dp.resolve_used_update_types()
        )
    except Exception as e:
        logger.error(f"❌ Критическая ошибка во время работы бота: {e}", exc_info=True)
    finally:
        # Остановка планировщика
        if scheduler and scheduler.running:
            scheduler.shutdown(wait=False)
            logger.info("✅ Планировщик остановлен")

        await on_shutdown()


async def main():
    """Точка входа в приложение"""
    # Проверка обязательных переменных окружения
    if not settings.BOT_TOKEN:
        logger.error("❌ Не указан BOT_TOKEN")
        return
    if not settings.DATABASE_URL:
        logger.error("❌ Не указан DATABASE_URL")
        return
    if not settings.API_ID or not settings.API_HASH:
        logger.error("❌ Не указаны API_ID или API_HASH для Telegram API")
        return

    # Основной цикл с автоматическим перезапуском
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

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Программа завершена пользователем")