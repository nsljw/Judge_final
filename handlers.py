import os
import uuid
from database import db

from aiogram import Router, types, F, Dispatcher
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated
)

from gemini_servise import gemini_service
from pdf_gen import PDFGenerator

router = Router()
pdf_generator = PDFGenerator()


# ===== СОСТОЯНИЯ =====
class DisputeState(StatesGroup):
    waiting_topic = State()
    waiting_category = State()
    waiting_claim_amount = State()
    plaintiff_arguments = State()
    defendant_arguments = State()
    finished = State()
    active = State()


CATEGORIES = [
    "Нарушение договора",
    "Плагиат. Интеллектуальная собственность",
    "Конфликт",
    "Долг/Займ",
    "Разделение имущества",
    "Спор",
    "Дебаты"
]


# ===== ГЕНЕРАЦИЯ ПРИГЛАШЕНИЙ =====
async def generate_invite_kb(bot, chat_id: int, case_number: str):
    """Создаёт ссылки-приглашения и сохраняет их в БД"""
    member = await bot.get_chat_member(chat_id, bot.id)
    if not isinstance(member, (types.ChatMemberAdministrator, types.ChatMemberOwner)):
        raise Exception("❌ Бот не является админом в этой группе, нельзя создать приглашение.")

    # Ссылка для ответчика
    defendant_link = await bot.create_chat_invite_link(
        chat_id=chat_id,
        name=f"Ответчик для {case_number}",
        member_limit=1
    )
    await db.add_invitation(case_number, chat_id, "defendant", defendant_link.invite_link)

    # Ссылка для свидетеля
    witness_link = await bot.create_chat_invite_link(
        chat_id=chat_id,
        name=f"Свидетель для {case_number}",
        member_limit=1
    )
    await db.add_invitation(case_number, chat_id, "witness", witness_link.invite_link)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨‍💼 Пригласить ответчика", url=defendant_link.invite_link)],
        [InlineKeyboardButton(text="👀 Пригласить свидетеля", url=witness_link.invite_link)]
    ])
    return kb


# ===== ПРИСОЕДИНЕНИЕ ПОЛЬЗОВАТЕЛЯ =====
@router.my_chat_member()
async def on_user_joined(event: ChatMemberUpdated):
    user = event.from_user
    chat_id = event.chat.id
    new_status = event.new_chat_member.status

    if new_status in ["member", "restricted"]:
        invitations = await db.get_active_invitations(chat_id)
        for invite in invitations:
            already = await db.is_participant(invite["case_number"], user.id)
            if not already:
                await db.add_participant(
                    case_number=invite["case_number"],
                    user_id=user.id,
                    username=user.username or user.full_name,
                    role=invite["role"]
                )
                try:
                    await event.bot.send_message(
                        user.id,
                        f"✅ Вы добавлены как *{invite['role'].capitalize()}* в дело {invite['case_number']}",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
        await db.mark_invitations_used(user.id, chat_id)


# ===== /start =====
@router.message(Command("start"))
async def start_command(message: types.Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        payload = args[1]

        if payload.startswith("invite_defendant_"):
            case_number = payload.replace("invite_defendant_", "")
            case = await db.get_case_by_number(case_number)
            if case:
                already = await db.is_participant(case_number, message.from_user.id)
                if not already:
                    await db.set_defendant(
                        case_number=case_number,
                        defendant_id=message.from_user.id,
                        defendant_username=message.from_user.username or message.from_user.full_name
                    )
                await message.answer(f"✅ Вы добавлены как *Ответчик* в дело {case_number}", parse_mode="Markdown")
            else:
                await message.answer("❌ Неверная или устаревшая ссылка на дело.")
            return

        elif payload.startswith("invite_witness_"):
            case_number = payload.replace("invite_witness_", "")
            case = await db.get_case_by_number(case_number)
            if case:
                already = await db.is_participant(case_number, message.from_user.id)
                if not already:
                    await db.add_participant(
                        case_number, message.from_user.id,
                        message.from_user.username or message.from_user.full_name,
                        "witness"
                    )
                await message.answer(f"👀 Вы добавлены как *Свидетель* в дело {case_number}", parse_mode="Markdown")
            else:
                await message.answer("❌ Неверная или устаревшая ссылка на дело.")
            return

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
    await message.answer(
        "Здравствуйте! ⚖️Я — ИИ судья.\n"
        "Я помогу объективно рассмотреть спор.",
        reply_markup=kb
    )


# ===== СПРАВКА =====
@router.message(F.text == "ℹ️ Справка")
async def help_command(message: types.Message):
    await message.answer(
        "📖 *Справка:*\n\n"
        "1️⃣ Нажмите «⚖️ Начать Дело».\n"
        "2️⃣ Введите тему спора.\n"
        "3️⃣ Выберите категорию.\n"
        "4️⃣ Укажите сумму иска (если требуется).\n"
        "5️⃣ Пригласите участников по ссылкам.\n"
        "6️⃣ Истец вводит аргументы.\n"
        "7️⃣ Ответчик вводит аргументы.\n"
        "8️⃣ Бот вынесет решение и сформирует PDF.\n",
        parse_mode="Markdown"
    )


# ===== МОИ ДЕЛА =====
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
        text += (
            f"📌 Дело {case['case_number']}\n"
            f"Тема: {case['topic']}\n"
            f"Категория: {case['category']}\n"
            f"Ваша роль: {role}\n"
            f"Статус: {status}\n\n"
        )
    await message.answer(text, parse_mode="Markdown")


# ===== НАЧАТЬ ДЕЛО =====
@router.message(F.text == "⚖ Начать Дело")
async def start_dispute(message: types.Message, state: FSMContext):
    await state.set_state(DisputeState.waiting_topic)
    await message.answer("Введите тему спора:", reply_markup=ReplyKeyboardRemove())


# ===== ТЕМА =====
@router.message(DisputeState.waiting_topic)
async def input_topic(message: types.Message, state: FSMContext):
    topic = message.text.strip()
    await state.update_data(topic=topic)

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=cat)] for cat in CATEGORIES],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await state.set_state(DisputeState.waiting_category)
    await message.answer("Выберите категорию:", reply_markup=kb)


