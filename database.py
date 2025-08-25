import uuid
import asyncpg
from typing import Optional, List, Dict
from datetime import datetime
from conf import settings


class Database:
    def __init__(self):
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(settings.DATABASE_URL)
        await self.create_tables()

    async def create_tables(self):
        async with self.pool.acquire() as conn:
            # дела
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS cases (
                    id SERIAL PRIMARY KEY,
                    case_number VARCHAR(50) UNIQUE,
                    topic TEXT,
                    category VARCHAR(100),
                    claim_amount DECIMAL(15,2),
                    mode VARCHAR(20),
                    plaintiff_id BIGINT,
                    plaintiff_username VARCHAR(100),
                    defendant_id BIGINT,
                    defendant_username VARCHAR(100),
                    status VARCHAR(50) DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            ''')

            # доказательства / аргументы
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS evidence (
                    id SERIAL PRIMARY KEY,
                    case_id INTEGER REFERENCES cases(id) ON DELETE CASCADE,
                    user_id BIGINT,
                    role VARCHAR(50), -- plaintiff / defendant / witness
                    type VARCHAR(50), -- text / photo / document
                    content TEXT,     
                    file_path VARCHAR(500), 
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')

            # решения
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS decisions (
                    id SERIAL PRIMARY KEY,
                    case_id INTEGER REFERENCES cases(id) ON DELETE CASCADE,
                    decision_text TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')

            # приглашения с ссылкой
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS invitations (
                    id SERIAL PRIMARY KEY,
                    case_number VARCHAR(50),
                    chat_id BIGINT,
                    role VARCHAR(50),  -- defendant / witness
                    invite_link TEXT,
                    used BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')

    # ===== CRUD для дела =====
    async def create_case(self, topic: str, category: str, claim_amount: Optional[float],
                          mode: str, plaintiff_id: int, plaintiff_username: str, status='active') -> str:
        async with self.pool.acquire() as conn:
            case_number = f"CASE-{uuid.uuid4().hex[:8].upper()}"
            await conn.execute('''
                INSERT INTO cases (case_number, topic, status, category, claim_amount, mode, plaintiff_id, plaintiff_username)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ''', case_number, topic, status, category, claim_amount, mode, plaintiff_id, plaintiff_username)
            return case_number

    async def set_defendant(self, case_number: str, defendant_id: int, defendant_username: str):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE cases SET defendant_id=$1, defendant_username=$2, updated_at=NOW()
                WHERE case_number=$3
            ''', defendant_id, defendant_username, case_number)

    async def update_case_status(self, case_number: str, status: str):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE cases SET status=$1, updated_at=NOW() WHERE case_number=$2
            ''', status, case_number)

    async def get_case_by_number(self, case_number: str) -> Optional[Dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM cases WHERE case_number=$1', case_number)
            return dict(row) if row else None

    async def get_user_cases(self, user_id: int) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT * FROM cases WHERE plaintiff_id=$1 OR defendant_id=$1 ORDER BY created_at DESC
            ''', user_id)
            return [dict(r) for r in rows]

    # ===== Аргументы / доказательства =====
    async def add_evidence(self, case_number: str, user_id: int, role: str,
                           ev_type: str, content: Optional[str], file_path: Optional[str]):
        async with self.pool.acquire() as conn:
            case_id = await conn.fetchval('SELECT id FROM cases WHERE case_number=$1', case_number)
            await conn.execute('''
                INSERT INTO evidence (case_id, user_id, role, type, content, file_path)
                VALUES ($1,$2,$3,$4,$5,$6)
            ''', case_id, user_id, role, ev_type, content, file_path)

    async def get_case_evidence(self, case_number: str) -> List[Dict]:
        async with self.pool.acquire() as conn:
            case_id = await conn.fetchval('SELECT id FROM cases WHERE case_number=$1', case_number)
            rows = await conn.fetch('SELECT * FROM evidence WHERE case_id=$1 ORDER BY created_at', case_id)
            return [dict(r) for r in rows]

    # ===== Решение =====
    async def save_decision(self, case_number: str, decision_text: str):
        async with self.pool.acquire() as conn:
            case_id = await conn.fetchval('SELECT id FROM cases WHERE case_number=$1', case_number)
            await conn.execute('''
                INSERT INTO decisions (case_id, decision_text) VALUES ($1,$2)
            ''', case_id, decision_text)

    async def get_case_decision(self, case_number: str) -> Optional[Dict]:
        async with self.pool.acquire() as conn:
            case_id = await conn.fetchval('SELECT id FROM cases WHERE case_number=$1', case_number)
            row = await conn.fetchrow('SELECT * FROM decisions WHERE case_id=$1', case_id)
            return dict(row) if row else None

    # ===== ПРИГЛАШЕНИЯ =====
    async def add_invitation(self, case_number: str, chat_id: int, role: str, invite_link: str):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO invitations (case_number, chat_id, role, invite_link)
                VALUES ($1,$2,$3,$4)
            ''', case_number, chat_id, role, invite_link)

    async def get_active_invitations(self, chat_id: int) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT * FROM invitations WHERE chat_id=$1 AND used=FALSE
            ''', chat_id)
            return [dict(r) for r in rows]

    async def mark_invitations_used(self, user_id: int, chat_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE invitations SET used=TRUE WHERE chat_id=$1
            ''', chat_id)

    # ===== Участники дела =====
    async def add_participant(self, case_number: str, user_id: int, username: str, role: str):
        if role == "defendant":
            await self.set_defendant(case_number, user_id, username)
        else:
            # witness или другие роли добавляем в evidence как отдельную таблицу или отдельный CRUD
            await self.add_evidence(case_number, user_id, role, "text", None, None)

    async def is_participant(self, case_number: str, user_id: int) -> bool:
        case = await self.get_case_by_number(case_number)
        if not case:
            return False
        if user_id in [case.get("plaintiff_id"), case.get("defendant_id")]:
            return True
        # проверка в evidence (для свидетелей)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT 1 FROM evidence WHERE case_id=$1 AND user_id=$2
            ''', case["id"], user_id)
            return bool(row)


db = Database()
