import os

from aiogram import Router, types, F, Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardRemove
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import db
from gemini_servise import gemini_service
from pdf_gen import PDFGenerator

router = Router()
pdf_generator = PDFGenerator()
CASES_PER_PAGE = 10


class DisputeState(StatesGroup):
    waiting_start_mode = State()
    waiting_group_link = State()
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
    "Breach of contract",
    "Plagiarism / Intellectual property",
    "Conflict / Dispute",
    "Debt / Loan",
    "Property division",
    "Debate",
]


def get_main_menu_keyboard():
    """Returns the main menu keyboard for private chats"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚖️ Start Case")],
            [KeyboardButton(text="📂 My Cases")],
            [KeyboardButton(text="📝 Draft")],
            [KeyboardButton(text="ℹ️ Help")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def get_back_to_menu_keyboard():
    """Returns keyboard with back-to-menu button"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔙 Back to Menu")]
        ],
        resize_keyboard=True
    )


async def return_to_main_menu(message: types.Message, state: FSMContext):
    """Return to the main menu"""
    await state.clear()
    kb = get_main_menu_keyboard()
    await message.answer(
        "Main menu:",
        reply_markup=kb
    )


@router.message(F.text == "🔙 Back to Menu")
async def back_to_menu_handler(message: types.Message, state: FSMContext):
    """Handler for the back-to-menu button"""
    await return_to_main_menu(message, state)


@router.message(Command("start"))
async def start_command(message: types.Message, state: FSMContext):
    """Handling /start in groups and private chats"""

    if message.chat.type in ("group", "supergroup"):
        bot_username = (await message.bot.get_me()).username
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="📍 Go to a private chat with the bot",
                url=f"https://t.me/{bot_username}?start=group_{message.chat.id}"
            )]
        ])

        await message.answer(
            "Hello! I am an AI judge for dispute resolution.\n\n"
            "To get started, go to a private chat with me:",
            reply_markup=kb
        )
        return

    # In private chat — full menu
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    # Save user to DB
    await db.save_bot_user(
        message.from_user.id,
        message.from_user.username or message.from_user.full_name
    )

    # If came from a group — save group chat_id
    group_chat_id = None
    if args and args[0].startswith("group_"):
        try:
            group_chat_id = int(args[0].replace("group_", ""))
            await state.update_data(group_chat_id=group_chat_id)
        except:
            pass

    # If this is a defendant invitation
    if args and args[0].startswith("defendant_"):
        case_number = args[0].replace("defendant_", "")

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✅ Participate in the case",
                callback_data=f"accept_defendant:{case_number}"
            )],
            [InlineKeyboardButton(
                text="❌ Reject",
                callback_data=f"reject_defendant:{case_number}"
            )]
        ])
        data = await state.get_data()
        topic = data.get("topic")
        claim_amount = data.get("claim_amount")
        claim_reason = data.get("claim_reason")

        await message.answer(
            f"You have been invited to participate in case #{case_number} as a defendant.\n\n"
            f"Accept or decline participation:\n"
            f"Case: {case_number}\n"
            f"Topic: {topic}\n"
            f"Claim: {claim_reason}"
            f"Claim amount: {claim_amount}",
            reply_markup=kb
        )
        return

    # Regular start in private chat
    kb = get_main_menu_keyboard()
    await message.answer(
        "Welcome! I am an AI judge.\n\n"
        "I will help resolve your dispute objectively.\n"
        "The entire process takes place here in private messages.\n\n"
        "Choose an action:",
        reply_markup=kb
    )


# =============================================================================
# CASE CREATION IN PRIVATE CHAT
# =============================================================================

@router.message(F.text == "⚖️ Start Case")
async def start_dispute_pm(message: types.Message, state: FSMContext):
    """Starting case creation in private chat"""
    if message.chat.type != "private":
        await message.answer("This command works only in private messages with the bot.")
        return

    data = await state.get_data()
    group_chat_id = data.get("group_chat_id")

    # If a group is linked — use it
    if group_chat_id:
        await state.update_data(chat_id=group_chat_id)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🔙 Back to Menu")]
            ],
            resize_keyboard=True
        )
        await state.set_state(DisputeState.waiting_topic)
        await message.answer(
            "Enter the dispute topic:",
            reply_markup=kb
        )
    else:
        # Ask: with group or without
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📱 Work without group")],
                [KeyboardButton(text="👥 Link to group")],
                [KeyboardButton(text="🔙 Back to Menu")]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await state.set_state(DisputeState.waiting_start_mode)
        await message.answer(
            "Choose the mode:\n\n"
            "📱 Work without group - the entire process is only in private chat\n"
            "👥 Link to group - the result will be sent to the group",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )


@router.message(DisputeState.waiting_start_mode)
async def select_start_mode(message: types.Message, state: FSMContext):
    """Choosing mode: with group or without"""
    if message.text == "🔙 Back to Menu":
        await return_to_main_menu(message, state)
        return

    if message.text == "📱 Work without group":
        await state.update_data(chat_id=None)
        await state.set_state(DisputeState.waiting_topic)
        kb = get_back_to_menu_keyboard()
        await message.answer(
            "Enter the dispute topic:",
            reply_markup=kb
        )

    elif message.text == "👥 Link to group":
        kb = get_back_to_menu_keyboard()
        await state.set_state(DisputeState.waiting_group_link)
        await message.answer(
            "Add me to the group as an administrator, then:\n\n"
            "1. In the group, type /start\n"
            "2. Press the button to go to private chat\n"
            "3. Continue creating the case here\n\n"
            "Or send /start again after adding me to the group.",
            reply_markup=kb
        )
        await state.clear()
    else:
        await message.answer("Please choose one of the suggested options.")


