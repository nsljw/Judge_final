from reportlab.lib.pagesizes import A4, letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
import os
import io
from datetime import datetime
from typing import Dict, List, Optional


class PDFGenerator:
    def __init__(self):
        self.setup_fonts()
        self.styles = self.create_custom_styles()

    def setup_fonts(self):
        """Настройка шрифтов для поддержки кириллицы"""
        try:
            font_paths = [
                '/System/Library/Fonts/Arial.ttf',  # macOS
                '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',  # Linux
                'C:/Windows/Fonts/arial.ttf',  # Windows
            ]

            font_found = False
            for font_path in font_paths:
                if os.path.exists(font_path):
                    pdfmetrics.registerFont(TTFont('CustomFont', font_path))
                    font_found = True
                    break

            if not font_found:
                print("Системный шрифт не найден, используется встроенный шрифт")

        except Exception as e:
            print(f"Ошибка настройки шрифтов: {e}")

    def create_custom_styles(self):
        """Создание пользовательских стилей"""
        styles = getSampleStyleSheet()
        font_available = 'CustomFont' in pdfmetrics.getRegisteredFontNames()

        styles.add(ParagraphStyle(
            'CustomTitle',
            parent=styles['Title'],
            fontSize=18,
            spaceAfter=20,
            alignment=TA_CENTER,
            textColor=colors.black,
            fontName='CustomFont' if font_available else 'Helvetica-Bold'
        ))

        styles.add(ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=14,
            spaceAfter=12,
            spaceBefore=12,
            textColor=colors.black,
            fontName='CustomFont' if font_available else 'Helvetica-Bold'
        ))

        styles.add(ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=11,
            spaceAfter=6,
            alignment=TA_JUSTIFY,
            fontName='CustomFont' if font_available else 'Helvetica'
        ))

        styles.add(ParagraphStyle(
            'CustomBullet',
            parent=styles['Normal'],
            fontSize=11,
            spaceAfter=3,
            leftIndent=20,
            fontName='CustomFont' if font_available else 'Helvetica'
        ))

        return styles

    def generate_verdict_pdf(self, case_data: Dict, decision: Dict,
                             participants: List[Dict], evidence: List[Dict]) -> bytes:
        """
        Генерация PDF документа с вердиктом.
        decision = полный результат от GeminiService.generate_full_decision()
        """

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

        title = Paragraph("РЕШЕНИЕ ИИ-СУДЬИ", self.styles['CustomTitle'])
        story.append(title)
        story.append(Spacer(1, 0.5 * cm))

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

        story.append(Paragraph("СОСТАВ АРБИТРАЖА:", self.styles['CustomHeading']))
        if participants:
            participants_data = [[p.get('role', 'Неизвестно').title(),
                                  f"@{p.get('username', 'неизвестно')}"]
                                 for p in participants]

            participants_table = Table(participants_data, colWidths=[5 * cm, 7 * cm])
            participants_table.setStyle(TableStyle([
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            story.append(participants_table)
        else:
            story.append(Paragraph("Участники не указаны", self.styles['CustomNormal']))
        story.append(Spacer(1, 0.5 * cm))

        story.append(Paragraph("УСТАНОВЛЕННЫЕ ФАКТЫ:", self.styles['CustomHeading']))
        for i, fact in enumerate(decision.get('established_facts', []), 1):
            story.append(Paragraph(f"{i}. {fact}", self.styles['CustomBullet']))
        if not decision.get('established_facts'):
            story.append(Paragraph("Факты не установлены", self.styles['CustomNormal']))
        story.append(Spacer(1, 0.3 * cm))

        story.append(Paragraph("ВЫЯВЛЕННЫЕ НАРУШЕНИЯ:", self.styles['CustomHeading']))
        for i, violation in enumerate(decision.get('violations', []), 1):
            story.append(Paragraph(f"{i}. {violation}", self.styles['CustomBullet']))
        if not decision.get('violations'):
            story.append(Paragraph("Нарушения не выявлены", self.styles['CustomNormal']))
        story.append(Spacer(1, 0.5 * cm))

        story.append(Paragraph("РЕШЕНИЕ:", self.styles['CustomHeading']))
        story.append(Paragraph(decision.get('decision', 'Решение не вынесено'), self.styles['CustomNormal']))
        story.append(Spacer(1, 0.5 * cm))

        # === ФИКС: используем amount_awarded ===
        story.append(Paragraph("ПОСТАНОВИЛ:", self.styles['CustomHeading']))

        verdict = decision.get('verdict', {})
        claim_amount = case_data.get('claim_amount', 0)
        awarded = verdict.get('awarded') or 0  # если None → станет 0

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

        if decision.get('reasoning'):
            story.append(Paragraph("ОБОСНОВАНИЕ:", self.styles['CustomHeading']))
            story.append(Paragraph(decision['reasoning'], self.styles['CustomNormal']))

        story.append(Spacer(1, 1*cm))
        story.append(Paragraph("ИИ-Судья", self.styles['CustomNormal']))
        story.append(Paragraph(f"Документ сгенерирован автоматически {current_date}", self.styles['CustomNormal']))

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
