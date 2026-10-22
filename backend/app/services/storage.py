import os
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import get_settings


def get_media_dir() -> Path:
    settings = get_settings()
    media_path = Path(settings.media_root)
    media_path.mkdir(parents=True, exist_ok=True)
    return media_path


def save_upload_file(file: UploadFile, relative_dir: str, filename_prefix: str = "") -> str:
    """파일을 media_root 내부 relative_dir에 저장하고 상대 경로(storage_key)를 반환한다."""
    # 경로 순회(Path Traversal) 공격 방어
    clean_dir = Path(relative_dir).as_posix().lstrip("/\\")
    if ".." in clean_dir:
        clean_dir = "uploads"

    media_dir = get_media_dir()
    target_dir = media_dir / clean_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename or "").suffix.lower()
    if not ext:
        ext = ".bin"

    unique_name = f"{filename_prefix}_{uuid4().hex[:8]}{ext}" if filename_prefix else f"{uuid4().hex[:8]}{ext}"
    target_file = target_dir / unique_name

    file.file.seek(0)
    with target_file.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # 상대 키 경로 반환 (예: courses/101/thumb.jpg)
    return f"{clean_dir.strip('/')}/{unique_name}"


def get_thumbnail_url(storage_key: str | None) -> str | None:
    if not storage_key:
        return None
    if storage_key.startswith("http://") or storage_key.startswith("https://") or storage_key.startswith("/"):
        return storage_key
    return f"/api/v1/media/{storage_key.lstrip('/')}"


def get_video_stream_url(video_id: int | None) -> str | None:
    if not video_id:
        return None
    return f"/api/v1/videos/{video_id}/stream"


def get_profile_image_url(profile_image_key: str | None) -> str | None:
    if not profile_image_key:
        return None
    if profile_image_key.startswith("http://") or profile_image_key.startswith("https://") or profile_image_key.startswith("/"):
        return profile_image_key
    return f"/api/v1/media/{profile_image_key.lstrip('/')}"
