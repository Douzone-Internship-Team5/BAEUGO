from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials

from app.core.auth import bearer_scheme
from app.core.database import DbSession
from app.services import media as media_service

router = APIRouter(tags=["media"])

STREAM_RESPONSES = {
    200: {
        "description": "전체 영상 미디어 스트림",
        "content": {
            "video/mp4": {},
            "video/webm": {},
            "video/quicktime": {},
            "video/x-msvideo": {},
            "video/x-matroska": {},
        },
    },
    206: {
        "description": "HTTP Range 기반 부분 영상 미디어 스트림",
        "content": {
            "video/mp4": {},
            "video/webm": {},
            "video/quicktime": {},
            "video/x-msvideo": {},
            "video/x-matroska": {},
        },
    },
}

STATIC_MEDIA_RESPONSES = {
    200: {
        "description": "정적 미디어(썸네일 또는 프로필 사진) 바이너리",
        "content": {
            "image/jpeg": {},
            "image/png": {},
        },
    },
}


@router.get(
    "/videos/{id}/stream",
    response_class=Response,
    responses=STREAM_RESPONSES,
)
def stream_video(
    request: Request,
    db: DbSession,
    video_id: Annotated[int, Path(alias="id", description="스트리밍할 영상 ID")],
    _credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> Response:
    return media_service.stream_video(db, request, video_id)


@router.get(
    "/media/{storageKey:path}",
    response_class=Response,
    responses=STATIC_MEDIA_RESPONSES,
)
def serve_static_media(
    storage_key: Annotated[str, Path(alias="storageKey", description="미디어 볼륨 상대 경로 키")],
) -> Response:
    return media_service.serve_static_media(storage_key)
