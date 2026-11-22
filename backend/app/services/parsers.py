import email
import email.parser
import email.policy
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.services.ocr import OCRService

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_LEGAL_HEADING_RE = re.compile(
    r"^(?:SECTION|ARTICLE|EXHIBIT|SCHEDULE|APPENDIX)\s+[0-9IVXLC]+(?:[.:]\s*.*)?$"
    r"|^[0-9]+\.\s+[A-Z][A-Z0-9\s,&/-]{3,}$"
)
_TRANSCRIPT_LINE_RE = re.compile(
    r"^(?:\[(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\]\s*)?"
    r"(?P<speaker>[A-Z][A-Z0-9\s._-]{1,28}|Q|A):\s+(?P<utterance>.+)$"
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(slots=True)
class ParsedSegment:
    """A logical paragraph/utterance extracted from a source document with provenance."""

    text: str
    page_number: int = 1
    paragraph_index: int = 1
    section_heading: str | None = None
    char_start: int = 0
    char_end: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedDocument:
    """Full parsed representation of an uploaded legal document."""

    segments: list[ParsedSegment]
    page_count: int
    word_count: int
    detected_document_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _is_heading(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    md_match = _MD_HEADING_RE.match(stripped)
    if md_match:
        return md_match.group(2).strip()[:255]
    if _LEGAL_HEADING_RE.match(stripped):
        return stripped[:255]
    if (
        len(stripped) <= 90
        and stripped.upper() == stripped
        and any(ch.isalpha() for ch in stripped)
        and not stripped.endswith(".")
    ):
        return stripped[:255]
    return None


def _split_paragraphs_with_offsets(
    text: str,
    *,
    page_number: int = 1,
    starting_paragraph_index: int = 1,
    base_char_offset: int = 0,
    initial_heading: str | None = None,
) -> tuple[list[ParsedSegment], int, str | None]:
    segments: list[ParsedSegment] = []
    current_heading = initial_heading
    para_idx = starting_paragraph_index

    # Split on blank lines while preserving character spans
    blocks = re.split(r"\r?\n\s*\r?\n", text)
    cursor = 0
    for block in blocks:
        clean_block = block.strip()
        if not clean_block:
            cursor += len(block) + 2
            continue

        pos = text.find(clean_block, cursor)
        if pos == -1:
            pos = cursor
        cursor = pos + len(clean_block)

        lines = [ln.strip() for ln in clean_block.splitlines() if ln.strip()]
        if lines:
            first_heading = _is_heading(lines[0])
            if first_heading:
                current_heading = first_heading

        segments.append(
            ParsedSegment(
                text=clean_block,
                page_number=page_number,
                paragraph_index=para_idx,
                section_heading=current_heading,
                char_start=base_char_offset + pos,
                char_end=base_char_offset + pos + len(clean_block),
            )
        )
        para_idx += 1

    return segments, para_idx, current_heading


def _parse_pdf(data: bytes, ocr_service: OCRService | None = None) -> ParsedDocument:
    ocr = ocr_service or OCRService(backend_mode="auto")
    reader = PdfReader(io.BytesIO(data))
    segments: list[ParsedSegment] = []
    para_idx = 1
    current_heading: str | None = None
    char_offset = 0
    scanned_pages_detected: list[int] = []
    ocr_pages: list[int] = []
    ocr_warnings: list[str] = []
    ocr_backend_used: str = ocr.backend_mode

    for page_num, page in enumerate(reader.pages, start=1):
        raw_page_text = page.extract_text() or ""
        if len(raw_page_text.strip()) >= ocr.min_chars_per_page:
            page_segments, para_idx, current_heading = _split_paragraphs_with_offsets(
                raw_page_text,
                page_number=page_num,
                starting_paragraph_index=para_idx,
                base_char_offset=char_offset,
                initial_heading=current_heading,
            )
            segments.extend(page_segments)
            char_offset += len(raw_page_text) + 1
            continue

        scanned_pages_detected.append(page_num)
        ocr_res = ocr.extract_pdf_page_text(page, page_num, data)
        ocr_backend_used = ocr_res.backend
        if ocr_res.applied and ocr_res.text.strip():
            ocr_pages.append(page_num)
            ocr_segments, para_idx, current_heading = _split_paragraphs_with_offsets(
                ocr_res.text,
                page_number=page_num,
                starting_paragraph_index=para_idx,
                base_char_offset=char_offset,
                initial_heading=current_heading,
            )
            for seg in ocr_segments:
                seg.metadata["source"] = "ocr"
                seg.metadata["ocr_backend"] = ocr_res.backend
            segments.extend(ocr_segments)
            char_offset += len(ocr_res.text) + 1
        else:
            if ocr_res.warning:
                ocr_warnings.append(ocr_res.warning)
            if raw_page_text.strip():
                page_segments, para_idx, current_heading = _split_paragraphs_with_offsets(
                    raw_page_text,
                    page_number=page_num,
                    starting_paragraph_index=para_idx,
                    base_char_offset=char_offset,
                    initial_heading=current_heading,
                )
                segments.extend(page_segments)
                char_offset += len(raw_page_text) + 1
            else:
                placeholder = f"[Scanned page {page_num} requires OCR — no embedded text layer]"
                segments.append(
                    ParsedSegment(
                        text=placeholder,
                        page_number=page_num,
                        paragraph_index=para_idx,
                        section_heading=current_heading,
                        char_start=char_offset,
                        char_end=char_offset + len(placeholder),
                        metadata={
                            "source": "scanned_unextracted",
                            "ocr_warning": ocr_res.warning or "OCR unavailable",
                        },
                    )
                )
                para_idx += 1

    page_count = max(1, len(reader.pages))
    if not segments:
        segments.append(
            ParsedSegment(
                text="[Empty or scanned PDF without embedded text layer]",
                page_number=1,
                paragraph_index=1,
                section_heading=None,
                char_start=0,
                char_end=0,
                metadata={"source": "scanned_unextracted"},
            )
        )
    word_count = sum(len(seg.text.split()) for seg in segments)
    ocr_required = len(scanned_pages_detected) > len(ocr_pages)
    return ParsedDocument(
        segments=segments,
        page_count=page_count,
        word_count=word_count,
        detected_document_type="general",
        metadata={
            "parser": "pypdf",
            "ocr_applied": len(ocr_pages) > 0,
            "ocr_required": ocr_required,
            "ocr_backend": ocr_backend_used,
            "ocr_pages": ocr_pages,
            "scanned_pages_detected": scanned_pages_detected,
            "ocr_warnings": ocr_warnings,
        },
    )


def _parse_docx_with_python_docx(data: bytes) -> ParsedDocument | None:
    try:
        from docx import Document as DocxReader
    except ImportError:
        return None

    doc = DocxReader(io.BytesIO(data))
    segments: list[ParsedSegment] = []
    current_heading: str | None = None
    char_offset = 0
    para_idx = 1

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style_name = (para.style.name if para.style else "") or ""
        if style_name.lower().startswith("heading"):
            current_heading = text[:255]
        elif heading := _is_heading(text):
            current_heading = heading

        segments.append(
            ParsedSegment(
                text=text,
                page_number=1,
                paragraph_index=para_idx,
                section_heading=current_heading,
                char_start=char_offset,
                char_end=char_offset + len(text),
            )
        )
        char_offset += len(text) + 2
        para_idx += 1

    word_count = sum(len(seg.text.split()) for seg in segments)
    return ParsedDocument(
        segments=segments,
        page_count=max(1, (word_count // 350) + 1),
        word_count=word_count,
        detected_document_type="general",
        metadata={"parser": "python-docx"},
    )


def _parse_docx_ooxml_fallback(data: bytes) -> ParsedDocument:
    """Pure-stdlib OOXML fallback reading word/document.xml inside .docx archives."""
    w_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    segments: list[ParsedSegment] = []
    current_heading: str | None = None
    char_offset = 0
    para_idx = 1

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if "word/document.xml" not in archive.namelist():
            raise ValueError("Invalid DOCX file: missing word/document.xml")
        xml_bytes = archive.read("word/document.xml")

    root = ET.fromstring(xml_bytes)
    for p_elem in root.iter(f"{w_ns}p"):
        texts = [t.text for t in p_elem.iter(f"{w_ns}t") if t.text]
        text = "".join(texts).strip()
        if not text:
            continue

        style_elem = p_elem.find(f"{w_ns}pPr/{w_ns}pStyle")
        style_val = style_elem.attrib.get(f"{w_ns}val", "") if style_elem is not None else ""
        if style_val.lower().startswith("heading"):
            current_heading = text[:255]
        elif heading := _is_heading(text):
            current_heading = heading

        segments.append(
            ParsedSegment(
                text=text,
                page_number=1,
                paragraph_index=para_idx,
                section_heading=current_heading,
                char_start=char_offset,
                char_end=char_offset + len(text),
            )
        )
        char_offset += len(text) + 2
        para_idx += 1

    word_count = sum(len(seg.text.split()) for seg in segments)
    return ParsedDocument(
        segments=segments,
        page_count=max(1, (word_count // 350) + 1),
        word_count=word_count,
        detected_document_type="general",
        metadata={"parser": "ooxml-stdlib"},
    )


def _parse_docx(data: bytes) -> ParsedDocument:
    parsed = _parse_docx_with_python_docx(data)
    if parsed is not None:
        return parsed
    return _parse_docx_ooxml_fallback(data)


def _extract_email_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        plain_parts: list[str] = []
        html_parts: list[str] = []
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            ctype = part.get_content_type()
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = _decode_text(bytes(payload))
            if ctype == "text/plain":
                plain_parts.append(text)
            elif ctype == "text/html":
                html_parts.append(_HTML_TAG_RE.sub(" ", text))
        return "\n\n".join(plain_parts or html_parts)
    payload = msg.get_payload(decode=True)
    if payload is not None:
        return _decode_text(bytes(payload))
    return str(msg.get_payload())


def _parse_email(data: bytes) -> ParsedDocument:
    msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(data)
    subject = str(msg.get("Subject") or "Email Correspondence").strip()
    sender = str(msg.get("From") or "").strip()
    recipient = str(msg.get("To") or "").strip()
    date_hdr = str(msg.get("Date") or "").strip()
    message_id = str(msg.get("Message-ID") or "").strip()

    body = _extract_email_body(msg)
    header_summary = f"Subject: {subject}\nFrom: {sender}\nTo: {recipient}\nDate: {date_hdr}"

    # Split multi-message threads separated by horizontal rules or embedded From: headers
    thread_messages = re.split(r"\r?\n-{10,}\r?\n", body)
    segments: list[ParsedSegment] = [
        ParsedSegment(
            text=header_summary,
            page_number=1,
            paragraph_index=1,
            section_heading=subject[:255],
            char_start=0,
            char_end=len(header_summary),
            metadata={"from": sender, "to": recipient, "date": date_hdr, "subject": subject},
        )
    ]

    para_idx = 2
    char_offset = len(header_summary) + 2
    for msg_idx, block in enumerate(thread_messages, start=1):
        clean_block = block.strip()
        if not clean_block:
            continue
        sub_subject_match = re.search(r"^Subject:\s*(.+)$", clean_block, flags=re.MULTILINE)
        heading = (
            sub_subject_match.group(1).strip()[:255]
            if sub_subject_match
            else f"{subject} (Message {msg_idx})"[:255]
        )
        sub_segs, para_idx, _ = _split_paragraphs_with_offsets(
            clean_block,
            page_number=1,
            starting_paragraph_index=para_idx,
            base_char_offset=char_offset,
            initial_heading=heading,
        )
        segments.extend(sub_segs)
        char_offset += len(clean_block) + 2

    word_count = sum(len(seg.text.split()) for seg in segments)
    return ParsedDocument(
        segments=segments,
        page_count=1,
        word_count=word_count,
        detected_document_type="email",
        metadata={
            "parser": "eml",
            "subject": subject,
            "from": sender,
            "to": recipient,
            "date": date_hdr,
            "message_id": message_id,
        },
    )


def _looks_like_transcript(text: str, ext: str) -> bool:
    if ext in {".vtt", ".srt"}:
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:25]
    if not lines:
        return False
    hits = sum(1 for ln in lines if _TRANSCRIPT_LINE_RE.match(ln))
    return hits >= 2


def _parse_transcript(text: str) -> ParsedDocument:
    segments: list[ParsedSegment] = []
    para_idx = 1
    char_offset = 0
    current_speaker: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.upper() == "WEBVTT" or line.isdigit() or "-->" in line:
            char_offset += len(raw_line) + 1
            continue

        match = _TRANSCRIPT_LINE_RE.match(line)
        meta: dict[str, Any] = {}
        if match:
            current_speaker = match.group("speaker").strip()
            ts = match.group("ts")
            if ts:
                meta["timestamp"] = ts
            meta["speaker"] = current_speaker

        segments.append(
            ParsedSegment(
                text=line,
                page_number=1,
                paragraph_index=para_idx,
                section_heading=f"Speaker: {current_speaker}" if current_speaker else "Transcript",
                char_start=char_offset,
                char_end=char_offset + len(line),
                metadata=meta,
            )
        )
        char_offset += len(raw_line) + 1
        para_idx += 1

    word_count = sum(len(seg.text.split()) for seg in segments)
    return ParsedDocument(
        segments=segments,
        page_count=max(1, (word_count // 400) + 1),
        word_count=word_count,
        detected_document_type="transcript",
        metadata={"parser": "transcript"},
    )


def parse_document_bytes(
    filename: str,
    data: bytes,
    *,
    content_type: str = "application/octet-stream",
    hinted_document_type: str | None = None,
    ocr_service: OCRService | None = None,
) -> ParsedDocument:
    """Parse PDF, DOCX, TXT, MD, EML, or transcript files into provenance-rich segments."""
    ext = Path(filename).suffix.lower()

    if ext == ".pdf" or content_type == "application/pdf":
        return _parse_pdf(data, ocr_service=ocr_service)
    if ext == ".docx" or "wordprocessingml" in content_type:
        return _parse_docx(data)
    if ext == ".eml" or content_type == "message/rfc822":
        return _parse_email(data)

    text = _decode_text(data)
    if hinted_document_type == "transcript" or _looks_like_transcript(text, ext):
        return _parse_transcript(text)

    segments, _, _ = _split_paragraphs_with_offsets(text, page_number=1)
    if not segments and text.strip():
        segments = [
            ParsedSegment(
                text=text.strip(),
                page_number=1,
                paragraph_index=1,
                section_heading=None,
                char_start=0,
                char_end=len(text.strip()),
            )
        ]

    word_count = sum(len(seg.text.split()) for seg in segments)
    lowered = text.lower()
    detected_type = hinted_document_type or "general"
    if detected_type == "general":
        if any(
            kw in lowered for kw in ["agreement", "indemnif", "governing law", "witness whereof"]
        ):
            detected_type = "contract"
        elif ext == ".md":
            detected_type = "memo"

    return ParsedDocument(
        segments=segments,
        page_count=max(1, (word_count // 450) + 1),
        word_count=word_count,
        detected_document_type=detected_type,
        metadata={"parser": "markdown" if ext == ".md" else "text"},
    )
