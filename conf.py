import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()


class Settings(BaseSettings):
    API_ID: int = int(os.getenv("API_ID", ""))
    API_HASH: str = os.getenv("API_HASH", "")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY")
    DATABASE_URL: str = os.getenv("DATABASE_URL")
    DISPUTE_TOKEN_WALLET: str = os.getenv("DISPUTE_TOKEN_WALLET")

    class Config:
        env_file = ".env"


settings = Settings()


class Roles:
    PLAINTIFF = "истец"
    DEFENDANT = "ответчик"
    OBSERVER = "наблюдатель"
    WITNESS_PLAINTIFF = "свидетель_истца"
    WITNESS_DEFENDANT = "свидетель_ответчика"
    EXPERT = "эксперт"


class CaseStatus:
    CREATED = "создано"
    GATHERING_EVIDENCE = "сбор_доказательств"
    ADDING_PARTICIPANTS = "добавление_участников"
    ANALYSIS = "анализ"
    DECISION = "решение"
    CLOSED = "закрыто"


class CaseMode:
    SIMPLIFIED = "упрощенный"
    DETAILED = "детальный"


DISPUTE_CATEGORIES = [
    "Договорные споры",
    "Займы и долги",
    "Услуги и работы",
    "Купля-продажа",
    "Аренда",
    "Другое"
]

DISPUTE_EXAMPLES = [
    "выполненная работа не соответствует договоренностям",
    "невозврат займа в срок",
    "некачественные услуги",
    "поставка бракованного товара",
    "нарушение сроков аренды"
]
