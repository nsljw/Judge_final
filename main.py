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


class BotApplication:
    """Класс для управления жизненным циклом бота"""

    def __init__(self):
        self.redis = None
        self.storage = None
        self.bot = None
        self.dp = None
        self.scheduler = None
        self.is_running = False

    async def initialize(self):
        """Инициализация всех компонентов"""
        if self.is_running:
            logger.warning("⚠️ Бот уже запущен, пропускаем инициализацию")
            return

        logger.info("🚀 Запуск ИИ-Судьи...")

        # Создание Redis подключения
        try:
            self.redis = Redis(
                host=settings.REDIS_HOST or "localhost",
                port=settings.REDIS_PORT or 6379,
                password=settings.REDIS_PASSWORD or "38856",
                db=settings.REDIS_DB or 0,
                decode_responses=True
            )
            await self.redis.ping()
            logger.info("✅ Подключение к Redis успешно")
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к Redis: {e}")
            raise

        # Создание storage
        self.storage = RedisStorage(
            redis=self.redis,
            state_ttl=3600 * 24 * 7,
            data_ttl=3600 * 24 * 7
        )

        # Создание бота и диспетчера
        self.bot = Bot(
            token=settings.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode="HTML")
        )
        self.dp = Dispatcher(storage=self.storage)

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
            register_handlers(self.dp)
            logger.info("✅ Хендлеры зарегистрированы")
        except Exception as e:
            logger.error(f"❌ Ошибка при регистрации хендлеров: {e}")
            raise

        # Запуск планировщика задач
        self.scheduler = AsyncIOScheduler()
        self.scheduler.add_job(
            db.clean_old_records,
            "interval",
            days=CLEAN_INTERVAL_DAYS,
            id="clean_old_records"
        )
        self.scheduler.start()
        logger.info(f"🕒 Планировщик запущен: очистка каждые {CLEAN_INTERVAL_DAYS} дня")

        self.is_running = True
        logger.info("✅ Инициализация завершена")

    async def shutdown(self):
        """Корректное завершение работы"""
        if not self.is_running:
            logger.warning("⚠️ Бот не запущен, пропускаем shutdown")
            return

        logger.info("🛑 Остановка ИИ-Судьи...")

        # Остановка планировщика
        if self.scheduler and self.scheduler.running:
            try:
                self.scheduler.shutdown(wait=False)
                logger.info("✅ Планировщик остановлен")
            except Exception as e:
                logger.error(f"❌ Ошибка при остановке планировщика: {e}")

        # Остановка polling если активен
        if self.dp:
            try:
                await self.dp.stop_polling()
                logger.info("✅ Polling остановлен")
            except Exception as e:
                logger.error(f"❌ Ошибка при остановке polling: {e}")

        # Закрытие сессии бота
        if self.bot:
            try:
                await self.bot.session.close()
                logger.info("✅ Сессия бота закрыта")
            except Exception as e:
                logger.error(f"❌ Ошибка при закрытии сессии бота: {e}")

        # Закрытие storage
        if self.storage:
            try:
                await self.storage.close()
                logger.info("✅ Storage закрыт")
            except Exception as e:
                logger.error(f"❌ Ошибка при закрытии storage: {e}")

        # Закрытие подключения к базе данных
        if db.pool:
            try:
                await db.pool.close()
                logger.info("✅ Соединение с базой данных закрыто")
            except Exception as e:
                logger.error(f"❌ Ошибка при закрытии БД: {e}")

        # Закрытие Redis подключения
        if self.redis:
            try:
                await self.redis.aclose()
                logger.info("✅ Redis подключение закрыто")
            except Exception as e:
                logger.error(f"❌ Ошибка при закрытии Redis: {e}")

        self.is_running = False
        logger.info("✅ Shutdown завершен")

    async def run(self):
        """Основной цикл работы бота"""
        try:
            await self.initialize()

            # Запуск polling
            logger.info("🔄 Начинается поллинг бота...")
            await self.dp.start_polling(
                self.bot,
                skip_updates=True,
                allowed_updates=self.dp.resolve_used_update_types()
            )
        except asyncio.CancelledError:
            logger.info("⚠️ Получен сигнал отмены")
        except Exception as e:
            logger.error(f"❌ Критическая ошибка во время работы бота: {e}", exc_info=True)
            raise
        finally:
            await self.shutdown()


async def main():
    """Точка входа в приложение"""
    # Проверка обязательных переменных окружения
    if not settings.BOT_TOKEN:
        logger.error("❌ Не указан BOT_TOKEN")
        return
    if not settings.DATABASE_URL:
        logger.error("❌ Не указан DATABASE_URL")
        return

    # Основной цикл с автоматическим перезапуском
    restart_count = 0
    max_restarts = 10  # Максимальное количество перезапусков

    while restart_count < max_restarts:
        app = BotApplication()

        try:
            await app.run()
            # Если выход был нормальным (не исключение), прерываем цикл
            break
        except KeyboardInterrupt:
            logger.info("⌨️ Получен сигнал остановки")
            await app.shutdown()
            break
        except Exception as e:
            restart_count += 1
            logger.error(
                f"🔥 Бот упал с ошибкой: {e}, "
                f"перезапуск {restart_count}/{max_restarts} через 5 секунд...",
                exc_info=True
            )

            # Принудительная очистка ресурсов
            try:
                await app.shutdown()
            except Exception as cleanup_error:
                logger.error(f"Ошибка при очистке ресурсов: {cleanup_error}")

            # Пауза перед перезапуском
            await asyncio.sleep(5)

    if restart_count >= max_restarts:
        logger.error(f"❌ Достигнут лимит перезапусков ({max_restarts}), завершение работы")


if __name__ == "__main__":
    if sys.version_info < (3, 8):
        logger.error("❌ Требуется Python 3.8 или новее")
        sys.exit(1)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Программа завершена пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)