# =============================================================================
# COLLECTING CASE INFORMATION (in private chat)
# =============================================================================

@router.message(DisputeState.waiting_topic)
async def input_topic(message: types.Message, state: FSMContext):
    """Entering dispute topic"""
    if message.text == "🔙 Back to Menu":
        await return_to_main_menu(message, state)
        return

    if not message.text:
        await message.answer("Please enter the dispute topic as text.")
        return

    topic = message.text.strip()
    await state.update_data(topic=topic)

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=cat)] for cat in CATEGORIES] +
                 [[KeyboardButton(text="🔙 Back to Menu")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await state.set_state(DisputeState.waiting_category)
    await message.answer("Choose dispute category:", reply_markup=kb)


@router.message(DisputeState.waiting_category, F.text.in_(CATEGORIES))
async def select_category(message: types.Message, state: FSMContext):
    """Category selection"""
    category = message.text.strip()
    await state.update_data(category=category)

    await state.set_state(DisputeState.waiting_claim_reason)
    kb = get_back_to_menu_keyboard()
    await message.answer(
        "<b>Describe your claim against the defendant</b>\n\n"
        "Provide detailed explanation of the dispute and your demands:",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )


@router.message(DisputeState.waiting_category)
async def invalid_category(message: types.Message):
    """Invalid category"""
    if message.text == "🔙 Back to Menu":
        return

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=cat)] for cat in CATEGORIES] +
                 [[KeyboardButton(text="🔙 Back to Menu")]],
        resize_keyboard=True
    )
    await message.answer("Please select a category from the list:", reply_markup=kb)


@router.message(DisputeState.waiting_claim_reason)
async def input_claim_reason(message: types.Message, state: FSMContext):
    """Entering claim description"""
    if message.text == "🔙 Back to Menu":
        await return_to_main_menu(message, state)
        return

    if not message.text:
        await message.answer("Please describe your claim.")
        return

    claim_reason = message.text.strip()
    await state.update_data(claim_reason=claim_reason)

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Yes"), KeyboardButton(text="No")],
            [KeyboardButton(text="🔙 Back to Menu")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await state.set_state(DisputeState.waiting_claim_amount)
    await message.answer("Do you want to specify the claim amount?(ETF)", reply_markup=kb)


@router.message(DisputeState.waiting_claim_amount)
async def input_claim_amount(message: types.Message, state: FSMContext):
    """Entering claim amount"""
    if message.text == "🔙 Back to Menu":
        await return_to_main_menu(message, state)
        return

    user_input = message.text.strip().lower()

    if user_input == "yes":
        kb = get_back_to_menu_keyboard()
        await message.answer(
            "Enter the claim amount in ETF:",
            reply_markup=kb
        )
        return

    elif user_input == "no":
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
            await message.answer("Enter a valid amount or choose 'No'.")


async def proceed_to_message_history(message: types.Message, state: FSMContext):
    """Proceed to message history review"""
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Add chat history")],
            [KeyboardButton(text="⏩ Skip")],
            [KeyboardButton(text="🔙 Back to Menu")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await state.set_state(DisputeState.waiting_message_history)
    await message.answer(
        "<b>Would you like to add chat history as evidence?</b>\n\n"
        "You can forward messages from your dispute here.",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )


@router.message(DisputeState.waiting_message_history)
async def handle_message_history_choice(message: types.Message, state: FSMContext):
    """Handling chat history choice"""
    if message.text == "🔙 Back to Menu":
        await return_to_main_menu(message, state)
        return

    if message.text == "➕ Add chat history":
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⏸ ️Finish adding")],
                [KeyboardButton(text="🔙 Back to Menu")]
            ],
            resize_keyboard=True
        )
        await state.set_state(DisputeState.waiting_forwarded_messages)
        await message.answer(
            "<b>Forward messages from the conversation here</b>\n\n"
            "When finished, press «⏸ ️Finish adding».",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )

    elif message.text == "⏩ Skip":
        await proceed_to_defendant_selection(message, state)

    else:
        await message.answer("Please choose one of the suggested options.")


@router.message(DisputeState.waiting_forwarded_messages)
async def handle_forwarded_messages(message: types.Message, state: FSMContext):
    """Handling forwarded messages"""
    if message.text == "🔙 Back to Menu":
        await return_to_main_menu(message, state)
        return

    if message.text == "⏸ ️Finish adding":
        data = await state.get_data()
        forwarded_messages = data.get("forwarded_messages", [])

        if forwarded_messages:
            await message.answer(f"Added {len(forwarded_messages)} messages as evidence.")

        await proceed_to_defendant_selection(message, state)
        return

    if message.forward_from or message.forward_from_chat:
        data = await state.get_data()
        forwarded_messages = data.get("forwarded_messages", [])
        added_message_ids = data.get("added_message_ids", set())

        # Ignore already added messages
        if message.message_id in added_message_ids:
            return

        # Add message to list
        forwarded_messages.append({
            "from_user": message.forward_from.username if message.forward_from else
            message.forward_from_chat.title if message.forward_from_chat else "Unknown",
            "text": message.text or message.caption or "(media file)",
            "date": message.forward_date.isoformat() if message.forward_date else None
        })

        added_message_ids.add(message.message_id)

        await state.update_data(forwarded_messages=forwarded_messages, added_message_ids=added_message_ids)

        if len(forwarded_messages) % 5 == 0:
            await message.answer(f"Added {len(forwarded_messages)} messages.")

    else:
        await message.answer("This is not a forwarded message. Use the forward function.")


# =============================================================================
# INVITING DEFENDANT
# =============================================================================

