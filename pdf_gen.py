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
from typing import Dict, List


class PDFGenerator:
    def __init__(self):
        self.font_name = self.setup_fonts()
        self.styles = self.create_custom_styles()

    def setup_fonts(self) -> str:
        """Настройка шрифтов для поддержки кириллицы"""
        font_paths = [
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',  # Linux
            '/System/Library/Fonts/Supplemental/Arial.ttf',     # macOS
            'C:/Windows/Fonts/arial.ttf',                       # Windows
        ]

        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    pdfmetrics.registerFont(TTFont('CustomFont', font_path))
                    return 'CustomFont'
                except Exception as e:
                    print(f"Ошибка загрузки шрифта {font_path}: {e}")

        print("⚠️ Системный шрифт не найден, используется Helvetica (без кириллицы)")
        return 'Helvetica'

    def create_custom_styles(self):
        """Создание пользовательских стилей"""
        styles = getSampleStyleSheet()

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
        story.append(Paragraph("РЕШЕНИЕ ИИ-СУДЬИ", self.styles['CustomTitle']))
        story.append(Spacer(1, 0.5 * cm))

        # Дело
        case_info = f"по делу № {case_data.get('case_number', 'Н/Д')}"
        story.append(Paragraph(case_info, self.styles['CustomHeading']))

        current_date = datetime.now().strftime("%d.%m.%Y")
        story.append(Paragraph(f"Дата: {current_date}", self.styles['CustomNormal']))
        story.append(Spacer(1, 0.3 * cm))

        subject = f"<b>Предмет спора:</b> {case_data.get('subject', 'Не указан')}"
        story.append(Paragraph(subject, self.styles['CustomNormal']))
        story.append(Spacer(1, 0.3 * cm))

        amount = case_data.get('claim_amount', 0)
        amount_text = f"<b>Сумма иска:</b> {amount:,.0f} USD." if amount else "<b>Сумма иска:</b> Не указана"
        story.append(Paragraph(amount_text, self.styles['CustomNormal']))
        story.append(Spacer(1, 0.5 * cm))

        # Участники
        story.append(Paragraph("СОСТАВ АРБИТРАЖА:", self.styles['CustomHeading']))
        if participants:
            role_map = {
                'plaintiff': 'Истец',
                'defendant': 'Ответчик',
                'judge': 'Судья',
                'witness': 'Свидетель'
            }
            participants_data = [[role_map.get(p.get('role', '').lower(), p.get('role', 'Неизвестно')),
                                  f"@{p.get('username', 'неизвестно')}"]
                                 for p in participants]

            participants_table = Table(participants_data, colWidths=[5 * cm, 7 * cm])
            participants_table.setStyle(TableStyle([
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, -1), self.font_name),
                ('FONTSIZE', (0, 0), (-1, -1), 11),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            story.append(participants_table)
        else:
            story.append(Paragraph("Участники не указаны", self.styles['CustomNormal']))
        story.append(Spacer(1, 0.5 * cm))

        # Факты
        story.append(Paragraph("УСТАНОВЛЕННЫЕ ФАКТЫ:", self.styles['CustomHeading']))
        for i, fact in enumerate(decision.get('established_facts', []), 1):
            story.append(Paragraph(f"{i}. {fact}", self.styles['CustomBullet']))
        if not decision.get('established_facts'):
            story.append(Paragraph("Факты не установлены", self.styles['CustomNormal']))
        story.append(Spacer(1, 0.3 * cm))

        # Нарушения
        story.append(Paragraph("ВЫЯВЛЕННЫЕ НАРУШЕНИЯ:", self.styles['CustomHeading']))
        for i, violation in enumerate(decision.get('violations', []), 1):
            story.append(Paragraph(f"{i}. {violation}", self.styles['CustomBullet']))
        if not decision.get('violations'):
            story.append(Paragraph("Нарушения не выявлены", self.styles['CustomNormal']))
        story.append(Spacer(1, 0.5 * cm))

        # Решение
        story.append(Paragraph("РЕШЕНИЕ:", self.styles['CustomHeading']))
        story.append(Paragraph(decision.get('decision', 'Решение не вынесено'), self.styles['CustomNormal']))
        story.append(Spacer(1, 0.5 * cm))

        # Постановил
        story.append(Paragraph("ПОСТАНОВИЛ:", self.styles['CustomHeading']))
        verdict = decision.get('verdict', {})
        claim_amount = case_data.get('claim_amount', 0)
        awarded = verdict.get('awarded') or 0

        if verdict.get('claim_satisfied') and awarded > 0:
            if awarded < claim_amount:
                story.append(Paragraph(
                    f"1. Удовлетворить иск частично, взыскать {awarded:,.0f} USD.",
                    self.styles['CustomBullet']
                ))
            else:
                story.append(Paragraph(
                    f"1. Удовлетворить иск полностью на сумму {awarded:,.0f} USD.",
                    self.styles['CustomBullet']
                ))
        else:
            story.append(Paragraph("1. В иске отказать.", self.styles['CustomBullet']))
        story.append(Spacer(1, 0.3 * cm))

        # Обоснование
        if decision.get('reasoning'):
            story.append(Paragraph("ОБОСНОВАНИЕ:", self.styles['CustomHeading']))
            story.append(Paragraph(decision['reasoning'], self.styles['CustomNormal']))

        # Подпись
        story.append(Spacer(1, 1 * cm))
        story.append(Paragraph("ИИ-Судья", self.styles['CustomNormal']))
        story.append(Paragraph(f"Документ сгенерирован автоматически {current_date}", self.styles['CustomNormal']))

        doc.build(story)
        buffer.seek(0)
        return buffer.getvalue()

    def save_pdf_to_file(self, pdf_data: bytes, filename: str) -> str:
        docs_dir = "documents"
        os.makedirs(docs_dir, exist_ok=True)
        filepath = os.path.join(docs_dir, filename,)
        with open(filepath, 'wb') as f:
            f.write(pdf_data)
        return filepath


pdf_generator = PDFGenerator()
