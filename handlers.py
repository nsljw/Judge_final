import asyncio
import os

from aiogram import Router, types, F, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated, CallbackQuery
)
from telethon.tl.functions.channels import EditAdminRequest, EditCreatorRequest, LeaveChannelRequest, \
    InviteToChannelRequest
from telethon.tl.functions.messages import ExportChatInviteRequest
from telethon.tl.types import ChatAdminRights

from database import db
from gemini_servise import gemini_service
from pdf_gen import PDFGenerator
from user_client import user_client

router = Router()
pdf_generator = PDFGenerator()


class DisputeState(StatesGroup):
    waiting_topic = State()
    waiting_category = State()
    waiting_claim_amount = State()
    plaintiff_arguments = State()
    defendant_arguments = State()
    finished = State()
    waiting_groupe = State()


class GroupState(StatesGroup):
    waiting_group_name = State()
    waiting_case_number = State()


CATEGORIES = [
    "Нарушение договора",
    "Плагиат. Интеллектуальная собственность",
    "Конфликт",
    "Долг/Займ",
    "Разделение имущества",
    "Спор",
    "Дебаты"
]


async def generate_invite_kb(bot, chat_id: int, case_number: str):
    try:
        print(f"🔗 Создаю invite-ссылку для дела {case_number} в чате {chat_id}")
        bot_member = await bot.get_chat_member(chat_id, bot.id)
        if bot_member.status not in ("administrator", "creator"):
            print("❌ Бот не является администратором!")
            return None

        invite_link_obj = await bot.create_chat_invite_link(
            chat_id=chat_id,
            name=f"Case {case_number}",
            member_limit=1,
            creates_join_request=False,
            expire_date=None
        )
        invite_link = invite_link_obj.invite_link
        print(f"✅ Ссылка создана: {invite_link}")

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
        await state.set_statef(DisputeState.defendant_arguments)
        print(f"✅ Ответчик {defendant_id} добавлен в дело {case_number}")


