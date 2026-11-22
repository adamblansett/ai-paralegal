import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.services.exports import export_chronology, export_review_table

router = APIRouter(tags=["exports"])


@router.get("/matters/{matter_id}/exports/review-table")
async def download_review_table(
    matter_id: uuid.UUID,
    fmt: Literal["csv", "json", "markdown"] = Query(default="csv", alias="format"),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Export extracted findings and citations as CSV, JSON, or Markdown."""
    try:
        content, media_type, filename = await export_review_table(
            session, matter_id=matter_id, fmt=fmt
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/matters/{matter_id}/exports/chronology")
async def download_chronology(
    matter_id: uuid.UUID,
    fmt: Literal["csv", "json", "markdown"] = Query(default="csv", alias="format"),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Export discovery chronology events with source provenance as CSV, JSON, or Markdown."""
    try:
        content, media_type, filename = await export_chronology(
            session, matter_id=matter_id, fmt=fmt
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
