import asyncio
import os
import re
from datetime import datetime, timedelta, timezone

from aiogram import Router, types, F, Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated, CallbackQuery
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from telethon.errors import UserAlreadyParticipantError, UserPrivacyRestrictedError
from telethon.tl.functions.channels import EditAdminRequest, InviteToChannelRequest
from telethon.tl.functions.messages import ExportChatInviteRequest
from telethon.tl.types import ChatAdminRights
from telethon.errors import UsernameInvalidError, UsernameNotOccupiedError, UserPrivacyRestrictedError
from telethon.errors.rpcerrorlist import PeerIdInvalidError, ChannelPrivateError, ChatWriteForbiddenError, ChatAdminRequiredError

from database import db
from gemini_servise import gemini_service
from pdf_gen import PDFGenerator
from user_client import user_client

router = Router()
pdf_generator = PDFGenerator()
CASES_PER_PAGE = 10


class DisputeState(StatesGroup):
    waiting_topic = State()
    waiting_category = State()
    waiting_claim_reason = State()
    waiting_claim_amount = State()
    case_created = State()
    plaintiff_arguments = State()
    case_paused = State()
    waiting_forwarded_messages = State()
    defendant_arguments = State()
    waiting_defendant_username = State()
    waiting_defendant_method = State()
    waiting_message_history = State()
    waiting_defendant_message = State()
    waiting_defendant_confirmation = State()
    waiting_history_dates = State()
    waiting_detailed_datetime = State()
    reviewing_messages = State()
    ai_asking_questions = State()
    waiting_ai_question_response = State()
    finished = State()
    waiting_groupe = State()
    waiting_for_group_add = State()
    stop_plaint_proceed = State()


class MenuState(StatesGroup):
    back_to_menu = State()


class GroupState(StatesGroup):
    waiting_group_name = State()
    waiting_case_number = State()


CATEGORIES = [
    "Нарушение договора",
    "Плагиат. Интеллектуальная собственность",
    "Конфликт/Спор",
    "Долг/Займ",
    "Разделение имущества",
    "Дебаты"
]


def get_main_menu_keyboard():
    """Возвращает клавиатуру главного меню"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚖ Начать Дело")],
            [KeyboardButton(text="📂 Мои дела")],
            [KeyboardButton(text="📝Черновик")],
            [KeyboardButton(text="ℹ️ Справка")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def get_back_to_menu_keyboard():
    """Возвращает клавиатуру с кнопкой возврата в меню"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )


async def return_to_main_menu(message: types.Message, state: FSMContext):
    """Возврат в главное меню"""
    await state.clear()
    kb = get_main_menu_keyboard()
    await message.answer(
        "📋 Главное меню:",
        reply_markup=kb
    )


@router.message(F.text == "🔙 Назад в Меню")
async def back_to_menu_handler(message: types.Message, state: FSMContext):
    """Обработчик кнопки возврата в главное меню"""
    await return_to_main_menu(message, state)


@router.message(F.text == "⛔️ Остановить процесс")
async def stop_proceed(message: types.Message, state: FSMContext):
    """Остановка процесса разборки"""
    data = await state.get_data()
    case_number = data.get("case_number")

    if not case_number:
        await message.answer("⚠️ Невозможно остановить процесс — нет номера дела.")
        return

    user_role = await check_user_role_in_case(case_number, message.from_user.id)
    if user_role != "plaintiff":
        return

    await state.set_state(DisputeState.stop_plaint_proceed)


async def get_chat_history_by_dates(chat_id: int, start_date: datetime, end_date: datetime):
    """Получить историю сообщений из чата по датам через user_client (используя iter_messages)"""
    try:
        if not user_client.is_connected:
            print("❌ User client не подключен")
            return None

        print(f"🔍 Ищу сообщения в чате {chat_id} за период {start_date} - {end_date}")

        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        else:
            start_date = start_date.astimezone(timezone.utc)

        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
        else:
            end_date = end_date.astimezone(timezone.utc)

        messages = []
        total_processed = 0

        async for msg in user_client.client.iter_messages(chat_id, offset_date=end_date):
            total_processed += 1

            if not hasattr(msg, "date") or not msg.date:
                continue

            msg_date = msg.date

            # Если сообщение старше диапазона → прекращаем
            if msg_date < start_date:
                print(f"⏹️ Достигли сообщений старше диапазона: {msg_date}")
                break

            # Если в диапазоне → сохраняем
            if start_date <= msg_date <= end_date:
                from_id = None
                if hasattr(msg, "from_id") and msg.from_id:
                    if hasattr(msg.from_id, "user_id"):
                        from_id = msg.from_id.user_id
                    else:
                        from_id = msg.from_id

                reply_to = None
                if hasattr(msg, "reply_to") and msg.reply_to:
                    if hasattr(msg.reply_to, "reply_to_msg_id"):
                        reply_to = msg.reply_to.reply_to_msg_id

                messages.append({
                    "id": msg.id,
                    "date": msg_date,
                    "from_id": from_id,
                    "message": msg.text,
                    "reply_to": reply_to
                })

        print(f"📊 Итого обработано {total_processed} сообщений, найдено в диапазоне: {len(messages)}")
        return messages

    except Exception as e:
        print(f"❌ Критическая ошибка при получении истории чата: {e}")
        import traceback
        traceback.print_exc()
        return None


async def diagnose_chat_access(chat_id: int):
    """Диагностика доступа к чату"""
    try:
        if not user_client.is_connected:
            return "User client не подключен"

        print(f"🔍 Диагностика доступа к чату {chat_id}")

        try:
            entity = await user_client.client.get_entity(chat_id)
            print(f"✅ Чат найден: {entity.title if hasattr(entity, 'title') else 'Без названия'}")
            print(f"📋 Тип: {type(entity).__name__}")
            return f"Доступ к чату есть: {entity.title if hasattr(entity, 'title') else 'ID: ' + str(chat_id)}"
        except Exception as e:
            print(f"❌ Не удалось получить информацию о чате: {e}")

            alternatives = []
            if chat_id < 0:
                if str(chat_id).startswith('-100'):
                    alternatives.append(int(str(chat_id)[4:]))  # Убираем -100
                else:
                    alternatives.append(abs(chat_id))

            for alt_id in alternatives:
                try:
                    entity = await user_client.client.get_entity(alt_id)
                    print(
                        f"✅ Чат найден с альтернативным ID {alt_id}: {entity.title if hasattr(entity, 'title') else 'Без названия'}")
                    return f"Доступ есть с ID {alt_id}: {entity.title if hasattr(entity, 'title') else 'Чат'}"
                except Exception as e2:
                    print(f"❌ Альтернативный ID {alt_id} тоже не работает: {e2}")

            return f"Нет доступа к чату {chat_id}: {str(e)}"

    except Exception as e:
        return f"Ошибка диагностики: {str(e)}"


async def format_messages_for_review(messages, participants_data):
    """Форматирование сообщений для просмотра"""
    if not messages:
        return "Сообщений за указанный период не найдено."

    messages.sort(key=lambda x: x['date'])

    formatted_text = f"📱 *Найдено {len(messages)} сообщений:*\n\n"

    for i, msg in enumerate(messages, 1):
        date_str = msg['date'].strftime("%d.%m.%Y %H:%M")
        sender = "Неизвестный"

        if msg['from_id']:
            for participant in participants_data:
                if participant.get('user_id') == msg['from_id']:
                    sender = participant.get('username', f"ID{msg['from_id']}")
                    break
            else:
                sender = f"ID{msg['from_id']}"

        formatted_text += f"*{i}.* [{date_str}] **{sender}:**\n{msg['message']}\n\n"

        if i >= 20:
            formatted_text += f"... и еще {len(messages) - 20} сообщений\n"
            break

    return formatted_text


def parse_date_time_input(text: str) -> tuple:
    """
    Парсит различные форматы ввода даты и времени.
    Возвращает (start_date, end_date) или (None, None) при ошибке
    """
    text = text.strip().lower()
    now = datetime.now()

    try:
        if "последний день" in text:
            return now - timedelta(days=1), now
        elif "последняя неделя" in text:
            return now - timedelta(weeks=1), now
        elif "последний месяц" in text:
            return now - timedelta(days=30), now
        elif "сегодня" in text:
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return today_start, now
        elif "вчера" in text:
            yesterday_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            yesterday_end = yesterday_start.replace(hour=23, minute=59, second=59)
            return yesterday_start, yesterday_end

        if " - " in text:
            date_parts = text.split(" - ")
            if len(date_parts) == 2:
                start_str = date_parts[0].strip()
                end_str = date_parts[1].strip()

                start_date = parse_single_datetime(start_str)
                end_date = parse_single_datetime(end_str)

                if start_date and end_date:
                    return start_date, end_date

        single_date = parse_single_datetime(text)
        if single_date:
            if ":" in text and "." not in text:
                date_part = now.replace(hour=single_date.hour, minute=single_date.minute, second=0, microsecond=0)
                return date_part, date_part + timedelta(hours=1)
            else:
                day_start = single_date.replace(hour=0, minute=0, second=0, microsecond=0)
                day_end = single_date.replace(hour=23, minute=59, second=59, microsecond=0)
                return day_start, day_end

        return None, None

    except Exception as e:
        print(f"Ошибка парсинга даты: {e}")
        return None, None


def parse_single_datetime(text: str) -> datetime:
    """Парсит одну дату/время в различных форматах"""
    text = text.strip()
    now = datetime.now()

    formats = [
        "%d.%m.%Y %H:%M",  # 25.12.2024 14:30
        "%d.%m.%Y",  # 25.12.2024
        "%H:%M",  # 14:30
        "%d.%m %H:%M",  # 25.12 14:30
        "%d.%m",  # 25.12
    ]

    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)

            # Если год не указан, используем текущий
            if parsed.year == 1900:
                parsed = parsed.replace(year=now.year)

            # Если дата не указана (только время), используем сегодняшнюю дату
            if parsed.date() == datetime(1900, 1, 1).date():
                parsed = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)

            return parsed
        except ValueError:
            continue

    return None


async def generate_invite_kb(bot, chat_id: int, case_number: str, is_supergroup: bool = True):
    """Генерация invite-ссылки с учетом типа группы"""
    try:
        print(f"🔗 Создаю invite-ссылку для дела {case_number} в чате {chat_id}")

        # Проверяем права бота
        bot_member = await bot.get_chat_member(chat_id, bot.id)
        if bot_member.status not in ("administrator", "creator"):
            print("❌ Бот не является администратором!")
            return None

        if is_supergroup:
            try:
                invite_link_obj = await bot.create_chat_invite_link(
                    chat_id=chat_id,
                    name=f"Case {case_number}",
                    member_limit=1,
                    creates_join_request=False,
                    expire_date=None
                )
                invite_link = invite_link_obj.invite_link
                print(f"✅ Персональная ссылка создана: {invite_link}")
            except Exception as e:
                print(f"⚠️ Не удалось создать персональную ссылку: {e}")
                invite_link_obj = await bot.export_chat_invite_link(chat_id)
                invite_link = invite_link_obj
                print(f"✅ Обычная ссылка экспортирована: {invite_link}")
        else:
            invite_link = await bot.export_chat_invite_link(chat_id)
            print(f"✅ Ссылка для обычной группы создана: {invite_link}")

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text=f"👨‍💼 Присоединиться к делу {case_number}",
                    url=invite_link
                )]
            ]
        )
        return kb
    except TelegramBadRequest as e:
        print(f"❌ Ошибка Telegram API: {e}")
        return None
    except Exception as e:
        print(f"❌ Неожиданная ошибка при создании ссылки: {e}")
        return None


async def ensure_bot_admin(bot, chat_id: int):
    try:
        bot_member = await bot.get_chat_member(chat_id, bot.id)
        if bot_member.status in ("administrator", "creator"):
            print(f"✅ Бот уже является администратором в чате {chat_id}")
            return True
        print(f"⚠️ Бот не является администратором в чате {chat_id}")
        return False
    except Exception as e:
        print(f"❌ Ошибка проверки статуса бота: {e}")
        return False


async def check_user_role_in_case(case_number: str, user_id: int):
    case = await db.get_case_by_number(case_number)
    if not case:
        return None
    if case["plaintiff_id"] == user_id:
        return "plaintiff"
    elif case.get("defendant_id") == user_id:
        return "defendant"
    return None


