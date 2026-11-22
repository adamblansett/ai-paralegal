from dataclasses import dataclass, field
from typing import Any

from app.services.parsers import ParsedSegment


@dataclass(slots=True)
class ChunkDraft:
    """Chunk ready for embedding and database persistence."""

    chunk_index: int
    content: str
    page_number: int
    paragraph_index: int
    section_heading: str | None
    char_start: int
    char_end: int
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


def _split_long_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            split_at = text.rfind(" ", start + max_chars // 2, end)
            if split_at != -1:
                end = split_at
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap_chars)
    return pieces


def chunk_segments(
    segments: list[ParsedSegment],
    *,
    max_chars: int = 1200,
    overlap_chars: int = 150,
) -> list[ChunkDraft]:
    """Group parsed document segments into bounded chunks while preserving provenance."""
    chunks: list[ChunkDraft] = []
    buffer: list[ParsedSegment] = []
    buffer_len = 0

    def _flush() -> None:
        nonlocal buffer, buffer_len
        if not buffer:
            return
        combined = "\n\n".join(seg.text for seg in buffer)
        first = buffer[0]
        last = buffer[-1]
        merged_meta: dict[str, Any] = {}
        for seg in buffer:
            if seg.metadata:
                merged_meta.update(seg.metadata)

        if len(combined) <= max_chars:
            chunks.append(
                ChunkDraft(
                    chunk_index=len(chunks),
                    content=combined,
                    page_number=first.page_number,
                    paragraph_index=first.paragraph_index,
                    section_heading=first.section_heading or last.section_heading,
                    char_start=first.char_start,
                    char_end=last.char_end,
                    token_count=len(combined.split()),
                    metadata=merged_meta,
                )
            )
        else:
            offset = first.char_start
            for piece in _split_long_text(
                combined, max_chars=max_chars, overlap_chars=overlap_chars
            ):
                chunks.append(
                    ChunkDraft(
                        chunk_index=len(chunks),
                        content=piece,
                        page_number=first.page_number,
                        paragraph_index=first.paragraph_index,
                        section_heading=first.section_heading or last.section_heading,
                        char_start=offset,
                        char_end=offset + len(piece),
                        token_count=len(piece.split()),
                        metadata=merged_meta,
                    )
                )
                offset += max(1, len(piece) - overlap_chars)

        buffer = []
        buffer_len = 0

    for seg in segments:
        if not seg.text.strip():
            continue
        if len(seg.text) > max_chars:
            _flush()
            offset = seg.char_start
            for piece in _split_long_text(
                seg.text, max_chars=max_chars, overlap_chars=overlap_chars
            ):
                chunks.append(
                    ChunkDraft(
                        chunk_index=len(chunks),
                        content=piece,
                        page_number=seg.page_number,
                        paragraph_index=seg.paragraph_index,
                        section_heading=seg.section_heading,
                        char_start=offset,
                        char_end=offset + len(piece),
                        token_count=len(piece.split()),
                        metadata=dict(seg.metadata),
                    )
                )
                offset += max(1, len(piece) - overlap_chars)
            continue

        same_page = not buffer or buffer[0].page_number == seg.page_number
        same_section = not buffer or buffer[-1].section_heading == seg.section_heading
        projected = buffer_len + len(seg.text) + (2 if buffer else 0)

        if buffer and (not same_page or not same_section or projected > max_chars):
            _flush()

        buffer.append(seg)
        buffer_len += len(seg.text) + (2 if len(buffer) > 1 else 0)

    _flush()
    return chunks