async def proceed_to_defendant_selection(message: types.Message, state: FSMContext):
    """Proceed to defendant selection"""
    data = await state.get_data()
    chat_id = data.get("chat_id")

    case_number = await db.create_case(
        topic=data["topic"],
        category=data["category"],
        claim_reason=data["claim_reason"],
        claim_amount=data.get("claim_amount"),
        mode="full",
        plaintiff_id=message.from_user.id,
        plaintiff_username=message.from_user.username or message.from_user.full_name,
        chat_id=chat_id,
        version="pm"
    )

    await state.update_data(case_number=case_number)
    await db.update_case_stage(case_number, "waiting_defendant")

    # Save forwarded chat as evidence
    forwarded_messages = data.get("forwarded_messages", [])
    if forwarded_messages:
        history_text = "Chat history:\n\n"
        for msg in forwarded_messages:
            history_text += f"[{msg.get('date', 'no date')}] {msg['from_user']}: {msg['text']}\n\n"

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
            [KeyboardButton(text="🔙 Back to Menu")]
        ],
        resize_keyboard=True
    )
    raw_amount = data.get('claim_amount')

    if raw_amount is None or raw_amount == 'not specified':
        claim_text = "not specified"
    else:
        claim_text = f"{float(raw_amount):,.8f}".rstrip('0').rstrip('.') + " ETF"
        if claim_text.endswith('.'):
            claim_text = claim_text[:-1] + " ETF"

    await state.set_state(DisputeState.waiting_defendant_username)
    await message.answer(
        f"📄 <b>Case #{case_number} created!</b>\n\n"
        f"📝 Topic: {data['topic']}\n"
        f"📂 Category: {data['category']}\n"
        f"💰 Claim amount: {claim_text}\n\n"
        f"👤 Enter defendant's username (e.g., @username or username):",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )


@router.message(DisputeState.waiting_defendant_username)
async def input_defendant_username(message: types.Message, state: FSMContext):
    """Entering defendant's username with DB check"""
    if message.text == "🔙 Back to Menu":
        await return_to_main_menu(message, state)
        return

    if not message.text:
        await message.answer("👤 Enter defendant's username.")
        return

    username = message.text.strip()
    if username.startswith('@'):
        username = username[1:]

    data = await state.get_data()
    case_number = data.get("case_number")

    try:
        defendant_user = await db.get_user_by_username(username)

        bot_username = (await message.bot.get_me()).username
        invite_link = f"https://t.me/{bot_username}?start=defendant_{case_number}"

        if defendant_user:
            defendant_id = defendant_user['user_id']
            await state.update_data(defendant_username=username)

            kb_defendant = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="✅ Participate in the case",
                    callback_data=f"accept_defendant:{case_number}"
                )],
                [InlineKeyboardButton(
                    text="❌ Reject",
                    callback_data=f"reject_defendant:{case_number}"
                )]
            ])

            try:
                await message.bot.send_message(
                    defendant_id,
                    f"<b>Invitation to case</b>\n\n"
                    f"Case #{case_number}\n"
                    f"Topic: {data['topic']}\n"
                    f"Category: {data['category']}\n"
                    f"Claim amount: {data.get('claim_amount', 'not specified')} ETF\n\n"
                    f"Plaintiff: @{message.from_user.username or message.from_user.full_name}\n\n"
                    f"You have been invited as a defendant.\n"
                    f"Accept or decline participation:",
                    reply_markup=kb_defendant,
                    parse_mode=ParseMode.HTML
                )

                await message.answer(
                    f"<b>Invitation sent!</b>\n\n"
                    f"Defendant: @{username}\n"
                    f"Invitation delivered directly.\n\n"
                    f"Waiting for defendant's response...",
                    parse_mode=ParseMode.HTML
                )

            except Exception:
                kb_copy = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="Copy link",
                        url=invite_link
                    )]
                ])

                await message.answer(
                    f"Could not send invitation to @{username} directly.\n\n"
                    f"The user may have blocked the bot or not started a chat.\n\n"
                    f"Send this link to the defendant manually:\n\n"
                    f"<code>{invite_link}</code>",
                    reply_markup=kb_copy,
                    parse_mode=ParseMode.HTML
                )
        else:
            await state.update_data(defendant_username=username)

            kb_copy = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="Copy link",
                    url=invite_link
                )]
            ])

            await message.answer(
                f"<b>User @{username} not found in the database</b>\n\n"
                f"This means they haven't interacted with the bot yet.\n\n"
                f"Send this link to @{username}:\n\n"
                f"<code>{invite_link}</code>\n\n"
                f"You will be notified when the defendant joins.",
                reply_markup=kb_copy,
                parse_mode=ParseMode.HTML
            )

        # Notify group if linked
        chat_id = data.get("chat_id")
        if chat_id:
            try:
                await message.bot.send_message(
                    chat_id,
                    f"Case #{case_number} created\n"
                    f"Topic: {data['topic']}\n"
                    f"Plaintiff: @{message.from_user.username or message.from_user.full_name}\n"
                    f"Defendant: @{username}\n\n"
                    f"The process takes place in private messages with the bot."
                )
            except:
                pass

        await state.set_state(DisputeState.waiting_defendant_confirmation)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📂 My Cases")],
                [KeyboardButton(text="🔙 Back to Menu")]
            ],
            resize_keyboard=True
        )
        await message.answer(
            "Waiting for defendant's confirmation...\n\n"
            "You can continue after the defendant accepts.",
            reply_markup=kb
        )

    except Exception as e:
        await message.answer(f"Error: {e}\nTry again.")


# =============================================================================
# DEFENDANT CONFIRMATION
# =============================================================================

