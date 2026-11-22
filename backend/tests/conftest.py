import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import clear_settings_cache
from app.db.session import dispose_engine, get_session_factory, init_db
from app.main import create_app
from app.services.seed import seed_defaults

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = REPO_ROOT / "samples"


def build_sample_pdf(pages: list[str]) -> bytes:
    """Construct a valid minimal multi-page PDF with exact xref byte offsets."""
    objects: list[bytes] = []
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

    page_obj_ids: list[int] = []
    next_id = 4
    page_entries: list[tuple[int, bytes, int, bytes]] = []
    for page_text in pages:
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        page_obj_ids.append(page_id)

        lines = [line.strip() for line in page_text.splitlines() if line.strip()]
        stream_cmds = ["BT", "/F1 11 Tf", "50 750 Td", "14 TL"]
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream_cmds.append(f"({escaped}) Tj T*")
        stream_cmds.append("ET")
        stream_bytes = "\n".join(stream_cmds).encode("latin-1")

        page_obj = (
            f"{page_id} 0 obj\n"
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>\n"
            f"endobj\n"
        ).encode("ascii")
        content_obj = (
            f"{content_id} 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii")
            + stream_bytes
            + b"\nendstream\nendobj\n"
        )
        page_entries.append((page_id, page_obj, content_id, content_obj))

    kids_str = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    pages_obj = (
        f"2 0 obj\n<< /Type /Pages /Kids [{kids_str}] "
        f"/Count {len(page_obj_ids)} >>\nendobj\n"
    )
    objects.append(pages_obj.encode("ascii"))
    objects.append(b"3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")
    for _pid, page_obj, _cid, content_obj in page_entries:
        objects.append(page_obj)
        objects.append(content_obj)

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for obj in objects:
        offsets.append(buf.tell())
        buf.write(obj)

    xref_pos = buf.tell()
    buf.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    buf.write(b"0000000000 65535 f \n")
    for off in offsets:
        buf.write(f"{off:010d} 00000 n \n".encode("ascii"))
    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    )
    buf.write(trailer.encode("ascii"))
    return buf.getvalue()


def build_scanned_pdf_stub(ocr_marker_text: str | None = None) -> bytes:
    """Construct a valid PDF with no embedded text stream (simulating a scanned page)."""
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R >>\nendobj\n"
        ),
        b"4 0 obj\n<< /Length 4 >>\nstream\nq Q\n\nendstream\nendobj\n",
    ]
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    if ocr_marker_text:
        buf.write(f"%OCR_TEXT: {ocr_marker_text}\n".encode())
    offsets: list[int] = []
    for obj in objects:
        offsets.append(buf.tell())
        buf.write(obj)
    xref_pos = buf.tell()
    buf.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    buf.write(b"0000000000 65535 f \n")
    for off in offsets:
        buf.write(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return buf.getvalue()


def build_sample_docx(paragraphs: list[tuple[str | None, str]]) -> bytes:
    """Build a valid OOXML .docx archive containing Heading styles and paragraph text."""
    w_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body_parts: list[str] = []
    for style_id, text in paragraphs:
        esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        ppr = f'<w:pPr><w:pStyle w:val="{style_id}"/></w:pPr>' if style_id else ""
        body_parts.append(f"<w:p>{ppr}<w:r><w:t>{esc}</w:t></w:r></w:p>")

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{w_ns}"><w:body>{"".join(body_parts)}</w:body></w:document>'
    )
    rel_ct = "application/vnd.openxmlformats-package.relationships+xml"
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        f'<Default Extension="rels" ContentType="{rel_ct}"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types.encode("utf-8"))
        zf.writestr("_rels/.rels", rels_xml.encode("utf-8"))
        zf.writestr("word/document.xml", document_xml.encode("utf-8"))
    return buf.getvalue()


async def register_and_login(
    client: AsyncClient,
    *,
    email: str,
    full_name: str,
    role: str = "associate",
    password: str = "StrongTestPass123!",
) -> tuple[str, dict[str, Any]]:
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "full_name": full_name,
            "role": role,
            "password": password,
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return str(data["access_token"]), dict(data["user"])


@pytest.fixture
async def raw_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    """Unauthenticated AsyncClient backed by isolated SQLite + local storage + seeded defaults."""
    db_file = tmp_path / "test_ai_paralegal.db"
    storage_dir = tmp_path / "storage"

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("TASK_EXECUTION_MODE", "inline")
    monkeypatch.setenv("LLM_DEFAULT_PROVIDER", "mock")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "deterministic")
    monkeypatch.setenv("AUTH_AUTO_SEED_DEMO_USERS", "true")

    clear_settings_cache()
    await dispose_engine()
    await init_db()
    async with get_session_factory()() as session:
        await seed_defaults(session)

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client

    await dispose_engine()
    clear_settings_cache()


@pytest.fixture
async def async_client(raw_client: AsyncClient) -> AsyncClient:
    """Authenticated Partner AsyncClient for end-to-end API workflow tests."""
    login = await raw_client.post(
        "/api/v1/auth/login",
        json={
            "email": "partner@firm.example.com",
            "password": "ParalegalDemo2026!",
        },
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    raw_client.headers["Authorization"] = f"Bearer {token}"
    return raw_client
