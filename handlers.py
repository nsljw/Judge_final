import os

from aiogram import Router, types, F, Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import db
from gemini_servise import gemini_service
from pdf_gen import PDFGenerator

router = Router()
pdf_generator = PDFGenerator()
CASES_PER_PAGE = 10


class DisputeState(StatesGroup):
    # Состояния для работы в ЛС
    waiting_start_mode = State()  # Выбор: с группой или без
    waiting_group_link = State()  # Если с группой - ввод ссылки
    waiting_topic = State()
    waiting_category = State()
    waiting_claim_reason = State()
    waiting_claim_amount = State()
    waiting_message_history = State()
    waiting_history_dates = State()
    waiting_detailed_datetime = State()
    waiting_forwarded_messages = State()
    reviewing_messages = State()
    waiting_defendant_username = State()
    waiting_defendant_confirmation = State()
    plaintiff_arguments = State()
    defendant_arguments = State()
    ai_asking_questions = State()
    waiting_ai_question_response = State()
    finished = State()
    case_paused = State()


class MenuState(StatesGroup):
    back_to_menu = State()


CATEGORIES = [
    "Нарушение договора",
    "Плагиат. Интеллектуальная собственность",
    "Конфликт/Спор",
    "Долг/Займ",
    "Разделение имущества",
    "Дебаты"
]