@router.callback_query(F.data.startswith("accept_defendant:"))
async def accept_defendant(callback: CallbackQuery, state: FSMContext):
    """Defendant accepting participation"""
    case_number = callback.data.split(":")[1]

    case = await db.get_case_by_number(case_number)
    if not case:
        await callback.answer("Case not found", show_alert=True)
        return

    if callback.from_user.id == case["plaintiff_id"]:
        await callback.answer("You cannot be a defendant in your own case", show_alert=True)
        return

    await db.set_defendant(
        case_number,
        callback.from_user.id,
        callback.from_user.username or callback.from_user.full_name
    )

    await callback.answer("You have been accepted as defendant!")

    # Notify plaintiff
    try:
        await callback.bot.send_message(
            case["plaintiff_id"],
            f"@{callback.from_user.username or callback.from_user.full_name} has accepted participation in case #{case_number}!\n\n"
            f"Starting argumentation phase."
        )
    except:
        pass

    # Notify group
    if case.get("chat_id"):
        try:
            await callback.bot.send_message(
                case["chat_id"],
                f"Defendant @{callback.from_user.username or callback.from_user.full_name} has joined case #{case_number}"
            )
        except:
            pass

    await db.update_case_stage(case_number, "plaintiff_arguments")

    await callback.message.answer(
        f"Case #{case_number}\n"
        f"Topic: {case['topic']}\n\n"
        f"Currently at plaintiff's argumentation stage.\n"
        f"You will be notified when it's your turn."
    )

    kb_plaintiff = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Finish arguments")],
            [KeyboardButton(text="⛔ Pause case")],
            [KeyboardButton(text="🔙 Back to Menu")]
        ],
        resize_keyboard=True
    )

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
            "<b>Present your arguments</b>\n\n"
            "You can send:\n"
            "• Text messages\n"
            "• Photos and videos\n"
            "• Documents\n\n"
            "When finished, press «✅ Finish arguments».",
            reply_markup=kb_plaintiff,
            parse_mode=ParseMode.HTML
        )
    except:
        pass


@router.callback_query(F.data.startswith("reject_defendant:"))
async def reject_defendant(callback: CallbackQuery):
    """Defendant rejecting participation"""
    case_number = callback.data.split(":")[1]

    case = await db.get_case_by_number(case_number)
    if not case:
        await callback.answer("Case not found", show_alert=True)
        return

    await callback.answer("You have declined participation")

    try:
        await callback.bot.send_message(
            case["plaintiff_id"],
            f"@{callback.from_user.username or callback.from_user.full_name} has declined participation in case #{case_number}.\n\n"
            f"You can invite another defendant."
        )
    except:
        pass

    kb = get_main_menu_keyboard()
    await callback.message.edit_text(
        f"You have declined participation in case #{case_number}."
    )


# =============================================================================
# PLAINTIFF ARGUMENTATION
# =============================================================================

@router.message(DisputeState.plaintiff_arguments)
async def plaintiff_arguments_handler(message: types.Message, state: FSMContext):
    """Handling plaintiff's arguments"""
    if message.text == "🔙 Back to Menu":
        await return_to_main_menu(message, state)
        return

    if message.text == "⛔ Pause case":
        await pause_case_handler(message, state)
        return

    if message.text == "✅ Finish arguments":
        data = await state.get_data()
        case_number = data.get("case_number")

        await db.update_case_stage(case_number, "defendant_arguments")

        case = await db.get_case_by_number(case_number)
        defendant_id = case.get("defendant_id")

        if not defendant_id:
            await message.answer("Defendant has not yet accepted participation.")
            return

        kb_defendant = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Finish arguments")],
                [KeyboardButton(text="⛔ Pause case")],
                [KeyboardButton(text="🔙 Back to Menu")]
            ],
            resize_keyboard=True
        )

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
                f"<b>Case #{case_number}</b>\n\n"
                f"It's your turn to present arguments.\n\n"
                f"You can send:\n"
                f"• Text messages\n"
                f"• Photos and videos\n"
                f"• Documents\n\n"
                f"When finished, press «✅ Finish arguments».",
                reply_markup=kb_defendant,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            await message.answer(f"Could not notify defendant: {e}")

        if case.get("chat_id"):
            try:
                await message.bot.send_message(
                    case["chat_id"],
                    f"Case #{case_number}\n"
                    f"Plaintiff has finished presenting arguments.\n"
                    f"Waiting for defendant's arguments."
                )
            except:
                pass

        kb = get_main_menu_keyboard()
        await message.answer(
            "Your arguments have been saved!\n\n"
            "Waiting for defendant's arguments...",
            reply_markup=kb
        )
        await state.clear()
        return

    # Save argument
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
        await message.answer("Argument added. Continue or press «✅ Finish arguments».")

    elif message.photo:
        file_id = message.photo[-1].file_id
        await db.add_evidence(
            case_number,
            message.from_user.id,
            "plaintiff",
            "photo",
            message.caption or "Photo",
            file_id
        )
        await message.answer("Photo added as evidence.")

    elif message.document:
        file_id = message.document.file_id
        await db.add_evidence(
            case_number,
            message.from_user.id,
            "plaintiff",
            "document",
            message.caption or "Document",
            file_id
        )
        await message.answer("Document added as evidence.")

    elif message.video:
        file_id = message.video.file_id
        await db.add_evidence(
            case_number,
            message.from_user.id,
            "plaintiff",
            "video",
            message.caption or "Video",
            file_id
        )
        await message.answer("Video added as evidence.")


# =============================================================================
# DEFENDANT ARGUMENTATION
# =============================================================================

