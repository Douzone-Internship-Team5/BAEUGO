from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials

from app.core.auth import bearer_scheme
from app.core.database import DbSession
from app.services import media as media_service

router = APIRouter(tags=["media"])


@router.get("/videos/{id}/stream")
def stream_video(
    request: Request,
    db: DbSession,
    video_id: Annotated[int, Path(alias="id", description="스트리밍할 영상 ID")],
    _credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> Response:
    return media_service.stream_video(db, request, video_id)


@router.get("/media/{storageKey:path}")
def serve_static_media(
    storage_key: Annotated[str, Path(alias="storageKey", description="미디어 볼륨 상대 경로 키")],
) -> Response:
    return media_service.serve_static_media(storage_key)
