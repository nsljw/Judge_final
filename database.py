import uuid
from typing import Optional, List, Dict
import asyncpg
from telethon import TelegramClient
from telethon.sessions import StringSession

from conf import settings, DELETE_OLDER_THAN_DAYS


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
                    claim_reason VARCHAR(100),
                    mode VARCHAR(20),
                    version VARCHAR (10) DEFAULT 'v2', 
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
                created_at TIMESTAMP DEFAULT NOW(),
                round_number INTEGER DEFAULT 0,
                question_id INTEGER
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
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id BIGINT PRIMARY KEY,
                    bot_version VARCHAR(10) DEFAULT 'v2',
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            ''')

    async def create_additional_tables(self):
        """Создание дополнительных таблиц для пользовательских сессий и групп"""
        async with self.pool.acquire() as conn:

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
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS ai_answers (
                    id SERIAL PRIMARY KEY,
                    case_number VARCHAR(50) NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    role VARCHAR(50) NOT NULL,
                    round_number INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS bot_users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    contacted_at TIMESTAMP DEFAULT NOW()
                )          
            ''')
            # Новая таблица для AI вопросов (если её нет)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS ai_questions (
                    id SERIAL PRIMARY KEY,
                    case_number VARCHAR(50) NOT NULL,
                    question TEXT NOT NULL,
                    target_role VARCHAR(50) NOT NULL,
                    round_number INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')

    async def create_case(
            self,
            topic: str,
            category: str,
            mode: str,
            claim_reason: str,
            plaintiff_id: int,
            plaintiff_username: str,
            chat_id: int,
            claim_amount: Optional[float] = None,
            status: str = "active",
            stage: str = "plaintiff",
            version: str = "v2",
    ) -> str:
        async with self.pool.acquire() as conn:
            case_number = f"CASE-{uuid.uuid4().hex[:8].upper()}"
            case_id = await conn.fetchval('''
                INSERT INTO cases (
                    case_number, chat_id, topic, category, claim_amount,
                    claim_reason, mode, plaintiff_id, plaintiff_username, status, stage, version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                RETURNING id
            ''', case_number, chat_id, topic, category, claim_amount, claim_reason, mode, plaintiff_id,
                                          plaintiff_username, status, stage, version)
            await conn.execute('''
                INSERT INTO participants (case_id, user_id, username, role)
                VALUES ($1, $2, $3, 'plaintiff')
                ON CONFLICT DO NOTHING
            ''', case_id, plaintiff_id, plaintiff_username)
            return case_number

    async def save_bot_user(self, user_id: int, username: str):
        """Сохранить пользователя, написавшего боту"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO bot_users (user_id, username, contacted_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (user_id) 
                DO UPDATE SET username = $2, contacted_at = NOW()
            """, user_id, username)

    async def get_defendant_by_username(self, username: str):
        """Получить информацию о пользователе по username"""
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                SELECT user_id, username 
                FROM bot_users 
                WHERE LOWER(username) = LOWER($1)
                ORDER BY contacted_at DESC
                LIMIT 1
            """, username)

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

    async def set_user_version(self, user_id: int, version: str):
        """Установить версию бота для пользователя"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO user_settings (user_id, bot_version, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (user_id) 
                DO UPDATE SET bot_version = $2, updated_at = NOW()
            ''', user_id, version)

    async def get_user_version(self, user_id: int) -> str:
        """Получить версию бота для пользователя"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT bot_version FROM user_settings WHERE user_id = $1
            ''', user_id)
            return row['bot_version'] if row else 'v2'

    async def get_case_version(self, case_number: str) -> str:
        """Получить версию бота по номеру дела"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT version FROM cases WHERE case_number = $1
            ''', case_number)
            return row['version'] if row else 'v2'

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

    async def save_ai_answer(self, case_number: str, question: str, answer: str, role: str, round_number: int):
        """Сохраняет ответ на вопрос ИИ"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO ai_answers (case_number, question, answer, role, round_number)
                VALUES ($1, $2, $3, $4, $5)
            ''', case_number, question, answer, role, round_number)

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

    async def update_case(self, *, case_number: str, **fields):
        if not fields:
            return

        async with self.pool.acquire() as conn:
            set_clause = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(fields.keys()))
            values = list(fields.values())
            query = f'''
                UPDATE cases
                SET {set_clause}, updated_at = NOW()
                WHERE case_number = $1
            '''
            await conn.execute(query, case_number, *values)

    async def add_evidence(self, case_number: str, user_id: int, role: str, ev_type: str, content: Optional[str],
                           file_id: Optional[str]):
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

    # ===== ИИ ВОПРОСЫ =====
    async def save_ai_question(self, case_number: str, question: str, target_role: str, round_number: int):
        """Сохранение вопроса от ИИ"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO ai_questions (case_number, question, target_role, round_number, created_at)
                VALUES ($1, $2, $3, $4, NOW())
            ''', case_number, question, target_role, round_number)

    async def get_ai_questions(self, case_number: str, target_role: str = None, round_number: int = None) -> List[Dict]:
        """Получение вопросов от ИИ"""
        async with self.pool.acquire() as conn:
            query = 'SELECT * FROM ai_questions WHERE case_number = $1'
            params = [case_number]

            if target_role:
                query += ' AND target_role = $2'
                params.append(target_role)

            if round_number:
                if target_role:
                    query += ' AND round_number = $3'
                else:
                    query += ' AND round_number = $2'
                params.append(round_number)

            query += ' ORDER BY created_at'

            rows = await conn.fetch(query, *params)
            return [dict(r) for r in rows]

    async def get_ai_questions_count(self, case_number: str, target_role: str) -> int:
        """Получение количества раундов вопросов для конкретной роли"""
        async with self.pool.acquire() as conn:
            count = await conn.fetchval('''
                SELECT COALESCE(MAX(round_number), 0) FROM ai_questions
                WHERE case_number = $1 AND target_role = $2
            ''', case_number, target_role)
            return count or 0

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

    async def get_case_with_full_info(self, case_number: str) -> Optional[Dict]:
        """Получение дела с полной информацией включая участников и доказательства"""
        async with self.pool.acquire() as conn:
            case_row = await conn.fetchrow('SELECT * FROM cases WHERE case_number = $1', case_number)
            if not case_row:
                return None

            case_dict = dict(case_row)

            participants = await conn.fetch('''
                SELECT role, username, user_id, joined_at
                FROM participants 
                WHERE case_id = $1
                ORDER BY joined_at
            ''', case_dict['id'])
            case_dict['participants'] = [dict(p) for p in participants]

            evidence = await conn.fetch('''
                SELECT * FROM evidence 
                WHERE case_id = $1 
                ORDER BY created_at
            ''', case_dict['id'])
            case_dict['evidence'] = [dict(e) for e in evidence]

            return case_dict

    async def delete_case(self, case_number: str) -> bool:
        """Удаление дела и всех связанных данных"""
        async with self.pool.acquire() as conn:
            try:
                await conn.execute('DELETE FROM ai_questions WHERE case_number = $1', case_number)
                await conn.execute('DELETE FROM ai_answers WHERE case_number = $1', case_number)
                await conn.execute('DELETE FROM decisions WHERE case_number = $1', case_number)
                await conn.execute('DELETE FROM dispute_groups WHERE case_number = $1', case_number)

                deleted_count = await conn.fetchval(
                    'DELETE FROM cases WHERE case_number = $1 RETURNING id',
                    case_number
                )

                return deleted_count is not None
            except Exception as e:
                print(f"Ошибка при удалении дела {case_number}: {e}")
                return False

    async def get_case_statistics(self, user_id: int) -> Dict:
        """Получение статистики дел для пользователя"""
        async with self.pool.acquire() as conn:
            stats = await conn.fetchrow('''
                SELECT 
                    COUNT(*) as total_cases,
                    COUNT(CASE WHEN c.status = 'active' THEN 1 END) as active_cases,
                    COUNT(CASE WHEN c.status = 'finished' THEN 1 END) as finished_cases,
                    COUNT(CASE WHEN p.role = 'plaintiff' THEN 1 END) as as_plaintiff,
                    COUNT(CASE WHEN p.role = 'defendant' THEN 1 END) as as_defendant
                FROM cases c
                JOIN participants p ON p.case_id = c.id
                WHERE p.user_id = $1
            ''', user_id)

            return dict(stats) if stats else {
                'total_cases': 0,
                'active_cases': 0,
                'finished_cases': 0,
                'as_plaintiff': 0,
                'as_defendant': 0
            }

    async def search_cases(self, user_id: int, search_query: str) -> List[Dict]:
        """Поиск дел пользователя по ключевым словам"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT c.*
                FROM cases c
                JOIN participants p ON p.case_id = c.id
                WHERE p.user_id = $1 
                AND (
                    LOWER(c.topic) LIKE LOWER($2) OR 
                    LOWER(c.category) LIKE LOWER($2) OR 
                    LOWER(c.claim_reason) LIKE LOWER($2) OR
                    c.case_number LIKE UPPER($2)
                )
                ORDER BY c.created_at DESC
            ''', user_id, f'%{search_query}%')

            return [dict(r) for r in rows]

    async def update_case_claim_amount(self, case_number: str, claim_amount: Optional[float]):
        """Обновление суммы иска"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE cases 
                SET claim_amount = $1, updated_at = NOW()
                WHERE case_number = $2
            ''', claim_amount, case_number)

    async def get_evidence_by_role(self, case_number: str, role: str) -> List[Dict]:
        """Получение доказательств по роли участника"""
        async with self.pool.acquire() as conn:
            case_id = await conn.fetchval('SELECT id FROM cases WHERE case_number = $1', case_number)
            if not case_id:
                return []

            rows = await conn.fetch('''
                SELECT * FROM evidence 
                WHERE case_id = $1 AND role = $2 
                ORDER BY created_at
            ''', case_id, role)

            result = []
            for row in rows:
                evidence_dict = dict(row)
                if evidence_dict.get('file_id'):
                    evidence_dict['file_path'] = evidence_dict['file_id']
                result.append(evidence_dict)
            return result

    async def get_answered_ai_questions_count(self, case_number: str, role: str, round_number: int) -> int:
        """Получить количество отвеченных вопросов в данном раунде"""
        async with self.pool.acquire() as conn:
            case_id = await conn.fetchval('SELECT id FROM cases WHERE case_number = $1', case_number)
            if not case_id:
                return 0

            count = await conn.fetchval('''
                SELECT COUNT(*) 
                FROM evidence 
                WHERE case_id = $1 
                AND role = $2 
                AND round_number = $3
                AND type = 'ai_response'
            ''', case_id, role, round_number)

            return count or 0

    async def clean_old_records(self):
        """Удаление дел старше DELETE_OLDER_THAN_DAYS дней"""
        try:
            async with self.pool.acquire() as conn:
                # Получаем список дел для удаления
                old_cases = await conn.fetch(f"""
                    SELECT case_number, topic, created_at
                    FROM cases
                    WHERE created_at < NOW() - INTERVAL '{DELETE_OLDER_THAN_DAYS} days'
                """)

                if not old_cases:
                    print(f"🧹 Нет дел старше {DELETE_OLDER_THAN_DAYS} дней для удаления")
                    return

                print(f"🗑️ Найдено {len(old_cases)} дел для удаления:")
                for case in old_cases:
                    print(f"  - {case['case_number']}: {case['topic'][:50]} (создано: {case['created_at']})")

                # Удаляем связанные данные
                deleted_ai_questions = await conn.execute(f"""
                    DELETE FROM ai_questions
                    WHERE case_number IN (
                        SELECT case_number FROM cases
                        WHERE created_at < NOW() - INTERVAL '{DELETE_OLDER_THAN_DAYS} days'
                    )
                """)

                deleted_ai_answers = await conn.execute(f"""
                    DELETE FROM ai_answers
                    WHERE case_number IN (
                        SELECT case_number FROM cases
                        WHERE created_at < NOW() - INTERVAL '{DELETE_OLDER_THAN_DAYS} days'
                    )
                """)

                deleted_decisions = await conn.execute(f"""
                    DELETE FROM decisions
                    WHERE case_number IN (
                        SELECT case_number FROM cases
                        WHERE created_at < NOW() - INTERVAL '{DELETE_OLDER_THAN_DAYS} days'
                    )
                """)

                deleted_groups = await conn.execute(f"""
                    DELETE FROM dispute_groups
                    WHERE case_number IN (
                        SELECT case_number FROM cases
                        WHERE created_at < NOW() - INTERVAL '{DELETE_OLDER_THAN_DAYS} days'
                    )
                """)

                # Удаляем сами дела (CASCADE удалит evidence и participants)
                result = await conn.execute(f"""
                    DELETE FROM cases
                    WHERE created_at < NOW() - INTERVAL '{DELETE_OLDER_THAN_DAYS} days'
                """)

                print(f"✅ Очистка завершена:")
                print(f"   - Удалено дел: {len(old_cases)}")
                print(f"   - Удалено AI вопросов: {deleted_ai_questions}")
                print(f"   - Удалено AI ответов: {deleted_ai_answers}")
                print(f"   - Удалено решений: {deleted_decisions}")
                print(f"   - Удалено групп: {deleted_groups}")

        except Exception as e:
            print(f"❌ Ошибка при очистке старых записей: {e}")
            import traceback
            traceback.print_exc()

    async def updated_at_case(self, case_number: str):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                    UPDATE cases
                    SET updated_at = NOW()
                    WHERE case_number = $1
                ''', case_number)




db = Database()