@router.message(Command("start"))
async def start_command(message: types.Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🏗 Создать группу")],
        [KeyboardButton(text="ℹ️ Справка")]
    ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer(
        "Здравстуйте, Я ИИ-бот Судья для решения ваших споров и конфликтов. Для начала работы ознакомьтесь с инструкцией: 'ℹ️ Справка' ",
        reply_markup=kb)


@router.callback_query(F.data == "start_chat")
async def start_chat_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚖ Начать Дело")],
            [KeyboardButton(text="📂 Мои дела")],
            [KeyboardButton(text="📝Черновик")],
            [KeyboardButton(text="ℹ️ Справка")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await callback.bot.send_message(
        chat_id=callback.message.chat.id,   # <--- вот этого у тебя не хватало
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


@router.message(GroupState.waiting_group_name)
async def input_group_name(message: types.Message, state: FSMContext):
    topic = message.text.strip()
    if not topic:
        await message.answer("❌ Название не может быть пустым.")
        return

    case_number = await db.create_case(
        topic=topic,
        chat_id=message.chat.id,
        category=None,
        claim_amount=None,
        mode="упрощенный",
        plaintiff_id=message.from_user.id,
        plaintiff_username=message.from_user.username or message.from_user.full_name,
        status="active"
    )

    result = await user_client.create_dispute_group(
        case_number=case_number,
        case_topic=topic,
        creator_id=message.from_user.id
    )

    if not result:
        await message.answer("❌ Произошла ошибка при создании группы.")
        await state.clear()
        return

    chat_id = result['chat_id']
    bot_id = (await message.bot.get_me()).id

    rights = ChatAdminRights(
        change_info=True,
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
        # Make bot admin first
        await user_client.client(EditAdminRequest(
            channel=chat_id,
            user_id=bot_id,
            admin_rights=rights,
            rank="Судья"
        ))

        await asyncio.sleep(1)
        await user_client.client(InviteToChannelRequest(
            channel=chat_id,
            users=[message.from_user.id]
        ))
        invite = await user_client.client(ExportChatInviteRequest(
            peer=chat_id,
            legacy_revoke_permanent=True,
            request_needed=False,
            usage_limit=1
        ))
        invite_link = invite.link

        await message.answer(
            f"✅ Группа успешно создана!\n"
            f"Название: {result['title']}\n"
            f"Истец нажмите на кнопку чтобы войти в группу.\n"
            f"Ссылка на группу: {invite_link}"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка при настройке группы: {e}")
        await state.clear()
        return

    await state.clear()


@router.my_chat_member()
async def bot_added(event: ChatMemberUpdated):
    if (event.old_chat_member is None or event.old_chat_member.status in ("kicked", "left")) \
            and event.new_chat_member.status in ("member", "administrator"):
        return

    if event.new_chat_member.user.id == (await event.bot.get_me()).id:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⚖ Начать", callback_data="start_chat")]
            ]
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
    await message.answer(
        "📖 *Справка по использованию ИИ судьи:*\n\n"
        "*Подготовка:*\n"
        "🔸 Создайте группу в Telegram «🏗 Создать группу » \n"
        "🔸 Перейдите в группу вашего дела \n"
        "*Процесс разбирательства:*\n"
        "1️⃣ Нажмите «⚖️ Начать Дело»\n"
        "2️⃣ Введите тему спора\n"
        "3️⃣ Выберите категорию\n"
        "4️⃣ Укажите сумму иска (опционально)\n"
        "5️⃣ Поделитесь ссылкой с ответчиком\n"
        "6️⃣ Истец представляет аргументы\n"
        "7️⃣ Ответчик представляет аргументы\n"
        "8️⃣ Бот выносит решение и генерирует PDF\n\n"
        "*Дополнительно:*\n"
        "📝 Используйте «Черновик» для продолжения незавершенных дел\n"
        "📂 Просматривайте историю в «Мои дела»",
        parse_mode="Markdown"
    )


@router.message(F.text == "🏗 Создать группу")
async def create_group(message: types.Message, state: FSMContext):
    if not user_client.is_connected:
        await message.answer("❌ Сначала необходимо авторизоваться!")
        return

    await state.set_state(GroupState.waiting_group_name)
    await message.answer("Введите тему спора / название группы:", reply_markup=ReplyKeyboardRemove())


@router.message(F.text == "📂 Мои дела")
async def my_cases(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_cases = await db.get_user_cases(user_id)
    if not user_cases:
        await message.answer("📭 У вас пока нет дел.")
        return

    text = "📂 *Ваши дела:*\n\n"
    for case in user_cases:
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
    await message.answer(text, parse_mode="Markdown")


@router.message(F.text == "📝Черновик")
async def draft_cases(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    active_cases = await db.get_user_active_cases(user_id)
    if not active_cases:
        await message.answer("📭 У вас нет активных дел.")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"📌 {case['case_number']} - {case['topic'][:30]}{'...' if len(case['topic']) > 30 else ''}",
                callback_data=f"resume_case:{case['case_number']}"
            )]
            for case in active_cases
        ]
    )
    await message.answer("📝 Ваши активные дела. Выберите дело для продолжения:", reply_markup=kb)


@router.callback_query(F.data.startswith("resume_case:"))
async def resume_case(callback: CallbackQuery, state: FSMContext):
    case_number = callback.data.split(":")[1]
    case = await db.get_case_by_number(case_number)
    if not case:
        await callback.answer("⚠ Дело не найдено", show_alert=True)
        return

    user_role = await check_user_role_in_case(case_number, callback.from_user.id)
    if not user_role:
        await callback.answer("⚠ У вас нет доступа к этому делу", show_alert=True)
        return

    await state.update_data(case_number=case_number)
    stage = case.get("stage", "plaintiff")

    if stage == "plaintiff":
        if user_role != "plaintiff":
            await callback.message.answer("⚠️ Сейчас стадия аргументов истца. Ожидайте своей очереди.")
            await callback.answer()
            return
        await state.set_state(DisputeState.plaintiff_arguments)
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Завершить аргументы")]],
            resize_keyboard=True
        )
        await callback.message.answer(
            f"✅ Вы продолжаете дело №{case_number}\n"
            f"*Стадия:* Аргументы истца\n\n"
            f"Истец, введите ваши аргументы:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
    else:
        if user_role != "defendant":
            await callback.message.answer("⚠️ Сейчас стадия аргументов ответчика. Ожидайте завершения.")
            await callback.answer()
            return
        await state.set_state(DisputeState.defendant_arguments)
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Завершить аргументы")]],
            resize_keyboard=True
        )
        await callback.message.answer(
            f"✅ Вы продолжаете дело №{case_number}\n"
            f"*Стадия:* Аргументы ответчика\n\n"
            f"Ответчик, введите ваши аргументы:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
    await callback.answer()


@router.message(F.text == "⚖ Начать Дело")
async def start_dispute(message: types.Message, state: FSMContext):
    if message.chat.type not in ("group", "supergroup"):
        await message.answer(
            "⚠️ *Внимание!* Дело нужно создавать в группе.\n\n"
            "📋 *Инструкция:*\n"
            "1. Создайте группу в Telegram\n"
            "2. Добавьте меня в группу как администратора\n"
            "3. В группе напишите /start и выберите «⚖ Начать Дело»",
            parse_mode="Markdown"
        )
        return

    # Проверяем, есть ли уже активное дело в этой группе
    # existing_case = await db.get_case_by_chat(message.chat.id)
    # if existing_case and existing_case["status"] == "active":
    #     await message.answer(
    #         f"⚠️ В этой группе уже есть активное дело №{existing_case['case_number']}\n"
    #         f"Тема: {existing_case['topic']}\n\n"
    #         f"Завершите текущее дело перед созданием нового."
    #     )
    #     return

    await state.set_state(DisputeState.waiting_topic)
    await message.answer(
        "⚖️ *Создание нового дела*\n\n"
        "Введите тему спора:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )


@router.message(DisputeState.waiting_topic)
async def input_topic(message: types.Message, state: FSMContext):
    if message.new_chat_members or message.left_chat_member:
        return
    if not message.text:
        await message.answer("⚠️ Пожалуйста, введите тему спора текстом.")
        return
    topic = message.text.strip()
    await state.update_data(topic=topic)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=cat)] for cat in CATEGORIES],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await state.set_state(DisputeState.waiting_category)
    await message.answer("Выберите категорию спора:", reply_markup=kb)


