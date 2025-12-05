from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
import os
import io
from datetime import datetime
from typing import Dict, List, Any


class PDFGenerator:
    def __init__(self):
        self.font_name = self.setup_fonts()
        self.styles = self.create_custom_styles()

    def setup_fonts(self) -> str:
        """Настройка шрифтов для поддержки кириллицы"""
        font_paths = [
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/System/Library/Fonts/Supplemental/Arial.ttf',
            'C:/Windows/Fonts/arial.ttf',
        ]

        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    pdfmetrics.registerFont(TTFont('CustomFont', font_path))
                    return 'CustomFont'
                except Exception as e:
                    print(f"Ошибка загрузки шрифта {font_path}: {e}")

        print("Системный шрифт не найден, используется Helvetica (без кириллицы)")
        return 'Helvetica'

    def create_custom_styles(self):
        styles = getSampleStyleSheet()

        styles.add(ParagraphStyle(
            'Custom',
            parent=styles['Normal'],
            fontSize=11,
            spaceAfter=6,
            alignment=TA_JUSTIFY,
            fontName=self.font_name
        ))

        styles.add(ParagraphStyle(
            'CustomTitle',
            parent=styles['Title'],
            fontSize=18,
            spaceAfter=20,
            alignment=TA_CENTER,
            textColor=colors.black,
            fontName=self.font_name
        ))

        styles.add(ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=14,
            spaceAfter=12,
            spaceBefore=12,
            textColor=colors.black,
            fontName=self.font_name
        ))

        styles.add(ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=11,
            spaceAfter=6,
            alignment=TA_JUSTIFY,
            fontName=self.font_name
        ))

        styles.add(ParagraphStyle(
            'CustomBullet',
            parent=styles['Normal'],
            fontSize=11,
            spaceAfter=3,
            leftIndent=20,
            fontName=self.font_name
        ))

        return styles

    def safe_btc(self, value: Any) -> str:
        """Безопасное форматирование BTC суммы (включая None и 0)"""
        if value is None or value == "not specified":
            return "not specified"
        try:
            f = float(value)
            if f == 0:
                return "0 ETF"
            return f"{f:.8f}".rstrip('0').rstrip('.') + " ETF"
        except (ValueError, TypeError):
            return "not specified"

    def generate_verdict_pdf(self, case_data: Dict, decision: Dict,
                             participants: List[Dict], evidence: List[Dict]) -> bytes:
        """Генерация PDF документа с вердиктом"""

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm
        )

        story = []

        # Заголовок
        story.append(Paragraph("AI JUDGE'S DECISION", self.styles['CustomTitle']))
        story.append(Spacer(1, 0.5 * cm))

        # Дело
        case_number = case_data.get('case_number', 'N/A')
        story.append(Paragraph(f"in case No. {case_number}", self.styles['CustomHeading']))

        current_date = datetime.now().strftime("%d.%m.%Y")
        story.append(Paragraph(f"Date: {current_date}", self.styles['Custom']))
        story.append(Spacer(1, 0.3 * cm))

        subject = f"<b>Subject of dispute:</b> {case_data.get('claim_reason', 'Not specified')}"
        story.append(Paragraph(subject, self.styles['Custom']))
        story.append(Spacer(1, 0.3 * cm))

        # Сумма иска — теперь безопасно
        claim_amount_str = self.safe_btc(case_data.get('claim_amount'))
        story.append(Paragraph(f"<b>Claim Amount:</b> {claim_amount_str}", self.styles['Custom']))
        story.append(Spacer(1, 0.5 * cm))

        # Участники
        story.append(Paragraph("COMPOSITION OF THE ARBITRATION:", self.styles['CustomHeading']))
        if participants:
            role_map = {
                'plaintiff': 'Plaintiff',
                'defendant': 'Defendant',
                'judge': 'Judge',
                'witness': 'Witness'
            }
            participants_data = [[
                role_map.get(p.get('role', '').lower(), p.get('role', 'Unknown')),
                f"@{p.get('username', 'unknown')}"
            ] for p in participants]

            participants_table = Table(participants_data, colWidths=[5 * cm, 7 * cm])
            participants_table.setStyle(TableStyle([
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, -1), self.font_name),
                ('FONTSIZE', (0, 0), (-1, -1), 11),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            story.append(participants_table)
        else:
            story.append(Paragraph("Participants not listed", self.styles['Custom']))
        story.append(Spacer(1, 0.5 * cm))

        # Факты
        story.append(Paragraph("ESTABLISHED FACTS:", self.styles['CustomHeading']))
        facts = decision.get('established_facts', [])
        if facts:
            for i, fact in enumerate(facts, 1):
                story.append(Paragraph(f"{i}. {fact}", self.styles['CustomBullet']))
        else:
            story.append(Paragraph("The facts have not been established.", self.styles['Custom']))
        story.append(Spacer(1, 0.3 * cm))

        # Нарушения
        story.append(Paragraph("VIOLATIONS IDENTIFIED:", self.styles['CustomHeading']))
        violations = decision.get('violations', [])
        if violations:
            for i, violation in enumerate(violations, 1):
                story.append(Paragraph(f"{i}. {violation}", self.styles['CustomBullet']))
        else:
            story.append(Paragraph("No violations were found", self.styles['Custom']))
        story.append(Spacer(1, 0.5 * cm))

        # Решение
        story.append(Paragraph("SOLUTION:", self.styles['CustomHeading']))
        decision_text = decision.get('decision', 'The decision has not been made')
        story.append(Paragraph(decision_text, self.styles['Custom']))
        story.append(Spacer(1, 0.5 * cm))

        # Постановил
        story.append(Paragraph("DECIDED:", self.styles['CustomHeading']))

        verdict = decision.get('verdict', {})
        awarded_raw = verdict.get('amount_awarded')
        awarded_str = self.safe_btc(awarded_raw)
        winner = decision.get('winner', 'defendant')

        # Победитель
        if winner == "plaintiff":
            winner_text = "The decision was made in favor of the PLAINTIFF"
        elif winner == "defendant":
            winner_text = "The decision was made in favor of the DEFENDANT"
        else:
            winner_text = "A compromise decision was made"

        story.append(Paragraph(f"<b>{winner_text}</b>", self.styles['Custom']))
        story.append(Spacer(1, 0.3 * cm))

        # Логика по сумме
        claim_amount_raw = case_data.get('claim_amount')

        if verdict.get('claim_satisfied') and awarded_raw not in (None, 0):
            if claim_amount_raw is not None:
                try:
                    if awarded_raw < float(claim_amount_raw):
                        story.append(Paragraph(
                            f"1. Satisfy the claim partially, recover {awarded_str}.",
                            self.styles['CustomBullet']
                        ))
                    else:
                        story.append(Paragraph(
                            f"1. Satisfy the claim in full for the amount {awarded_str}.",
                            self.styles['CustomBullet']
                        ))
                except:
                    story.append(Paragraph(
                        f"1. Satisfy the claim for the amount {awarded_str}.",
                        self.styles['CustomBullet']
                    ))
            else:
                story.append(Paragraph(
                    f"1. Satisfy the claim for the amount {awarded_str}.",
                    self.styles['CustomBullet']
                ))
        else:
            story.append(Paragraph("1. The claim is dismissed.", self.styles['CustomBullet']))

        story.append(Spacer(1, 0.3 * cm))

        # Обоснование
        if decision.get('reasoning'):
            story.append(Paragraph("RATIONALE:", self.styles['CustomHeading']))
            story.append(Paragraph(decision['reasoning'], self.styles['Custom']))

        # Подпись
        story.append(Spacer(1, 1 * cm))
        story.append(Paragraph("AI-Judge", self.styles['Custom']))
        story.append(Paragraph(f"The document was generated automatically. {current_date}", self.styles['Custom']))

        doc.build(story)
        buffer.seek(0)
        return buffer.getvalue()

    def save_pdf_to_file(self, pdf_data: bytes, filename: str) -> str:
        docs_dir = "documents"
        os.makedirs(docs_dir, exist_ok=True)
        filepath = os.path.join(docs_dir, filename)
        with open(filepath, 'wb') as f:
            f.write(pdf_data)
        return filepath


pdf_generator = PDFGenerator()