@router.message(DisputeState.defendant_arguments)
async def defendant_arguments_handler(message: types.Message, state: FSMContext):
    """Handling defendant's arguments"""
    if message.text == "🔙 Back to Menu":
        await return_to_main_menu(message, state)
        return

    if message.text == "⛔ Pause case":
        await pause_case_handler(message, state)
        return

    if message.text == "✅ Finish arguments":
        data = await state.get_data()
        case_number = data.get("case_number")

        await check_and_ask_ai_questions(message, state, case_number, "plaintiff")
        return

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
        await message.answer("Argument added. Continue or press «✅ Finish arguments».")

    elif message.photo:
        file_id = message.photo[-1].file_id
        await db.add_evidence(
            case_number,
            message.from_user.id,
            "defendant",
            "photo",
            message.caption or "Photo",
            file_id
        )
        await message.answer("Photo added as evidence.")

    elif message.document:
        file_id = message.document.file_id
        await db.add_evidence(
            case_number,
            message.from_user.id,
            "defendant",
            "document",
            message.caption or "Document",
            file_id
        )
        await message.answer("Document added as evidence.")

    elif message.video:
        file_id = message.video.file_id
        await db.add_evidence(
            case_number,
            message.from_user.id,
            "defendant",
            "video",
            message.caption or "Video",
            file_id
        )
        await message.answer("Video added as evidence.")


# =============================================================================
# AI QUESTIONS
# =============================================================================

async def check_and_ask_ai_questions(message: types.Message, state: FSMContext, case_number: str, role: str):
    """Check and generate AI clarifying questions"""
    data = await state.get_data()
    ai_round = data.get(f"ai_round_{role}", 0)

    if ai_round >= 3:  # Max 2 rounds of questions
        if role == "defendant":
            await generate_final_verdict(message, state, case_number)
        else:
            await check_and_ask_ai_questions(message, state, case_number, "defendant")
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
        case, participants_info, evidence_info, role, ai_round + 1, message.bot
    )

    if not ai_questions or len(ai_questions) == 0:
        if role == "defendant":
            await generate_final_verdict(message, state, case_number)
        else:
            await check_and_ask_ai_questions(message, state, case_number, "defendant")
        return

    for question in ai_questions:
        await db.save_ai_question(case_number, question, role, ai_round + 1)

    case = await db.get_case_by_number(case_number)
    target_user_id = case["plaintiff_id"] if role == "plaintiff" else case["defendant_id"]

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
            [KeyboardButton(text="Skip question")],
            [KeyboardButton(text="🔙 Back to Menu")]
        ],
        resize_keyboard=True
    )

    role_text = "Plaintiff" if role == "plaintiff" else "Defendant"

    try:
        await message.bot.send_message(
            target_user_id,
            f"<b>AI judge is asking clarifying questions</b>\n\n"
            f"<b>{role_text}</b>, please answer the question:\n\n"
            f"? {ai_questions[0]}\n\n"
            f"Question 1 of {len(ai_questions)}",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )
    except:
        pass

    if case.get("chat_id"):
        try:
            await message.bot.send_message(
                case["chat_id"],
                f"Case #{case_number}\n"
                f"AI judge is asking additional questions to the {role_text.lower()}."
            )
        except:
            pass