@router.message(DisputeState.waiting_category, F.text.in_(CATEGORIES))
async def select_category(message: types.Message, state: FSMContext):
    category = message.text.strip()
    await state.update_data(category=category)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Да"), KeyboardButton(text="Нет")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await state.set_state(DisputeState.waiting_claim_amount)
    await message.answer("Желаете указать сумму иска?", reply_markup=kb)


@router.message(DisputeState.waiting_category)
async def invalid_category(message: types.Message, state: FSMContext):
    if message.new_chat_members or message.left_chat_member:
        return
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=cat)] for cat in CATEGORIES],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer("⚠️ Пожалуйста, выберите категорию из предложенных:", reply_markup=kb)


@router.message(DisputeState.waiting_claim_amount)
async def input_claim_amount(message: types.Message, state: FSMContext):
    if message.new_chat_members or message.left_chat_member:
        return
    if not message.text:
        await message.answer("⚠️ Пожалуйста, ответьте «Да» или «Нет»")
        return

    data = await state.get_data()
    claim_amount = None
    if message.text.lower() == "да":
        await message.answer("Введите сумму иска в $ (например: 1500$):")
        return
    elif message.text.lower() == "нет":
        claim_amount = None
    else:
        try:
            claim_amount = float(message.text.replace('$', '').replace(',', '.').strip())
        except ValueError:
            await message.answer("⚠️ Введите корректное число. Например: 1200$")
            return

    chat_id = message.chat.id
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("⚠️ Создание дела возможно только в группе!")
        return

    case_number = await db.create_case(
        topic=data["topic"],
        category=data["category"],
        claim_amount=claim_amount,
        mode="упрощенный",
        plaintiff_id=message.from_user.id,
        plaintiff_username=message.from_user.username or message.from_user.full_name,
        chat_id=chat_id
    )
    await state.update_data(case_number=case_number)
    await state.set_state(DisputeState.plaintiff_arguments)

    is_admin = await ensure_bot_admin(message.bot, chat_id)
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
            parse_mode="Markdown"
        )
    else:
        kb = await generate_invite_kb(message.bot, chat_id, case_number)
        if kb:
            await message.answer(
                f"✅ *Дело успешно создано!*\n\n"
                f"📋 Номер дела: `{case_number}`\n"
                f"📝 Тема: {data['topic']}\n"
                f"📂 Категория: {data['category']}\n"
                f"💰 Сумма иска: {claim_amount if claim_amount else 'не указана'}\n\n"
                f"👇 Отправьте эту ссылку ответчику:\n\n\n",
                reply_markup=kb,
                parse_mode="Markdown"
            )
        else:
            await message.answer(
                f"✅ *Дело создано!*\n\n"
                f"📋 Номер дела: `{case_number}`\n"
                f"📝 Тема: {data['topic']}\n"
                f"📂 Категория: {data['category']}\n"
                f"💰 Сумма иска: {claim_amount if claim_amount else 'не указана'}\n\n"
                f"⚠️ Не удалось создать автоматическую ссылку.\n"
                f"Пригласите ответчика в группу вручную.",
                parse_mode="Markdown"
            )

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Завершить аргументы")]],
        resize_keyboard=True
    )
    await message.answer(
        "📝 *Истец*, представьте ваши аргументы.\n"
        "Вы можете отправлять текст, фото, документы и видео.\n\n"
        "После завершения нажмите «Завершить аргументы».",
        reply_markup=kb,
        parse_mode="Markdown"
    )


