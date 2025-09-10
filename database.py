import uuid
from typing import Optional, List, Dict
import asyncpg
from telethon import TelegramClient
from telethon.sessions import StringSession

from conf import settings


class Database:
    def __init__(self):
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(settings.DATABASE_URL)
        await self.create_tables()

    async def create_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS cases (
                    id SERIAL PRIMARY KEY,
                    case_number VARCHAR(50) UNIQUE,
                    chat_id BIGINT,
                    topic TEXT,
                    category VARCHAR(100),
                    claim_amount DECIMAL(15,2),
                    mode VARCHAR(20),
                    plaintiff_id BIGINT,
                    plaintiff_username VARCHAR(100),
                    defendant_id BIGINT,
                    defendant_username VARCHAR(100),
                    status VARCHAR(50) DEFAULT 'active',
                    stage VARCHAR(50) DEFAULT 'plaintiff',
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS evidence (
                    id SERIAL PRIMARY KEY,
                    case_id INTEGER REFERENCES cases(id) ON DELETE CASCADE,
                    user_id BIGINT,
                    role VARCHAR(50),
                    type VARCHAR(50),
                    content TEXT,
                    file_id VARCHAR(500),
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS participants (
                    id SERIAL PRIMARY KEY,
                    case_id INT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    username TEXT,
                    role TEXT CHECK (role IN ('plaintiff', 'defendant', 'witness')),
                    joined_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(case_id, user_id, role)
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS decisions (
                    id SERIAL PRIMARY KEY,
                    case_number VARCHAR(50) UNIQUE,
                    file_path TEXT,
                    file_data BYTEA,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')

    async def create_additional_tables(self):
        """Создание дополнительных таблиц для пользовательских сессий и групп"""
        async with self.pool.acquire() as conn:
            # Таблица для сессий
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS user_sessions (
                    id SERIAL PRIMARY KEY,
                    session_string TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            ''')

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS dispute_groups (
                    id SERIAL PRIMARY KEY,
                    case_number VARCHAR(50) UNIQUE,
                    chat_id BIGINT NOT NULL,
                    title VARCHAR(255),
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            ''')

    async def create_case(self, topic: str, category: str, claim_amount: Optional[float], mode: str,
                         plaintiff_id: int, plaintiff_username: str, chat_id: int, status: str = 'active',
                         stage: str = 'plaintiff') -> str:
        async with self.pool.acquire() as conn:
            case_number = f"CASE-{uuid.uuid4().hex[:8].upper()}"
            case_id = await conn.fetchval('''
                INSERT INTO cases (
                    case_number, chat_id, topic, category, claim_amount, mode,
                    plaintiff_id, plaintiff_username, status, stage
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id
            ''', case_number, chat_id, topic, category, claim_amount, mode, plaintiff_id, plaintiff_username, status, stage)
            await conn.execute('''
                INSERT INTO participants (case_id, user_id, username, role)
                VALUES ($1, $2, $3, 'plaintiff')
                ON CONFLICT DO NOTHING
            ''', case_id, plaintiff_id, plaintiff_username)
            return case_number

    async def set_defendant(self, case_number: str, defendant_id: int, defendant_username: str):
        async with self.pool.acquire() as conn:
            case = await conn.fetchrow('SELECT id FROM cases WHERE case_number = $1', case_number)
            if not case:
                print(f"❌ Дело {case_number} не найдено")
                return
            case_id = case['id']
            await conn.execute('''
                UPDATE cases
                SET defendant_id = $1, defendant_username = $2, updated_at = NOW()
                WHERE case_number = $3
            ''', defendant_id, defendant_username, case_number)
            await conn.execute('''
                INSERT INTO participants (case_id, user_id, username, role)
                VALUES ($1, $2, $3, 'defendant')
                ON CONFLICT DO NOTHING
            ''', case_id, defendant_id, defendant_username)
            print(f"✅ Ответчик {defendant_id} назначен для дела {case_number}")

    async def get_case_by_number(self, case_number: str) -> Optional[Dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM cases WHERE case_number = $1', case_number)
            return dict(row) if row else None

    async def get_case_by_chat(self, chat_id: int) -> Optional[Dict]:
        """Возвращает активное дело по chat_id"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM cases
                WHERE chat_id = $1
                  AND status = $2
                """,
                chat_id,
                'active'
            )
            if row:
                return dict(row)
            return None

    async def get_user_cases(self, user_id: int) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT c.*
                FROM cases c
                JOIN participants p ON p.case_id = c.id
                WHERE p.user_id = $1
                ORDER BY c.created_at DESC
            ''', user_id)
            return [dict(r) for r in rows]

    async def get_user_active_cases(self, user_id: int) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT c.*
                FROM cases c
                JOIN participants p ON p.case_id = c.id
                WHERE p.user_id = $1 AND c.status = 'active'
                ORDER BY c.created_at DESC
            ''', user_id)
            return [dict(r) for r in rows]

    async def update_case_stage(self, case_number: str, stage: str):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE cases SET stage = $1, updated_at = NOW()
                WHERE case_number = $2
            ''', stage, case_number)

    async def update_case_status(self, case_number: str, status: str):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE cases SET status = $1, updated_at = NOW()
                WHERE case_number = $2
            ''', status, case_number)

    async def add_evidence(self, case_number: str, user_id: int, role: str, ev_type: str, content: Optional[str], file_id: Optional[str]):
        async with self.pool.acquire() as conn:
            case_id = await conn.fetchval('SELECT id FROM cases WHERE case_number = $1', case_number)
            if not case_id:
                raise ValueError(f"Дело {case_number} не найдено")
            await conn.execute('''
                INSERT INTO evidence (case_id, user_id, role, type, content, file_path)
                VALUES ($1, $2, $3, $4, $5, $6)
            ''', case_id, user_id, role, ev_type, content, file_id)

    async def get_case_evidence(self, case_number: str) -> List[Dict]:
        async with self.pool.acquire() as conn:
            case_id = await conn.fetchval('SELECT id FROM cases WHERE case_number = $1', case_number)
            if not case_id:
                return []
            rows = await conn.fetch('SELECT * FROM evidence WHERE case_id = $1 ORDER BY created_at', case_id)
            return [dict(r) for r in rows]

    async def save_decision(self, case_number: str, file_path: str = None, file_data: bytes = None):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO decisions (case_number, file_path, file_data, created_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (case_number)
                DO UPDATE SET file_path = EXCLUDED.file_path,
                              file_data = EXCLUDED.file_data,
                              created_at = NOW()
            """, case_number, file_path, file_data)

    async def get_decision_file(self, case_number: str):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT file_data FROM decisions WHERE case_number = $1",
                case_number
            )
            if row and row["file_data"]:
                return row["file_data"]
            return None


    async def add_participant(self, case_number: str, user_id: int, username: str, role: str):
        async with self.pool.acquire() as conn:
            # Получаем ID дела по его номеру
            case = await conn.fetchrow(
                "SELECT id FROM cases WHERE case_number=$1", case_number
            )
            if not case:
                raise ValueError(f"Дело с номером {case_number} не найдено")

            case_id = case["id"]

            await conn.execute("""
                INSERT INTO participants (case_id, user_id, username, role)
                VALUES ($1, $2, $3, $4)
            """, case_id, user_id, username, role)

    async def list_participants(self, case_id: int):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                   SELECT role, username, user_id
                   FROM participants
                   WHERE case_id = $1
               """, case_id)
        return [dict(r) for r in rows]

    # ===== ГРУППЫ ДЕЛ =====
    async def save_dispute_group(self, case_number: str, chat_id: int, title: str):
        """Сохранение информации о группе дела"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO dispute_groups (case_number, chat_id, title, created_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (case_number) DO UPDATE SET
                chat_id = $2, title = $3, updated_at = NOW()
            ''', case_number, chat_id, title)

    async def get_dispute_group(self, case_number: str) -> Optional[Dict]:
        """Получение информации о группе дела"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT * FROM dispute_groups WHERE case_number = $1
            ''', case_number)
            return dict(row) if row else None

    # ===== ПОЛЬЗОВАТЕЛЬСКИЕ СЕССИИ =====
    async def save_user_session(self, session_string: str):
        """Сохранение пользовательской сессии"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                DELETE FROM user_sessions;
                INSERT INTO user_sessions (session_string, created_at) 
                VALUES ($1, NOW())
            ''', session_string)

    async def get_user_session(self) -> Optional[Dict]:
        """Получение пользовательской сессии"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT session_string, created_at FROM user_sessions 
                ORDER BY created_at DESC LIMIT 1
            ''')
            return dict(row) if row else None


db = Database()
