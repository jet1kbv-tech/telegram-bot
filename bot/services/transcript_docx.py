from __future__ import annotations

import re
import zipfile
from html import escape
from datetime import datetime
from pathlib import Path

from bot.services.transcript_processing import TranscriptTurn


def safe_name(value: str, fallback: str = "audio") -> str:
    stem = Path(value).stem
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яЁё_-]+", "_", stem).strip("_-")[:60]
    return cleaned or fallback


def output_filename(original: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"Расшифровка_{safe_name(original)}_{now:%Y-%m-%d}.docx"


def duration_text(seconds: float) -> str:
    value = max(0, int(seconds))
    return f"{value // 3600:02d}:{value % 3600 // 60:02d}:{value % 60:02d}"


def create_docx(path: Path, *, original_filename: str, processed_at: datetime, duration_seconds: float,
                speaker_count: int, turns: list[TranscriptTurn]) -> None:
    paragraphs = [("Расшифровка", True), (f"Файл: {safe_name(original_filename)}", False),
                  (f"Дата обработки: {processed_at:%d.%m.%Y %H:%M}", False),
                  (f"Длительность: {duration_text(duration_seconds)}", False),
                  (f"Спикеров: {speaker_count}", False)]
    for turn in turns:
        paragraphs.extend([(f"[{turn.timestamp}] {turn.speaker}", True), (turn.text, False)])
    body = "".join(_paragraph(text, bold) for text, bold in paragraphs)
    document_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    f'<w:body>{body}<w:sectPr/></w:body></w:document>')
    content_types = ('<?xml version="1.0" encoding="UTF-8"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '</Types>')
    relationships = ('<?xml version="1.0" encoding="UTF-8"?>'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                     '</Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document_xml)


def _paragraph(text: str, bold: bool) -> str:
    properties = '<w:rPr><w:b/><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/></w:rPr>' if bold else '<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/></w:rPr>'
    return f'<w:p><w:pPr><w:spacing w:after="160"/></w:pPr><w:r>{properties}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
