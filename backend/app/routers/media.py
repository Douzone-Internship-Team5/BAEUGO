from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.core.auth import get_optional_current_user
from app.core.database import DbSession
from app.core.errors import AppError
from app.models.course import Course, Video
from app.models.learning import Enrollment
from app.services.storage import get_media_dir

router = APIRouter(tags=["media"])


@router.get("/media/{storageKey:path}")
def serve_media_file(storageKey: str):
    if ".." in storageKey:
        raise AppError(404, "NOT_FOUND", "이미지를 찾을 수 없습니다.")

    media_dir = get_media_dir().resolve()
    try:
        file_path = (media_dir / storageKey).resolve()
    except Exception:
        raise AppError(404, "NOT_FOUND", "이미지를 찾을 수 없습니다.")

    if not file_path.is_relative_to(media_dir) or not file_path.is_file():
        raise AppError(404, "NOT_FOUND", "이미지를 찾을 수 없습니다.")

    # 파일 확장자에 따른 Content-Type
    ext = file_path.suffix.lower()
    if ext == ".png":
        media_type = "image/png"
    elif ext == ".webp":
        media_type = "image/webp"
    else:
        media_type = "image/jpeg"
    return FileResponse(file_path, media_type=media_type)


@router.get("/videos/{id}/stream")
def stream_video(
    id: int,
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
):
    video = db.get(Video, id)
    if not video or not video.is_active:
        raise AppError(404, "NOT_FOUND", "영상 파일을 찾을 수 없습니다.")

    # 학습 영상(LESSON)은 수강 중인 학습자, 담당 강사, 운영자만 시청 가능
    if video.video_type == "LESSON":
        current_user = get_optional_current_user(db, authorization)
        if not current_user:
            raise AppError(403, "FORBIDDEN", "수강 중인 학습자 또는 담당 강사만 시청할 수 있습니다.")

        course = video.course or db.get(Course, video.course_id)
        is_owner_instructor = course is not None and course.instructor_id == current_user.id
        is_operator = (
            current_user.account_type == "OPERATOR" or current_user.active_mode == "OPERATOR"
        )
        is_enrolled = (
            db.scalar(
                select(Enrollment.id).where(
                    Enrollment.course_id == video.course_id,
                    Enrollment.learner_id == current_user.id,
                )
            )
            is not None
        )
        if not (is_owner_instructor or is_operator or is_enrolled):
            raise AppError(403, "FORBIDDEN", "수강 중인 학습자 또는 담당 강사만 시청할 수 있습니다.")

    media_dir = get_media_dir().resolve()
    try:
        file_path = (media_dir / video.video_storage_key).resolve()
    except Exception:
        raise AppError(404, "NOT_FOUND", "영상 파일을 찾을 수 없습니다.")

    if not file_path.is_relative_to(media_dir) or not file_path.is_file():
        raise AppError(404, "NOT_FOUND", "영상 파일을 찾을 수 없습니다.")

    return FileResponse(file_path, media_type="video/mp4")