@router.chat_member()
async def on_user_join(event: ChatMemberUpdated, state: FSMContext):
    if event.new_chat_member.status == "member":
        defendant_id = event.new_chat_member.user.id
        chat_id = event.chat.id
        case = await db.get_case_by_chat(chat_id)
        if not case:
            print(f"⚠️ В чате {chat_id} нет активного дела")
            return

        case_number = case["case_number"]
        await db.set_defendant(
            case_number=case_number,
            defendant_id=defendant_id,
            defendant_username=event.new_chat_member.user.username or event.new_chat_member.user.full_name
        )
        await state.update_data(case_number=case_number)
        await state.set_state(DisputeState.defendant_arguments)
        print(f"✅ Ответчик {defendant_id} добавлен в дело {case_number}")


@router.message(Command("start"))
async def start_command(message: types.Message, state: FSMContext):
    if message.chat.type == "private":
        await db.save_bot_user(
            message.from_user.id,
            message.from_user.username or message.from_user.full_name
        )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Версия 1 (Создание группы)", callback_data="start_v1")],
        [InlineKeyboardButton(text="Версия 2 (Работа в готовой группе)", callback_data="start_v2")]
    ])
    await message.answer("Добро пожаловать, я ИИ судья, который поможет вам решить ваш спор или конфликт\n"
                         "Выберите подходящую версию чтобы продолжить:\n\n"
                         "*⚠ Важно! перед добавлением бота в группу вручную,"
                         " измените настроки группы(история чата должна быть открыта!)*",
                         reply_markup=kb)


@router.callback_query(F.data.startswith("start_v1"))
async def start_v1_command(callback: types.CallbackQuery, state: FSMContext):
    """Проверяем лимит попыток"""
    # user_id = callback.from_user.id
    # if await redis_service.is_start_limit(user_id):
    #     await callback.message.answer("⛔ Лимит попыток за сегодня исчерпан. Попробуйте завтра.")
    #     return
    # await db.set_version(bot_version="v1")
    user_id = callback.from_user.id
    await db.set_user_version(user_id, "v1")

    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🏗 Создать группу")],
        [KeyboardButton(text="ℹ️ Справка")],
        [KeyboardButton(text="🔙 Назад в Меню")]
    ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await callback.bot.send_message(chat_id=callback.message.chat.id,
                                    text=
                                    "Здравстуйте, Я ИИ-бот Судья для решения ваших споров и конфликтов."
                                    " Для начала работы ознакомьтесь с инструкцией: 'ℹ️ Справка' ",
                                    reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("start_v2"))
async def start_v2_callback(callback: types.CallbackQuery, state: FSMContext):
    """Устанавливаем 2-ю версию бота"""
    user_id = callback.from_user.id
    await db.set_user_version(user_id, "v2")

    await state.set_state(DisputeState.waiting_for_group_add)
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="ℹ️ Справка")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await callback.bot.send_message(
        chat_id=callback.message.chat.id,
        text=(
            "📋 *Инструкция для работы с готовой группой\\:*\n\n"
            "1️⃣ Добавьте меня в вашу группу @judge\\_ai\\_tgbot\n"
            "2️⃣ Назначьте меня администратором группы\n"
            "3️⃣ Нажмите 'Начать'\n\n"
            "⚠️ *Важно\\:* Без прав администратора я не смогу корректно работать\\!"
        ),
        reply_markup=kb,
        parse_mode="MarkdownV2"
    )
    await callback.answer()


