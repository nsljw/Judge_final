import base64
import io
import json
from typing import List, Dict, Union

import PyPDF2
import google.generativeai as genai
from aiogram import Bot
from aiogram.types import File
from docx import Document

from conf import settings


class GeminiService:
    def __init__(self):
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-2.5-flash')

    async def generate_clarifying_questions(
            self,
            case_data: Dict,
            participants: List[Dict],
            evidence: List[Dict],
            current_role: str,
            round_number: int,
            bot: Bot = None
    ) -> List[str]:
        """
        Generates clarifying questions for a case participant
        """
        role_text = "plaintiff" if current_role == "plaintiff" else "defendant"

        instruction = f"""
        You are an AI judge. Analyze the arguments and evidence of the {"plaintiff" if current_role == "plaintiff" else "defendant"} and ask clarifying questions to reveal details and fill gaps. 
        Focus on material evidence; without it, state that a decision cannot be made objectively.

        IMPORTANT: 
        - This is round {round_number} out of a maximum of 3 possible rounds
        - Ask only those questions that help understand the facts of the case more deeply
        - Avoid overly general or philosophical questions (e.g., "Why do you think you're right?")
        - Maximum 3 questions at a time
        - If there is sufficient information to make a decision, return an empty array []
        - Pay special attention to chat history provided as evidence

        Criteria for questions:
        1. Specification of details (exact dates, amounts, locations, participants, actions).
        2. Verification of evidence validity (e.g., "What documents confirm this?", "Are there witnesses?").
        3. Clarification of relationships between events and evidence.
        4. Identification of weak points or contradictions in the {"plaintiff's" if current_role == "plaintiff" else "defendant's"} position.
        5. Focus on facts that directly affect the case outcome (not secondary details).
        6. Reference to specific messages from chat history if provided.

        Examples of question style:
        - "Please specify on what exact day the event occurred?"
        - "Who was present at the signing of the contract?"
        - "What confirms the amount you are claiming?"
        - "Why do your documents show different dates?"
        - "In the chat history from [date], you mentioned X. Can you clarify this?"

        Return JSON in the format:
        {{
                "questions": ["question1", "question2", "question3"]
        }}

        If no questions are needed, return: {{"questions": []}}
        """

        messages = await self._build_multimodal_prompt(
            instruction, case_data, participants, evidence, bot
        )

        try:
            response = self.model.generate_content(messages)
            result = self._parse_questions_response(response.text)
            return result.get("questions", [])
        except Exception as e:
            print(f"Error generating questions: {e}")
            return []

    def _parse_questions_response(self, response_text: str) -> Dict:
        """Parses the response from Gemini with questions"""
        try:
            start = response_text.find('{')
            end = response_text.rfind('}') + 1
            if start >= 0 and end > start:
                json_str = response_text[start:end]
                return json.loads(json_str)
            else:
                return {"questions": []}
        except Exception as e:
            print(f"Error parsing questions: {e}")
            return {"questions": []}

    async def analyze_case(self, case_data: Dict, participants: List[Dict], evidence: List[Dict],
                           bot: Bot = None) -> Dict:
        """
        Basic case analysis (JSON with facts, violations, decision).
        """
        messages = await self._build_multimodal_prompt(
            "You are an AI judge. Conduct a case analysis and return JSON in English.",
            case_data, participants, evidence, bot
        )
        try:
            response = self.model.generate_content(messages)
            analysis = self._parse_analysis_response(response.text)
            return analysis
        except Exception as e:
            return {
                "error": f"Analysis error: {str(e)}",
                "established_facts": [],
                "violations": [],
                "decision": "Unable to make a decision due to technical error",
                "verdict": {},
                "additional_questions": []
            }

    async def generate_reasoning(self, case_data: Dict, participants: List[Dict], evidence: List[Dict],
                                 bot: Bot = None) -> str:
        """
        Generation of reasoning text only.
        """
        messages = await self._build_multimodal_prompt(
            "You are an AI judge. Formulate only the reasoning for the decision in English (plain text).",
            case_data, participants, evidence, bot
        )
        try:
            response = self.model.generate_content(messages)
            return response.text.strip()
        except Exception as e:
            return f"Failed to generate reasoning due to error: {str(e)}"

    async def generate_full_decision(
            self,
            case_data: Dict,
            participants: List[Dict],
            evidence: List[Dict],
            bot: Bot = None,
            no_evidence: bool = False
    ) -> Dict:
        """
        Generation of full ruling and decision (JSON).
        """
        raw_amount = case_data.get('claim_amount')
        if raw_amount is None:
            claim_amount_text = "not specified"
        else:
            formatted = f"{float(raw_amount):,.8f}".rstrip('0').rstrip('.').strip()
            claim_amount_text = f"{formatted} USD" if formatted != "0" else "0 USD"

        instruction = f"""You are an AI judge. You must issue a final, legally sound verdict IN ENGLISH.

        CRITICAL: The plaintiff filed a claim for {claim_amount_text}.
        You MUST mention this exact amount in the "decision" field, for example:
        "The plaintiff filed a claim for the amount of {claim_amount_text}..."
        Even if the claim is rejected — still write the original amount.

        Consider ALL provided material evidence, including:
        - Images and documents
        - Chat history (EXTREMELY IMPORTANT - analyze the chronology, context, and content of messages)
        - Text arguments
        - Answers to AI questions

        Pay SPECIAL attention to:
        - Chat correspondence (type 'chat_history') — this is PRIMARY evidence showing actual communication between parties
        - Answers to AI questions (type 'ai_response')
        - The chronology of events as shown in chat messages
        - Contradictions or confirmations between chat history and other evidence

        CHAT HISTORY ANALYSIS REQUIREMENTS:
        1. Carefully read all forwarded messages with dates and authors
        2. Identify key statements, agreements, or disputes in the chat
        3. Note any contradictions between chat history and parties' claims
        4. Use specific quotes from chat messages in your reasoning
        5. Consider the timeline of events as shown in messages

        ALL TEXT MUST BE IN ENGLISH.

        Return STRICTLY valid JSON in this format:
        {{
          "established_facts": [...],
          "violations": [...],
          "decision": "Full verdict text IN ENGLISH, mentioning the claim amount {claim_amount_text} and referencing specific chat messages if relevant",
          "verdict": {{
              "claim_granted": true/false,
              "amount_awarded": number,
              "court_costs": number
          }},
          "winner": "plaintiff" | "defendant" | "draw",
          "reasoning": "Detailed reasoning IN ENGLISH with specific references to chat history..."
        }}"""

        if no_evidence:
            instruction += "\n⚠️ Attention: no evidence provided. The decision must be made based solely on the parties' arguments."

        instruction += """
    Return JSON strictly in the format (ALL TEXT IN ENGLISH):
    {
      "established_facts": ["fact1 (supported by chat message from [date])", "fact2"],
      "violations": ["violation1", "violation2"],
      "decision": "Text of the final decision IN ENGLISH with references to chat evidence",
      "verdict": {
          "claim_granted": true/false,
          "amount_awarded": number (amount awarded to plaintiff),
          "court_costs": number
      },
      "winner": "plaintiff" or "defendant" or "draw",
      "reasoning": "Detailed reasoning IN ENGLISH citing specific chat messages and their dates"
    }

    IMPORTANT about the "winner" field:
    - "plaintiff" - if the decision is in favor of the plaintiff (claim fully or partially satisfied)
    - "defendant" - if the decision is in favor of the defendant (claim denied)
    - "draw" - if both parties are partially right (compromise decision)

    REMEMBER: 
    - All text content must be in English language
    - Chat history is PRIMARY evidence and must be thoroughly analyzed
    - Reference specific messages from chat history in your reasoning
    """

        messages = await self._build_multimodal_prompt(
            instruction, case_data, participants, evidence, bot
        )

        try:
            response = self.model.generate_content(messages)
            decision_data = self._parse_analysis_response(response.text)

            # IMPORTANT: Determine winner if AI didn't specify
            if 'winner' not in decision_data or not decision_data['winner']:
                decision_data['winner'] = self._determine_winner(decision_data)

            return decision_data

        except Exception as e:
            return {
                "error": f"Error generating decision: {str(e)}",
                "established_facts": [ev.get("description", "") for ev in evidence],
                "violations": [],
                "decision": "Failed to make decision due to error",
                "verdict": {
                    "claim_granted": False,
                    "amount_awarded": 0,
                    "court_costs": 0
                },
                "winner": "defendant",
                "reasoning": ""
            }

    def _determine_winner(self, decision_data: Dict) -> str:
        """
        Determines the winner based on decision data
        Used as fallback if AI didn't specify winner
        """
        verdict = decision_data.get('verdict', {})
        claim_granted = verdict.get('claim_granted', False)
        amount_awarded = verdict.get('amount_awarded', 0)

        if claim_granted and amount_awarded > 0:
            return "plaintiff"
        elif claim_granted:
            return "plaintiff"
        else:
            return "defendant"

    async def _download_telegram_file(self, bot: Bot, file_id: str) -> bytes:
        """Downloads a file from Telegram by file_id"""
        try:
            file_info: File = await bot.get_file(file_id)
            file_bytes = io.BytesIO()
            await bot.download_file(file_info.file_path, file_bytes)
            return file_bytes.getvalue()
        except Exception as e:
            print(f"Error downloading file {file_id}: {e}")
            return b""

    async def _extract_text_from_pdf(self, file_bytes: bytes) -> str:
        """Extracts text from PDF"""
        try:
            pdf_file = io.BytesIO(file_bytes)
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
            return text.strip()
        except Exception as e:
            return f"Error reading PDF: {e}"

    async def _extract_text_from_docx(self, file_bytes: bytes) -> str:
        """Extracts text from DOCX"""
        try:
            doc_file = io.BytesIO(file_bytes)
            doc = Document(doc_file)
            text = ""
            for paragraph in doc.paragraphs:
                text += paragraph.text + "\n"
            return text.strip()
        except Exception as e:
            return f"Error reading DOCX: {e}"

    async def _extract_text_from_document(self, file_bytes: bytes, filename: str) -> str:
        """Universal function for extracting text from documents"""
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
                    return "Error decoding text file"
        else:
            return f"Unsupported document format: {filename}"

    def _is_image(self, filename: str) -> bool:
        """Checks if the file is an image"""
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif', '.svg']
        return any(filename.lower().endswith(ext) for ext in image_extensions)

    async def _build_multimodal_prompt(
            self, task_instruction: str, case_data: Dict, participants: List[Dict], evidence: List[Dict],
            bot: Bot = None
    ) -> List[Union[str, Dict]]:
        """
        Forming multimodal input (text + images + document contents).
        """

        raw_amount = case_data.get('claim_amount')

        if raw_amount is None or raw_amount == 'not specified':
            claim_text = "not specified"
        else:
            claim_text = f"{float(raw_amount):,.8f}".rstrip('0').rstrip('.') + " USD"
            if claim_text.endswith('.'):
                claim_text = claim_text[:-1] + " USD"

        # Separate chat history from other evidence
        chat_history = [ev for ev in evidence if ev.get("type") == "chat_history"]
        other_evidence = [ev for ev in evidence if ev.get("type") != "chat_history"]

        base_prompt = f"""
    {task_instruction}

    Case number: {case_data.get('case_number')}
    Subject of dispute: {case_data.get('topic')}
    Category: {case_data.get('category')}
    Claim amount: {claim_text}
    Claim reason: {case_data.get('claim_reason', 'not specified')}

    Participants:
    {self._format_participants(participants)}
    """

        messages: List[Union[str, Dict]] = [base_prompt]

        # Add chat history first with special emphasis
        if chat_history:
            messages.append("\n" + "=" * 80 + "\n")
            messages.append("🔴 CRITICAL EVIDENCE: CHAT HISTORY (PRIMARY SOURCE)\n")
            messages.append("=" * 80 + "\n")
            messages.append("This is actual communication between the parties. Analyze carefully:\n\n")

            for i, ev in enumerate(chat_history, 1):
                role_text = "Plaintiff" if ev.get("role") == "plaintiff" else "Defendant"
                content = ev.get('content', ev.get('description', ''))

                if content and content.strip():
                    messages.append(
                        f"\n📱 CHAT HISTORY #{i} (Provided by {role_text}):\n"
                        f"{'-' * 80}\n"
                        f"{content}\n"
                        f"{'-' * 80}\n"
                        f"[This chat correspondence is PRIMARY EVIDENCE. Extract key facts, dates, agreements, and disputes from these messages.]\n\n"
                    )

            messages.append("=" * 80 + "\n")
            messages.append("END OF CHAT HISTORY\n")
            messages.append("=" * 80 + "\n\n")

        # Then add other evidence
        messages.append("Additional Evidence and Arguments:\n\n")

        for i, ev in enumerate(other_evidence, 1):
            role_text = "Plaintiff" if ev.get("role") == "plaintiff" else "Defendant"

            if ev["type"] == "text":
                messages.append(f"\n{i}. {role_text} - Argument:\n{ev.get('content', ev.get('description', ''))}\n")

            elif ev["type"] == "ai_response":
                messages.append(
                    f"\n{i}. {role_text} - Answer to AI question:\n{ev.get('content', ev.get('description', ''))}\n")

            elif ev["type"] == "photo" and bot and ev.get("file_path"):
                try:
                    file_bytes = await self._download_telegram_file(bot, ev["file_path"])
                    if file_bytes:
                        mime_type = "image/jpeg"
                        if len(file_bytes) >= 4:
                            if file_bytes[:4] == b'\x89PNG':
                                mime_type = "image/png"
                            elif file_bytes[:4] == b'RIFF' and file_bytes[8:12] == b'WEBP':
                                mime_type = "image/webp"
                            elif file_bytes[:3] == b'GIF':
                                mime_type = "image/gif"

                        messages.append({
                            "mime_type": mime_type,
                            "data": base64.b64encode(file_bytes).decode()
                        })
                        caption = ev.get('content', 'Photo evidence')
                        messages.append(f"\n{i}. {role_text} - Image: {caption}\n")
                    else:
                        messages.append(f"\n{i}. {role_text} - [Error loading image]\n")
                except Exception as e:
                    messages.append(f"\n{i}. {role_text} - [Error processing image: {e}]\n")

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
                            mime_type = "image/jpeg"
                            if filename.lower().endswith('.png'):
                                mime_type = "image/png"
                            elif filename.lower().endswith('.webp'):
                                mime_type = "image/webp"
                            elif filename.lower().endswith('.gif'):
                                mime_type = "image/gif"

                            messages.append({
                                "mime_type": mime_type,
                                "data": base64.b64encode(file_bytes).decode()
                            })
                            caption = ev.get('content', 'Image (document)')
                            messages.append(f"\n{i}. {role_text} - Image-document: {caption}\n")
                        else:
                            extracted_text = await self._extract_text_from_document(file_bytes, filename)
                            messages.append(f"\n{i}. {role_text} - Document ({filename}):\n{extracted_text}\n")
                    else:
                        messages.append(f"\n{i}. {role_text} - [Error loading document]\n")
                except Exception as e:
                    messages.append(f"\n{i}. {role_text} - [Error processing document: {e}]\n")

            elif ev["type"] == "video" and ev.get("file_path"):
                caption = ev.get('content', 'Video evidence')
                messages.append(
                    f"\n{i}. {role_text} - Video: {caption}\n[Video content is not automatically analyzed]\n")

            elif ev["type"] == "audio" and ev.get("file_path"):
                caption = ev.get('content', 'Audio evidence')
                messages.append(
                    f"\n{i}. {role_text} - Audio: {caption}\n[Audio content is not automatically analyzed]\n")

            else:
                description = ev.get('content', ev.get('description', 'Evidence without description'))
                messages.append(f"\n{i}. {role_text} - {ev['type']}: {description}\n")

        return messages

    def _format_participants(self, participants: List[Dict]) -> str:
        result = []
        for p in participants:
            role_en = "Plaintiff" if p['role'] == 'plaintiff' else "Defendant"
            username = p.get('username', 'unknown')
            result.append(f"{role_en}: @{username}")
        return ", ".join(result)

    def _parse_analysis_response(self, response_text: str) -> Dict:
        """Parses response from Gemini, extracting JSON"""
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
                        "claim_granted": False,
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
                    "claim_granted": False,
                    "amount_awarded": 0,
                    "court_costs": 0
                },
                "reasoning": response_text,
                "parse_error": str(e)
            }


gemini_service = GeminiService()