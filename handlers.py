from aiogram import Router, types, F, Dispatcher
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
import uuid
import os

from gemini_servise import gemini_service
from pdf_gen import PDFGenerator
from database import db

router = Router()
pdf_generator = PDFGenerator()

# ===== СОСТОЯНИЯ =====
class DisputeState(StatesGroup):
    waiting_topic = State()
    waiting_category = State()
    waiting_claim_amount = State()
    waiting_defendant = State()
    plaintiff_arguments = State()
    defendant_arguments = State()
    finished = State()


# ===== ДАННЫЕ =====
rooms = {}             # room_id -> данные спора
user_roles = {}        # user_id -> роль (plaintiff/defendant)

CATEGORIES = [
    "Нарушение договора",
    "Плагиат. Интеллектуальная собственность",
    "Конфликт",
    "Долг/Займ",
    "Разделение имущества",
    "Спор",
    "Дебаты"
]


# ===== /start =====
@router.message(Command("start"))
async def start_command(message: types.Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Начать разбирательство")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer(
        "Здравствуйте! ⚖️Я — ИИ судья, созданный для объективного и беспристрастного разрешения споров и конфликтных ситуаций.\n"
        "Моя цель — обеспечить справедливое рассмотрение дела, выслушать позиции сторон и способствовать поиску решения на основе фактов и аргументов.\n"
        "📑 Нажмите кнопку ниже, чтобы инициировать процедуру разбирательства.\n"
        "После этого мы последовательно зафиксируем стороны (истца и ответчика), изучим обстоятельства дела и перейдём к рассмотрению аргументов."
        "Нажмите кнопку, чтобы начать разбирательство.",
        reply_markup=kb
    )


# ===== НАЧАТЬ РАЗБИРАТЕЛЬСТВО =====
@router.message(F.text == "Начать разбирательство")
async def start_dispute(message: types.Message, state: FSMContext):
    await state.set_state(DisputeState.waiting_topic)
    await message.answer("| 🏛️ СУДЕБНОЕ ЗАСЕДАНИЕ |\n "
                         "Правосудие начинается!")
    await message.answer("Введите тему спора:", reply_markup=ReplyKeyboardRemove())


# ===== ВВОД ТЕМЫ СПОРА =====
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
    await message.answer("Выберите категорию спора:", reply_markup=kb)


# ===== ВЫБОР КАТЕГОРИИ =====
@router.message(DisputeState.waiting_category, F.text.in_(CATEGORIES))
async def select_category(message: types.Message, state: FSMContext):
    category = message.text.strip()
    await state.update_data(category=category)

    await state.set_state(DisputeState.waiting_claim_amount)
    await message.answer("Введите сумму иска (число в валюте):", reply_markup=ReplyKeyboardRemove())


# ===== ВВОД СУММЫ ИСКА =====
@router.message(DisputeState.waiting_claim_amount)
async def input_claim_amount(message: types.Message, state: FSMContext):
    try:
        claim_amount = float(message.text.replace(',', '.'))
    except ValueError:
        await message.answer("Введите корректное число для суммы иска.")
        return

    await state.update_data(claim_amount=claim_amount)

    room_id = str(uuid.uuid4())
    data = await state.get_data()
    rooms[room_id] = {
        "topic": data["topic"],
        "category": data["category"],
        "mode": "упрощенный",
        "claim_amount": data["claim_amount"],
        "plaintiff": message.from_user.id,
        "plaintiff_username": message.from_user.username,
        "defendant": None,
        "defendant_username": None,
        "plaintiff_arguments": [],
        "defendant_arguments": []
    }
    user_roles[message.from_user.id] = "plaintiff"

    await state.update_data(room_id=room_id)
    await state.set_state(DisputeState.waiting_defendant)

    await message.answer("Введите @юзернейм ответчика:", reply_markup=ReplyKeyboardRemove())


# ===== ДОБАВЛЕНИЕ ОТВЕТЧИКА =====
@router.message(DisputeState.waiting_defendant)
async def add_defendant(message: types.Message, state: FSMContext):
    data = await state.get_data()
    room_id = data["room_id"]

    username = message.text.strip("@")
    rooms[room_id]["defendant_username"] = username
    rooms[room_id]["defendant"] = message.from_user.id  # упрощенно

    user_roles[rooms[room_id]["plaintiff"]] = "plaintiff"
    user_roles[rooms[room_id]["defendant"]] = "defendant"

    await state.set_state(DisputeState.plaintiff_arguments)

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Завершить аргументы")]],
        resize_keyboard=True
    )
    await message.answer(
        f"Комната {room_id} создана!\n"
        f"Тема: {rooms[room_id]['topic']}\n"
        f"Категория: {rooms[room_id]['category']}\n"
        f"Сумма иска: {rooms[room_id]['claim_amount']}\n\n"
        f"👉 Сначала истец (@{rooms[room_id]['plaintiff_username']}) вводит аргументы.",
        reply_markup=kb
    )