@router.message(F.text == "⚖ Начать")
async def start_chat_handler(message: types.Message, state: FSMContext):
    await state.clear()
    kb = get_main_menu_keyboard()

    await message.answer(
        text=(
            "Здравствуйте! ⚖️ Я — ИИ судья.\n"
            "Я помогу объективно рассмотреть спор.\n\n"
            "💡 *Важно:* Для корректной работы добавьте меня администратором в группу, "
            "где будет проходить дело."
        ),
        reply_markup=kb,
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "start_chat")
async def start_chat_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    kb = get_main_menu_keyboard()

    await callback.bot.send_message(
        chat_id=callback.message.chat.id,
        text=(
            "Здравствуйте! ⚖️ Я — ИИ судья.\n"
            "Я помогу объективно рассмотреть спор.\n\n"
            "💡 *Важно:* Для корректной работы добавьте меня администратором в группу, "
            "где будет проходить дело."
        ),
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await callback.answer()


def escape_md_v2(text: str) -> str:
    """Экранирует все спецсимволы для MarkdownV2"""
    if not text:
        return ""
    special_chars = r'([_*\[\]()~`>#+\-=|{}.!\\])'
    return re.sub(special_chars, r'\\\1', text)


@router.message(GroupState.waiting_group_name)
async def input_group_name(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    topic = message.text.strip()
    if not topic:
        await message.answer("❌ Название не может быть пустым.")
        return

    group_title = topic

    result = await user_client.create_dispute_group(
        case_number=None,
        case_topic=group_title,
        creator_id=message.from_user.id
    )

    if not result:
        await message.answer("❌ Произошла ошибка при создании группы.")
        await state.clear()
        return

    chat_id = result["chat_id"]
    bot_id = (await message.bot.get_me()).id

    rights = ChatAdminRights(
        change_info=False,
        post_messages=True,
        edit_messages=True,
        delete_messages=True,
        ban_users=True,
        invite_users=True,
        pin_messages=True,
        add_admins=True,
        anonymous=False,
        manage_call=True,
        other=True
    )

    try:
        # Назначаем бота администратором
        await user_client.client(EditAdminRequest(
            channel=chat_id,
            user_id=bot_id,
            admin_rights=rights,
            rank="Судья"
        ))

        await asyncio.sleep(5)
        try:
            invite = await user_client.client(ExportChatInviteRequest(peer=chat_id))
            invite_link = invite.link

            # 🔹 Экранируем всё для MarkdownV2
            safe_title = escape_md_v2(result["title"])
            safe_link = escape_md_v2(invite_link)

            text = (
                f"✅ Группа успешно создана\\!\n"
                f"📋 Название: {safe_title}\n\n"
                f"🔗 Ссылка для входа в группу:\n{safe_link}\n\n"
                f"👆 Нажмите на ссылку выше, чтобы присоединиться к группе и начать дело\\.\n\n"
                f"⚠️ *Важно:* После входа в группу нажмите «⚖ Начать Дело»"
            )

            await message.answer(text, parse_mode="MarkdownV2")

        except Exception as e:
            print(f"❌ Ошибка создания ссылки: {e}")

            text = (
                f"✅ Группа создана: {escape_md_v2(result['title'])}\n"
                f"❌ Не удалось создать ссылку-приглашение\\.\n\n"
                f"Попробуйте найти группу вручную\\."
            )
            await message.answer(text, parse_mode="MarkdownV2")

    except Exception as e:
        print(f"❌ Критическая ошибка при настройке группы: {e}")
        import traceback
        traceback.print_exc()

        text = (
            "❌ Ошибка при настройке группы\\.\n\n"
            "Попробуйте:\n"
            "1\\. Создать группу заново\n"
            "2\\. Проверить подключение к интернету\n"
            "3\\. Обратиться в поддержку, если проблема повторяется"
        )
        await message.answer(text, parse_mode="MarkdownV2")
        await state.clear()
        return

    await state.clear()


@router.message(F.left_chat_member)
async def delete_left_event(message: types.Message):
    try:
        await message.delete()
    except TelegramForbiddenError:
        print("Бота кикнули с канала, не удалось удалить сообщение")


@router.my_chat_member()
async def bot_added(event: ChatMemberUpdated):
    if (event.old_chat_member is None or event.old_chat_member.status in ("kicked", "left")) \
            and event.new_chat_member.status in ("member", "administrator"):
        return

    if event.new_chat_member.user.id == (await event.bot.get_me()).id:
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⚖ Начать")]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        try:
            await event.bot.send_message(
                chat_id=event.chat.id,
                text="Привет! Я готов вести это дело. Нажмите кнопку ниже для начала:",
                reply_markup=kb
            )
        except Exception as e:
            print(f"Не удалось отправить сообщение в группу: {e}")


@router.message(F.text == "ℹ️ Справка")
async def help_command(message: types.Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )

    await message.answer(
        "📖 *Справка по использованию ИИ судьи:*\n\n"
        "*Подготовка:*\n"
        "Версия-1:"
        "🔸 Создайте группу в Telegram «🏗 Создать группу » \n"
        "🔸 Перейдите в группу вашего дела \n"
        "Версия-2:"
        "🔸 Добавьте меня в группу обсуждения\n"
        "🔸 Дайте мне права администартора для корректной работы системы\n"
        "*Процесс разбирательства:*\n"
        "1️⃣ Нажмите «⚖️ Начать Дело»\n"
        "2️⃣ Введите тему спора\n"
        "3️⃣ Выберите категорию\n"
        "4️⃣ Введите вашу претензию(конкретную причину)\n"
        "5️⃣  Укажите сумму иска (опционально)\n"
        "6️⃣ Поделитесь ссылкой с ответчиком\n"
        "7️⃣ Истец представляет аргументы\n"
        "8️⃣ Ответчик представляет аргументы\n"
        "9️⃣ Бот выносит решение и генерирует PDF\n\n"
        "*Дополнительно:*\n"
        "📝 Используйте «Черновик» для продолжения незавершенных дел\n"
        "📂 Просматривайте историю в «Мои дела»",
        parse_mode="Markdown",
        reply_markup=kb
    )


@router.message(F.text == "🏗 Создать группу")
async def create_group(message: types.Message, state: FSMContext):
    if not user_client.is_connected:
        await message.answer("❌ Сначала необходимо авторизоваться!")
        return

    await state.set_state(GroupState.waiting_group_name)
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )
    await message.answer("Введите тему спора / название группы:", reply_markup=kb)


async def build_cases_text(user_cases, user_id, page: int):
    start = page * CASES_PER_PAGE
    end = start + CASES_PER_PAGE
    total = len(user_cases)
    user_cases = list(reversed(user_cases))
    page_cases = user_cases[start:end]

    text = "📂 *Ваши дела:*\n\n"
    for case in page_cases:
        role = "Истец" if case["plaintiff_id"] == user_id else "Ответчик"
        status = "⚖️ В процессе" if case["status"] != "finished" else "✅ Завершено"
        claim_text = f" ({case['claim_amount']}$)" if case.get("claim_amount") else ""
        text += (
            f"📌 *Дело {case['case_number']}*\n"
            f"Тема: {case['topic']}{claim_text}\n"
            f"Категория: {case['category']}\n"
            f"Ваша роль: {role}\n"
            f"Статус: {status}\n\n"
        )
    text += f"📊 Всего дел: {total}\n"
    return text, total


def build_pagination_keyboard(page: int, total: int):
    builder = InlineKeyboardBuilder()
    max_page = (total - 1) // CASES_PER_PAGE
    buttons = []
    if page > 0:
        buttons.append(types.InlineKeyboardButton(text="⬅️", callback_data=f"cases_page:{page - 1}"))
    if page < max_page:
        buttons.append(types.InlineKeyboardButton(text="➡️", callback_data=f"cases_page:{page + 1}"))

    builder.row(*buttons)
    builder.row(types.InlineKeyboardButton(text="🔙 Назад в Меню", callback_data="back_to_menu"), )

    return builder.as_markup()


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu_callback(callback: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки возврата в главное меню через callback"""
    await state.clear()
    kb = get_main_menu_keyboard()
    await callback.message.edit_text("📋 Главное меню:", reply_markup=None)
    await callback.bot.send_message(
        chat_id=callback.message.chat.id,
        text="📋 Главное меню:",
        reply_markup=kb
    )
    await callback.answer()


@router.message(F.text == "📂 Мои дела")
async def my_cases(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_cases = await db.get_user_cases(user_id)
    if not user_cases:
        kb = get_back_to_menu_keyboard()
        await message.answer("📭 У вас пока нет дел.", reply_markup=kb)
        return

    page = 0
    text, total = await build_cases_text(user_cases, user_id, page)
    keyboard = build_pagination_keyboard(page, total)
    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)


@router.callback_query(F.data.startswith("cases_page:"))
async def paginate_cases(callback: types.CallbackQuery):
    page = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    user_cases = await db.get_user_cases(user_id)

    text, total = await build_cases_text(user_cases, user_id, page)
    keyboard = build_pagination_keyboard(page, total)

    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    await callback.answer()


@router.message(F.text == "📝Черновик")
async def draft_cases(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    active_cases = await db.get_user_active_cases(user_id)
    if not active_cases:
        kb = get_back_to_menu_keyboard()
        await message.answer("📭 У вас нет активных дел.", reply_markup=kb)
        return

    builder = InlineKeyboardBuilder()
    for case in active_cases:
        builder.row(InlineKeyboardButton(
            text=f"📌 {case['case_number']} - {case['topic'][:30]}{'...' if len(case['topic']) > 30 else ''}",
            callback_data=f"resume_case:{case['case_number']}"
        ))
    builder.row(InlineKeyboardButton(text="🔙 Назад в Меню", callback_data="back_to_menu"))

    await message.answer("📝 Ваши активные дела. Выберите дело для продолжения:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("resume_case:"))
async def resume_case(callback: CallbackQuery, state: FSMContext):
    case_number = callback.data.split(":")[1]
    case = await db.get_case_by_number(case_number)
    if not case:
        await callback.answer("⚠ Дело не найдено", show_alert=True)
        return

    user_role = await check_user_role_in_case(case_number, callback.from_user.id)
    if not user_role:
        await callback.answer("⚠ Вы не являетесь участником дела", show_alert=True)
        return

    await state.update_data(case_number=case_number)
    stage = case.get("stage", "plaintiff")

    bot_version = await db.get_case_version(case_number)
    # Обработка стадии AI вопросов
    if stage and stage.startswith("ai_questions_"):
        answering_role = stage.split("_")[-1]  # plaintiff или defendant

        if user_role != answering_role:
            role_text = "истца" if answering_role == "plaintiff" else "ответчика"
            await callback.answer(f"⚠ Сейчас этап вопросов ИИ для {role_text}", show_alert=True)
            return

        ai_questions_data = await db.get_ai_questions(case_number, answering_role)

        if not ai_questions_data:
            await callback.message.answer("⚠️ Вопросы ИИ не найдены. Переходим к следующему этапу.")
            if answering_role == "plaintiff":
                await proceed_to_defendant_stage(callback.message, state, case_number)
            else:
                await proceed_to_final_decision(callback.message, state, case_number)
            await callback.answer()
            return

        current_questions = [q['question'] for q in ai_questions_data]
        ai_questions_count = ai_questions_data[0]['round_number'] if ai_questions_data else 1

        answered_count = await db.get_answered_ai_questions_count(case_number, answering_role, ai_questions_count)
        current_index = answered_count

        if current_index >= len(current_questions):
            if answering_role == "plaintiff":
                await proceed_to_defendant_stage(callback.message, state, case_number)
            else:
                await proceed_to_final_decision(callback.message, state, case_number)
            await callback.answer()
            return

        await state.update_data(
            ai_questions_count=ai_questions_count,
            current_ai_questions=current_questions,
            current_question_index=current_index,
            answering_role=answering_role,
            skip_count=0
        )
        await state.set_state(DisputeState.waiting_ai_question_response)

        role_text = "Истец" if answering_role == "plaintiff" else "Ответчик"
        kb_questions = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Пропустить вопрос")],
                [KeyboardButton(text="⏸️ Поставить дело на паузу")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )

        await callback.message.answer(
            f"✅ Вы продолжаете дело №{case_number}\n"
            f"*Стадия:* Вопросы ИИ для {answering_role}\n\n"
            f"📝 *{role_text}*, пожалуйста, ответьте на следующий вопрос:\n\n"
            f"❓ {current_questions[current_index]}\n\n"
            f"Вопрос {current_index + 1} из {len(current_questions)}",
            reply_markup=kb_questions,
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    # Обработка стадий создания дела
    if stage == "topic":
        if user_role != "plaintiff":
            await callback.answer("⚠ На этой стадии продолжить может только истец", show_alert=True)
            return

        await state.set_state(DisputeState.waiting_topic)
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="🔙 Назад в Меню")]],
            resize_keyboard=True
        )
        await callback.message.answer(
            f"✅ Вы продолжаете дело №{case_number}\n"
            f"*Стадия:* Ввод темы спора\n\n"
            f"Введите тему спора:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    elif stage == "category":
        if user_role != "plaintiff":
            await callback.answer("⚠ На этой стадии продолжить может только истец", show_alert=True)
            return

        await state.update_data(topic=case.get('topic', ''))
        await state.set_state(DisputeState.waiting_category)
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=cat)] for cat in CATEGORIES] +
                     [[KeyboardButton(text="⏸️ Поставить дело на паузу")],
                      [KeyboardButton(text="🔙 Назад в Меню")]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await callback.message.answer(
            f"✅ Вы продолжаете дело №{case_number}\n"
            f"*Стадия:* Выбор категории\n\n"
            f"Выберите категорию спора:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    elif stage == "claim_reason":
        if user_role != "plaintiff":
            await callback.answer("⚠ На этой стадии продолжить может только истец", show_alert=True)
            return

        await state.update_data(
            topic=case.get('topic', ''),
            category=case.get('category', '')
        )
        await state.set_state(DisputeState.waiting_claim_reason)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⏸️ Поставить дело на паузу")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        await callback.message.answer(
            f"✅ Вы продолжаете дело №{case_number}\n"
            f"*Стадия:* Описание претензии\n\n"
            f"📝 Опишите вашу претензию к ответчику:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    elif stage == "claim_amount":
        if user_role != "plaintiff":
            await callback.answer("⚠ На этой стадии продолжить может только истец", show_alert=True)
            return

        await state.update_data(
            topic=case.get('topic', ''),
            category=case.get('category', ''),
            claim_reason=case.get('claim_reason', '')
        )
        await state.set_state(DisputeState.waiting_claim_amount)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Да"), KeyboardButton(text="Нет")],
                [KeyboardButton(text="⏸️ Поставить дело на паузу")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await callback.message.answer(
            f"✅ Вы продолжаете дело №{case_number}\n"
            f"*Стадия:* Указание суммы иска\n\n"
            f"💰 Желаете указать сумму иска?",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    elif stage == "defendant_method":
        if user_role != "plaintiff":
            await callback.answer("⚠ На этой стадии продолжить может только истец", show_alert=True)
            return
        await state.update_data(
            topic=case.get('topic', ''),
            category=case.get('category', ''),
            claim_reason=case.get('claim_reason', ''),
            claim_amount=case.get('claim_amount')
        )
        await state.set_state(DisputeState.waiting_defendant_method)

        keyboard_buttons = []

        if bot_version == "v2":
            keyboard_buttons.append([KeyboardButton(text="👤 По юзернейму (@username)")])
        keyboard_buttons.append([KeyboardButton(text="🔗 Пригласительная ссылка")])
        keyboard_buttons.append([KeyboardButton(text="⏸️ Поставить дело на паузу")])
        keyboard_buttons.append([KeyboardButton(text="🔙 Назад в Меню")])
        kb = ReplyKeyboardMarkup(
            keyboard=keyboard_buttons,
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await callback.message.answer(
            f"✅ Вы продолжаете дело №{case_number}\n"
            f"*Стадия:* Выбор способа добавления ответчика\n\n"
            f"🤝 Выберите способ добавления ответчика:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    # Обработка стадий аргументации
    elif stage == "plaintiff":
        if user_role != "plaintiff":
            await callback.answer("⚠ Сейчас этап аргументов истца", show_alert=True)
            return

        await state.set_state(DisputeState.plaintiff_arguments)
        kb_with_back = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Завершить аргументы")],
                [KeyboardButton(text="⏸️ Поставить дело на паузу")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        await callback.message.answer(
            f"✅ Вы продолжаете дело №{case_number}\n"
            f"*Стадия:* Аргументы истца\n\n"
            f"Истец, введите ваши аргументы:",
            reply_markup=kb_with_back,
            parse_mode="Markdown"
        )

    elif stage == "defendant":
        # TODO on user_role
        # if user_role != "defendant":
        #     return

        await state.set_state(DisputeState.defendant_arguments)
        kb_with_back = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Завершить аргументы")],
                [KeyboardButton(text="⏸️ Поставить дело на паузу")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        data = await state.get_data()
        defendant_username = data.get('defendant_username') or case.get('defendant_username')
        defendant_mention = f'@{defendant_username}'
        await callback.message.answer(
            f"✅ Вы продолжаете дело №{case_number}\n"
            f"*Стадия:* Аргументы ответчика\n\n"
            f"Ответчик {defendant_mention}, введите ваши аргументы:",
            reply_markup=kb_with_back,
            parse_mode="Markdown"
        )

    elif stage == "final_decision":
        await callback.answer("⚠ Это дело уже завершено", show_alert=True)
        return

    else:
        await callback.answer(f"⚠ Неизвестный этап дела: {stage}", show_alert=True)
        return

    await callback.answer()


@router.message(F.text == "⚖ Начать Дело")
async def start_dispute(message: types.Message, state: FSMContext):
    if message.chat.type not in ("group", "supergroup"):
        kb = get_back_to_menu_keyboard()
        await message.answer(
            "⚠️ *Внимание!* Дело нужно создавать в группе.\n\n"
            "📋 *Инструкция:*\n"
            "1. Создайте группу в Telegram\n"
            "2. Добавьте меня в группу как администратора\n"
            "3. В группе напишите /start и выберите «⚖ Начать Дело»",
            parse_mode="Markdown",
            reply_markup=kb
        )
        return

    chat = message.chat
    is_supergroup = chat.type == "supergroup"

    user_id = message.from_user.id
    bot_version = await db.get_user_version(user_id)

    chat_id = message.chat.id
    case_number = await db.create_case(
        topic="",
        category="",
        claim_reason="",
        mode="упрощенный",
        plaintiff_id=user_id,
        plaintiff_username=message.from_user.username or message.from_user.full_name,
        chat_id=chat_id,
        version=bot_version
    )

    await state.update_data(case_number=case_number, is_supergroup=is_supergroup)
    await db.update_case_stage(case_number, "topic")

    await state.set_state(DisputeState.waiting_topic)
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⏸️ Поставить дело на паузу")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )

    warning_text = ""
    if not is_supergroup:
        warning_text = "\n\n⚠️ *Внимание:* Вы используете обычную группу. Некоторые функции могут быть ограничены." \
                       " Рекомендуется использовать супергруппу для полного функционала."

    await message.answer(
        f"⚖️ *Создано дело #{case_number}*{warning_text}\n\n"
        "Введите тему спора:",
        reply_markup=kb,
        parse_mode="Markdown"
    )


@router.message(DisputeState.waiting_topic)
async def input_topic(message: types.Message, state: FSMContext):
    if message.new_chat_members or message.left_chat_member:
        return

    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    # ДОБАВЛЕНО: обработка паузы
    if message.text == "⏸️ Поставить дело на паузу":
        await pause_case_handler(message, state)
        return

    if not message.text:
        await message.answer("⚠️ Пожалуйста, введите тему спора текстом.")
        return

    topic = message.text.strip()
    data = await state.get_data()
    case_number = data.get("case_number")

    await db.update_case(case_number=case_number, topic=topic)
    await db.update_case_stage(case_number, "category")
    await state.update_data(topic=topic)

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=cat)] for cat in CATEGORIES] +
                 [[KeyboardButton(text="⏸️ Поставить дело на паузу")],
                  [KeyboardButton(text="🔙 Назад в Меню")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await state.set_state(DisputeState.waiting_category)
    await message.answer("Выберите категорию спора:", reply_markup=kb)


@router.message(DisputeState.waiting_category, F.text.in_(CATEGORIES))
async def select_category(message: types.Message, state: FSMContext):
    category = message.text.strip()
    data = await state.get_data()
    case_number = data.get("case_number")

    await db.update_case(case_number=case_number, category=category)
    await db.update_case_stage(case_number, "claim_reason")
    await state.update_data(category=category)

    await state.set_state(DisputeState.waiting_claim_reason)
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⏸️ Поставить дело на паузу")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )
    await message.answer(
        "📝 *Опишите вашу претензию к ответчику*\n\n"
        "Подробно изложите суть спора и ваши требования:",
        reply_markup=kb,
        parse_mode="Markdown"
    )


@router.message(DisputeState.waiting_category, F.text.in_(CATEGORIES))
async def select_category(message: types.Message, state: FSMContext):
    category = message.text.strip()
    data = await state.get_data()
    case_number = data.get("case_number")

    await db.update_case(case_number=case_number, category=category)
    await db.update_case_stage(case_number, "claim_reason")
    await state.update_data(category=category)

    await state.set_state(DisputeState.waiting_claim_reason)
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )
    await message.answer(
        "📝 *Опишите вашу претензию к ответчику*\n\n"
        "Подробно изложите суть спора и ваши требования:",
        reply_markup=kb,
        parse_mode="Markdown"
    )


@router.message(DisputeState.waiting_category)
async def invalid_category(message: types.Message, state: FSMContext):
    if message.new_chat_members or message.left_chat_member:
        return

    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    if message.text == "⏸️ Поставить дело на паузу":
        await pause_case_handler(message, state)
        return

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=cat)] for cat in CATEGORIES] +
                 [[KeyboardButton(text="⏸️ Поставить дело на паузу")],
                  [KeyboardButton(text="🔙 Назад в Меню")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


@router.message(DisputeState.waiting_claim_reason)
async def input_claim_reason(message: types.Message, state: FSMContext):
    if message.new_chat_members or message.left_chat_member:
        return

    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    # ДОБАВЛЕНО: обработка паузы
    if message.text == "⏸️ Поставить дело на паузу":
        await pause_case_handler(message, state)
        return

    if not message.text:
        await message.answer("⚠️ Пожалуйста, введите вашу претензию к ответчику")
        return

    data = await state.get_data()
    case_number = data.get("case_number")
    claim_reason = message.text.strip()

    await db.update_case(case_number=case_number, claim_reason=claim_reason)
    await db.update_case_stage(case_number, "claim_amount")

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Да"), KeyboardButton(text="Нет")],
            [KeyboardButton(text="⏸️ Поставить дело на паузу")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await state.set_state(DisputeState.waiting_claim_amount)
    await message.answer("💰 Желаете указать сумму иска?", reply_markup=kb)


@router.message(DisputeState.waiting_claim_amount)
async def input_claim_amount(message: types.Message, state: FSMContext):
    if message.new_chat_members or message.left_chat_member:
        return

    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    if message.text == "⏸️ Поставить дело на паузу":
        await pause_case_handler(message, state)
        return

    if not message.text:
        await message.answer("⚠️ Пожалуйста, ответьте «Да» или «Нет»")
        return

    data = await state.get_data()
    case_number = data.get("case_number")
    if not case_number:
        await message.answer("⚠️ Ошибка: номер дела не найден. Попробуйте начать заново.")
        await state.clear()
        return

    user_input = message.text.strip().lower()
    bot_version = await db.get_case_version(case_number)
    if user_input == "да":
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⏸️ Поставить дело на паузу")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        await message.answer(
            "💰 Введите сумму иска в $ (например: 1500):",
            reply_markup=kb
        )
        return
    elif user_input == "нет":
        claim_amount = None
        await db.update_case(case_number=case_number, claim_amount=claim_amount)
        await db.update_case_stage(case_number, "defendant_method")

        if bot_version == 'v1':
            await proceed_to_arguments_from_history(message, state, data, case_number)
        else:
            await proceed_to_message_history(message, state, data, case_number, claim_amount)
        return
    else:
        try:
            claim_amount = float(message.text.replace(',', '').replace(' ', '.').strip())
            await db.update_case(case_number=case_number, claim_amount=claim_amount)
            await db.update_case_stage(case_number, "defendant_method")
            await proceed_to_message_history(message, state, data, case_number, claim_amount)
            return
        except ValueError:
            kb = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="Да"), KeyboardButton(text="Нет")],
                    [KeyboardButton(text="⏸️ Поставить дело на паузу")],
                    [KeyboardButton(text="🔙 Назад в Меню")]
                ],
                resize_keyboard=True,
                one_time_keyboard=True
            )
            await message.answer("⚠️ Пожалуйста, ответьте «Да» или «Нет», либо введите корректную сумму:",
                                 reply_markup=kb)
            return


async def proceed_to_arguments_from_history(message: types.Message, state: FSMContext, data: dict, case_number: str):
    """Переход к выбору способа добавления ответчика после рассмотра переписки"""
    await state.set_state(DisputeState.waiting_defendant_method)

    bot_version = await db.get_case_version(case_number)
    claim_amount = data.get('claim_amount')

    keyboard_buttons = []

    if bot_version == "v2":
        keyboard_buttons.append([KeyboardButton(text="👤 По юзернейму (@username)")])

    keyboard_buttons.append([KeyboardButton(text="🔗 Пригласительная ссылка")])
    keyboard_buttons.append([KeyboardButton(text="🔙 Назад в Меню")])

    kb = ReplyKeyboardMarkup(
        keyboard=keyboard_buttons,
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await message.answer(
        f"✅ *Дело создано!*\n\n"
        f"📋 Номер дела: `{case_number}`\n"
        f"📝 Тема: {data['topic']}\n"
        f"📂 Категория: {data['category']}\n"
        f"💰 Сумма иска: {claim_amount if claim_amount else 'не указана'}\n\n"
        f"🤝 *Выберите способ добавления ответчика:*",
        reply_markup=kb,
        parse_mode="Markdown"
    )


async def proceed_to_arguments_from_history_v1(message: types.Message, state: FSMContext, data: dict, case_number: str,
                                               claim_amount):
    """Переход к выбору способа добавления ответчика для v1 (без рассмотра переписки)"""
    await state.set_state(DisputeState.waiting_defendant_method)

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔗 Пригласительная ссылка")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await message.answer(
        f"✅ *Дело создано!*\n\n"
        f"📋 Номер дела: `{case_number}`\n"
        f"📝 Тема: {data['topic']}\n"
        f"📂 Категория: {data['category']}\n"
        f"💰 Сумма иска: {claim_amount if claim_amount else 'не указана'}\n\n"
        f"🤝 *Выберите способ добавления ответчика:*",
        reply_markup=kb,
        parse_mode="Markdown"
    )


async def proceed_to_message_history(message: types.Message, state: FSMContext, data: dict, case_number: str,
                                     claim_amount):
    """Переход к рассмотру переписки (только для v2)"""
    bot_version = await db.get_case_version(case_number)

    if bot_version == "v1":
        await proceed_to_arguments_from_history_v1(message, state, data, case_number, claim_amount)
        return

    await state.set_state(DisputeState.waiting_message_history)

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Рассмотреть переписку")],
            [KeyboardButton(text="⏭️ Пропустить переписку")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await message.answer(
        f"📱 *Хотите добавить переписку как доказательство?*\n\n"
        f"Я могу проанализировать историю сообщений из этого чата или другого чата "
        f"за определенный период времени и добавить релевантные сообщения как доказательства.",
        reply_markup=kb,
        parse_mode="Markdown"
    )


@router.message(DisputeState.waiting_message_history)
async def handle_message_history_choice(message: types.Message, state: FSMContext):
    if message.new_chat_members or message.left_chat_member:
        return

    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    if not message.text:
        await message.answer("⚠️ Пожалуйста, выберите один из вариантов")
        return

    data = await state.get_data()
    case_number = data.get("case_number")

    # Получаем версию из БД
    bot_version = await db.get_case_version(case_number)

    if message.text == "📱 Рассмотреть переписку":
        if bot_version != "v2":
            await message.answer(
                "❌ Функция рассмотра переписки доступна только во второй версии.\n"
                "Переходим к основной аргументации."
            )
            await proceed_to_arguments_from_history(message, state, data, case_number)
            return

        if not user_client.is_connected:
            await message.answer(
                "❌ Функция рассмотра переписки временно недоступна.\n"
                "Переходим к основной аргументации."
            )
            await proceed_to_arguments_from_history(message, state, data, case_number)
            return

        await state.set_state(DisputeState.waiting_history_dates)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                # [KeyboardButton(text="последний день")],
                # [KeyboardButton(text="последняя неделя")],
                # [KeyboardButton(text="последний месяц")],
                # [KeyboardButton(text="сегодня"), KeyboardButton(text="вчера")],
                # [KeyboardButton(text="📅 Указать точные даты")],
                [KeyboardButton(text="🔍 Выборочно добавить")],

                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )

        await message.answer(
            "📅 *Выберите период для анализа переписки:*\n\n"
            "🔸 *Быстрые варианты:* последний день, неделя, месяц\n"
            "🔸 *Конкретные даты:* сегодня, вчера\n"
            "🔸 *Точный период:* нажмите «📅 Указать точные даты»\n\n"
            "Или введите вручную в формате:\n"
            "• `25.12.2024 14:30 - 26.12.2024 18:00`\n"
            "• `25.12.2024 - 26.12.2024`\n"
            "• `14:30` (сегодня с этого времени)\n"
            "• `25.12` (весь указанный день)",
            parse_mode="Markdown",
            reply_markup=kb
        )

    elif message.text == "⏭️ Пропустить переписку":
        await proceed_to_arguments_from_history(message, state, data, case_number)
    else:
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📱 Рассмотреть переписку")] if bot_version == "v2" else [],
                [KeyboardButton(text="⏭️ Пропустить переписку")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer("⚠️ Пожалуйста, выберите один из предложенных вариантов:", reply_markup=kb)


@router.message(DisputeState.waiting_history_dates)
async def handle_history_dates(message: types.Message, state: FSMContext):
    if message.new_chat_members or message.left_chat_member:
        return

    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    # 📅 Указать точные даты
    if message.text == "📅 Указать точные даты":
        await state.set_state(DisputeState.waiting_detailed_datetime)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        await message.answer(
            "🕒 *Укажите точный период для анализа переписки:*\n\n"
            "*Поддерживаемые форматы:*\n"
            "• `25.12.2024 14:30 - 26.12.2024 18:00`\n"
            "• `25.12.2024 - 26.12.2024` (весь день)\n"
            "• `25.12 14:30 - 26.12 18:00`\n"
            "• `14:30 - 18:00` (сегодня)\n"
            "• `14:30` (с этого времени до сейчас)\n"
            "• `25.12` (весь указанный день)\n\n"
            "*Примеры:*\n"
            "• `01.01.2025 10:00 - 01.01.2025 15:30`\n"
            "• `01.01 - 03.01`\n"
            "• `09:00 - 17:00`",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return

    # 🔍 Ручная пересылка сообщений
    elif message.text == "🔍 Выборочно добавить":
        await state.set_state(DisputeState.waiting_forwarded_messages)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Завершить пересылку")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        await message.answer(
            "📨 Перешлите сюда сообщения из переписки, которые хотите добавить как доказательства.\n\n"
            "После завершения нажмите «✅ Завершить пересылку».",
            reply_markup=kb
        )
        return

    if not message.text:
        await message.answer("⚠️ Пожалуйста, укажите период для анализа переписки")
        return

    await process_date_input(message, state)


@router.message(DisputeState.waiting_detailed_datetime)
async def handle_detailed_datetime_input(message: types.Message, state: FSMContext):
    if message.new_chat_members or message.left_chat_member:
        return

    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    if not message.text:
        await message.answer("⚠️ Пожалуйста, укажите период для анализа переписки")
        return

    await process_date_input(message, state)


async def process_date_input(message: types.Message, state: FSMContext):
    """Обработка ввода даты и времени с диагностикой"""
    data = await state.get_data()
    case_number = data.get("case_number")
    chat_id = message.chat.id

    diagnosis = await diagnose_chat_access(chat_id)
    print(f"🏥 Диагностика: {diagnosis}")

    if "Нет доступа" in diagnosis or "не подключен" in diagnosis:
        await message.answer(
            f"⚠️ Не удается получить доступ к истории чата.\n"
            f"Причина: {diagnosis}\n\n"
            f"Переходим к основной аргументации без анализа переписки."
        )
        await proceed_to_arguments_from_history(message, state, data, case_number)
        return

    start_date, end_date = parse_date_time_input(message.text)

    if not start_date or not end_date:
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="последний день")],
                [KeyboardButton(text="последняя неделя")],
                [KeyboardButton(text="сегодня"), KeyboardButton(text="вчера")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        await message.answer(
            "❌ Неверный формат даты.\n\n"
            "*Поддерживаемые форматы:*\n"
            "• `ДД.ММ.ГГГГ ЧЧ:ММ - ДД.ММ.ГГГГ ЧЧ:ММ`\n"
            "• `ДД.ММ.ГГГГ - ДД.ММ.ГГГГ`\n"
            "• `ЧЧ:ММ - ЧЧ:ММ` (сегодня)\n"
            "• `ДД.ММ` (весь день)\n"
            "• или используйте быстрые кнопки",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return

    if end_date < start_date:
        await message.answer(
            "❌ Дата окончания не может быть раньше даты начала.\n"
            "Проверьте введенный период."
        )
        return

    time_diff = end_date - start_date
    if time_diff > timedelta(days=90):
        await message.answer(
            "⚠️ Слишком большой период для анализа (больше 90 дней).\n"
            "Рекомендуется выбрать меньший период для более точного анализа."
        )
        return

    period_text = f"{start_date.strftime('%d.%m.%Y %H:%M')} - {end_date.strftime('%d.%m.%Y %H:%M')}"
    await message.answer(f"🔍 Анализирую переписку за период: {period_text}")

    case = await db.get_case_by_number(case_number)
    participants_data = []
    if case:
        participants_data.append({
            'user_id': case['plaintiff_id'],
            'username': case.get('plaintiff_username', 'Истец')
        })
        if case.get('defendant_id'):
            participants_data.append({
                'user_id': case['defendant_id'],
                'username': case.get('defendant_username', 'Ответчик')
            })

    messages = await get_chat_history_by_dates(chat_id, start_date, end_date)

    if not messages:
        await message.answer(
            f"📱 За период {period_text} сообщений не найдено или нет доступа к истории чата.\n"
            f"Диагностика: {diagnosis}\n\n"
            "Переходим к основной аргументации."
        )
        await proceed_to_arguments_from_history(message, state, data, case_number)
        return

    formatted_messages = await format_messages_for_review(messages, participants_data)

    await state.update_data(
        history_messages=messages,
        history_participants=participants_data,
        history_start_date=start_date,
        history_end_date=end_date
    )
    await state.set_state(DisputeState.reviewing_messages)

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Добавить всю переписку")],
            [KeyboardButton(text="🔍 Выборочно добавить")],
            [KeyboardButton(text="❌ Не добавлять переписку")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    if len(formatted_messages) > 4000:
        parts = [formatted_messages[i:i + 4000] for i in range(0, len(formatted_messages), 4000)]
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                await message.answer(
                    part + f"\n\n*Что делать с найденными сообщениями?*",
                    parse_mode="Markdown",
                    reply_markup=kb
                )
            else:
                await message.answer(part, parse_mode="Markdown")
    else:
        await message.answer(
            formatted_messages + f"\n\n*Что делать с найденными сообщениями?*",
            parse_mode="Markdown",
            reply_markup=kb
        )


@router.message(DisputeState.reviewing_messages)
async def handle_message_review_choice(message: types.Message, state: FSMContext):
    if message.new_chat_members or message.left_chat_member:
        return

    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    if not message.text:
        await message.answer("⚠️ Пожалуйста, выберите один из вариантов")
        return

    data = await state.get_data()
    case_number = data.get("case_number")
    messages = data.get("history_messages", [])
    start_date = data.get("history_start_date")
    end_date = data.get("history_end_date")

    if message.text == "✅ Добавить всю переписку":
        if messages:
            formatted_history = f"📱 *Переписка за период {start_date.strftime('%d.%m.%Y %H:%M')} - {end_date.strftime('%d.%m.%Y %H:%M')}*\n\n"

            for msg in messages:
                date_str = msg['date'].strftime("%d.%m.%Y %H:%M")
                sender = f"ID{msg['from_id']}" if msg['from_id'] else "Неизвестный"

                for participant in data.get('history_participants', []):
                    if participant.get('user_id') == msg['from_id']:
                        sender = participant.get('username', sender)
                        break

                formatted_history += f"[{date_str}] {sender}: {msg['message']}\n\n"

            await db.add_evidence(
                case_number,
                message.from_user.id,
                "plaintiff",
                "chat_history",
                formatted_history,
                None
            )

            await message.answer(
                f"✅ Переписка ({len(messages)} сообщений) добавлена как доказательство.\n"
                f"Переходим к основной аргументации."
            )

        await proceed_to_arguments_from_history(message, state, data, case_number)

    elif message.text == "✅ Добавить всю переписку":
        await message.answer("✅ Вся переписка добавлена как доказательство.")
        await proceed_to_arguments_from_history(message, state, data, case_number)
        return

    elif message.text == "🔍 Выборочно добавить":
        await state.set_state(DisputeState.waiting_forwarded_messages)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Завершить пересылку")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        await message.answer(
            "📨 *Перешлите сюда сообщения из переписки, которые хотите добавить как доказательства.*\n\n"
            "После того как закончите — нажмите «✅ Завершить пересылку».",
            parse_mode="Markdown",
            reply_markup=kb
        )
        return

    elif message.text == "❌ Не добавлять переписку":
        await message.answer("Переписка не добавлена. Переходим к основной аргументации.")
        await proceed_to_arguments_from_history(message, state, data, case_number)

    else:
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Добавить всю переписку")],
                [KeyboardButton(text="🔍 Выборочно добавить")],
                [KeyboardButton(text="❌ Не добавлять переписку")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer("⚠️ Пожалуйста, выберите один из предложенных вариантов:", reply_markup=kb)


@router.message(DisputeState.waiting_forwarded_messages)
async def handle_forwarded_messages(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    if message.text == "✅ Завершить пересылку":
        data = await state.get_data()
        case_number = data.get("case_number")

        forwarded_messages = data.get("forwarded_messages", [])
        if not forwarded_messages:
            await message.answer("⚠️ Вы не переслали ни одного сообщения.")
        else:
            await message.answer(
                f"✅ Добавлено {len(forwarded_messages)} сообщений как доказательства по делу №{case_number}."
            )
        await proceed_to_arguments_from_history(message, state, data, case_number)
        return

    if message.forward_from or message.forward_from_chat:
        data = await state.get_data()
        forwarded_messages = data.get("forwarded_messages", [])
        forwarded_messages.append({
            "from_user": message.forward_from.username if message.forward_from else
                          message.forward_from_chat.title if message.forward_from_chat else "Неизвестно",
            "text": message.text or message.caption or "(медиафайл)"
        })
        await state.update_data(forwarded_messages=forwarded_messages)
        await message.answer("📩 Сообщение добавлено. Перешлите следующее или нажмите «✅ Завершить пересылку».")
    else:
        await message.answer("⚠️ Это не пересланное сообщение. Пожалуйста, используйте пересылку.")


@router.message(DisputeState.waiting_defendant_method)
async def select_defendant_method(message: types.Message, state: FSMContext):
    """Выбор способа добавления ответчика"""
    if message.new_chat_members or message.left_chat_member:
        return

    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    text = message.text.strip() if message.text else ""
    data = await state.get_data()
    case_number = data.get("case_number")

    # Получаем версию из БД
    bot_version = await db.get_case_version(case_number)

    chat_id = message.chat.id
    is_supergroup = data.get("is_supergroup", True)

    if not case_number:
        await message.answer("⚠️ Ошибка: дело не найдено.")
        await state.clear()
        return

    # 🔗 Пригласительная ссылка (доступна для всех версий)
    if text == "🔗 Пригласительная ссылка":
        await db.update_case_stage(case_number, "plaintiff")

        is_admin = await ensure_bot_admin(message.bot, chat_id)
        if not is_admin:
            kb = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="🔙 Назад в Меню")]],
                resize_keyboard=True
            )
            await message.answer(
                "❌ У бота нет прав администратора!\n"
                "Сделайте меня админом, чтобы я мог создать ссылку.\n\n"
                "Пока вы можете пригласить ответчика вручную.",
                reply_markup=kb
            )
            return

        kb_invite = await generate_invite_kb(message.bot, chat_id, case_number, is_supergroup)
        if kb_invite:
            await message.answer(
                f"🔗 Пригласительная ссылка для ответчика по делу №{case_number}:\n\n"
                f"Отправьте её ответчику, чтобы он присоединился к делу.",
                reply_markup=kb_invite
            )
        else:
            kb = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="🔙 Назад в Меню")]],
                resize_keyboard=True
            )
            await message.answer(
                "⚠️ Не удалось создать автоматическую ссылку.\n"
                "Пригласите ответчика в группу вручную.",
                reply_markup=kb
            )

        await start_plaintiff_arguments(message, state, case_number)
        return

    # 👤 По юзернейму (только для v2)
    elif text == "👤 По юзернейму (@username)":
        if bot_version != "v2":
            await message.answer("⚠️ Эта функция доступна только во второй версии бота.")
            return

        await state.set_state(DisputeState.waiting_defendant_username)
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="🔙 Назад в Меню")]],
            resize_keyboard=True
        )
        await message.answer(
            "👤 Введите юзернейм ответчика (например, @username или username):",
            reply_markup=kb
        )
        await state.set_state(DisputeState.waiting_defendant_message)
        return

    keyboard_buttons = []
    if bot_version == "v2":
        keyboard_buttons.append([KeyboardButton(text="👤 По юзернейму (@username)")])
    keyboard_buttons.append([KeyboardButton(text="🔗 Пригласительная ссылка")])
    keyboard_buttons.append([KeyboardButton(text="🔙 Назад в Меню")])

    kb_choices = ReplyKeyboardMarkup(keyboard=keyboard_buttons, resize_keyboard=True)
    await message.answer("⚠️ Пожалуйста, выберите один из предложенных вариантов:", reply_markup=kb_choices)

async def find_user_in_chat(user_client, chat_id, username, limit=100):
    found_user = None
    # сначала ищем по сообщениям в чате
    async for msg in user_client.client.iter_messages(chat_id, limit=limit):
        sender = getattr(msg, 'sender', None)
        if sender and getattr(sender, 'username', None):
            if sender.username.lower() == username.lower():
                found_user = sender
                break
    # если не нашли — пробуем get_entity (может выкинуть исключение PeerIdInvalidError и т.д.)
    if not found_user:
        found_user = await user_client.client.get_entity(username)
    return found_user

# общая логика после того как пользователь найден
async def finalize_after_found(source_message, state, case_number, found_user, username):
    defendant_id = found_user.id
    if getattr(found_user, 'bot', False):
        await source_message.answer(f"⚠️ @{username} — это бот. Укажите реального пользователя.")
        return
    if defendant_id == source_message.from_user.id:
        await source_message.answer("⚠️ Вы не можете быть ответчиком в собственном деле.")
        return

    await state.update_data(
        temp_defendant_id=defendant_id,
        temp_defendant_username=username
    )

    kb_confirm = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Принять участие в деле",
                callback_data=f"defendant_confirm:{case_number}:{defendant_id}:{username}"
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"defendant_reject:{case_number}:{defendant_id}:{username}"
            )
        ]
    ])

    notification_text = (
        f"@{username}, вас назначили ответчиком в деле #{case_number}.\n\n"
        f"Нажмите нужную кнопку ниже, чтобы подтвердить участие:"
    )
    await source_message.answer(notification_text, reply_markup=kb_confirm)
    await source_message.answer(
        f"📨 Уведомление отправлено @{username}\nОжидаем подтверждения от ответчика..."
    )
    await state.set_state(DisputeState.waiting_defendant_confirmation)


@router.message(DisputeState.waiting_defendant_message)
async def input_defendant_from_messages(message: types.Message, state: FSMContext):
    """Обработка username ответчика с поиском в сообщениях группы"""
    data = await state.get_data()
    if message.new_chat_members or message.left_chat_member:
        return
    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return
    if not message.text:
        await message.answer("⚠️ Пожалуйста, введите юзернейм ответчика.")
        return

    username = message.text.strip()
    if username.startswith('@'):
        username = username[1:]
    case_number = data.get("case_number")
    chat_id = message.chat.id
    if not case_number:
        await message.answer("⚠️ Ошибка: дело не найдено.")
        await state.clear()
        return

    # сохраняем попытку в state — понадобится при Retry
    await state.update_data(last_attempt_username=username,
                            last_attempt_chat_id=chat_id,
                            last_attempt_case_number=case_number)

    if user_client and user_client.is_connected:
        try:
            found_user = await find_user_in_chat(user_client, chat_id, username, limit=100)
            if not found_user:
                await message.answer(
                    f"⚠️ Не удалось найти пользователя @{username} в этом чате.\n\n"
                    "Убедитесь что:\n"
                    "✓ Ответчик находится в группе\n"
                    "✓ Ответчик написал хотя бы одно сообщение\n"
                    "✓ Username указан правильно"
                )
                return

            await finalize_after_found(message, state, case_number, found_user, username)

        except Exception as e:
            # распознаём "invalid peer" по типу исключения или по тексту
            is_invalid_peer = False
            if PeerIdInvalidError is not None and isinstance(e, PeerIdInvalidError):
                is_invalid_peer = True
            elif isinstance(e, (UsernameInvalidError, UsernameNotOccupiedError, UserPrivacyRestrictedError,
                                ChannelPrivateError, ChatWriteForbiddenError, ChatAdminRequiredError)):
                is_invalid_peer = True
            else:
                # fallback: часто Telethon пишет 'An invalid Peer was used'
                if 'invalid peer' in str(e).lower() or 'invalid peer' in repr(e).lower():
                    is_invalid_peer = True

            print(f"Ошибка поиска через Telethon: {e}")

            if is_invalid_peer:
                # уведомляем пользователя и даём кнопки: открыть бота в ЛС и повторить поиск
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Открыть @judge_ai_tgbot (в ЛС и выполнить /start)",
                            url="https://t.me/judge_ai_tgbot"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="✅ Я сделал /start — Повторить поиск",
                            callback_data=f"retry_find_defendant:{case_number}:{username}:{chat_id}"
                        )
                    ]
                ])
                await message.answer(
                    "⚠️ Не удалось получить доступ к пользователю из-за ограничений (invalid peer).\n\n"
                    "1) Откройте личные сообщения с ботом @judge_ai_tgbot и выполните команду /start.\n"
                    "2) Вернитесь в этот чат и нажмите кнопку «Повторить поиск» — я повторю попытку автоматически.",
                    reply_markup=kb
                )
                return
            else:
                # прочая ошибка — лог и уведомление
                await message.answer(
                    "⚠️ Произошла ошибка при поиске пользователя.\n"
                    "Попробуйте ещё раз или используйте другой способ добавления ответчика."
                )
    else:
        await message.answer(
            "⚠️ Функция временно недоступна.\n"
            "Используйте пригласительную ссылку или контакт."
        )


@router.callback_query()
async def handle_retry_find_defendant(callback: CallbackQuery, state: FSMContext):
    data = callback.data or ""
    if not data.startswith("retry_find_defendant:"):
        return

    await callback.answer()

    try:
        _, case_number, username, chat_id_str = data.split(":", 3)
        chat_id = int(chat_id_str)
    except Exception:
        await callback.message.answer("⚠️ Некорректные данные для повтора. Повторите действие вручную.")
        return
    if user_client and user_client.is_connected:
        try:
            found_user = await find_user_in_chat(user_client, chat_id, username, limit=100)
            if not found_user:
                await callback.message.answer(
                    f"⚠️ Всё ещё не удалось найти @{username}.\n"
                    "Проверьте, что ответчик выполнил /start у @judge_ai_tgbot и доступен в группе."
                )
                return

            await finalize_after_found(callback.message, state, case_number, found_user, username)
        except Exception as e:
            print(f"Ошибка при повторном поиске: {e}")
            # повторная проверка на invalid peer
            if 'invalid peer' in str(e).lower() or (PeerIdInvalidError is not None and isinstance(e, PeerIdInvalidError)):
                await callback.message.answer(
                    "⚠️ Всё ещё ошибка доступа (invalid peer). Убедитесь, что ответчик действительно открыл чат с @judge_ai_tgbot и выполнил /start."
                )
            else:
                await callback.message.answer("⚠️ Ошибка при повторном поиске. Попробуйте позже.")
    else:
        await callback.message.answer("⚠️ User-client отключён. Попробуйте позже.")


@router.callback_query(F.data.startswith("defendant_confirm:"))
async def handle_defendant_confirm(callback: types.CallbackQuery, state: FSMContext):
    """Обработка подтверждения роли ответчика через callback"""
    data = await state.get_data()
    # bot_version = data.get("bot_version")
    # if bot_version != 'v2':
    #     return
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("⚠️ Ошибка данных подтверждения", show_alert=True)
        return

    _, case_number, defendant_id, username = parts

    await db.set_defendant(
        case_number=case_number,
        defendant_id=int(defendant_id),
        defendant_username=username
    )

    await db.update_case_stage(case_number, "plaintiff")

    await callback.message.edit_text(
        f"✅ @{username} подтвердил участие в деле #{case_number} как ответчик.\n\n"
        f"Дело переходит к стадии аргументов.",
        reply_markup=None
    )

    # Уведомляем истца и запускаем аргументы
    try:
        case = await db.get_case_by_number(case_number)
        plaintiff_id = case['plaintiff_id']
        plaintiff_username = case['plaintiff_username']

        kb_plaintiff = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Завершить аргументы")],
                [KeyboardButton(text="⏸️ Поставить дело на паузу")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )

        plaintiff_mention = f"@{plaintiff_username}" if plaintiff_username.startswith('@') else plaintiff_username
        await callback.message.answer(
            f"📝 *{plaintiff_mention}*, представьте ваши аргументы.\n"
            f"Вы можете отправлять текст, фото, документы и видео.\n\n"
            f"После завершения нажмите «Завершить аргументы».",
            reply_markup=kb_plaintiff,
            parse_mode="Markdown"
        )
        await state.set_state(DisputeState.plaintiff_arguments)
        # Устанавливаем состояние для истца (если он взаимодействует)
        # Но поскольку state per user, истец должен resume или начать писать
        # Здесь мы просто уведомляем в группе

    except Exception as e:
        print(f"Ошибка отправки уведомления истцу: {e}")

    await callback.answer("✅ Подтверждение принято!")


@router.callback_query(F.data.startswith("defendant_reject:"))
async def handle_defendant_reject(callback: types.CallbackQuery, state: FSMContext):
    """Обработка отклонения роли ответчика"""
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("⚠️ Ошибка данных отклонения", show_alert=True)
        return

    _, case_number, defendant_id, username = parts

    await callback.message.edit_text(
        f"❌ @{username} отклонил участие в деле #{case_number}.\n\n"
        f"Истцу необходимо указать другого ответчика.",
        reply_markup=None
    )

    # Возвращаем истца к выбору метода
    kb_method = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔗 Пригласительная ссылка")],
            [KeyboardButton(text="👤 По юзернейму (@username)")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await callback.message.answer(
        f"🤝 *Выберите другой способ добавления ответчика:*",
        reply_markup=kb_method,
        parse_mode="Markdown"
    )

    await callback.answer("❌ Отклонено")


async def finalize_defendant_addition(message: types.Message, state: FSMContext, case_number: str, defendant_id: int,
                                      username: str):
    """Финальное добавление ответчика без подтверждения"""
    await state.update_data(defendant_username=username, defendant_id=defendant_id)

    await db.set_defendant(
        case_number=case_number,
        defendant_id=defendant_id,
        defendant_username=username
    )

    await db.update_case_stage(case_number, "plaintiff")

    await message.answer(
        f"✅ Ответчик @{username} (ID: {defendant_id}) успешно добавлен!\n\n"
        f"Начинаем этап аргументов истца."
    )

    await start_plaintiff_arguments(message, state, case_number)


async def start_plaintiff_arguments(message: types.Message, state: FSMContext, case_number: str):
    """Начало этапа аргументов истца"""
    await state.set_state(DisputeState.plaintiff_arguments)

    kb_with_back = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Завершить аргументы")],
            [KeyboardButton(text="⏸️ Поставить дело на паузу")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )

    await message.answer(
        "📝 *Истец*, представьте ваши аргументы.\n"
        "Вы можете отправлять текст, фото, документы и видео.\n\n"
        "После завершения нажмите «Завершить аргументы».",
        reply_markup=kb_with_back,
        parse_mode="Markdown"
    )


def escape_markdown(text: str) -> str:
    """Экранирование специальных символов для Markdown"""
    if not text:
        return "Ответчик"
    special_chars = r'([_\*\[\]\(\)~`>#\+-=|\{\}\.!])'
    return re.sub(special_chars, r'\\\1', text)


async def proceed_to_defendant_stage(message: types.Message, state: FSMContext, case_number: str):
    """Переход к стадии аргументов ответчика"""
    await db.update_case_stage(case_number, "defendant")
    await state.set_state(DisputeState.defendant_arguments)

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Завершить аргументы")],
            [KeyboardButton(text="⏸️ Поставить дело на паузу")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )

    case = await db.get_case_by_number(case_number)
    data = await state.get_data()
    defendant_username = data.get('defendant_username') or case.get('defendant_username')

    if defendant_username:
        defendant_mention = f"@{defendant_username}"
    else:
        if case.get('defendant_id'):
            try:
                chat_member = await message.bot.get_chat_member(message.chat.id, case['defendant_id'])
                escaped_full_name = escape_markdown(chat_member.user.full_name)
                defendant_mention = f"[{escaped_full_name}](tg://user?id={case['defendant_id']})"
            except Exception as e:
                print(f"Ошибка получения информации о пользователе: {e}")
                defendant_mention = "Ответчик"
        else:
            defendant_mention = "Ответчик"

    notification_text = (
        f"✅ *Этап аргументов истца завершен!*\n\n"
        f"📝 {defendant_mention}, теперь ваша очередь представить свою позицию.\n"
        f"Вы можете отправлять текст, фото, документы и видео.\n\n"
        f"После завершения нажмите «Завершить аргументы»."
    )

    try:
        await message.answer(notification_text, reply_markup=kb, parse_mode="Markdown")

    except TelegramBadRequest as e:
        print(f"Ошибка отправки сообщения: {e}")
        await message.answer(notification_text, reply_markup=kb, parse_mode=None)


async def proceed_to_arguments(message: types.Message, state: FSMContext, data: dict, case_number: str, claim_amount):
    """Переход к этапу аргументов"""
    await proceed_to_arguments_from_history(message, state, data, case_number)
    await state.set_state(DisputeState.plaintiff_arguments)

    chat_id = message.chat.id
    is_admin = await ensure_bot_admin(message.bot, chat_id)
    is_supergroup = data.get("is_supergroup", True)

    kb_with_back = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Завершить аргументы")],
            [KeyboardButton(text="⏸️ Поставить дело на паузу")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )

    if not is_admin:
        await message.answer(
            f"⚠️ *Дело создано, но есть проблема!*\n\n"
            f"📋 Номер дела: {case_number}\n"
            f"📝 Тема: {data['topic']}\n"
            f"📂 Категория: {data['category']}\n"
            f"💰 Сумма иска: {claim_amount if claim_amount else 'не указана'}\n\n"
            f"❌ *Я не являюсь администратором этой группы!*\n"
            f"Сделайте меня администратором для корректной работы.\n\n"
            f"После этого пригласите ответчика в группу вручную.",
            parse_mode="Markdown",
            reply_markup=kb_with_back
        )
    else:
        kb = await generate_invite_kb(message.bot, chat_id, case_number, is_supergroup)
        if kb:
            await message.answer(
                f"✅ *Дело создано!* \n\n"
                f"📋 Номер дела: `{case_number}`\n"
                f"📝 Тема: {data['topic']}\n"
                f"📂 Категория: {data['category']}\n"
                f"💰 Сумма иска: {claim_amount if claim_amount else 'не указана'}\n\n"
                f"👇 Отправьте эту ссылку ответчику:",
                reply_markup=kb,
                parse_mode="Markdown"
            )
        else:
            await message.answer(
                f"✅ *Дело создано!* \n\n"
                f"📋 Номер дела: `{case_number}`\n"
                f"📝 Тема: {data['topic']}\n"
                f"📂 Категория: {data['category']}\n"
                f"💰 Сумма иска: {claim_amount if claim_amount else 'не указана'}\n\n"
                f"⚠️ Не удалось создать автоматическую ссылку.\n"
                f"Пригласите ответчика в группу вручную.",
                parse_mode="Markdown",
                reply_markup=kb_with_back
            )

    await message.answer(
        "📝 *Истец*, представьте ваши аргументы.\n"
        "Вы можете отправлять текст, фото, документы и видео.\n\n"
        "После завершения нажмите «Завершить аргументы».",
        reply_markup=kb_with_back,
        parse_mode="Markdown"
    )


@router.message(F.text == "⏸️ Поставить дело на паузу")
async def pause_case_handler(message: types.Message, state: FSMContext):
    """Обработчик постановки дела на паузу"""
    data = await state.get_data()
    case_number = data.get("case_number")

    if not case_number:
        await message.answer("⚠️ Невозможно поставить дело на паузу — нет активного дела.")
        return

    user_role = await check_user_role_in_case(case_number, message.from_user.id)

    current_state = await state.get_state()

    if current_state == DisputeState.waiting_ai_question_response.state:
        answering_role = data.get("answering_role")
        if user_role != "plaintiff":
            await message.answer("⚠️ Только истец может поставить дело на паузу.")
            return
    elif user_role != "plaintiff":
        await message.answer("⚠️ Только истец может поставить дело на паузу.")
        return

    await state.update_data(paused_from_state=current_state)

    await state.set_state(DisputeState.case_paused)
    await db.update_case_status(case_number, status="paused")

    continue_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="▶️ Продолжить дело")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )

    await message.answer(
        f"⏸️ *Дело {case_number} поставлено на паузу*\n\n"
        f"🔸 Истец или ответчик могут нажать кнопку для продолжения\n"
        f"🔸 Все сообщения до продолжения будут игнорироваться",
        reply_markup=continue_kb,
        parse_mode="Markdown"
    )


@router.message(F.text == "▶️ Продолжить дело")
async def continue_case_button_handler(message: types.Message, state: FSMContext):
    """Обработчик кнопки продолжения дела"""
    data = await state.get_data()
    case_number = data.get("case_number")

    if not case_number:
        await message.answer("⚠️ Нет активного дела для продолжения.")
        return

    user_id = message.from_user.id
    case = await db.get_case_by_number(case_number)

    if not case:
        await message.answer("⚠️ Дело не найдено")
        return

    if case.get('status') != 'paused':
        await message.answer("⚠️ Дело не на паузе")
        return

    user_role = await check_user_role_in_case(case_number, user_id)

    if user_role not in ("plaintiff", "defendant"):
        await message.answer("⚠️ Только истец или ответчик могут продолжить дело")
        return

    stage = case.get("stage")

    await db.update_case_status(case_number, status="active")
    await state.update_data(case_number=case_number, is_supergroup=True)  # Восстанавливаем is_supergroup

    # Обработка стадий создания дела
    if stage == "topic":
        if user_role != "plaintiff":
            await message.answer("⚠️ Продолжить может только истец")
            return

        await state.set_state(DisputeState.waiting_topic)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⏸️ Поставить дело на паузу")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        await message.answer(
            f"✅ Дело {case_number} продолжено!\n\n"
            f"*Стадия:* Ввод темы спора\n\n"
            f"Введите тему спора:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return

    elif stage == "category":
        if user_role != "plaintiff":
            await message.answer("⚠️ Продолжить может только истец")
            return

        await state.update_data(topic=case.get('topic', ''))
        await state.set_state(DisputeState.waiting_category)
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=cat)] for cat in CATEGORIES] +
                     [[KeyboardButton(text="⏸️ Поставить дело на паузу")],
                      [KeyboardButton(text="🔙 Назад в Меню")]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer(
            f"✅ Дело {case_number} продолжено!\n\n"
            f"*Стадия:* Выбор категории\n\n"
            f"Выберите категорию спора:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return

    elif stage == "claim_reason":
        if user_role != "plaintiff":
            await message.answer("⚠️ Продолжить может только истец")
            return

        await state.update_data(
            topic=case.get('topic', ''),
            category=case.get('category', '')
        )
        await state.set_state(DisputeState.waiting_claim_reason)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⏸️ Поставить дело на паузу")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        await message.answer(
            f"✅ Дело {case_number} продолжено!\n\n"
            f"*Стадия:* Описание претензии\n\n"
            f"📝 Опишите вашу претензию к ответчику:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return

    elif stage == "claim_amount":
        if user_role != "plaintiff":
            await message.answer("⚠️ Продолжить может только истец")
            return

        await state.update_data(
            topic=case.get('topic', ''),
            category=case.get('category', ''),
            claim_reason=case.get('claim_reason', '')
        )
        await state.set_state(DisputeState.waiting_claim_amount)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Да"), KeyboardButton(text="Нет")],
                [KeyboardButton(text="⏸️ Поставить дело на паузу")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer(
            f"✅ Дело {case_number} продолжено!\n\n"
            f"*Стадия:* Указание суммы иска\n\n"
            f"💰 Желаете указать сумму иска?",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return

    # Остальные стадии остаются без изменений
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Завершить аргументы")],
            [KeyboardButton(text="⏸️ Поставить дело на паузу")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )

    if stage == "plaintiff":
        if user_role != "plaintiff":
            await message.answer("⚠️ Сейчас этап аргументов истца. Продолжить может только истец.")
            return
        await state.set_state(DisputeState.plaintiff_arguments)
        await message.answer(
            f"✅ Дело {case_number} продолжено!\n\n"
            f"Продолжается этап аргументов истца.\n\n"
            f"📝 Продолжайте представление аргументов.\n"
            f"После завершения нажмите «Завершить аргументы».",
            reply_markup=kb,
            parse_mode="Markdown"
        )

    elif stage == "defendant":
        if user_role != "defendant":
            await message.answer("⚠️ Сейчас этап аргументов ответчика. Продолжить может только ответчик.")
            return
        await state.set_state(DisputeState.defendant_arguments)
        await message.answer(
            f"✅ Дело {case_number} продолжено!\n\n"
            f"Продолжается этап аргументов ответчика.\n\n"
            f"📝 Продолжайте представление аргументов.\n"
            f"После завершения нажмите «Завершить аргументы».",
            reply_markup=kb,
            parse_mode="Markdown"
        )

    elif stage and stage.startswith("ai_questions_"):
        answering_role = stage.split("_")[-1]

        if user_role != answering_role:
            role_text = "истца" if answering_role == "plaintiff" else "ответчика"
            await message.answer(f"⚠️ Сейчас этап вопросов ИИ для {role_text}.")
            return

        ai_questions_data = await db.get_ai_questions(case_number, answering_role)

        if not ai_questions_data:
            if answering_role == "plaintiff":
                await proceed_to_defendant_stage(message, state, case_number)
            else:
                await proceed_to_final_decision(message, state, case_number)
            return

        current_questions = [q['question'] for q in ai_questions_data]
        ai_questions_count = ai_questions_data[0]['round_number'] if ai_questions_data else 1

        answered_count = await db.get_answered_ai_questions_count(case_number, answering_role, ai_questions_count)
        current_index = answered_count

        if current_index >= len(current_questions):
            if answering_role == "plaintiff":
                await proceed_to_defendant_stage(message, state, case_number)
            else:
                await proceed_to_final_decision(message, state, case_number)
            return

        await state.update_data(
            ai_questions_count=ai_questions_count,
            current_ai_questions=current_questions,
            current_question_index=current_index,
            answering_role=answering_role,
            skip_count=0
        )
        await state.set_state(DisputeState.waiting_ai_question_response)

        role_text = "Истец" if answering_role == "plaintiff" else "Ответчик"
        kb_questions = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Пропустить вопрос")],
                [KeyboardButton(text="⏸️ Поставить дело на паузу")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )

        await message.answer(
            f"✅ Дело {case_number} продолжено!\n\n"
            f"🤖 Продолжаем вопросы ИИ Судьи\n\n"
            f"📝 *{role_text}*, пожалуйста, ответьте на следующий вопрос:\n\n"
            f"❓ {current_questions[current_index]}\n\n"
            f"Вопрос {current_index + 1} из {len(current_questions)}",
            reply_markup=kb_questions,
            parse_mode="Markdown"
        )

    else:
        await db.update_case_stage(case_number, "plaintiff")
        await state.set_state(DisputeState.plaintiff_arguments)
        await message.answer(
            f"✅ Дело {case_number} продолжено!\n\n"
            f"⚠️ Этап дела был неопределен, начинаем с аргументов истца.\n\n"
            f"📝 Продолжайте представление аргументов.",
            reply_markup=kb,
            parse_mode="Markdown"
        )


# @router.callback_query(F.data.startswith("continue_case:"))
# async def continue_case_handler(callback: types.CallbackQuery, state: FSMContext):
#     """Обработчик продолжения дела"""
#     case_number = callback.data.split(":")[1]
#     user_id = callback.from_user.id
#
#     user_role = await check_user_role_in_case(case_number, user_id)
#     if user_role not in ("plaintiff", "defendant"):
#         await callback.answer("⚠️ Только истец или ответчик могут продолжить дело", show_alert=True)
#         return
#
#     case = await db.get_case_by_number(case_number)
#     if not case or case.get('status') != 'paused':
#         await callback.answer("⚠️ Дело не найдено или не на паузе", show_alert=True)
#         return
#
#     await state.update_data(case_number=case_number)
#     stage = case.get("stage", "plaintiff")
#
#     if stage == "plaintiff":
#         await state.set_state(DisputeState.plaintiff_arguments)
#         role_text = "истца"
#         if user_role != "plaintiff":
#             await callback.answer("⚠️ Сейчас этап аргументов истца", show_alert=True)
#             return
#     else:
#         await state.set_state(DisputeState.defendant_arguments)
#         role_text = "ответчика"
#         if user_role != "defendant":
#             await callback.answer("⚠️ Сейчас этап аргументов ответчика", show_alert=True)
#             return
#
#     await db.update_case_status(case_number, status="active")
#
#     # chat_members_count = await callback.bot.get_chat_member_count(callback.message.chat.id)
#
#     kb = ReplyKeyboardMarkup(
#         keyboard=[
#             [KeyboardButton(text="Завершить аргументы")],
#             [KeyboardButton(text="⏸️ Поставить дело на паузу")],
#             [KeyboardButton(text="🔙 Назад в Меню")]
#         ],
#         resize_keyboard=True
#     )
#
#     await callback.message.edit_text(
#         f"✅ *Дело {case_number} продолжено!*\n\n"
#         f"Продолжается этап аргументов {role_text}.",
#         parse_mode="Markdown"
#     )
#
#     await callback.bot.send_message(
#         chat_id=callback.message.chat.id,
#         text=(
#             f"📝 Продолжайте представление аргументов.\n"
#             f"После завершения нажмите «Завершить аргументы»."
#         ),
#         reply_markup=kb
#     )
#
#     await callback.answer("✅ Дело продолжено!")


@router.message(DisputeState.case_paused)
async def handle_paused_case_messages(message: types.Message, state: FSMContext):
    """Блокировка сообщений во время паузы"""
    if message.new_chat_members or message.left_chat_member:
        return

    if message.text == "▶️ Продолжить дело":
        return

    data = await state.get_data()
    case_number = data.get("case_number")

    if case_number:
        user_role = await check_user_role_in_case(case_number, message.from_user.id)
        # TODO on user_role in
        # if user_role in ("plaintiff", "defendant"):
        #     return


@router.message(DisputeState.plaintiff_arguments)
async def plaintiff_args(message: types.Message, state: FSMContext):
    if message.new_chat_members or message.left_chat_member:
        return

    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    if message.text == "⏸️ Поставить дело на паузу":
        await pause_case_handler(message, state)
        return

    data = await state.get_data()
    case_number = data.get("case_number")
    if not case_number:
        await message.answer("⚠️ Ошибка: дело не найдено. Начните новое дело.")
        await state.clear()
        return

    user_role = await check_user_role_in_case(case_number, message.from_user.id)
    # TODO on user_role
    if user_role != "plaintiff":
        return

    if not message.text:
        await message.answer("⚠️ Пожалуйста, отправьте текстовое сообщение с аргументами.")
        return

    if message.text.lower().startswith("завершить"):
        await check_and_ask_ai_questions(message, state, case_number, "plaintiff")
        return

    await db.add_evidence(case_number, message.from_user.id, "plaintiff", "text", message.text, None)
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Завершить аргументы")],
            [KeyboardButton(text="⏸️ Поставить дело на паузу")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )

    await message.answer(
        "📝 Аргумент истца добавлен.\n\n"
        "Введите следующий аргумент или нажмите «Завершить аргументы».",
        reply_markup=kb
    )


@router.message(DisputeState.defendant_arguments)
async def defendant_args(message: types.Message, state: FSMContext):
    if message.new_chat_members or message.left_chat_member:
        return

    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    if message.text == "⏸️ Поставить дело на паузу":
        data = await state.get_data()
        case_number = data.get("case_number")
        user_role = await check_user_role_in_case(case_number, message.from_user.id)
        if user_role == "plaintiff":
            await pause_case_handler(message, state)
        else:
            await message.answer("⚠️ Только истец может поставить дело на паузу.")
        return

    data = await state.get_data()
    case_number = data.get("case_number")
    if not case_number:
        await message.answer("⚠️ Ошибка: дело не найдено.")
        await state.clear()
        return
    # TODO user_role
    # user_role = await check_user_role_in_case(case_number, message.from_user.id)
    # if user_role != "defendant":
    #     return

    if not message.text:
        await message.answer("⚠️ Пожалуйста, отправьте текстовое сообщение с аргументами.")
        return

    if message.text.lower().startswith("завершить"):
        await check_and_ask_ai_questions(message, state, case_number, "defendant")
        return

    escaped_text = escape_markdown(message.text)
    await db.add_evidence(case_number, message.from_user.id, "defendant", "text", escaped_text, None)

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Завершить аргументы")],
            [KeyboardButton(text="⏸️ Поставить дело на паузу")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )

    notification_text = (
        "📝 Аргумент добавлен.\n\n"
        "Введите следующий аргумент или нажмите «Завершить аргументы»."
    )

    try:
        await message.answer(notification_text, reply_markup=kb)
    except TelegramBadRequest as e:
        print(f"Ошибка отправки сообщения: {e}")
        await message.answer(notification_text, reply_markup=kb, parse_mode=None)


async def check_and_ask_ai_questions(message: types.Message, state: FSMContext, case_number: str, current_role: str):
    """Проверяет, нужны ли дополнительные вопросы от ИИ и задает их"""
    data = await state.get_data()
    ai_questions_count = data.get("ai_questions_count", 0)

    if ai_questions_count >= 3:
        if current_role == "plaintiff":
            await proceed_to_defendant_stage(message, state, case_number)
        else:
            await proceed_to_final_decision(message, state, case_number)
        return

    case = await db.get_case_by_number(case_number)
    participants = await db.list_participants(case["id"])
    evidence = await db.get_case_evidence(case_number)

    participants_info = [
        {"role": p["role"], "username": p["username"], "description": p["role"].capitalize()}
        for p in participants
    ]
    evidence_info = [
        {
            "type": e["type"],
            "content": e["content"],
            "file_path": e["file_path"],
            "role": e.get("role", "unknown")
        }
        for e in evidence
    ]

    ai_questions = await gemini_service.generate_clarifying_questions(
        case, participants_info, evidence_info, current_role, ai_questions_count + 1, message.bot
    )

    if not ai_questions or len(ai_questions) == 0:
        if current_role == "plaintiff":
            await proceed_to_defendant_stage(message, state, case_number)
        else:
            await proceed_to_final_decision(message, state, case_number)
        return

    for question in ai_questions:
        await db.save_ai_question(case_number, question, current_role, ai_questions_count + 1)

    await db.update_case_stage(case_number, f"ai_questions_{current_role}")

    await state.update_data(
        ai_questions_count=ai_questions_count + 1,
        current_ai_questions=ai_questions,
        current_question_index=0,
        answering_role=current_role
    )
    await state.set_state(DisputeState.waiting_ai_question_response)

    role_text = "Истец" if current_role == "plaintiff" else "Ответчик"
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Пропустить вопрос")],
            [KeyboardButton(text="⏸️ Поставить дело на паузу")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )

    # Экранируем первый вопрос для корректного Markdown
    escaped_question = escape_markdown(ai_questions[0])

    notification_text = (
        f"🤖 *ИИ Судья задает дополнительные вопросы для уточнения*\n\n"
        f"📝 *{role_text}*, пожалуйста, ответьте на следующий вопрос:\n\n"
        f"❓ {escaped_question}\n\n"
        f"Вопрос 1 из {len(ai_questions)}"
    )

    try:
        await message.answer(
            notification_text,
            reply_markup=kb,
            parse_mode="Markdown"
        )
    except TelegramBadRequest as e:
        print(f"Ошибка отправки сообщения: {e}")
        print(f"Текст сообщения: {notification_text}")
        # Пробуем отправить без Markdown в случае ошибки
        await message.answer(
            notification_text,
            reply_markup=kb,
            parse_mode=None
        )


@router.message(DisputeState.waiting_ai_question_response)
async def handle_ai_question_response(message: types.Message, state: FSMContext):
    if message.new_chat_members or message.left_chat_member:
        return

    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    if message.text == "⏸️ Поставить дело на паузу":
        data = await state.get_data()
        case_number = data.get("case_number")
        user_role = await check_user_role_in_case(case_number, message.from_user.id)
        if user_role == "plaintiff":
            await pause_case_handler(message, state)
        else:
            await message.answer("⚠️ Только истец может поставить дело на паузу.")
        return

    data = await state.get_data()
    case_number = data.get("case_number")
    current_questions = data.get("current_ai_questions", [])
    current_index = data.get("current_question_index", 0)
    answering_role = data.get("answering_role")
    ai_questions_count = data.get("ai_questions_count", 1)
    skip_count = data.get("skip_count", 0)

    if not case_number or not current_questions:
        await message.answer("⚠️ Ошибка: данные сессии потеряны.")
        await state.clear()
        return
    # TODO user_role
    # user_role = await check_user_role_in_case(case_number, message.from_user.id)
    # if user_role != answering_role:
    #     return

    if message.text and message.text.lower().startswith("пропустить"):
        skip_count += 1
        await state.update_data(skip_count=skip_count)

        # Если пропущено 3 вопроса подряд - завершаем
        if skip_count >= 3:
            await message.answer("❌ Вы трижды отказались отвечать. Дополнительные вопросы ИИ завершены.")
            await state.update_data(skip_count=0, current_question_index=0)
            if answering_role == "plaintiff":
                await proceed_to_defendant_stage(message, state, case_number)
            else:
                await proceed_to_final_decision(message, state, case_number)
            return
    else:
        # Получен ответ - сбрасываем счетчик пропусков
        skip_count = 0
        await state.update_data(skip_count=0)

        question_text = current_questions[current_index]
        response_text = f"Вопрос ИИ: {question_text}\nОтвет: {message.text}"

        # Сохраняем ответ с указанием номера раунда
        await db.add_evidence(
            case_number, message.from_user.id, answering_role,
            "ai_response", response_text, None
        )

        # Сохраняем ответ как отдельную запись
        await db.save_ai_answer(
            case_number,
            question_text,
            message.text,
            answering_role,
            ai_questions_count
        )

    next_index = current_index + 1

    if next_index < len(current_questions):
        # Есть еще вопросы в этом раунде
        await state.update_data(current_question_index=next_index)
        role_text = "Истец" if answering_role == "plaintiff" else "Ответчик"
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Пропустить вопрос")],
                [KeyboardButton(text="⏸️ Поставить дело на паузу")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )

        escaped_question = escape_markdown(current_questions[next_index])

        notification_text = (
            f"✅ Ответ принят.\n\n"
            f"📝 *{role_text}*, следующий вопрос:\n\n"
            f"❓ {escaped_question}\n\n"
            f"Вопрос {next_index + 1} из {len(current_questions)}"
        )

        try:
            await message.answer(notification_text, reply_markup=kb, parse_mode="Markdown")
        except TelegramBadRequest as e:
            print(f"Ошибка отправки: {e}")
            await message.answer(notification_text, reply_markup=kb, parse_mode=None)
    else:
        # Все вопросы текущего раунда завершены
        await message.answer("✅ Спасибо за ответы на вопросы ИИ судьи!")
        await state.update_data(skip_count=0, current_question_index=0)

        if answering_role == "plaintiff":
            await proceed_to_defendant_stage(message, state, case_number)
        elif answering_role == "defendant":
            await proceed_to_final_decision(message, state, case_number)


async def proceed_to_final_decision(message: types.Message, state: FSMContext, case_number: str):
    """Переход к финальному решению"""
    await db.update_case_stage(case_number, "final_decision")
    await db.update_case_status(case_number, status="finished")
    await state.set_state(DisputeState.finished)

    case = await db.get_case_by_number(case_number)
    participants = await db.list_participants(case["id"])
    evidence = await db.get_case_evidence(case_number)

    participants_info = [
        {"role": p["role"], "username": p["username"], "description": p["role"].capitalize()}
        for p in participants
    ]
    evidence_info = [
        {
            "type": e["type"],
            "content": e["content"],
            "file_path": e["file_path"],
            "role": e.get("role", "unknown")
        }
        for e in evidence
    ]

    await message.answer("⚖️ *ИИ Судья анализирует дело и выносит решение...*", parse_mode="Markdown")

    decision = await gemini_service.generate_full_decision(
        case, participants_info, evidence_info, bot=message.bot
    )

    pdf_bytes = pdf_generator.generate_verdict_pdf(case, decision, participants_info, evidence_info)

    filepath = f"verdict_{case_number}.pdf"
    with open(filepath, "wb") as f:
        f.write(pdf_bytes)

    verdict_kb = get_main_menu_keyboard()
    await message.answer("⚖️ Суд завершён. Итоговый вердикт:", reply_markup=verdict_kb)

    await db.save_decision(case_number=case_number, file_path=filepath)

    sent = await message.answer_document(FSInputFile(filepath))
    try:
        await message.bot.pin_chat_message(
            chat_id=message.chat.id,
            message_id=sent.message_id,
            disable_notification=False
        )
    except Exception as e:
        print(f"Не удалось закрепить файл:{e}")
    os.remove(filepath)
    await state.clear()


@router.message(F.content_type.in_({"photo", "video", "document", "audio"}))
async def media_handler(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state not in (DisputeState.plaintiff_arguments.state, DisputeState.defendant_arguments.state):
        await message.answer("📎 Медиа-файлы принимаются только во время представления аргументов.")
        return

    data = await state.get_data()
    case_number = data.get("case_number")
    if not case_number:
        await message.answer("⚠️ Ошибка: дело не найдено.")
        return

    user_role = await check_user_role_in_case(case_number, message.from_user.id)
    if not user_role:
        return

    if (current_state == DisputeState.plaintiff_arguments.state and user_role != "plaintiff") or \
            (current_state == DisputeState.defendant_arguments.state and user_role != "defendant"):
        stage_name = "истца" if current_state == DisputeState.plaintiff_arguments.state else "ответчика"
        return

    file_info = None
    content_type = None
    if message.photo:
        file_info = message.photo[-1].file_id
        content_type = "photo"
    elif message.document:
        file_info = message.document.file_id
        content_type = "document"
    elif message.video:
        file_info = message.video.file_id
        content_type = "video"
    elif message.audio:
        file_info = message.audio.file_id
        content_type = "audio"

    if file_info:
        await db.add_evidence(case_number, message.from_user.id, user_role, content_type,
                              message.caption or f"Файл ({content_type})", file_info)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Завершить аргументы")],
                [KeyboardButton(text="⏸️ Поставить дело на паузу")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        role_text = "истца" if user_role == "plaintiff" else "ответчика"
        await message.answer(
            f"📎 Доказательство {role_text} добавлено.\n\n"
            f"Добавьте еще материалы или нажмите «Завершить аргументы».",
            reply_markup=kb
        )
    else:
        await message.answer("⚠️ Формат файла не поддерживается.")


@router.message()
async def unknown_message_handler(message: types.Message, state: FSMContext):
    if message.new_chat_members or message.left_chat_member or \
            message.migrate_from_chat_id or message.migrate_to_chat_id or \
            message.group_chat_created or message.supergroup_chat_created or \
            message.channel_chat_created:
        return

    current_state = await state.get_state()

    if current_state is None:
        if message.chat.type == "private":
            kb = get_main_menu_keyboard()
            await message.answer(
                "❓ Я не понял вашу команду.\n\n"
                "Выберите одну из доступных опций:",
                reply_markup=kb
            )
    else:
        kb_with_back = get_back_to_menu_keyboard()

        if current_state == DisputeState.waiting_topic.state:
            await message.answer("⚠️ Пожалуйста, введите тему спора текстом.", reply_markup=kb_with_back)
        elif current_state == DisputeState.waiting_category.state:
            kb = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text=cat)] for cat in CATEGORIES] +
                         [[KeyboardButton(text="🔙 Назад в Меню")]],
                resize_keyboard=True
            )
            await message.answer("⚠️ Выберите категорию из предложенных:", reply_markup=kb)
        elif current_state == DisputeState.waiting_claim_amount.state:
            kb = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="Да"), KeyboardButton(text="Нет")],
                    [KeyboardButton(text="🔙 Назад в Меню")]
                ],
                resize_keyboard=True
            )
            await message.answer("⚠️ Ответьте «Да» или «Нет» на вопрос о сумме иска:", reply_markup=kb)
        elif current_state == DisputeState.waiting_claim_reason.state:
            await message.answer("⚠️ Пожалуйста, опишите вашу претензию к ответчику.", reply_markup=kb_with_back)
        elif current_state == DisputeState.waiting_for_group_add.state:
            kb = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="ℹ️ Справка")],
                    [KeyboardButton(text="🔙 Назад в Меню")]
                ],
                resize_keyboard=True
            )
            await message.answer(
                "⚠️ Сначала добавьте меня в группу как администратора, затем нажмите 'Начать'",
                reply_markup=kb
            )
        else:
            await message.answer(
                "⚠️ Неизвестная команда. Используйте кнопку ниже для возврата в главное меню.",
                reply_markup=kb_with_back
            )


@router.message(F.text == "🔍 Тест чата")
async def test_chat_access(message: types.Message):
    """Тестовая команда для проверки доступа к чату"""
    if message.chat.type in ("group", "supergroup"):
        chat_id = message.chat.id
        diagnosis = await diagnose_chat_access(chat_id)
        await message.answer(f"🔍 Результат диагностики:\n{diagnosis}")

        now = datetime.now()
        start_time = now - timedelta(hours=1)

        messages = await get_chat_history_by_dates(chat_id, start_time, now)

        if messages:
            await message.answer(f"✅ Найдено {len(messages)} сообщений за последний час")
        else:
            await message.answer("❌ Сообщения не найдены или нет доступа")
    else:
        await message.answer("Эта команда работает только в группах")


@router.callback_query()
async def unknown_callback_handler(callback: CallbackQuery):
    await callback.answer("⚠️ Неизвестная команда", show_alert=True)


def register_handlers(dp: Dispatcher):
    dp.include_router(router)