@router.message(DisputeState.ai_asking_questions)
async def handle_ai_question_response(message: types.Message, state: FSMContext):
    """Handling AI question responses"""
    if message.text == "🔙 Back to Menu":
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
        await message.answer("Error: questions not found.")
        await state.clear()
        return

    if message.text == "Skip question":
        skip_count += 1
        await state.update_data(skip_count=skip_count)

        if skip_count >= 3:
            await message.answer("Too many skips. Moving on.")
            await finish_ai_questions(message, state, case_number, answering_role)
            return
    else:
        question_text = ai_questions[current_index]
        response_text = f"AI Question: {question_text}\nAnswer: {message.text}"

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

    next_index = current_index + 1
    await state.update_data(current_question_index=next_index, skip_count=skip_count)

    if next_index < len(ai_questions):
        role_text = "Plaintiff" if answering_role == "plaintiff" else "Defendant"
        await message.answer(
            f"Answer accepted.\n\n"
            f"<b>{role_text}</b>, next question:\n\n"
            f"? {ai_questions[next_index]}\n\n"
            f"Question {next_index + 1} of {len(ai_questions)}",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer("All questions answered!")
        await finish_ai_questions(message, state, case_number, answering_role)


async def finish_ai_questions(message: types.Message, state: FSMContext, case_number: str, answering_role: str):
    """Finish AI questions round"""
    data = await state.get_data()
    ai_round = data.get("ai_round", 1)

    case = await db.get_case_by_number(case_number)
    plaintiff_state = FSMContext(
        storage=state.storage,
        key=StorageKey(bot_id=(await message.bot.get_me()).id, chat_id=case["plaintiff_id"],
                       user_id=case["plaintiff_id"])
    )
    defendant_state = FSMContext(
        storage=state.storage,
        key=StorageKey(bot_id=(await message.bot.get_me()).id, chat_id=case["defendant_id"],
                       user_id=case["defendant_id"])
    )

    if answering_role == "plaintiff":
        await plaintiff_state.update_data(ai_round_plaintiff=ai_round)
        await check_and_ask_ai_questions(message, defendant_state, case_number, "defendant")
    else:
        await defendant_state.update_data(ai_round_defendant=ai_round)
        await generate_final_verdict(message, state, case_number)

    await state.clear()


# =============================================================================
# FINAL VERDICT
# =============================================================================

async def generate_final_verdict(message: types.Message, state: FSMContext, case_number: str):
    """Generate final verdict"""
    await db.update_case_stage(case_number, "final_decision")
    await db.update_case_status(case_number, "finished")

    case = await db.get_case_by_number(case_number)
    if not case:
        await message.answer("Error: case not found.")
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

    plaintiff_id = case["plaintiff_id"]
    defendant_id = case.get("defendant_id")

    try:
        await message.bot.send_message(
            plaintiff_id,
            "<b>⚖️ AI judge is analyzing the case and rendering a decision...</b>\n\n"
            "⏳ Please wait, this may take a moment...",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.HTML
        )
    except:
        pass

    if defendant_id:
        try:
            await message.bot.send_message(
                defendant_id,
                "<b>⚖️ AI judge is analyzing the case and rendering a decision...</b>\n\n"
                "⏳ Please wait, this may take a moment...",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode=ParseMode.HTML
            )
        except:
            pass

    try:
        decision = await gemini_service.generate_full_decision(
            case, participants_info, evidence_info, bot=message.bot
        )
        if not decision:
            decision = {
                "decision": "Decision was not generated.",
                "winner": "defendant",
                "verdict": {"claim_satisfied": False, "amount_awarded": 0},
                "reasoning": ""
            }
    except Exception as e:
        print(f"Error generating decision: {e}")
        decision = {
            "decision": "Technical error while rendering decision.",
            "winner": "defendant",
            "verdict": {"claim_satisfied": False, "amount_awarded": 0},
            "reasoning": ""
        }

    try:
        pdf_bytes = pdf_generator.generate_verdict_pdf(case, decision, participants_info, evidence_info)
        filepath = f"verdict_{case_number}.pdf"
        with open(filepath, "wb") as f:
            f.write(pdf_bytes)
        await db.save_decision(case_number=case_number, file_path=filepath)
    except Exception as e:
        print(f"PDF generation error: {e}")
        filepath = None

    kb = get_main_menu_keyboard()

    try:
        await message.bot.send_message(
            plaintiff_id,
            "✅ <b>Case closed!</b>\n\n"
            "📄 Here is the final verdict:",
            parse_mode=ParseMode.HTML
        )
        if filepath:
            await message.bot.send_document(
                plaintiff_id,
                FSInputFile(filepath),
                reply_markup=kb
            )
        else:
            await message.bot.send_message(
                plaintiff_id,
                "⚠️ Error generating PDF document.",
                reply_markup=kb
            )
    except Exception as e:
        print(f"Failed to send to plaintiff: {e}")

    if defendant_id:
        try:
            await message.bot.send_message(
                defendant_id,
                "✅ <b>Case closed!</b>\n\n"
                "📄 Here is the final verdict:",
                parse_mode=ParseMode.HTML
            )
            if filepath:
                await message.bot.send_document(
                    defendant_id,
                    FSInputFile(filepath),
                    reply_markup=kb
                )
            else:
                await message.bot.send_message(
                    defendant_id,
                    "⚠️ Error generating PDF document.",
                    reply_markup=kb
                )
        except Exception as e:
            print(f"Failed to send to defendant: {e}")

    # Send to group
    if case.get("chat_id"):
        try:
            winner_code = decision.get("winner", "draw")
            plaintiff_username = case['plaintiff_username']
            defendant_username = case.get('defendant_username', 'unknown')

            if winner_code == "plaintiff":
                winner_text = f"@{plaintiff_username} (Plaintiff)"
                winner_emoji = "🏆"
            elif winner_code == "defendant":
                winner_text = f"@{defendant_username} (Defendant)"
                winner_emoji = "🏆"
            else:
                winner_text = "Compromise decision (both sides partially right)"
                winner_emoji = "⚖️"

            verdict = decision.get("verdict", {})
            amount_awarded = verdict.get("amount_awarded", 0)
            claim_amount = case.get("claim_amount", 0)

            verdict_details = ""
            if amount_awarded > 0:
                if claim_amount and amount_awarded < claim_amount:
                    verdict_details = f"\n💰 Awarded: {amount_awarded} ETF (partial satisfaction out of {claim_amount} ETF)"
                else:
                    verdict_details = f"\n💰 Awarded: {amount_awarded} ETF"

            group_text = (
                f"<b>⚖️ VERDICT FOR CASE #{case_number}</b>\n\n"
                f"📝 Topic: {case['topic']}\n"
                f"👤 Plaintiff: @{plaintiff_username}\n"
                f"👤 Defendant: @{defendant_username}\n"
                f"{verdict_details}\n\n"
                f"{winner_emoji} <b>Decision in favor of:</b>\n{winner_text}\n\n"
                f"📄 Full document sent to participants in private messages."
            )

            await message.bot.send_message(
                case["chat_id"],
                group_text,
                parse_mode=ParseMode.HTML
            )

            if filepath:
                await message.bot.send_document(
                    case["chat_id"],
                    FSInputFile(filepath),
                    caption=f"📄 Full verdict for case #{case_number}"
                )
        except Exception as e:
            print(f"Error sending to group: {e}")

    if filepath:
        try:
            os.remove(filepath)
        except:
            pass

    await state.clear()

# =============================================================================
# HELP & AUXILIARY COMMANDS
# =============================================================================

@router.message(F.text == "ℹ️ Help")
async def help_command(message: types.Message):
    """Help command"""
    kb = get_back_to_menu_keyboard()
    await message.answer(
        "<b>How to use the AI Judge:</b>\n\n"
        "<b>Process:</b>\n"
        "1. Press «⚖️ Start Case»\n"
        "2. Choose: with group or without\n"
        "3. Enter dispute details\n"
        "4. Invite defendant by username\n"
        "5. Present arguments\n"
        "6. Answer AI judge's questions\n"
        "7. Receive verdict\n\n"
        "<b>Features:</b>\n"
        "• Entire process in private messages\n"
        "• If group selected — only the verdict is sent there\n"
        "• Can work completely without a group\n\n"
        "<b>Evidence:</b>\n"
        "• Text messages\n"
        "• Forwarded messages\n"
        "• Photos, videos, documents",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )


@router.message(F.text == "📂 My Cases")
async def my_cases(message: types.Message, state: FSMContext):
    """User's cases list"""
    user_id = message.from_user.id
    user_cases = await db.get_user_cases(user_id)

    if not user_cases:
        kb = get_back_to_menu_keyboard()
        await message.answer("You have no cases yet.", reply_markup=kb)
        return

    page = 0
    text, total = await build_cases_text(user_cases, user_id, page)
    keyboard = build_pagination_keyboard(page, total)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def build_cases_text(user_cases, user_id, page: int):
    """Build the text for the list of cases"""
    start = page * CASES_PER_PAGE
    end = start + CASES_PER_PAGE
    total = len(user_cases)
    user_cases = list(reversed(user_cases))
    page_cases = user_cases[start:end]

    text = "Your cases:\n\n"
    for case in page_cases:
        role = "Plaintiff" if case["plaintiff_id"] == user_id else "Defendant"
        status = "In progress" if case["status"] != "finished" else "Completed"
        claim_text = f" ({case['claim_amount']} ETF)" if case.get("claim_amount") else ""
        text += (
            f"<b>Case {case['case_number']}</b>\n"
            f"Topic: {case['topic']}{claim_text}\n"
            f"Category: {case['category']}\n"
            f"Your role: {role}\n"
            f"Status: {status}\n\n"
        )
    text += f"Total cases: {total}\n"
    return text, total


def build_pagination_keyboard(page: int, total: int):
    """Pagination keyboard"""
    builder = InlineKeyboardBuilder()
    max_page = (total - 1) // CASES_PER_PAGE
    buttons = []

    if page > 0:
        buttons.append(types.InlineKeyboardButton(text="Previous", callback_data=f"cases_page:{page - 1}"))
    if page < max_page:
        buttons.append(types.InlineKeyboardButton(text="Next", callback_data=f"cases_page:{page + 1}"))

    if buttons:
        builder.row(*buttons)
    builder.row(types.InlineKeyboardButton(text="🔙 Back to Menu", callback_data="back_to_menu"))

    return builder.as_markup()


@router.callback_query(F.data.startswith("cases_page:"))
async def paginate_cases(callback: CallbackQuery):
    """Cases pagination"""
    page = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    user_cases = await db.get_user_cases(user_id)

    text, total = await build_cases_text(user_cases, user_id, page)
    keyboard = build_pagination_keyboard(page, total)

    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu_callback(callback: CallbackQuery, state: FSMContext):
    """Return to menu via callback"""
    await state.clear()
    kb = get_main_menu_keyboard()
    await callback.message.edit_text("Main menu:")
    await callback.bot.send_message(
        chat_id=callback.message.chat.id,
        text="Choose an action:",
        reply_markup=kb
    )
    await callback.answer()


@router.message(F.text == "📝 Draft")
async def draft_cases(message: types.Message, state: FSMContext):
    """Active (in-progress) cases"""
    user_id = message.from_user.id
    active_cases = await db.get_user_active_cases(user_id)

    if not active_cases:
        kb = get_back_to_menu_keyboard()
        await message.answer("You have no active cases.", reply_markup=kb)
        return

    builder = InlineKeyboardBuilder()
    for case in active_cases:
        truncated_topic = case['topic'][:30] + ('...' if len(case['topic']) > 30 else '')
        builder.row(InlineKeyboardButton(
            text=f"{case['case_number']} - {truncated_topic}",
            callback_data=f"resume_case:{case['case_number']}"
        ))
    builder.row(InlineKeyboardButton(text="🔙 Back to Menu", callback_data="back_to_menu"))

    await message.answer(
        "Your active cases. Choose one to continue:",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("resume_case:"))
async def resume_case(callback: CallbackQuery, state: FSMContext):
    """Resume a case"""
    case_number = callback.data.split(":")[1]
    case = await db.get_case_by_number(case_number)

    if not case:
        await callback.answer("Case not found", show_alert=True)
        return

    user_id = callback.from_user.id
    stage = case.get("stage", "")

    await state.update_data(case_number=case_number)

    # Restore state depending on the current stage
    if stage == "plaintiff_arguments":
        await state.set_state(DisputeState.plaintiff_arguments)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Finish arguments")],
                [KeyboardButton(text="⛔ Pause case")],
                [KeyboardButton(text="🔙 Back to Menu")]
            ],
            resize_keyboard=True
        )
        await callback.message.answer(
            f"Continuing case #{case_number}\n\n"
            f"Continue presenting plaintiff's arguments.",
            reply_markup=kb
        )

    elif stage == "defendant_arguments":
        await state.set_state(DisputeState.defendant_arguments)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Finish arguments")],
                [KeyboardButton(text="⛔ Pause case")],
                [KeyboardButton(text="🔙 Back to Menu")]
            ],
            resize_keyboard=True
        )
        await callback.message.answer(
            f"Continuing case #{case_number}\n\n"
            f"Continue presenting defendant's arguments.",
            reply_markup=kb
        )

    else:
        await callback.message.answer(
            f"Case #{case_number} is at stage: {stage}\n"
            f"Please wait for further notifications."
        )

    await callback.answer()


