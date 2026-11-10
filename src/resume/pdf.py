"""Настоящий PDF из сохранённого резюме: кириллица, переносы и безопасный текст."""

from html import escape
from io import BytesIO
from pathlib import Path
from threading import Lock

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

from src.resume.schemas import Content

font_lock = Lock()


def register_fonts() -> None:
    with font_lock:
        if "ResumeHub" not in pdfmetrics.getRegisteredFontNames():
            fonts = Path(__file__).with_name("fonts")
            pdfmetrics.registerFont(
                TTFont("ResumeHub", str(fonts / "NotoSans-Regular.ttf"))
            )
            pdfmetrics.registerFont(
                TTFont("ResumeHubBold", str(fonts / "NotoSans-Bold.ttf"))
            )


def render_resume_pdf(content: Content, title: str) -> bytes:
    """Не загружает ссылки и не интерпретирует пользовательский текст как разметку."""
    register_fonts()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=48,
        leftMargin=48,
        topMargin=42,
        bottomMargin=44,
        title=title,
        author=content.contacts.fullName,
    )
    body = ParagraphStyle(
        "body",
        fontName="ResumeHub",
        fontSize=10,
        leading=15,
        spaceAfter=7,
        alignment=TA_LEFT,
        splitLongWords=True,
    )
    heading = ParagraphStyle(
        "heading",
        parent=body,
        fontName="ResumeHubBold",
        fontSize=13,
        leading=18,
        spaceBefore=15,
        spaceAfter=7,
        keepWithNext=True,
    )
    name = ParagraphStyle(
        "name", parent=heading, fontSize=22, leading=28, spaceBefore=0
    )
    subheading = ParagraphStyle(
        "entry",
        parent=body,
        fontName="ResumeHubBold",
        keepWithNext=True,
        spaceBefore=7,
        spaceAfter=3,
    )
    items: list[Flowable] = []

    def paragraph(text: str, style: ParagraphStyle = body) -> None:
        if text.strip():
            clean = "".join(
                char for char in text.strip() if char >= " " or char in "\n\t"
            )
            items.append(Paragraph(escape(clean).replace("\n", "<br/>"), style))

    def section(label: str, text: str) -> None:
        if text.strip():
            paragraph(label, heading)
            paragraph(text)

    paragraph(content.contacts.fullName or title, name)
    paragraph(content.contacts.position, subheading)
    paragraph(
        " | ".join(
            value
            for value in (content.contacts.email, content.contacts.location)
            if value.strip()
        )
    )
    items.append(Spacer(1, 6))
    section("О себе", content.about)
    experience = [
        entry
        for entry in content.experience
        if any(
            value.strip()
            for value in (entry.company, entry.role, entry.period, entry.achievements)
        )
    ]
    if experience:
        paragraph("Опыт работы", heading)
        for entry in experience:
            paragraph(
                " - ".join(
                    value for value in (entry.role, entry.company) if value.strip()
                ),
                subheading,
            )
            paragraph(entry.period)
            paragraph(entry.achievements)
    projects = [
        entry
        for entry in content.projects
        if any(
            value.strip()
            for value in (entry.name, entry.technologies, entry.contribution)
        )
    ]
    if projects:
        paragraph("Проекты", heading)
        for entry in projects:
            paragraph(entry.name, subheading)
            paragraph(entry.technologies)
            paragraph(entry.contribution)
    section(
        "Навыки",
        ", ".join(content.skillItems) if content.skillItems else content.skills,
    )
    if content.educationEntries:
        entries = [
            entry
            for entry in content.educationEntries
            if any(
                value.strip()
                for value in (entry.institution, entry.specialty, entry.period)
            )
        ]
        if entries:
            paragraph("Образование", heading)
            for entry in entries:
                paragraph(entry.institution, subheading)
                paragraph(
                    " | ".join(
                        value
                        for value in (entry.specialty, entry.period)
                        if value.strip()
                    )
                )
    else:
        section("Образование", content.education)
    if content.additionalEntries:
        entries = [
            entry
            for entry in content.additionalEntries
            if entry.title.strip() or entry.description.strip()
        ]
        if entries:
            paragraph("Дополнительная информация", heading)
            for entry in entries:
                paragraph(entry.title, subheading)
                paragraph(entry.description)
    else:
        section("Дополнительная информация", content.additional)

    def footer(canvas: Canvas, doc: BaseDocTemplate) -> None:
        canvas.saveState()
        canvas.setFont("ResumeHub", 9)
        canvas.setFillColor(colors.HexColor("#647077"))
        canvas.drawRightString(A4[0] - 48, 24, str(doc.page))
        canvas.restoreState()

    document.build(items, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