# ===== АРГУМЕНТЫ ИСТЦА =====
@router.message(DisputeState.plaintiff_arguments)
async def plaintiff_args(message: types.Message, state: FSMContext):
    data = await state.get_data()
    room_id = data["room_id"]

    # Завершение аргументов
    if message.text and message.text.lower().startswith("завершить"):
        await state.set_state(DisputeState.defendant_arguments)
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Завершить аргументы")]],
            resize_keyboard=True
        )
        await message.answer("✅ Истец закончил. Теперь ответчик вводит аргументы:", reply_markup=kb)
        return

    # === Фото ===
    if message.photo:
        photo = message.photo[-1]  # лучшее качество
        file_path = f"evidence_{uuid.uuid4()}.jpg"
        await message.bot.download(photo, destination=file_path)
        rooms[room_id]["plaintiff_arguments"].append(f"[Фото-доказательство: {file_path}]")
        await message.answer("📷 Фото-доказательство добавлено.")
        return

    # === Документы (.txt) ===
    if message.document:
        if message.document.file_name.endswith(".txt"):
            file_path = f"evidence_{uuid.uuid4()}.txt"
            await message.bot.download(message.document, destination=file_path)
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text_content = f.read()
            rooms[room_id]["plaintiff_arguments"].append(f"[Текстовый документ: {text_content}]")
            await message.answer("📑 Текстовый документ добавлен.")
        else:
            await message.answer("❌ Разрешены только документы формата .txt")
        return

    # === Текстовые аргументы ===
    if message.text:
        rooms[room_id]["plaintiff_arguments"].append(message.text)
        await message.answer("Аргумент истца добавлен.")


# ===== АРГУМЕНТЫ ОТВЕТЧИКА =====
@router.message(DisputeState.defendant_arguments)
async def defendant_args(message: types.Message, state: FSMContext):
    data = await state.get_data()
    room_id = data["room_id"]

    # Завершение аргументов
    if message.text and message.text.lower().startswith("завершить"):
        await state.set_state(DisputeState.finished)
        await message.answer("✅ Ответчик закончил. Формируем решение…", reply_markup=ReplyKeyboardRemove())

        case_data = {
            "case_number": room_id,
            "subject": rooms[room_id]["topic"],
            "category": rooms[room_id]["category"],
            "mode": rooms[room_id]["mode"],
            "claim_amount": rooms[room_id]["claim_amount"]
        }
        participants_info = [
            {"role": "plaintiff", "username": rooms[room_id]["plaintiff_username"], "description": "Истец"},
            {"role": "defendant", "username": rooms[room_id]["defendant_username"], "description": "Ответчик"}
        ]
        evidence = (
            [{"type": "argument", "description": arg} for arg in rooms[room_id]["plaintiff_arguments"]]
            + [{"type": "argument", "description": arg} for arg in rooms[room_id]["defendant_arguments"]]
        )

        # 🚀 Решение теперь полностью генерируется сервисом
        decision = await gemini_service.generate_full_decision(case_data, participants_info, evidence)

        pdf_bytes = pdf_generator.generate_verdict_pdf(case_data, decision, participants_info, evidence)

        filepath = f"verdict_{room_id}.pdf"
        with open(filepath, "wb") as f:
            f.write(pdf_bytes)

        from aiogram.types import FSInputFile
        await message.answer_document(FSInputFile(filepath))
        os.remove(filepath)
        return

    # === Фото ===
    if message.photo:
        photo = message.photo[-1]
        file_path = f"evidence_{uuid.uuid4()}.jpg"
        await message.bot.download(photo, destination=file_path)
        rooms[room_id]["defendant_arguments"].append(f"[Фото-доказательство: {file_path}]")
        await message.answer("📷 Фото-доказательство добавлено.")
        return

    # === Документы (.txt) ===
    if message.document:
        if message.document.file_name.endswith(".txt"):
            file_path = f"evidence_{uuid.uuid4()}.txt"
            await message.bot.download(message.document, destination=file_path)
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text_content = f.read()
            rooms[room_id]["defendant_arguments"].append(f"[Текстовый документ: {text_content}]")
            await message.answer("📑 Текстовый документ добавлен.")
        else:
            await message.answer("❌ Разрешены только документы формата .txt")
        return

    # === Текстовые аргументы ===
    if message.text:
        rooms[room_id]["defendant_arguments"].append(message.text)
        await message.answer("Аргумент ответчика добавлен.")

# ===== РЕГИСТРАЦИЯ ХЕНДЛЕРОВ =====
def register_handlers(dp: Dispatcher):
    dp.include_router(router)
