from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.core.auth import OptionalUser
from app.core.database import DbSession
from app.core.errors import AppError
from app.models.course import Course, Video
from app.models.learning import Enrollment
from app.services.storage import get_media_dir

router = APIRouter(tags=["media"])


@router.get("/media/{storage_key:path}")
def serve_media_file(storage_key: str):
    if ".." in storage_key:
        raise AppError(404, "NOT_FOUND", "이미지를 찾을 수 없습니다.")

    media_dir = get_media_dir().resolve()
    try:
        file_path = (media_dir / storage_key).resolve()
    except Exception as err:
        raise AppError(404, "NOT_FOUND", "이미지를 찾을 수 없습니다.") from err

    if not file_path.is_relative_to(media_dir) or not file_path.is_file():
        raise AppError(404, "NOT_FOUND", "이미지를 찾을 수 없습니다.")

    # 파일 확장자 검증: 명세에 따라 jpg, jpeg, png만 허용 (영상 및 기타 확장자 차단)
    ext = file_path.suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png"}:
        raise AppError(404, "NOT_FOUND", "이미지를 찾을 수 없습니다.")

    media_type = "image/png" if ext == ".png" else "image/jpeg"
    return FileResponse(file_path, media_type=media_type)


@router.get("/videos/{id}/stream")
def stream_video(
    id: int,
    db: DbSession,
    current_user: OptionalUser = None,
):
    video = db.get(Video, id)
    if not video or not video.is_active:
        raise AppError(404, "NOT_FOUND", "영상 파일을 찾을 수 없습니다.")

    course = video.course or db.get(Course, video.course_id)
    if not course:
        raise AppError(404, "NOT_FOUND", "영상 파일을 찾을 수 없습니다.")

    is_owner_instructor = current_user is not None and course.instructor_id == current_user.id
    is_operator = current_user is not None and (
        current_user.account_type == "OPERATOR" or current_user.active_mode == "OPERATOR"
    )

    # 무료 OT(OT)는 공개 강좌면 즉시 제공하고, 미공개 강좌면 담당 강사나 운영자만 가능
    if video.video_type == "OT":
        if course.status != "PUBLISHED" and not (is_owner_instructor or is_operator):
            raise AppError(403, "FORBIDDEN", "영상 시청 권한이 없거나 잠금 상태입니다.")
    elif video.video_type == "LESSON":
        if not current_user:
            raise AppError(403, "FORBIDDEN", "영상 시청 권한이 없거나 잠금 상태입니다.")

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
            raise AppError(403, "FORBIDDEN", "영상 시청 권한이 없거나 잠금 상태입니다.")

    media_dir = get_media_dir().resolve()
    try:
        file_path = (media_dir / video.video_storage_key).resolve()
    except Exception as err:
        raise AppError(404, "NOT_FOUND", "영상 파일을 찾을 수 없습니다.") from err

    if not file_path.is_relative_to(media_dir) or not file_path.is_file():
        raise AppError(404, "NOT_FOUND", "영상 파일을 찾을 수 없습니다.")

    return FileResponse(file_path, media_type="video/mp4")
