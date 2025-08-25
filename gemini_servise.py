import google.generativeai as genai
from typing import List, Dict, Union
import json
import base64
from conf import settings


class GeminiService:
    def __init__(self):
        genai.configure(api_key=settings.GEMINI_API_KEY)
        # ⚖️ Лучше брать vision-модель
        self.model = genai.GenerativeModel('gemini-2.0-flash')

    async def analyze_case(self, case_data: Dict, participants: List[Dict], evidence: List[Dict]) -> Dict:
        """
        Базовый анализ дела (JSON c фактами, нарушениями, решением).
        """
        messages = self._build_multimodal_prompt(
            "Ты — ИИ судья. Проведи анализ дела и верни JSON.",
            case_data, participants, evidence
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

    async def generate_reasoning(self, case_data: Dict, participants: List[Dict], evidence: List[Dict]) -> str:
        """
        Генерация только текста обоснования (чистый текст).
        """
        messages = self._build_multimodal_prompt(
            "Ты — ИИ судья. Сформулируй только обоснование решения (чистый текст).",
            case_data, participants, evidence
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
            no_evidence: bool = False
    ) -> Dict:
        """
        Генерация полного постановления и решения (JSON).
        """
        instruction = """Ты — ИИ судья. Рассмотри дело и сформируй полное постановление. 
    Учитывай предоставленные вещественные доказательства."""

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

        messages = self._build_multimodal_prompt(
            instruction, case_data, participants, evidence
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

    # ================= Вспомогательные =================

    def _build_multimodal_prompt(
        self, task_instruction: str, case_data: Dict, participants: List[Dict], evidence: List[Dict]
    ) -> List[Union[str, Dict]]:
        """
        Формируем мультимодальный ввод (текст + изображения).
        """
        base_prompt = f"""
{task_instruction}

Номер: {case_data.get('case_number')}
Предмет: {case_data.get('subject')}
Категория: {case_data.get('category')}
Сумма иска: {case_data.get('claim_amount')}

Участники:
{self._format_participants(participants)}

Доказательства:
"""
        messages: List[Union[str, Dict]] = [base_prompt]

        for ev in evidence:
            if ev["type"] == "argument":
                messages.append(f"- {ev['description']}")
            elif ev["type"] == "image" and "file_path" in ev:
                try:
                    with open(ev["file_path"], "rb") as f:
                        image_bytes = f.read()
                    messages.append({
                        "mime_type": "image/jpeg",
                        "data": base64.b64encode(image_bytes).decode()
                    })
                    messages.append(f"(Фото-доказательство: {ev.get('description','')})")
                except Exception as e:
                    messages.append(f"[Ошибка чтения фото: {e}]")
            elif ev["type"] == "document":
                messages.append(f"(Текст из документа: {ev['description']})")

        return messages

    def _format_participants(self, participants: List[Dict]) -> str:
        return ", ".join(f"{p['role']}: @{p.get('username','неизвестно')}" for p in participants)

    def _parse_analysis_response(self, response_text: str) -> Dict:
        try:
            start = response_text.find('{')
            end = response_text.rfind('}') + 1
            return json.loads(response_text[start:end])
        except Exception:
            return {
                "established_facts": [],
                "violations": [],
                "decision": response_text,
                "verdict": {},
                "reasoning": response_text
            }


gemini_service = GeminiService()