@router.message(DisputeState.plaintiff_arguments)
async def plaintiff_args(message: types.Message, state: FSMContext):
    if message.new_chat_members or message.left_chat_member:
        return

    data = await state.get_data()
    case_number = data.get("case_number")
    if not case_number:
        await message.answer("⚠️ Ошибка: дело не найдено. Начните новое дело.")
        await state.clear()
        return

    user_role = await check_user_role_in_case(case_number, message.from_user.id)
    if user_role != "plaintiff":
        await message.answer("⚠️ Только истец может добавлять аргументы на этой стадии.")
        return

    if not message.text:
        await message.answer("⚠️ Пожалуйста, отправьте текстовое сообщение с аргументами.")
        return

    if message.text.lower().startswith("завершить"):
        await db.update_case_stage(case_number, "defendant")
        await state.set_state(DisputeState.defendant_arguments)

        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Завершить аргументы")]],
            resize_keyboard=True
        )
        await message.answer(
            f"✅ *Этап аргументов истца завершен!*\n\n"
            f"📝 *Ответчик*, теперь ваша очередь представить свою позицию.\n"
            f"Вы можете отправлять текст, фото, документы и видео.\n\n"
            f"После завершения нажмите «Завершить аргументы».",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return

    await db.add_evidence(case_number, message.from_user.id, "plaintiff", "text", message.text, None)

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Завершить аргументы")]],
        resize_keyboard=True
    )
    await message.answer(
        f"📝 Аргумент истца добавлен.\n\n"
        f"Введите следующий аргумент или нажмите «Завершить аргументы».",
        reply_markup=kb
    )