# =============================================================================
# MEDIA HANDLING (during argumentation stages)
# =============================================================================

@router.message(F.content_type.in_({"photo", "video", "document", "audio"}))
async def media_handler(message: types.Message, state: FSMContext):
    """Handle media files"""
    current_state = await state.get_state()

    if current_state not in (DisputeState.plaintiff_arguments.state, DisputeState.defendant_arguments.state):
        return

    data = await state.get_data()
    case_number = data.get("case_number")

    if not case_number:
        await message.answer("Error: case not found.")
        return

    # Determine sender's role
    case = await db.get_case_by_number(case_number)
    if message.from_user.id == case["plaintiff_id"]:
        role = "plaintiff"
    elif message.from_user.id == case.get("defendant_id"):
        role = "defendant"
    else:
        return

    # Save media
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
            message.caption or f"File ({content_type})",
            file_info
        )
        await message.answer(f"{content_type.capitalize()} added as evidence.")


# =============================================================================
# PAUSE HANDLING (optional)
# =============================================================================

@router.message(F.text == "⛔ Pause case")
async def pause_case_handler(message: types.Message, state: FSMContext):
    """Pause the case"""
    data = await state.get_data()
    case_number = data.get("case_number")

    if not case_number:
        await message.answer("No active case to pause.")
        return

    case = await db.get_case_by_number(case_number)

    # Only plaintiff can pause the case
    if message.from_user.id != case["plaintiff_id"]:
        await message.answer("Only the plaintiff can pause the case.")
        return

    await db.update_case_status(case_number, status="paused")
    await state.set_state(DisputeState.case_paused)

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⏩ Resume case")],
            [KeyboardButton(text="🔙 Back to Menu")]
        ],
        resize_keyboard=True
    )

    await message.answer(
        f"<b>Case #{case_number} has been paused</b>\n\n"
        f"To resume, press «⏩ Resume case»",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

    # Notify defendant
    if case.get("defendant_id"):
        try:
            await message.bot.send_message(
                case["defendant_id"],
                f"Case #{case_number} has been paused by the plaintiff.\n"
                f"Please wait for resumption."
            )
        except:
            pass

    # Notify group
    if case.get("chat_id"):
        try:
            await message.bot.send_message(
                case["chat_id"],
                f"Case #{case_number} has been paused."
            )
        except:
            pass


@router.message(F.text == "⏩ Resume case")
async def continue_case_handler(message: types.Message, state: FSMContext):
    """⏩ Resume case after pause"""
    data = await state.get_data()
    case_number = data.get("case_number")

    if not case_number:
        await message.answer("No case to resume.")
        return

    case = await db.get_case_by_number(case_number)

    if case.get("status") != "paused":
        await message.answer("The case is not paused.")
        return

    await db.update_case_status(case_number, status="active")

    stage = case.get("stage", "")

    # Restore state
    if stage == "plaintiff_arguments":
        await state.set_state(DisputeState.plaintiff_arguments)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Finish arguments")],
                [KeyboardButton(text="⛔ Pause case")],
                [KeyboardButton(text="🔙 Back to Menu")]
            ],
            resize_keyboard=True
        )
        await message.answer(
            f"Case #{case_number} resumed!\n\n"
            f"Continue presenting arguments.",
            reply_markup=kb
        )

    elif stage == "defendant_arguments":
        await state.set_state(DisputeState.defendant_arguments)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Finish arguments")],
                [KeyboardButton(text="🔙 Back to Menu")]
            ],
            resize_keyboard=True
        )
        await message.answer(
            f"Case #{case_number} resumed!\n\n"
            f"Continue presenting arguments.",
            reply_markup=kb
        )

    # Notify group
    if case.get("chat_id"):
        try:
            await message.bot.send_message(
                case["chat_id"],
                f"Case #{case_number} has been resumed."
            )
        except:
            pass


