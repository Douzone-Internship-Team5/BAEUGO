from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, Header
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.core.database import DbSession
from app.core.errors import AppError
from app.models.course import Video
from app.services.storage import get_media_dir

router = APIRouter(tags=["media"])


@router.get("/media/{storageKey:path}")
def serve_media_file(storageKey: str):
    if ".." in storageKey:
        raise AppError(404, "NOT_FOUND", "이미지를 찾을 수 없습니다.")

    media_dir = get_media_dir()
    file_path = media_dir / storageKey
    if not file_path.is_file():
        # 파일이 실제 존재하지 않으면 기본 빈 응답 또는 404
        raise AppError(404, "NOT_FOUND", "이미지를 찾을 수 없습니다.")

    # 파일 확장자에 따른 Content-Type
    ext = file_path.suffix.lower()
    media_type = "image/png" if ext == ".png" else "image/jpeg"
    return FileResponse(file_path, media_type=media_type)


@router.get("/videos/{id}/stream")
def stream_video(id: int, db: DbSession):
    video = db.get(Video, id)
    if not video or not video.is_active:
        raise AppError(404, "NOT_FOUND", "영상 파일을 찾을 수 없습니다.")

    media_dir = get_media_dir()
    file_path = media_dir / video.video_storage_key
    if not file_path.is_file():
        raise AppError(404, "NOT_FOUND", "영상 파일을 찾을 수 없습니다.")

    return FileResponse(file_path, media_type="video/mp4")
