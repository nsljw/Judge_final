from aiogram import types, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

router = Router()

CASES_PER_PAGE = 10

async def build_cases_text(user_cases, user_id, page: int):
    start = page * CASES_PER_PAGE
    end = start + CASES_PER_PAGE
    # берём последние дела
    total = len(user_cases)
    user_cases = list(reversed(user_cases))  # чтобы последние были первыми
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
        buttons.append(types.InlineKeyboardButton(text="⬅️", callback_data=f"cases_page:{page-1}"))
    if page < max_page:
        buttons.append(types.InlineKeyboardButton(text="➡️", callback_data=f"cases_page:{page+1}"))
    builder.row(*buttons)
    return builder.as_markup()

@router.message(F.text == "📂 Мои дела")
async def my_cases(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_cases = await db.get_user_cases(user_id)
    if not user_cases:
        await message.answer("📭 У вас пока нет дел.")
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





import asyncio
import datetime
import os
from datetime import datetime, timedelta

from aiogram import Router, types, F, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated, CallbackQuery
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from telethon.tl.functions.channels import EditAdminRequest, LeaveChannelRequest, \
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


def get_main_menu_keyboard():
    """Возвращает основную клавиатуру с главным меню"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚖ Начать Дело")],
            [KeyboardButton(text="📂 Мои дела")],
            [KeyboardButton(text="📝Черновик")],
            [KeyboardButton(text="ℹ️ Справка")]
        ],
        resize_keyboard=True
    )


def get_keyboard_with_home(buttons):
    """Добавляет кнопку 'В главное меню' к существующим кнопкам"""
    keyboard = buttons + [[KeyboardButton(text="🏠 В главное меню")]]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=True
    )


@router.message(F.text == "🏠 В главное меню")
async def go_to_main_menu(message: types.Message, state: FSMContext):
    """Обработчик кнопки возврата в главное меню"""
    await state.clear()
    kb = get_main_menu_keyboard()
    await message.answer(
        "🏠 Главное меню:\n\n"
        "⚖️ *Начать Дело* - создание нового дела\n"
        "📂 *Мои дела* - просмотр истории дел\n"
        "📝 *Черновик* - продолжение активных дел\n"
        "ℹ️ *Справка* - инструкция по использованию",
        reply_markup=kb,
        parse_mode="Markdown"
    )


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
            defendant_username=event.new_chat_member.user.username or ev