# ===== КАТЕГОРИЯ =====
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


# ===== СУММА ИСКА =====
@router.message(DisputeState.waiting_claim_amount)
async def input_claim_amount(message: types.Message, state: FSMContext):
    data = await state.get_data()
    claim_amount = None

    if message.text.lower() == "да":
        await message.answer("Введите сумму иска в $:")
        return
    elif message.text.lower() == "нет":
        claim_amount = None
    else:
        try:
            claim_amount = float(message.text.replace('$', '').replace(',', '.').strip())
        except ValueError:
            await message.answer("Введите корректное число. Например: 1200$")
            return

    # Создаём дело
    case_number = await db.create_case(
        topic=data["topic"],
        category=data["category"],
        claim_amount=claim_amount,
        mode="упрощенный",
        plaintiff_id=message.from_user.id,
        plaintiff_username=message.from_user.username or message.from_user.full_name,
        status="active"
    )

    await state.update_data(case_number=case_number)
    await state.set_state(DisputeState.plaintiff_arguments)

    chat_id = message.chat.id
    try:
        kb = await generate_invite_kb(message.bot, chat_id, case_number)
    except Exception as e:
        await message.answer(f"⚠ Не удалось создать приглашение: {e}")
        kb = None

    await message.answer(
        f"✅ Дело создано!\n"
        f"Номер: {case_number}\n"
        f"Тема: {data['topic']}\n"
        f"Категория: {data['category']}\n"
        f"Сумма иска: {claim_amount}\n\n"
        "Пригласите участников:",
        reply_markup=kb
    )
    await message.answer("Теперь истец вводит аргументы.")


# ===== АРГУМЕНТЫ ИСТЦА =====
@router.message(DisputeState.plaintiff_arguments)
async def plaintiff_args(message: types.Message, state: FSMContext):
    data = await state.get_data()
    case_number = data["case_number"]
    if message.new_chat_members:
        return
    if message.text and message.text.lower().startswith("завершить"):
        await state.set_state(DisputeState.defendant_arguments)
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Завершить аргументы")]],
            resize_keyboard=True
        )
        await message.answer("✅ Истец завершил. Теперь ответчик вводит аргументы:", reply_markup=kb)
        return

    await db.add_evidence(case_number, message.from_user.id, "plaintiff", "text", message.text, None)
    await message.answer("Аргумент истца добавлен. Введите следующий или завершите.")


# ===== АРГУМЕНТЫ ОТВЕТЧИКА =====
@router.message(DisputeState.defendant_arguments)
async def defendant_args(message: types.Message, state: FSMContext):
    data = await state.get_data()
    case_number = data["case_number"]

    if message.text and message.text.lower().startswith("завершить"):
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
            {"type": e["type"], "description": e["content"] or e["file_path"]}
            for e in evidence
        ]

        decision = await gemini_service.generate_full_decision(case, participants_info, evidence_info)
        pdf_bytes = pdf_generator.generate_verdict_pdf(case, decision, participants_info, evidence_info)

        filepath = f"verdict_{case_number}.pdf"
        with open(filepath, "wb") as f:
            f.write(pdf_bytes)

        verdict_kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⚖ Начать Дело")],
                [KeyboardButton(text="ℹ️ Справка")]
            ],
            resize_keyboard=True
        )
        await message.answer("⚖️ Суд завершён. Итоговый вердикт:", reply_markup=verdict_kb)
        await message.answer_document(FSInputFile(filepath))
        os.remove(filepath)
        return

    await db.add_evidence(case_number, message.from_user.id, "defendant", "text", message.text, None)
    await message.answer("Аргумент ответчика добавлен. Введите следующий или завершите.")


# ===== РЕГИСТРАЦИЯ =====
def register_handlers(dp: Dispatcher):
    dp.include_router(router)
