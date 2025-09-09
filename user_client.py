import logging
from typing import Optional
from telethon import TelegramClient
from telethon.tl.functions.channels import InviteToChannelRequest, CreateChannelRequest
from database import db
from conf import settings

logger = logging.getLogger(__name__)


SESSION_FILE = "user.session"


class UserClient:
    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.is_connected = False
        self.session_file = "user.session"

    async def initialize(self) -> bool:
        try:
            self.client = TelegramClient(self.session_file, settings.API_ID, settings.API_HASH)
            await self.client.start()
            if await self.client.is_user_authorized():
                self.is_connected = True
                return True
            return False
        except Exception as e:
            logger.error(f"Ошибка подключения через .session: {e}")
            return False

    async def authorize_user(self, phone: str) -> dict:
        """Авторизация пользователя (если сессии нет)"""
        try:
            if not self.client:
                self.client = TelegramClient(
                    SESSION_FILE,
                    settings.API_ID,
                    settings.API_HASH
                )

            await self.client.connect()

            # Отправка кода
            sent_code = await self.client.send_code_request(phone)

            return {
                'success': True,
                'phone_code_hash': sent_code.phone_code_hash,
                'message': 'Код отправлен на телефон'
            }

        except Exception as e:
            logger.error(f"❌ Ошибка при отправке кода: {e}")
            return {'success': False, 'error': str(e)}

    async def complete_auth(self, phone: str, phone_code_hash: str, code: str) -> dict:
        """Завершение авторизации"""
        try:
            await self.client.sign_in(phone, code, phone_code_hash=phone_code_hash)

            # Сохранение сессии
            self.session_string = self.client.session.save()
            await db.save_user_session(self.session_string)

            self.is_connected = True
            logger.info("✅ Авторизация завершена успешно")

            return {'success': True, 'message': 'Авторизация успешна'}

        except Exception as e:
            logger.error(f"❌ Ошибка при завершении авторизации: {e}")
            return {'success': False, 'error': str(e)}

    async def create_dispute_group(self, case_number: str, case_topic: str, creator_id: int) -> Optional[dict]:
        """Создание супергруппы для рассмотрения дела"""
        if not self.is_connected:
            logger.error("Пользовательский клиент не подключен")
            return None

        try:
            group_title = f"⚖️ Дело {case_number}"

            # Создание супергруппы (аналог обычного группового чата)
            result = await self.client(CreateChannelRequest(
                title=group_title,
                about=f"Группа для рассмотрения дела №{case_number}. Тема: {case_topic}",
                megagroup=True
            ))

            chat = result.chats[0]
            chat_id = chat.id

            # Добавление создателя (если это не он сам)
            try:
                creator = await self.client.get_entity(creator_id)
                await self.client(InviteToChannelRequest(
                    channel=chat,
                    users=[creator]
                ))
            except Exception as e:
                logger.warning(f"⚠️ Не удалось добавить создателя {creator_id} в группу: {e}")

            # Добавление бота в группу
            bot_username = settings.BOT_USERNAME.replace('@', '')
            try:
                bot_entity = await self.client.get_entity(bot_username)
                await self.client(InviteToChannelRequest(
                    channel=chat,
                    users=[bot_entity]
                ))
                logger.info(f"✅ Бот добавлен в группу {chat_id}")
            except Exception as e:
                logger.error(f"❌ Не удалось добавить бота в группу: {e}")

            # Сохраняем информацию о группе в БД
            await db.save_dispute_group(case_number, chat_id, group_title)

            logger.info(f"✅ Супергруппа создана: {group_title} (ID: {chat_id})")

            return {
                'chat_id': chat_id,
                'title': group_title,
                'invite_link': f"https://t.me/c/{str(chat_id)[4:]}"  # для приватных супергрупп
            }

        except Exception as e:
            logger.error(f"❌ Ошибка при создании группы: {e}")
            return None

    async def add_user_to_group(self, chat_id: int, user_id: int) -> bool:
        """Добавление пользователя в группу"""
        if not self.is_connected:
            return False

        try:
            user = await self.client.get_entity(user_id)
            await self.client(InviteToChannelRequest(
                channel=chat_id,
                users=[user]
            ))
            logger.info(f"✅ Пользователь {user_id} добавлен в группу {chat_id}")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка при добавлении пользователя в группу: {e}")
            return False

    async def get_group_info(self, chat_id: int) -> Optional[dict]:
        """Получение информации о группе"""
        if not self.is_connected:
            return None

        try:
            chat = await self.client.get_entity(chat_id)
            return {
                'id': chat.id,
                'title': chat.title,
                'participants_count': getattr(chat, 'participants_count', 0)
            }
        except Exception as e:
            logger.error(f"❌ Ошибка при получении информации о группе: {e}")
            return None

    async def disconnect(self):
        """Отключение клиента"""
        if self.client and self.client.is_connected():
            await self.client.disconnect()
            self.is_connected = False
            logger.info("🔌 Пользовательский клиент отключен")


# Глобальный экземпляр клиента
user_client = UserClient()