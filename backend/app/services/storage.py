import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.errors import AppError

MAX_VIDEO_SIZE = 200 * 1024 * 1024
ALLOWED_THUMBNAIL_EXTENSIONS = {".jpg", ".jpeg", ".png"}
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".avi", ".mkv"}


def _get_upload_file_size(file: UploadFile) -> int:
    if file.size is not None:
        return file.size
    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(0)
    return size


def validate_thumbnail_file(file: UploadFile) -> None:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_THUMBNAIL_EXTENSIONS:
        raise AppError(400, "INVALID_FILE", "지원하지 않는 썸네일 이미지 형식입니다.")
    size = _get_upload_file_size(file)
    if size <= 0:
        raise AppError(400, "INVALID_FILE", "빈 이미지 파일은 업로드할 수 없습니다.")


def validate_video_file(file: UploadFile) -> None:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise AppError(400, "INVALID_FILE", "재생할 수 없거나 200MB를 초과한 영상 파일입니다.")
    size = _get_upload_file_size(file)
    if size <= 0 or size > MAX_VIDEO_SIZE:
        raise AppError(400, "INVALID_FILE", "재생할 수 없거나 200MB를 초과한 영상 파일입니다.")


def extract_video_duration(file_path: Path) -> int | None:
    """FFprobe를 통해 실제 동영상 재생 길이(초)를 추출한다.

    추출 실패(더미 파일, 미디어 파싱 오류, 타임아웃 등) 시 None을 반환한다.
    """
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(file_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            duration = float(result.stdout.strip())
            if duration > 0:
                return max(1, round(duration))
    except Exception:
        pass
    return None


def extract_video_thumbnail(video_path: Path, output_path: Path) -> bool:
    """FFmpeg를 통해 동영상의 첫 프레임을 추출하여 이미지 파일로 저장한다.

    성공 시 True, 실패 시 False를 반환한다.
    """
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            "00:00:00.500",
            "-i",
            str(video_path),
            "-vframes",
            "1",
            "-q:v",
            "2",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=5)
        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            return True
        cmd2 = [
            "ffmpeg",
            "-y",
            "-ss",
            "00:00:00",
            "-i",
            str(video_path),
            "-vframes",
            "1",
            "-q:v",
            "2",
            str(output_path),
        ]
        result2 = subprocess.run(cmd2, capture_output=True, timeout=5)
        return result2.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0
    except Exception:
        return False


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

    unique_name = (
        f"{filename_prefix}_{uuid4().hex[:8]}{ext}"
        if filename_prefix
        else f"{uuid4().hex[:8]}{ext}"
    )
    target_file = target_dir / unique_name

    file.file.seek(0)
    with target_file.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # 상대 키 경로 반환 (예: courses/101/thumb.jpg)
    return f"{clean_dir.strip('/')}/{unique_name}"


def get_thumbnail_url(storage_key: str | None) -> str | None:
    if not storage_key:
        return None
    if (
        storage_key.startswith("http://")
        or storage_key.startswith("https://")
        or storage_key.startswith("/")
    ):
        return storage_key
    return f"/api/v1/media/{storage_key.lstrip('/')}"


def get_video_stream_url(video_id: int | None) -> str | None:
    if not video_id:
        return None
    return f"/api/v1/videos/{video_id}/stream"


def get_profile_image_url(profile_image_key: str | None) -> str | None:
    if not profile_image_key:
        return None
    if (
        profile_image_key.startswith("http://")
        or profile_image_key.startswith("https://")
        or profile_image_key.startswith("/")
    ):
        return profile_image_key
    return f"/api/v1/media/{profile_image_key.lstrip('/')}"
