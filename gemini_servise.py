import google.generativeai as genai
from typing import List, Dict, Union
import json
import base64
import io
from aiogram import Bot
from aiogram.types import File
from conf import settings
import PyPDF2
from docx import Document


class GeminiService:
    def __init__(self):
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-2.0-flash-exp')

    async def analyze_case(self, case_data: Dict, participants: List[Dict], evidence: List[Dict],
                           bot: Bot = None) -> Dict:
        """
        Базовый анализ дела (JSON c фактами, нарушениями, решением).
        """
        messages = await self._build_multimodal_prompt(
            "Ты — ИИ судья. Проведи анализ дела и верни JSON.",
            case_data, participants, evidence, bot
        )
        try:
            response = self.model.generate_content(messages)
            analysis = self._parse_analysis_response(response.text)
            return analysis
        except Exception as e:
            return {
                "error": f"Ошибка анализа: {str(e)}",
                "established_facts": [],
                "violations": [],
                "decision": "Невозможно вынести решение из-за технической ошибки",
                "verdict": {},
                "additional_questions": []
            }

    async def generate_reasoning(self, case_data: Dict, participants: List[Dict], evidence: List[Dict],
                                 bot: Bot = None) -> str:
        """
        Генерация только текста обоснования.
        """
        messages = await self._build_multimodal_prompt(
            "Ты — ИИ судья. Сформулируй только обоснование решения (чистый текст).",
            case_data, participants, evidence, bot
        )
        try:
            response = self.model.generate_content(messages)
            return response.text.strip()
        except Exception as e:
            return f"Не удалось сгенерировать обоснование из-за ошибки: {str(e)}"

    async def generate_full_decision(
            self,
            case_data: Dict,
            participants: List[Dict],
            evidence: List[Dict],
            bot: Bot = None,
            no_evidence: bool = False
    ) -> Dict:
        """
        Генерация полного постановления и решения (JSON).
        """
        instruction = """Ты — ИИ судья. Рассмотри дело и сформируй полное постановление. 
    Учитывай предоставленные вещественные доказательства, включая изображения и содержимое документов."""

        if no_evidence:
            instruction += "\n⚠️ Внимание: доказательства не предоставлены. Решение нужно вынести только на основании аргументов сторон."

        instruction += """
    Верни JSON строго в формате:
    {
      "established_facts": ["факт1", "факт2"],
      "violations": ["нарушение1", "нарушение2"],
      "decision": "Текст итогового решения",
      "verdict": {
          "claim_satisfied": true/false,
          "amount_awarded": число,
          "court_costs": число
      },
      "reasoning": "Подробное обоснование решения (текст)"
    }"""

        messages = await self._build_multimodal_prompt(
            instruction, case_data, participants, evidence, bot
        )

        try:
            response = self.model.generate_content(messages)
            return self._parse_analysis_response(response.text)
        except Exception as e:
            return {
                "error": f"Ошибка генерации решения: {str(e)}",
                "established_facts": [ev.get("description", "") for ev in evidence],
                "violations": [],
                "decision": "Решение не удалось вынести из-за ошибки",
                "verdict": {
                    "claim_satisfied": False,
                    "amount_awarded": 0,
                    "court_costs": 0
                },
                "reasoning": ""
            }


    async def _download_telegram_file(self, bot: Bot, file_id: str) -> bytes:
        """Загружает файл из Telegram по file_id"""
        try:
            file_info: File = await bot.get_file(file_id)
            file_bytes = io.BytesIO()
            await bot.download_file(file_info.file_path, file_bytes)
            return file_bytes.getvalue()
        except Exception as e:
            print(f"Ошибка загрузки файла {file_id}: {e}")
            return b""

    async def _extract_text_from_pdf(self, file_bytes: bytes) -> str:
        """Извлекает текст из PDF"""
        try:
            pdf_file = io.BytesIO(file_bytes)
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
            return text.strip()
        except Exception as e:
            return f"Ошибка чтения PDF: {e}"

    async def _extract_text_from_docx(self, file_bytes: bytes) -> str:
        """Извлекает текст из DOCX"""
        try:
            doc_file = io.BytesIO(file_bytes)
            doc = Document(doc_file)
            text = ""
            for paragraph in doc.paragraphs:
                text += paragraph.text + "\n"
            return text.strip()
        except Exception as e:
            return f"Ошибка чтения DOCX: {e}"

    async def _extract_text_from_document(self, file_bytes: bytes, filename: str) -> str:
        """Универсальная функция извлечения текста из документов"""
        filename_lower = filename.lower()

        if filename_lower.endswith('.pdf'):
            return await self._extract_text_from_pdf(file_bytes)
        elif filename_lower.endswith('.docx'):
            return await self._extract_text_from_docx(file_bytes)
        elif filename_lower.endswith('.txt'):
            try:
                return file_bytes.decode('utf-8')
            except:
                try:
                    return file_bytes.decode('cp1251')
                except:
                    return "Ошибка декодирования текстового файла"
        else:
            return f"Неподдерживаемый формат документа: {filename}"

    def _is_image(self, filename: str) -> bool:
        """Проверяет, является ли файл изображением"""
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        return any(filename.lower().endswith(ext) for ext in image_extensions)


    async def _build_multimodal_prompt(
            self, task_instruction: str, case_data: Dict, participants: List[Dict], evidence: List[Dict],
            bot: Bot = None
    ) -> List[Union[str, Dict]]:
        """
        Формируем мультимодальный ввод (текст + изображения + содержимое документов).
        """
        base_prompt = f"""
{task_instruction}

Номер дела: {case_data.get('case_number')}
Предмет спора: {case_data.get('topic')}
Категория: {case_data.get('category')}
Сумма иска: {case_data.get('claim_amount', 'не указана')}

Участники:
{self._format_participants(participants)}

Доказательства и аргументы:
"""
        messages: List[Union[str, Dict]] = [base_prompt]

        for i, ev in enumerate(evidence, 1):
            role_text = "Истец" if ev.get("role") == "plaintiff" else "Ответчик"

            if ev["type"] == "text":
                messages.append(f"\n{i}. {role_text} - Аргумент:\n{ev.get('content', ev.get('description', ''))}\n")

            elif ev["type"] == "photo" and bot and ev.get("file_path"):
                try:
                    file_bytes = await self._download_telegram_file(bot, ev["file_path"])
                    if file_bytes:
                        messages.append({
                            "mime_type": "image/jpeg",
                            "data": base64.b64encode(file_bytes).decode()
                        })
                        caption = ev.get('content', 'Фото-доказательство')
                        messages.append(f"\n{i}. {role_text} - Изображение: {caption}\n")
                    else:
                        messages.append(f"\n{i}. {role_text} - [Ошибка загрузки изображения]\n")
                except Exception as e:
                    messages.append(f"\n{i}. {role_text} - [Ошибка обработки изображения: {e}]\n")

            elif ev["type"] == "document" and bot and ev.get("file_path"):
                try:
                    file_bytes = await self._download_telegram_file(bot, ev["file_path"])
                    if file_bytes:
                        try:
                            file_info = await bot.get_file(ev["file_path"])
                            filename = file_info.file_path.split('/')[-1] if file_info.file_path else "document"
                        except:
                            filename = "document"
                        if self._is_image(filename):
                            messages.append({
                                "mime_type": "image/jpeg",
                                "data": base64.b64encode(file_bytes).decode()
                            })
                            caption = ev.get('content', 'Изображение (документ)')
                            messages.append(f"\n{i}. {role_text} - Изображение-документ: {caption}\n")
                        else:
                            extracted_text = await self._extract_text_from_document(file_bytes, filename)
                            messages.append(f"\n{i}. {role_text} - Документ ({filename}):\n{extracted_text}\n")
                    else:
                        messages.append(f"\n{i}. {role_text} - [Ошибка загрузки документа]\n")
                except Exception as e:
                    messages.append(f"\n{i}. {role_text} - [Ошибка обработки документа: {e}]\n")

            elif ev["type"] == "video" and ev.get("file_path"):
                caption = ev.get('content', 'Видео-доказательство')
                messages.append(
                    f"\n{i}. {role_text} - Видео: {caption}\n[Содержимое видео не анализируется автоматически]\n")

            else:
                description = ev.get('content', ev.get('description', 'Доказательство без описания'))
                messages.append(f"\n{i}. {role_text} - {ev['type']}: {description}\n")

        return messages

    def _format_participants(self, participants: List[Dict]) -> str:
        result = []
        for p in participants:
            role_ru = "Истец" if p['role'] == 'plaintiff' else "Ответчик"
            username = p.get('username', 'неизвестно')
            result.append(f"{role_ru}: @{username}")
        return ", ".join(result)

    def _parse_analysis_response(self, response_text: str) -> Dict:
        """Парсит ответ от Gemini, извлекая JSON"""
        try:
            start = response_text.find('{')
            end = response_text.rfind('}') + 1
            if start >= 0 and end > start:
                json_str = response_text[start:end]
                return json.loads(json_str)
            else:
                return {
                    "established_facts": [],
                    "violations": [],
                    "decision": response_text,
                    "verdict": {
                        "claim_satisfied": False,
                        "amount_awarded": 0,
                        "court_costs": 0
                    },
                    "reasoning": response_text
                }
        except Exception as e:
            return {
                "established_facts": [],
                "violations": [],
                "decision": response_text,
                "verdict": {
                    "claim_satisfied": False,
                    "amount_awarded": 0,
                    "court_costs": 0
                },
                "reasoning": response_text,
                "parse_error": str(e)
            }


gemini_service = GeminiService()