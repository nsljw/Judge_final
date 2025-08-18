import tempfile
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
import google.generativeai as genai
from settings import rooms


router = Router()

class DisputeState(StatesGroup):
    waiting_room_name = State()
    
    in_room = State()


@router.message(Command("create_now"))
async def create_room(message: types.Message, state: FSMContext):
    await message.answer("Введите название комнаты")
    await state.set_state(DisputeState.waiting_room_name)

@router.message(DisputeState.in_room)
async def is_process(message: types.Message, state: FSMContext):
    room_name = message.text.strip()
    room_id = f"{room_name}{int(datetime.now().timestamp())}"
    rooms[room_id] = {"messages": [], "plaintiff": message.from_user.id}
    await state.update_data(room_id=room_id)
    await message.answer(
        f"Комната создана! ID: {room_id}\n"
        f""
    )