@router.message(DisputeState.defendant_arguments)
async def defendant_args(message: types.Message, state: FSMContext):
    if message.new_chat_members or message.left_chat_member:
        return

    data = await state.get_data()
    case_number = data.get("case_number")
    if not case_number:
        await message.answer("⚠️ Ошибка: дело не найдено.")
        await state.clear()
        return

    user_role = await check_user_role_in_case(case_number, message.from_user.id)
    if user_role != "defendant":
        await message.answer("⚠️ Только ответчик может добавлять аргументы на этой стадии.")
        return

    if not message.text:
        await message.answer("⚠️ Пожалуйста, отправьте текстовое сообщение с аргументами.")
        return

    if message.text.lower().startswith("завершить"):
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

        decision = await gemini_service.generate_full_decision(
            case, participants_info, evidence_info, bot=message.bot
        )

        pdf_bytes = pdf_generator.generate_verdict_pdf(case, decision, participants_info, evidence_info)

        filepath = f"verdict_{case_number}.pdf"
        with open(filepath, "wb") as f:
            f.write(pdf_bytes)

        verdict_kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⚖ Начать Дело")],
                [KeyboardButton(text="📂 Мои дела")],
                [KeyboardButton(text="📝Черновик")],
                [KeyboardButton(text="ℹ️ Справка")]
            ],
            resize_keyboard=True
        )
        await message.answer("⚖️ Суд завершён. Итоговый вердикт:", reply_markup=verdict_kb)

        sent = await message.answer_document(FSInputFile(filepath))
        try:
            await message.bot.pin_chat_message(
                chat_id=message.chat.id,
                message_id=sent.message_id,
                disable_notification=False
            )
        except Exception as e:
            print(f"Не удалось закрепить файл:{e}")
        await db.save_decision(filepath)
        os.remove(filepath)
        await state.clear()
        return

    await db.add_evidence(
        case_number,
        message.from_user.id,
        "defendant",
        "text",
        message.text,
        None
    )

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Завершить аргументы")]],
        resize_keyboard=True
    )

    await message.answer(
        "📝 Аргумент добавлен.\n\n"
        "Введите следующий аргумент или нажмите «Завершить аргументы».",
        reply_markup=kb
    )


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
        await message.answer("⚠️ Вы не являетесь участником этого дела.")
        return

    if (current_state == DisputeState.plaintiff_arguments.state and user_role != "plaintiff") or \
            (current_state == DisputeState.defendant_arguments.state and user_role != "defendant"):
        stage_name = "истца" if current_state == DisputeState.plaintiff_arguments.state else "ответчика"
        await message.answer(f"⚠️ Сейчас стадия аргументов {stage_name}. Ожидайте своей очереди.")
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
            keyboard=[[KeyboardButton(text="Завершить аргументы")]],
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

    # Если пользователь находится в процессе дела, но отправляет неподходящее сообщение
    # if current_state in (DisputeState.plaintiff_arguments.state, DisputeState.defendant_arguments.state):
    #     data = await state.get_data()
    #     case_number = data.get("case_number")
    #     if case_number:
    #         user_role = await check_user_role_in_case(case_number, message.from_user.id)
    #         if user_role:
    #             # Если пользователь участник дела, но сейчас не его очередь
    #             if (current_state == DisputeState.plaintiff_arguments.state and user_role != "plaintiff"):
    #                 await message.answer("⚠️ Сейчас стадия аргументов истца. Ожидайте своей очереди.")
    #                 return
    #             elif (current_state == DisputeState.defendant_arguments.state and user_role != "defendant"):
    #                 await message.answer("⚠️ Сейчас стадия аргументов ответчика. Ожидайте завершения.")
    #                 return

    if current_state is None:
        if message.chat.type == "private":
            kb = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="⚖ Начать Дело")],
                    [KeyboardButton(text="📂 Мои дела")],
                    [KeyboardButton(text="📝Черновик")],
                    [KeyboardButton(text="ℹ️ Справка")]
                ],
                resize_keyboard=True
            )
            await message.answer(
                "❓ Я не понял вашу команду.\n\n"
                "Выберите одну из доступных опций:",
                reply_markup=kb
            )
        # else:
        #     # В группе проверяем активное дело только если нет текущего состояния
        #     case = await db.get_case_by_chat(message.chat.id)
        #     if case and case["status"] == "active":
        #         stage = case.get("stage", "plaintiff")
        #         stage_text = "истца" if stage == "plaintiff" else "ответчика"
        #         await message.answer(
        #             f"⚖️ В этой группе есть активное дело №{case['case_number']}\n"
        #             f"Тема: {case['topic']}\n"
        #             f"Текущая стадия: аргументы {stage_text}\n\n"
        #             f"Используйте команду /start для управления делом."
        #         )
    else:
        if current_state == DisputeState.waiting_topic.state:
            await message.answer("⚠️ Пожалуйста, введите тему спора текстом.")
        elif current_state == DisputeState.waiting_category.state:
            kb = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text=cat)] for cat in CATEGORIES],
                resize_keyboard=True
            )
            await message.answer("⚠️ Выберите категорию из предложенных:", reply_markup=kb)
        elif current_state == DisputeState.waiting_claim_amount.state:
            kb = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="Да"), KeyboardButton(text="Нет")]],
                resize_keyboard=True
            )
            await message.answer("⚠️ Ответьте «Да» или «Нет» на вопрос о сумме иска:", reply_markup=kb)


@router.callback_query()
async def unknown_callback_handler(callback: CallbackQuery):
    await callback.answer("⚠️ Неизвестная команда", show_alert=True)


def register_handlers(dp: Dispatcher):
    dp.include_router(router)