@router.message(DisputeState.case_paused)
async def handle_paused_messages(message: types.Message):
    """Block messages while case is paused"""
    if message.text not in ["⏩ Resume case", "🔙 Back to Menu"]:
        await message.answer("Case is paused. Press «⏩ Resume case» to continue.")


# =============================================================================
# UNKNOWN MESSAGE HANDLER
# =============================================================================

@router.message()
async def unknown_message_handler(message: types.Message, state: FSMContext):
    """Handle unknown messages"""
    # Ignore service messages
    if message.new_chat_members or message.left_chat_member or \
            message.migrate_from_chat_id or message.migrate_to_chat_id or \
            message.group_chat_created or message.supergroup_chat_created or \
            message.channel_chat_created:
        return

    # In groups, ignore everything except /start
    if message.chat.type in ("group", "supergroup"):
        return

    current_state = await state.get_state()

    if current_state is None:
        kb = get_main_menu_keyboard()
        await message.answer(
            "I didn't understand your command.\n\n"
            "Please choose one of the available options:",
            reply_markup=kb
        )
    else:
        kb_with_back = get_back_to_menu_keyboard()

        state_messages = {
            DisputeState.waiting_topic.state: "Please enter the dispute topic as text.",
            DisputeState.waiting_category.state: "Please select a category from the list.",
            DisputeState.waiting_claim_reason.state: "Please describe your claim in text.",
            DisputeState.waiting_claim_amount.state: "Answer 'Yes' or 'No', or enter the amount.",
            DisputeState.waiting_defendant_username.state: "Enter the defendant's username.",
            DisputeState.plaintiff_arguments.state: "Send an argument or press '✅ Finish arguments'.",
            DisputeState.defendant_arguments.state: "Send an argument or press '✅ Finish arguments'.",
            DisputeState.waiting_ai_question_response.state: "Please answer the AI judge's question.",
        }

        response_text = state_messages.get(current_state, "Unknown command.")
        await message.answer(response_text, reply_markup=kb_with_back)


# =============================================================================
# UNKNOWN CALLBACK HANDLER
# =============================================================================

@router.callback_query()
async def unknown_callback_handler(callback: CallbackQuery):
    """Handle unknown callbacks"""
    await callback.answer("Unknown command", show_alert=True)


# =============================================================================
# REGISTER HANDLERS
# =============================================================================

def register_handlers(dp: Dispatcher):
    """Register all handlers"""
    dp.include_router(router)