def get_main_menu_keyboard():
    """Возвращает клавиатуру главного меню для ЛС"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚖️ Начать Дело")],
            [KeyboardButton(text="📂 Мои дела")],
            [KeyboardButton(text="📝 Черновик")],
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


# =============================================================================
# ОБРАБОТКА /start В ГРУППЕ И ЛС
# =============================================================================

@router.message(Command("start"))
async def start_command(message: types.Message, state: FSMContext):
    """Обработка /start в группе и ЛС"""

    # В ГРУППЕ - минимальное сообщение с переходом в ЛС
    if message.chat.type in ("group", "supergroup"):
        bot_username = (await message.bot.get_me()).username
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="📩 Перейти в личный чат с ботом",
                url=f"https://t.me/{bot_username}?start=group_{message.chat.id}"
            )]
        ])

        await message.answer(
            "👋 Привет! Я ИИ-судья для разрешения споров.\n\n"
            "🔹 Для начала работы перейдите в личный чат со мной:",
            reply_markup=kb
        )
        return

    # В ЛС - полноценное меню
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    # Сохраняем пользователя в БД
    await db.save_bot_user(
        message.from_user.id,
        message.from_user.username or message.from_user.full_name
    )

    # Если пришли из группы - сохраняем chat_id группы
    group_chat_id = None
    if args and args[0].startswith("group_"):
        try:
            group_chat_id = int(args[0].replace("group_", ""))
            await state.update_data(group_chat_id=group_chat_id)
        except:
            pass

    # Если это приглашение ответчика
    if args and args[0].startswith("defendant_"):
        case_number = args[0].replace("defendant_", "")

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✅ Принять участие в деле",
                callback_data=f"accept_defendant:{case_number}"
            )],
            [InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"reject_defendant:{case_number}"
            )]
        ])

        await message.answer(
            f"📋 Вас пригласили участвовать в деле #{case_number} в качестве ответчика.\n\n"
            f"Примите или отклоните участие:",
            reply_markup=kb
        )
        return

    # Обычный старт в ЛС
    kb = get_main_menu_keyboard()
    await message.answer(
        "👋 Добро пожаловать! Я ИИ-судья.\n\n"
        "Я помогу объективно разрешить ваш спор.\n"
        "Весь процесс происходит здесь, в личных сообщениях.\n\n"
        "Выберите действие:",
        reply_markup=kb
    )


# =============================================================================
# СОЗДАНИЕ ДЕЛА В ЛС
# =============================================================================

@router.message(F.text == "⚖️ Начать Дело")
async def start_dispute_pm(message: types.Message, state: FSMContext):
    """Начало создания дела в ЛС"""
    if message.chat.type != "private":
        await message.answer("⚠️ Эта команда работает только в личных сообщениях с ботом.")
        return

    data = await state.get_data()
    group_chat_id = data.get("group_chat_id")

    # Если есть привязанная группа - используем её
    if group_chat_id:
        await state.update_data(chat_id=group_chat_id)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        await state.set_state(DisputeState.waiting_topic)
        await message.answer(
            "📝 Введите тему спора:",
            reply_markup=kb
        )
    else:
        # Спрашиваем: с группой или без
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📱 Работать без группы")],
                [KeyboardButton(text="👥 Связать с группой")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await state.set_state(DisputeState.waiting_start_mode)
        await message.answer(
            "Выберите режим работы:\n\n"
            "📱 <b> Без группы </b> - весь процесс только в ЛС\n"
            "👥 <b> С группой </b> - результат будет отправлен в группу",
            reply_markup=kb,
            parse_mode=ParseMode.HTML

        )


@router.message(DisputeState.waiting_start_mode)
async def select_start_mode(message: types.Message, state: FSMContext):
    """Выбор режима: с группой или без"""
    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    if message.text == "📱 Работать без группы":
        await state.update_data(chat_id=None)
        await state.set_state(DisputeState.waiting_topic)
        kb = get_back_to_menu_keyboard()
        await message.answer(
            "📝 Введите тему спора:",
            reply_markup=kb
        )

    elif message.text == "👥 Связать с группой":
        kb = get_back_to_menu_keyboard()
        await state.set_state(DisputeState.waiting_group_link)
        await message.answer(
            "📎 Добавьте меня в группу как администратора, затем:\n\n"
            "1️⃣ В группе напишите /start\n"
            "2️⃣ Нажмите кнопку для перехода в ЛС\n"
            "3️⃣ Продолжите создание дела здесь\n\n"
            "Или отправьте команду /start снова после добавления в группу.",
            reply_markup=kb
        )
        await state.clear()
    else:
        await message.answer("⚠️ Выберите один из предложенных вариантов.")


# =============================================================================
# СБОР ИНФОРМАЦИИ О ДЕЛЕ (в ЛС)
# =============================================================================

@router.message(DisputeState.waiting_topic)
async def input_topic(message: types.Message, state: FSMContext):
    """Ввод темы спора"""
    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    if not message.text:
        await message.answer("⚠️ Пожалуйста, введите тему спора текстом.")
        return

    topic = message.text.strip()
    await state.update_data(topic=topic)

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=cat)] for cat in CATEGORIES] +
                 [[KeyboardButton(text="🔙 Назад в Меню")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await state.set_state(DisputeState.waiting_category)
    await message.answer("📂 Выберите категорию спора:", reply_markup=kb)


@router.message(DisputeState.waiting_category, F.text.in_(CATEGORIES))
async def select_category(message: types.Message, state: FSMContext):
    """Выбор категории"""
    category = message.text.strip()
    await state.update_data(category=category)

    await state.set_state(DisputeState.waiting_claim_reason)
    kb = get_back_to_menu_keyboard()
    await message.answer(
        "📝 <b> Опишите вашу претензию к ответчику </b>\n\n"
        "Подробно изложите суть спора и ваши требования:",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )


@router.message(DisputeState.waiting_category)
async def invalid_category(message: types.Message):
    """Неверная категория"""
    if message.text == "🔙 Назад в Меню":
        return

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=cat)] for cat in CATEGORIES] +
                 [[KeyboardButton(text="🔙 Назад в Меню")]],
        resize_keyboard=True
    )
    await message.answer("⚠️ Выберите категорию из списка:", reply_markup=kb)


@router.message(DisputeState.waiting_claim_reason)
async def input_claim_reason(message: types.Message, state: FSMContext):
    """Ввод претензии"""
    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    if not message.text:
        await message.answer("⚠️ Пожалуйста, введите вашу претензию.")
        return

    claim_reason = message.text.strip()
    await state.update_data(claim_reason=claim_reason)

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Да"), KeyboardButton(text="Нет")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await state.set_state(DisputeState.waiting_claim_amount)
    await message.answer("💰 Желаете указать сумму иска?", reply_markup=kb)


@router.message(DisputeState.waiting_claim_amount)
async def input_claim_amount(message: types.Message, state: FSMContext):
    """Ввод суммы иска"""
    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    user_input = message.text.strip().lower()

    if user_input == "да":
        kb = get_back_to_menu_keyboard()
        await message.answer(
            "💰 Введите сумму иска в BTC (например: 0.00001):",
            reply_markup=kb
        )
        return

    elif user_input == "нет":
        claim_amount = None
        await state.update_data(claim_amount=claim_amount)
        await proceed_to_message_history(message, state)
        return

    else:
        try:
            claim_amount = float(message.text.replace(',', '').replace(' ', '.').strip())
            await state.update_data(claim_amount=claim_amount)
            await proceed_to_message_history(message, state)
            return
        except ValueError:
            await message.answer("⚠️ Введите корректную сумму или выберите 'Нет'.")


async def proceed_to_message_history(message: types.Message, state: FSMContext):
    """Переход к рассмотрению переписки"""
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Добавить переписку")],
            [KeyboardButton(text="⏭️ Пропустить")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await state.set_state(DisputeState.waiting_message_history)
    await message.answer(
        "📱 <b>Хотите добавить переписку как доказательство?</b>\n\n"
        "Вы можете переслать сюда сообщения из вашего спора.",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )


@router.message(DisputeState.waiting_message_history)
async def handle_message_history_choice(message: types.Message, state: FSMContext):
    """Обработка выбора переписки"""
    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    if message.text == "📱 Добавить переписку":
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Завершить добавление")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        await state.set_state(DisputeState.waiting_forwarded_messages)
        await message.answer(
            "📨 <b>Перешлите сюда сообщения из переписки</b>\n\n"
            "После завершения нажмите «✅ Завершить добавление».",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )

    elif message.text == "⏭️ Пропустить":
        await proceed_to_defendant_selection(message, state)

    else:
        await message.answer("⚠️ Выберите один из предложенных вариантов.")


@router.message(DisputeState.waiting_forwarded_messages)
async def handle_forwarded_messages(message: types.Message, state: FSMContext):
    """Обработка пересланных сообщений"""
    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    if message.text == "✅ Завершить добавление":
        data = await state.get_data()
        forwarded_messages = data.get("forwarded_messages", [])

        if forwarded_messages:
            await message.answer(f"✅ Добавлено {len(forwarded_messages)} сообщений как доказательства.")

        await proceed_to_defendant_selection(message, state)
        return

    if message.forward_from or message.forward_from_chat:
        data = await state.get_data()
        forwarded_messages = data.get("forwarded_messages", [])
        added_message_ids = data.get("added_message_ids", set())

        # Игнорируем уже добавленные сообщения
        if message.message_id in added_message_ids:
            return  # Не обрабатываем повторно

        # Добавляем сообщение в список
        forwarded_messages.append({
            "from_user": message.forward_from.username if message.forward_from else
            message.forward_from_chat.title if message.forward_from_chat else "Неизвестно",
            "text": message.text or message.caption or "(медиафайл)",
            "date": message.forward_date.isoformat() if message.forward_date else None
        })

        # Добавляем ID сообщения в set, чтобы не обрабатывать повторно
        added_message_ids.add(message.message_id)

        # Обновляем состояние один раз
        await state.update_data(forwarded_messages=forwarded_messages, added_message_ids=added_message_ids)

        # Отвечаем пользователю с минимальной нагрузкой
        if len(forwarded_messages) % 5 == 0:  # например, каждые 5 сообщений
            await message.answer(f"📩 Добавлено {len(forwarded_messages)} сообщений.")

    else:
        await message.answer("⚠️ Это не пересланное сообщение. Используйте функцию пересылки.")


# =============================================================================
# ПРИГЛАШЕНИЕ ОТВЕТЧИКА
# =============================================================================

async def proceed_to_defendant_selection(message: types.Message, state: FSMContext):
    """Переход к выбору ответчика"""
    # Создаем дело в БД
    data = await state.get_data()
    chat_id = data.get("chat_id")  # Может быть None если без группы

    case_number = await db.create_case(
        topic=data["topic"],
        category=data["category"],
        claim_reason=data["claim_reason"],
        mode="полный",
        plaintiff_id=message.from_user.id,
        plaintiff_username=message.from_user.username or message.from_user.full_name,
        chat_id=chat_id,
        version="pm"  # Пометка, что дело создано через ЛС
    )

    await state.update_data(case_number=case_number)
    await db.update_case_stage(case_number, "waiting_defendant")

    # Сохраняем переписку как доказательство
    forwarded_messages = data.get("forwarded_messages", [])
    if forwarded_messages:
        history_text = "📱 Переписка:\n\n"
        for msg in forwarded_messages:
            history_text += f"[{msg.get('date', 'без даты')}] {msg['from_user']}: {msg['text']}\n\n"

        await db.add_evidence(
            case_number,
            message.from_user.id,
            "plaintiff",
            "chat_history",
            history_text,
            None
        )

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )

    await state.set_state(DisputeState.waiting_defendant_username)
    await message.answer(
        f"✅ <b>Дело #{case_number} создано!</b>\n\n"
        f"📝 Тема: {data['topic']}\n"
        f"📂 Категория: {data['category']}\n"
        f"💰 Сумма иска: {data.get('claim_amount', 'не указана')}\n\n"
        f"👤 Введите username ответчика (например: @username или username):",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )


@router.message(DisputeState.waiting_defendant_username)
async def input_defendant_username(message: types.Message, state: FSMContext):
    """Ввод username ответчика с проверкой в БД"""
    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    if not message.text:
        await message.answer("⚠️ Введите username ответчика.")
        return

    username = message.text.strip()
    if username.startswith('@'):
        username = username[1:]

    data = await state.get_data()
    case_number = data.get("case_number")

    try:
        # Проверяем, есть ли пользователь в базе данных
        defendant_user = await db.get_user_by_username(username)

        bot_username = (await message.bot.get_me()).username
        invite_link = f"https://t.me/{bot_username}?start=defendant_{case_number}"

        if defendant_user:
            # Пользователь найден в БД - отправляем приглашение напрямую
            defendant_id = defendant_user['user_id']

            await state.update_data(defendant_username=username)

            # Создаем клавиатуру для ответчика
            kb_defendant = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="✅ Принять участие в деле",
                    callback_data=f"accept_defendant:{case_number}"
                )],
                [InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"reject_defendant:{case_number}"
                )]
            ])

            # Отправляем приглашение ответчику
            try:
                await message.bot.send_message(
                    defendant_id,
                    f"⚖️ <b>Приглашение в судебное дело</b>\n\n"
                    f"📋 Дело #{case_number}\n"
                    f"📝 Тема: {data['topic']}\n"
                    f"📂 Категория: {data['category']}\n"
                    f"💰 Сумма иска: {data.get('claim_amount', 'не указана')}\n\n"
                    f"👨‍⚖️ Истец: @{message.from_user.username or message.from_user.full_name}\n\n"
                    f"Вас пригласили участвовать в деле в качестве ответчика.\n"
                    f"Примите или отклоните участие:",
                    reply_markup=kb_defendant,
                    parse_mode=ParseMode.HTML
                )

                # Уведомляем истца об успешной отправке
                await message.answer(
                    f"✅ <b>Приглашение отправлено!</b>\n\n"
                    f"👤 Ответчик: @{username}\n"
                    f"📨 Приглашение доставлено напрямую.\n\n"
                    f"⏳ Ожидаем ответа от ответчика...",
                    parse_mode=ParseMode.HTML
                )

            except Exception as e:
                # Если не удалось отправить (например, пользователь заблокировал бота)
                kb_copy = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="📋 Скопировать ссылку",
                        url=invite_link
                    )]
                ])

                await message.answer(
                    f"⚠️ Не удалось отправить приглашение @{username} напрямую.\n\n"
                    f"Возможно, пользователь заблокировал бота или не начинал диалог.\n\n"
                    f"📨 Отправьте эту ссылку ответчику вручную:\n\n"
                    f"<code>{invite_link}</code>",
                    reply_markup=kb_copy,
                    parse_mode=ParseMode.HTML
                )
        else:
            # Пользователь не найден в БД - высылаем ссылку истцу
            await state.update_data(defendant_username=username)

            kb_copy = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📋 Скопировать ссылку",
                    url=invite_link
                )]
            ])

            await message.answer(
                f"ℹ️ <b>Пользователь @{username} не найден в базе данных</b>\n\n"
                f"Это означает, что он еще не взаимодействовал с ботом.\n\n"
                f"📨 Отправьте эту ссылку ответчику @{username}:\n\n"
                f"<code>{invite_link}</code>\n\n"
                f"Когда ответчик примет участие, вы получите уведомление.",
                reply_markup=kb_copy,
                parse_mode=ParseMode.HTML
            )

        # Уведомление в группу (если есть)
        chat_id = data.get("chat_id")
        if chat_id:
            try:
                await message.bot.send_message(
                    chat_id,
                    f"⚖️ Создано дело #{case_number}\n"
                    f"📝 Тема: {data['topic']}\n"
                    f"👨‍⚖️ Истец: @{message.from_user.username or message.from_user.full_name}\n"
                    f"👤 Ответчик: @{username}\n\n"
                    f"Процесс проходит в личных сообщениях с ботом."
                )
            except:
                pass

        await state.set_state(DisputeState.waiting_defendant_confirmation)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📂 Мои дела")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        await message.answer(
            "⏳ Ожидаем подтверждения от ответчика...\n\n"
            "Вы можете продолжить после того, как ответчик примет участие.",
            reply_markup=kb
        )

    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}\nПопробуйте еще раз.")


# =============================================================================
# ПОДТВЕРЖДЕНИЕ ОТВЕТЧИКОМ
# =============================================================================

@router.callback_query(F.data.startswith("accept_defendant:"))
async def accept_defendant(callback: CallbackQuery, state: FSMContext):
    """Принятие участия ответчиком"""
    case_number = callback.data.split(":")[1]

    case = await db.get_case_by_number(case_number)
    if not case:
        await callback.answer("⚠️ Дело не найдено", show_alert=True)
        return

    # Проверяем, что это не истец
    if callback.from_user.id == case["plaintiff_id"]:
        await callback.answer("⚠️ Вы не можете быть ответчиком в собственном деле", show_alert=True)
        return

    # Сохраняем ответчика
    await db.set_defendant(
        case_number,
        callback.from_user.id,
        callback.from_user.username or callback.from_user.full_name
    )

    await callback.answer("✅ Вы приняты в качестве ответчика!")

    # Уведомляем истца
    try:
        await callback.bot.send_message(
            case["plaintiff_id"],
            f"✅ @{callback.from_user.username or callback.from_user.full_name} принял участие в деле #{case_number}!\n\n"
            f"Начинаем процесс аргументации."
        )
    except:
        pass

    # Уведомление в группу
    if case.get("chat_id"):
        try:
            await callback.bot.send_message(
                case["chat_id"],
                f"✅ Ответчик @{callback.from_user.username or callback.from_user.full_name} принял участие в деле #{case_number}"
            )
        except:
            pass

    # Начинаем аргументацию истца
    await db.update_case_stage(case_number, "plaintiff_arguments")

    # Отправляем меню ответчику
    await callback.message.answer(
        f"📋 Дело #{case_number}\n"
        f"📝 Тема: {case['topic']}\n\n"
        f"⏳ Сейчас этап аргументов истца.\n"
        f"Вы получите уведомление, когда настанет ваша очередь."
    )

    # Уведомляем истца о начале аргументации
    kb_plaintiff = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Завершить аргументы")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )

    # Получаем FSMContext истца
    from aiogram.fsm.storage.base import StorageKey
    plaintiff_state = FSMContext(
        storage=state.storage,
        key=StorageKey(
            bot_id=(await callback.bot.get_me()).id,
            chat_id=case["plaintiff_id"],
            user_id=case["plaintiff_id"]
        )
    )

    await plaintiff_state.set_state(DisputeState.plaintiff_arguments)
    await plaintiff_state.update_data(case_number=case_number)

    try:
        await callback.bot.send_message(
            case["plaintiff_id"],
            "📝 <b>Представьте ваши аргументы</b>\n\n"
            "Вы можете отправлять:\n"
            "• Текстовые сообщения\n"
            "• Фото и видео\n"
            "• Документы\n\n"
            "После завершения нажмите «✅ Завершить аргументы».",
            reply_markup=kb_plaintiff,
            parse_mode=ParseMode.HTML
        )
    except:
        pass


@router.callback_query(F.data.startswith("reject_defendant:"))
async def reject_defendant(callback: CallbackQuery):
    """Отклонение участия ответчиком"""
    case_number = callback.data.split(":")[1]

    case = await db.get_case_by_number(case_number)
    if not case:
        await callback.answer("⚠️ Дело не найдено", show_alert=True)
        return

    await callback.answer("Вы отклонили участие в деле")

    # Уведомляем истца
    try:
        await callback.bot.send_message(
            case["plaintiff_id"],
            f"❌ @{callback.from_user.username or callback.from_user.full_name} отклонил участие в деле #{case_number}.\n\n"
            f"Вы можете пригласить другого ответчика."
        )
    except:
        pass

    kb = get_main_menu_keyboard()
    await callback.message.edit_text(
        f"❌ Вы отклонили участие в деле #{case_number}."
    )


# =============================================================================
# АРГУМЕНТАЦИЯ ИСТЦА
# =============================================================================

@router.message(DisputeState.plaintiff_arguments)
async def plaintiff_arguments_handler(message: types.Message, state: FSMContext):
    """Обработка аргументов истца"""
    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    if message.text == "✅ Завершить аргументы":
        data = await state.get_data()
        case_number = data.get("case_number")

        # Переходим к аргументам ответчика
        await db.update_case_stage(case_number, "defendant_arguments")

        case = await db.get_case_by_number(case_number)
        defendant_id = case.get("defendant_id")

        if not defendant_id:
            await message.answer("⚠️ Ответчик еще не принял участие в деле.")
            return

        # Уведомляем ответчика
        kb_defendant = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Завершить аргументы")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )

        from aiogram.fsm.storage.base import StorageKey
        defendant_state = FSMContext(
            storage=state.storage,
            key=StorageKey(
                bot_id=(await message.bot.get_me()).id,
                chat_id=defendant_id,
                user_id=defendant_id
            )
        )

        await defendant_state.set_state(DisputeState.defendant_arguments)
        await defendant_state.update_data(case_number=case_number)

        try:
            await message.bot.send_message(
                defendant_id,
                f"📝 <b>Дело #{case_number}</b>\n\n"
                f"Настала ваша очередь представить аргументы.\n\n"
                f"Вы можете отправлять:\n"
                f"• Текстовые сообщения\n"
                f"• Фото и видео\n"
                f"• Документы\n\n"
                f"После завершения нажмите «✅ Завершить аргументы».",
                reply_markup=kb_defendant,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            await message.answer(f"⚠️ Не удалось уведомить ответчика: {e}")

        # Уведомление в группу
        if case.get("chat_id"):
            try:
                await message.bot.send_message(
                    case["chat_id"],
                    f"⚖️ Дело #{case_number}\n"
                    f"✅ Истец завершил представление аргументов.\n"
                    f"⏳ Ожидаем аргументы ответчика."
                )
            except:
                pass

        kb = get_main_menu_keyboard()
        await message.answer(
            "✅ Ваши аргументы сохранены!\n\n"
            "⏳ Ожидаем аргументы ответчика...",
            reply_markup=kb
        )
        await state.clear()
        return

    # Сохраняем аргумент
    data = await state.get_data()
    case_number = data.get("case_number")

    if message.text:
        await db.add_evidence(
            case_number,
            message.from_user.id,
            "plaintiff",
            "text",
            message.text,
            None
        )
        await message.answer("✅ Аргумент добавлен. Продолжайте или нажмите «✅ Завершить аргументы».")

    elif message.photo:
        file_id = message.photo[-1].file_id
        await db.add_evidence(
            case_number,
            message.from_user.id,
            "plaintiff",
            "photo",
            message.caption or "Фото",
            file_id
        )
        await message.answer("📸 Фото добавлено как доказательство.")

    elif message.document:
        file_id = message.document.file_id
        await db.add_evidence(
            case_number,
            message.from_user.id,
            "plaintiff",
            "document",
            message.caption or "Документ",
            file_id
        )
        await message.answer("📎 Документ добавлен как доказательство.")

    elif message.video:
        file_id = message.video.file_id
        await db.add_evidence(
            case_number,
            message.from_user.id,
            "plaintiff",
            "video",
            message.caption or "Видео",
            file_id
        )
        await message.answer("🎥 Видео добавлено как доказательство.")


# =============================================================================
# АРГУМЕНТАЦИЯ ОТВЕТЧИКА
# =============================================================================

@router.message(DisputeState.defendant_arguments)
async def defendant_arguments_handler(message: types.Message, state: FSMContext):
    """Обработка аргументов ответчика"""
    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    if message.text == "✅ Завершить аргументы":
        data = await state.get_data()
        case_number = data.get("case_number")

        # Переходим к вопросам ИИ истцу
        await check_and_ask_ai_questions(message, state, case_number, "plaintiff")
        return

    # Сохраняем аргумент
    data = await state.get_data()
    case_number = data.get("case_number")

    if message.text:
        await db.add_evidence(
            case_number,
            message.from_user.id,
            "defendant",
            "text",
            message.text,
            None
        )
        await message.answer("✅ Аргумент добавлен. Продолжайте или нажмите «✅ Завершить аргументы».")

    elif message.photo:
        file_id = message.photo[-1].file_id
        await db.add_evidence(
            case_number,
            message.from_user.id,
            "defendant",
            "photo",
            message.caption or "Фото",
            file_id
        )
        await message.answer("📸 Фото добавлено как доказательство.")

    elif message.document:
        file_id = message.document.file_id
        await db.add_evidence(
            case_number,
            message.from_user.id,
            "defendant",
            "document",
            message.caption or "Документ",
            file_id
        )
        await message.answer("📎 Документ добавлен как доказательство.")

    elif message.video:
        file_id = message.video.file_id
        await db.add_evidence(
            case_number,
            message.from_user.id,
            "defendant",
            "video",
            message.caption or "Видео",
            file_id
        )
        await message.answer("🎥 Видео добавлено как доказательство.")


# =============================================================================
# ВОПРОСЫ ИИ
# =============================================================================

async def check_and_ask_ai_questions(message: types.Message, state: FSMContext, case_number: str, role: str):
    """Проверка и задавание вопросов ИИ"""
    data = await state.get_data()
    ai_round = data.get(f"ai_round_{role}", 0)

    if ai_round >= 3:  # Максимум 2 раунда вопросов
        # Если это был ответчик - переходим к вердикту
        if role == "defendant":
            await generate_final_verdict(message, state, case_number)
        else:
            # Если истец - переходим к вопросам ответчику
            await check_and_ask_ai_questions(message, state, case_number, "defendant")
        return

    # Генерируем вопросы через ИИ
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
        case, participants_info, evidence_info, role, ai_round + 1, message.bot
    )

    if not ai_questions or len(ai_questions) == 0:
        if role == "defendant":
            await generate_final_verdict(message, state, case_number)
        else:
            await check_and_ask_ai_questions(message, state, case_number, "defendant")
        return

    # Сохраняем вопросы
    for question in ai_questions:
        await db.save_ai_question(case_number, question, role, ai_round + 1)

    # Определяем кому задаем вопросы
    case = await db.get_case_by_number(case_number)
    target_user_id = case["plaintiff_id"] if role == "plaintiff" else case["defendant_id"]

    from aiogram.fsm.storage.base import StorageKey
    target_state = FSMContext(
        storage=state.storage,
        key=StorageKey(
            bot_id=(await message.bot.get_me()).id,
            chat_id=target_user_id,
            user_id=target_user_id
        )
    )

    await target_state.set_state(DisputeState.ai_asking_questions)
    await target_state.update_data(
        case_number=case_number,
        ai_questions=ai_questions,
        current_question_index=0,
        answering_role=role,
        ai_round=ai_round + 1,
        skip_count=0
    )

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⏭️ Пропустить вопрос")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )

    role_text = "Истец" if role == "plaintiff" else "Ответчик"

    try:
        await message.bot.send_message(
            target_user_id,
            f"🤖 <b>ИИ-судья задает уточняющие вопросы</b>\n\n"
            f"📝 <b>{role_text}</b>, ответьте на вопрос:\n\n"
            f"❓ {ai_questions[0]}\n\n"
            f"Вопрос 1 из {len(ai_questions)}",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )
    except:
        pass
    # Уведомление в группу
    if case.get("chat_id"):
        try:
            await message.bot.send_message(
                case["chat_id"],
                f"⚖️ Дело #{case_number}\n"
                f"🤖 ИИ-судья задает дополнительные вопросы {role_text}у."
            )
        except:
            pass


@router.message(DisputeState.ai_asking_questions)
async def handle_ai_question_response(message: types.Message, state: FSMContext):
    """Обработка ответов на вопросы ИИ (универсальный обработчик)"""
    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    data = await state.get_data()
    case_number = data.get("case_number")
    ai_questions = data.get("ai_questions", [])
    current_index = data.get("current_question_index", 0)
    answering_role = data.get("answering_role")
    ai_round = data.get("ai_round", 1)
    skip_count = data.get("skip_count", 0)

    if not ai_questions or current_index >= len(ai_questions):
        await message.answer("⚠️ Ошибка: вопросы не найдены.")
        await state.clear()
        return

    # Обработка пропуска
    if message.text == "⏭️ Пропустить вопрос":
        skip_count += 1
        await state.update_data(skip_count=skip_count)

        if skip_count >= 3:
            await message.answer("⚠️ Слишком много пропусков. Переходим дальше.")
            await finish_ai_questions(message, state, case_number, answering_role)
            return
    else:
        # Сохраняем ответ
        question_text = ai_questions[current_index]
        response_text = f"Вопрос ИИ: {question_text}\nОтвет: {message.text}"

        await db.add_evidence(
            case_number,
            message.from_user.id,
            answering_role,
            "ai_response",
            response_text,
            None
        )

        await db.save_ai_answer(
            case_number,
            question_text,
            message.text,
            answering_role,
            ai_round
        )
        skip_count = 0

    # Переход к следующему вопросу
    next_index = current_index + 1
    await state.update_data(current_question_index=next_index, skip_count=skip_count)

    if next_index < len(ai_questions):
        role_text = "Истец" if answering_role == "plaintiff" else "Ответчик"
        await message.answer(
            f"✅ Ответ принят.\n\n"
            f"📝 <b>{role_text}</b>, следующий вопрос:\n\n"
            f"❓ {ai_questions[next_index]}\n\n"
            f"Вопрос {next_index + 1} из {len(ai_questions)}",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer("✅ Все вопросы отвечены!")
        await finish_ai_questions(message, state, case_number, answering_role)


async def finish_ai_questions(message: types.Message, state: FSMContext, case_number: str, answering_role: str):
    """Завершение раунда вопросов ИИ"""
    data = await state.get_data()
    ai_round = data.get("ai_round", 1)

    # Сохраняем раунд
    case = await db.get_case_by_number(case_number)
    plaintiff_state = FSMContext(
        storage=state.storage,
        key=StorageKey(bot_id=(await message.bot.get_me()).id, chat_id=case["plaintiff_id"], user_id=case["plaintiff_id"])
    )
    defendant_state = FSMContext(
        storage=state.storage,
        key=StorageKey(bot_id=(await message.bot.get_me()).id, chat_id=case["defendant_id"], user_id=case["defendant_id"])
    )

    if answering_role == "plaintiff":
        await plaintiff_state.update_data(ai_round_plaintiff=ai_round)
        # Переход к ответчику
        await check_and_ask_ai_questions(message, defendant_state, case_number, "defendant")
    else:
        await defendant_state.update_data(ai_round_defendant=ai_round)
        # Финальный вердикт
        await generate_final_verdict(message, state, case_number)

    await state.clear()


@router.message(DisputeState.waiting_ai_question_response)
async def handle_ai_question_response(message: types.Message, state: FSMContext):
    """Обработка ответов на вопросы ИИ"""
    if message.text == "🔙 Назад в Меню":
        await return_to_main_menu(message, state)
        return

    data = await state.get_data()
    case_number = data.get("case_number")
    ai_questions = data.get("ai_questions", [])
    current_index = data.get("current_question_index", 0)
    answering_role = data.get("answering_role")
    ai_round = data.get("ai_round", 1)
    skip_count = data.get("skip_count", 0)

    if message.text == "⏭️ Пропустить вопрос":
        skip_count += 1
        await state.update_data(skip_count=skip_count)

        if skip_count >= 3:
            await message.answer("⚠️ Вы пропустили слишком много вопросов. Переходим к следующему этапу.")
            if answering_role == "plaintiff":
                await check_and_ask_ai_questions(message, state, case_number, "defendant")
            else:
                await generate_final_verdict(message, state, case_number)
            return
    else:
        # Сохраняем ответ
        skip_count = 0
        question_text = ai_questions[current_index]
        response_text = f"Вопрос ИИ: {question_text}\nОтвет: {message.text}"

        await db.add_evidence(
            case_number,
            message.from_user.id,
            answering_role,
            "ai_response",
            response_text,
            None
        )

        await db.save_ai_answer(
            case_number,
            question_text,
            message.text,
            answering_role,
            ai_round
        )

    # Следующий вопрос
    next_index = current_index + 1

    if next_index < len(ai_questions):
        await state.update_data(
            current_question_index=next_index,
            skip_count=skip_count
        )

        role_text = "Истец" if answering_role == "plaintiff" else "Ответчик"

        await message.answer(
            f"✅ Ответ принят.\n\n"
            f"📝 <b>{role_text}</b>, следующий вопрос:\n\n"
            f"❓ {ai_questions[next_index]}\n\n"
            f"Вопрос {next_index + 1} из {len(ai_questions)}",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer("✅ Спасибо за ответы!")

        # Сохраняем номер раунда
        await state.update_data(**{f"ai_round_{answering_role}": ai_round})

        if answering_role == "plaintiff":
            # Переходим к вопросам ответчику
            await check_and_ask_ai_questions(message, state, case_number, "defendant")
        else:
            # Генерируем финальный вердикт
            await generate_final_verdict(message, state, case_number)


# =============================================================================
# ФИНАЛЬНЫЙ ВЕРДИКТ
# =============================================================================

# =============================================================================
# ФАЙЛ: handlers.py
# ЗАМЕНИТЕ функцию generate_final_verdict
# =============================================================================

async def generate_final_verdict(message: types.Message, state: FSMContext, case_number: str):
    """Генерация финального вердикта"""
    await db.update_case_stage(case_number, "final_decision")
    await db.update_case_status(case_number, "finished")

    case = await db.get_case_by_number(case_number)
    if not case:
        await message.answer("Ошибка: дело не найдено.")
        return

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

    # Уведомляем о начале анализа
    plaintiff_id = case["plaintiff_id"]
    defendant_id = case.get("defendant_id")

    try:
        await message.bot.send_message(
            plaintiff_id,
            "⚖️ <b>ИИ-судья анализирует дело и выносит решение...</b>",
            parse_mode=ParseMode.HTML
        )
    except:
        pass

    if defendant_id:
        try:
            await message.bot.send_message(
                defendant_id,
                "⚖️ <b>ИИ-судья анализирует дело и выносит решение...</b>",
                parse_mode=ParseMode.HTML
            )
        except:
            pass

    # Генерируем решение
    try:
        decision = await gemini_service.generate_full_decision(
            case, participants_info, evidence_info, bot=message.bot
        )
        if not decision:
            decision = {
                "decision": "Решение не было сгенерировано.",
                "winner": "defendant",
                "verdict": {"claim_satisfied": False, "amount_awarded": 0},
                "reasoning": ""
            }
    except Exception as e:
        print(f"Ошибка генерации решения: {e}")
        decision = {
            "decision": "Техническая ошибка при вынесении решения.",
            "winner": "defendant",
            "verdict": {"claim_satisfied": False, "amount_awarded": 0},
            "reasoning": ""
        }

    # Генерируем PDF
    try:
        pdf_bytes = pdf_generator.generate_verdict_pdf(case, decision, participants_info, evidence_info)
        filepath = f"verdict_{case_number}.pdf"
        with open(filepath, "wb") as f:
            f.write(pdf_bytes)
        await db.save_decision(case_number=case_number, file_path=filepath)
    except Exception as e:
        print(f"Ошибка генерации PDF: {e}")
        filepath = None

    kb = get_main_menu_keyboard()

    # === ОТПРАВКА ИСТЦУ ===
    try:
        await message.bot.send_message(
            plaintiff_id,
            "⚖️ <b>Суд завершён!</b>\n\n"
            "Вот итоговый вердикт:",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )
        if filepath:
            await message.bot.send_document(plaintiff_id, FSInputFile(filepath))
    except Exception as e:
        print(f"Не удалось отправить истцу: {e}")

    # === ОТПРАВКА ОТВЕТЧИКУ ===
    if defendant_id:
        try:
            await message.bot.send_message(
                defendant_id,
                "⚖️ <b>Суд завершён!</b>\n\n"
                "Вот итоговый вердикт:",
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
            if filepath:
                await message.bot.send_document(defendant_id, FSInputFile(filepath))
        except Exception as e:
            print(f"Не удалось отправить ответчику: {e}")

    # === ОТПРАВКА В ГРУППУ ===
    if case.get("chat_id"):
        try:
            # Определяем победителя из решения
            winner_code = decision.get("winner", "draw")

            # Получаем информацию об участниках
            plaintiff_username = case['plaintiff_username']
            defendant_username = case.get('defendant_username', 'неизвестен')

            # Формируем текст победителя
            if winner_code == "plaintiff":
                winner_text = f"@{plaintiff_username} (Истец)"
                winner_emoji = "🏆"
            elif winner_code == "defendant":
                winner_text = f"@{defendant_username} (Ответчик)"
                winner_emoji = "🏆"
            else:  # draw
                winner_text = "Компромиссное решение (обе стороны частично правы)"
                winner_emoji = "⚖️"

            # Информация о суммах
            verdict = decision.get("verdict", {})
            amount_awarded = verdict.get("amount_awarded", 0)
            claim_amount = case.get("claim_amount", 0)

            verdict_details = ""
            if amount_awarded > 0:
                if claim_amount and amount_awarded < claim_amount:
                    verdict_details = f"\n💰 Присуждено: {amount_awarded} BTC (частичное удовлетворение из {claim_amount} BTC)"
                else:
                    verdict_details = f"\n💰 Присуждено: {amount_awarded} BTC"

            group_text = (
                f"⚖️ <b>ВЕРДИКТ ПО ДЕЛУ #{case_number}</b>\n\n"
                f"📋 Тема: {case['topic']}\n"
                f"👨‍⚖️ Истец: @{plaintiff_username}\n"
                f"👤 Ответчик: @{defendant_username}\n"
                f"{verdict_details}\n\n"
                f"{winner_emoji} <b>Решение вынесено в пользу:</b>\n{winner_text}\n\n"
                f"📄 Полный документ отправлен участникам в личные сообщения."
            )

            await message.bot.send_message(
                case["chat_id"],
                group_text,
                parse_mode=ParseMode.HTML
            )

            # Отправляем PDF в группу
            if filepath:
                await message.bot.send_document(
                    case["chat_id"],
                    FSInputFile(filepath),
                    caption=f"📄 Полный вердикт по делу #{case_number}"
                )
        except Exception as e:
            print(f"Ошибка отправки в группу: {e}")

    # === УДАЛЕНИЕ ВРЕМЕННОГО ФАЙЛА ===
    if filepath:
        try:
            os.remove(filepath)
        except:
            pass

    # === СООБЩЕНИЕ ИНИЦИАТОРУ ===
    await message.answer(
        "✅ Вердикт успешно вынесен и отправлен всем участникам!",
        reply_markup=kb
    )

    # Очищаем состояние
    await state.clear()


# =============================================================================
# СПРАВКА И ВСПОМОГАТЕЛЬНЫЕ КОМАНДЫ
# =============================================================================

@router.message(F.text == "ℹ️ Справка")
async def help_command(message: types.Message):
    """Справка"""
    kb = get_back_to_menu_keyboard()
    await message.answer(
        "📖 <b>Справка по использованию ИИ-судьи:</b>\n\n"
        "<b>Процесс работы:</b>\n"
        "1️⃣ Нажмите «⚖️ Начать Дело»\n"
        "2️⃣ Выберите: с группой или без\n"
        "3️⃣ Введите информацию о споре\n"
        "4️⃣ Пригласите ответчика по username\n"
        "5️⃣ Представьте аргументы\n"
        "6️⃣ Ответьте на вопросы ИИ-судьи\n"
        "7️⃣ Получите вердикт\n\n"
        "<b>Особенности:</b>\n"
        "• Весь процесс проходит в личных сообщениях\n"
        "• Если выбрана группа - туда отправляется только итоговый вердикт\n"
        "• Можно работать полностью без группы\n\n"
        "<b>Доказательства:</b>\n"
        "• Текстовые сообщения\n"
        "• Пересланные сообщения\n"
        "• Фото, видео, документы",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )


@router.message(F.text == "📂 Мои дела")
async def my_cases(message: types.Message, state: FSMContext):
    """Список дел пользователя"""
    user_id = message.from_user.id
    user_cases = await db.get_user_cases(user_id)

    if not user_cases:
        kb = get_back_to_menu_keyboard()
        await message.answer("📭 У вас пока нет дел.", reply_markup=kb)
        return

    page = 0
    text, total = await build_cases_text(user_cases, user_id, page)
    keyboard = build_pagination_keyboard(page, total)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def build_cases_text(user_cases, user_id, page: int):
    """Формирование текста списка дел"""
    start = page * CASES_PER_PAGE
    end = start + CASES_PER_PAGE
    total = len(user_cases)
    user_cases = list(reversed(user_cases))
    page_cases = user_cases[start:end]

    text = "📂 <b>Ваши дела:</b>\n\n"
    for case in page_cases:
        role = "Истец" if case["plaintiff_id"] == user_id else "Ответчик"
        status = "⚖️ В процессе" if case["status"] != "finished" else "✅ Завершено"
        claim_text = f" ({case['claim_amount']} BTC)" if case.get("claim_amount") else ""
        text += (
            f"📌 <b>Дело {case['case_number']}</b>\n"
            f"Тема: {case['topic']}{claim_text}\n"
            f"Категория: {case['category']}\n"
            f"Ваша роль: {role}\n"
            f"Статус: {status}\n\n"
        )
    text += f"📊 Всего дел: {total}\n"
    return text, total


def build_pagination_keyboard(page: int, total: int):
    """Клавиатура пагинации"""
    builder = InlineKeyboardBuilder()
    max_page = (total - 1) // CASES_PER_PAGE
    buttons = []

    if page > 0:
        buttons.append(types.InlineKeyboardButton(text="⬅️", callback_data=f"cases_page:{page - 1}"))
    if page < max_page:
        buttons.append(types.InlineKeyboardButton(text="➡️", callback_data=f"cases_page:{page + 1}"))

    if buttons:
        builder.row(*buttons)
    builder.row(types.InlineKeyboardButton(text="🔙 Назад в Меню", callback_data="back_to_menu"))

    return builder.as_markup()


@router.callback_query(F.data.startswith("cases_page:"))
async def paginate_cases(callback: CallbackQuery):
    """Пагинация дел"""
    page = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    user_cases = await db.get_user_cases(user_id)

    text, total = await build_cases_text(user_cases, user_id, page)
    keyboard = build_pagination_keyboard(page, total)

    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu_callback(callback: CallbackQuery, state: FSMContext):
    """Возврат в меню через callback"""
    await state.clear()
    kb = get_main_menu_keyboard()
    await callback.message.edit_text("📋 Главное меню:")
    await callback.bot.send_message(
        chat_id=callback.message.chat.id,
        text="Выберите действие:",
        reply_markup=kb
    )
    await callback.answer()


@router.message(F.text == "📝 Черновик")
async def draft_cases(message: types.Message, state: FSMContext):
    """Активные дела"""
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

    await message.answer(
        "📝 Ваши активные дела. Выберите для продолжения:",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("resume_case:"))
async def resume_case(callback: CallbackQuery, state: FSMContext):
    """Продолжение дела"""
    case_number = callback.data.split(":")[1]
    case = await db.get_case_by_number(case_number)

    if not case:
        await callback.answer("⚠️ Дело не найдено", show_alert=True)
        return

    user_id = callback.from_user.id
    stage = case.get("stage", "")

    await state.update_data(case_number=case_number)

    # Восстанавливаем состояние в зависимости от стадии
    if stage == "plaintiff_arguments":
        await state.set_state(DisputeState.plaintiff_arguments)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Завершить аргументы")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        await callback.message.answer(
            f"✅ Продолжаем дело #{case_number}\n\n"
            f"Продолжайте представление аргументов истца.",
            reply_markup=kb
        )

    elif stage == "defendant_arguments":
        await state.set_state(DisputeState.defendant_arguments)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Завершить аргументы")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        await callback.message.answer(
            f"✅ Продолжаем дело #{case_number}\n\n"
            f"Продолжайте представление аргументов ответчика.",
            reply_markup=kb
        )

    else:
        await callback.message.answer(
            f"⚠️ Дело #{case_number} находится на стадии: {stage}\n"
            f"Ожидайте дальнейших уведомлений."
        )

    await callback.answer()


# =============================================================================
# ОБРАБОТКА МЕДИА (для любого состояния аргументации)
# =============================================================================

@router.message(F.content_type.in_({"photo", "video", "document", "audio"}))
async def media_handler(message: types.Message, state: FSMContext):
    """Обработка медиафайлов"""
    current_state = await state.get_state()

    if current_state not in (DisputeState.plaintiff_arguments.state, DisputeState.defendant_arguments.state):
        return

    data = await state.get_data()
    case_number = data.get("case_number")

    if not case_number:
        await message.answer("⚠️ Ошибка: дело не найдено.")
        return

    # Определяем роль отправителя
    case = await db.get_case_by_number(case_number)
    if message.from_user.id == case["plaintiff_id"]:
        role = "plaintiff"
    elif message.from_user.id == case.get("defendant_id"):
        role = "defendant"
    else:
        return

    # Сохраняем медиа
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
        await db.add_evidence(
            case_number,
            message.from_user.id,
            role,
            content_type,
            message.caption or f"Файл ({content_type})",
            file_info
        )
        await message.answer(f"📎 {content_type.capitalize()} добавлен как доказательство.")


# =============================================================================
# ОБРАБОТКА ПАУЗЫ (опционально)
# =============================================================================

@router.message(F.text == "⏸️ Поставить дело на паузу")
async def pause_case_handler(message: types.Message, state: FSMContext):
    """Постановка дела на паузу"""
    data = await state.get_data()
    case_number = data.get("case_number")

    if not case_number:
        await message.answer("⚠️ Нет активного дела для паузы.")
        return

    case = await db.get_case_by_number(case_number)

    # Только истец может ставить на паузу
    if message.from_user.id != case["plaintiff_id"]:
        await message.answer("⚠️ Только истец может поставить дело на паузу.")
        return

    await db.update_case_status(case_number, status="paused")
    await state.set_state(DisputeState.case_paused)

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="▶️ Продолжить дело")],
            [KeyboardButton(text="🔙 Назад в Меню")]
        ],
        resize_keyboard=True
    )

    await message.answer(
        f"⏸️ <b>Дело #{case_number} поставлено на паузу</b>\n\n"
        f"Для продолжения нажмите «▶️ Продолжить дело»",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

    # Уведомляем ответчика
    if case.get("defendant_id"):
        try:
            await message.bot.send_message(
                case["defendant_id"],
                f"⏸️ Дело #{case_number} поставлено на паузу истцом.\n"
                f"Ожидайте возобновления."
            )
        except:
            pass

    # Уведомление в группу
    if case.get("chat_id"):
        try:
            await message.bot.send_message(
                case["chat_id"],
                f"⏸️ Дело #{case_number} поставлено на паузу."
            )
        except:
            pass


@router.message(F.text == "▶️ Продолжить дело")
async def continue_case_handler(message: types.Message, state: FSMContext):
    """Продолжение дела после паузы"""
    data = await state.get_data()
    case_number = data.get("case_number")

    if not case_number:
        await message.answer("⚠️ Нет дела для продолжения.")
        return

    case = await db.get_case_by_number(case_number)

    if case.get("status") != "paused":
        await message.answer("⚠️ Дело не на паузе.")
        return

    await db.update_case_status(case_number, status="active")

    stage = case.get("stage", "")

    # Восстанавливаем состояние
    if stage == "plaintiff_arguments":
        await state.set_state(DisputeState.plaintiff_arguments)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Завершить аргументы")],
                [KeyboardButton(text="⏸️ Поставить дело на паузу")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        await message.answer(
            f"▶️ Дело #{case_number} продолжено!\n\n"
            f"Продолжайте представление аргументов.",
            reply_markup=kb
        )

    elif stage == "defendant_arguments":
        await state.set_state(DisputeState.defendant_arguments)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Завершить аргументы")],
                [KeyboardButton(text="🔙 Назад в Меню")]
            ],
            resize_keyboard=True
        )
        await message.answer(
            f"▶️ Дело #{case_number} продолжено!\n\n"
            f"Продолжайте представление аргументов.",
            reply_markup=kb
        )

    # Уведомление в группу
    if case.get("chat_id"):
        try:
            await message.bot.send_message(
                case["chat_id"],
                f"▶️ Дело #{case_number} продолжено."
            )
        except:
            pass


@router.message(DisputeState.case_paused)
async def handle_paused_messages(message: types.Message):
    """Блокировка сообщений во время паузы"""
    if message.text not in ["▶️ Продолжить дело", "🔙 Назад в Меню"]:
        await message.answer("⏸️ Дело на паузе. Нажмите «▶️ Продолжить дело» для возобновления.")


# =============================================================================
# ОБРАБОТЧИК НЕИЗВЕСТНЫХ СООБЩЕНИЙ
# =============================================================================

@router.message()
async def unknown_message_handler(message: types.Message, state: FSMContext):
    """Обработка неизвестных сообщений"""
    # Игнорируем служебные сообщения
    if message.new_chat_members or message.left_chat_member or \
            message.migrate_from_chat_id or message.migrate_to_chat_id or \
            message.group_chat_created or message.supergroup_chat_created or \
            message.channel_chat_created:
        return

    # В группах игнорируем все сообщения кроме /start
    if message.chat.type in ("group", "supergroup"):
        return

    current_state = await state.get_state()

    if current_state is None:
        kb = get_main_menu_keyboard()
        await message.answer(
            "❓ Я не понял вашу команду.\n\n"
            "Выберите одну из доступных опций:",
            reply_markup=kb
        )
    else:
        kb_with_back = get_back_to_menu_keyboard()

        state_messages = {
            DisputeState.waiting_topic.state: "⚠️ Введите тему спора текстом.",
            DisputeState.waiting_category.state: "⚠️ Выберите категорию из предложенных.",
            DisputeState.waiting_claim_reason.state: "⚠️ Опишите вашу претензию текстом.",
            DisputeState.waiting_claim_amount.state: "⚠️ Ответьте 'Да' или 'Нет', либо введите сумму.",
            DisputeState.waiting_defendant_username.state: "⚠️ Введите username ответчика.",
            DisputeState.plaintiff_arguments.state: "⚠️ Отправьте аргумент или нажмите 'Завершить аргументы'.",
            DisputeState.defendant_arguments.state: "⚠️ Отправьте аргумент или нажмите 'Завершить аргументы'.",
            DisputeState.waiting_ai_question_response.state: "⚠️ Ответьте на вопрос ИИ-судьи.",
        }

        response_text = state_messages.get(current_state, "⚠️ Неизвестная команда.")
        await message.answer(response_text, reply_markup=kb_with_back)


# =============================================================================
# ОБРАБОТЧИК НЕИЗВЕСТНЫХ CALLBACK
# =============================================================================

@router.callback_query()
async def unknown_callback_handler(callback: CallbackQuery):
    """Обработка неизвестных callback"""
    await callback.answer("⚠️ Неизвестная команда", show_alert=True)


# =============================================================================
# РЕГИСТРАЦИЯ ХЕНДЛЕРОВ
# =============================================================================

def register_handlers(dp: Dispatcher):
    """Регистрация всех хендлеров"""
    dp.include_router(router)
