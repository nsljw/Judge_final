# database.py
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
            # Таблица дел
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS cases (
                    id SERIAL PRIMARY KEY,
                    case_number VARCHAR(50) UNIQUE,
                    plaintiff_id BIGINT,
                    defendant_id BIGINT,
                    subject TEXT,
                    category VARCHAR(100),
                    description TEXT,
                    claim_amount DECIMAL(15,2),
                    status VARCHAR(50),
                    mode VARCHAR(20),
                    group_id BIGINT,
                    group_invite VARCHAR(500),
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            ''')

            # Таблица участников
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS case_participants (
                    id SERIAL PRIMARY KEY,
                    case_id INTEGER REFERENCES cases(id),
                    user_id BIGINT,
                    username VARCHAR(100),
                    role VARCHAR(50),
                    description TEXT,
                    added_at TIMESTAMP DEFAULT NOW()
                )
            ''')

            # Таблица доказательств
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS evidence (
                    id SERIAL PRIMARY KEY,
                    case_id INTEGER REFERENCES cases(id),
                    user_id BIGINT,
                    type VARCHAR(50),
                    content TEXT,
                    file_path VARCHAR(500),
                    description TEXT,
                    added_at TIMESTAMP DEFAULT NOW()
                )
            ''')

            # Таблица решений
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS decisions (
                    id SERIAL PRIMARY KEY,
                    case_id INTEGER REFERENCES cases(id),
                    decision_text TEXT,
                    established_facts TEXT,
                    violations TEXT,
                    verdict TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')

    # ===== Методы работы с делами =====
    async def create_case(self, plaintiff_id: int, subject: str, category: str,
                          description: str, claim_amount: float, mode: str) -> str:
        async with self.pool.acquire() as conn:
            case_number = f"CASE-{uuid.uuid4().hex[:10].upper()}"
            await conn.execute('''
                INSERT INTO cases (case_number, plaintiff_id, subject, category, 
                                   description, claim_amount, status, mode)
                VALUES ($1, $2, $3, $4, $5, $6, 'created', $7)
            ''', case_number, plaintiff_id, subject, category, description,
                               claim_amount, mode)
            case_id = await conn.fetchval('SELECT id FROM cases WHERE case_number = $1', case_number)
            await self.add_participant(case_id, plaintiff_id, None, 'истец', 'Инициатор спора')
            return case_number

    async def get_case_by_number(self, case_number: str) -> Optional[Dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM cases WHERE case_number = $1', case_number)
            return dict(row) if row else None

    async def set_defendant(self, case_number: str, defendant_id: int, username: str):
        async with self.pool.acquire() as conn:
            case_id = await conn.fetchval('SELECT id FROM cases WHERE case_number = $1', case_number)
            await conn.execute('UPDATE cases SET defendant_id = $1 WHERE case_number = $2',
                               defendant_id, case_number)
            await self.add_participant(case_id, defendant_id, username, 'ответчик',
                                       'Лицо, к которому предъявлен иск')

    async def add_participant(self, case_id: int, user_id: int, username: Optional[str],
                              role: str, description: str):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO case_participants (case_id, user_id, username, role, description)
                VALUES ($1, $2, $3, $4, $5)
            ''', case_id, user_id, username, role, description)

    async def get_case_participants(self, case_id: int) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('SELECT * FROM case_participants WHERE case_id = $1 ORDER BY added_at', case_id)
            return [dict(row) for row in rows]

    async def add_evidence(self, case_id: int, user_id: int, evidence_type: str,
                           content: str, file_path: Optional[str], description: str):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO evidence (case_id, user_id, type, content, file_path, description)
                VALUES ($1, $2, $3, $4, $5, $6)
            ''', case_id, user_id, evidence_type, content, file_path, description)

    async def get_case_evidence(self, case_id: int) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('SELECT * FROM evidence WHERE case_id = $1 ORDER BY added_at', case_id)
            return [dict(row) for row in rows]

    async def save_decision(self, case_id: int, decision_text: str, established_facts: str,
                            violations: str, verdict: str):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO decisions (case_id, decision_text, established_facts, violations, verdict)
                VALUES ($1, $2, $3, $4, $5)
            ''', case_id, decision_text, established_facts, violations, verdict)

    async def get_case_decision(self, case_id: int) -> Optional[Dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM decisions WHERE case_id = $1', case_id)
            return dict(row) if row else None

    async def set_case_group(self, case_number: str, group_id: int, invite_link: str):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE cases 
                SET group_id = $1, group_invite = $2, updated_at = NOW()
                WHERE case_number = $3
            ''', group_id, invite_link, case_number)

    async def update_case_status(self, case_number: str, status: str):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                   UPDATE cases
                   SET status = $1,
                       updated_at = NOW()
                   WHERE case_number = $2
               ''', status, case_number)


db = Database()
