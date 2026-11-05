from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials

from app.core.auth import CurrentUser, bearer_scheme
from app.core.database import DbSession
from app.schemas.common import DataResponse
from app.schemas.media import PlaybackUrlData
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
    token: Annotated[
        str | None,
        Query(description="재생 URL 발급 API의 영상 전용 서명 토큰"),
    ] = None,
) -> Response:
    return media_service.stream_video(db, request, video_id, token=token)


@router.post(
    "/videos/{id}/playback-url",
    response_model=DataResponse[PlaybackUrlData],
    summary="영상 재생용 서명 URL 발급",
    description=(
        "로그인 및 기존 영상 시청 권한·잠금 조건을 확인한 뒤 5분 유효 URL을 반환합니다. "
        "반환된 URL로 Authorization 헤더 없이 Range 재생이 가능합니다. "
        "만료 전에 재발급하며, 스트리밍의 401 응답 시에도 재발급이 필요합니다. "
        "공개 OT는 기존 스트리밍 URL로 비로그인 재생할 수 있습니다."
    ),
    responses={
        401: {"description": "로그인 토큰 없음·만료·무효"},
        403: {"description": "영상 시청 권한 없음 또는 선행 학습 잠금"},
        404: {"description": "영상·활성 섹션·파일 없음"},
    },
)
def issue_playback_url(
    request: Request,
    response: Response,
    db: DbSession,
    user: CurrentUser,
    video_id: Annotated[int, Path(alias="id", description="재생할 영상 ID")],
) -> DataResponse[PlaybackUrlData]:
    response.headers["Cache-Control"] = "no-store"
    return DataResponse(data=media_service.issue_playback_url(db, request, video_id, user))


@router.get(
    "/media/{storageKey:path}",
    response_class=Response,
    responses=STATIC_MEDIA_RESPONSES,
)
def serve_static_media(
    storage_key: Annotated[str, Path(alias="storageKey", description="미디어 볼륨 상대 경로 키")],
) -> Response:
    return media_service.serve_static_media(storage_key)
