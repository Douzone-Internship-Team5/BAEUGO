from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote

from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session, joinedload

from app.core.errors import AppError, error_body
from app.core.security import decode_access_token
from app.models.course import Course
from app.models.learning import Enrollment, QuizAttempt, VideoProgress
from app.models.user import AccountType, Role, User
from app.models.video import Video
from app.services.storage import get_media_dir

VIDEO_FORBIDDEN_MESSAGE = "영상 시청 권한이 없거나 잠금 상태입니다."
VIDEO_NOT_FOUND_MESSAGE = "영상 파일을 찾을 수 없습니다."
IMAGE_NOT_FOUND_MESSAGE = "이미지를 찾을 수 없습니다."

CHUNK_SIZE = 64 * 1024

IMAGE_CONTENT_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def resolve_optional_user(request: Request, db: Session) -> User | None:
    """Authorization 헤더가 있으면 검증하여 User를 반환하고, 없으면 None을 반환한다.

    잘못된 형식이나 만료·무효 토큰이 전달된 경우 401 UNAUTHORIZED를 발생시킨다.
    """
    raw_auth = request.headers.get("Authorization")
    if raw_auth is None or not raw_auth.strip():
        return None

    invalid_token_error = AppError(401, "UNAUTHORIZED", "유효하지 않거나 만료된 토큰입니다.")
    parts = raw_auth.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise invalid_token_error

    token = parts[1].strip()
    if not token:
        raise invalid_token_error

    user_id = decode_access_token(token)
    if user_id is None:
        raise invalid_token_error

    user = db.get(User, user_id)
    if user is None or not user.is_login_allowed:
        raise invalid_token_error

    return user


def _resolve_safe_media_path(storage_key: str | None, *, not_found_message: str) -> Path:
    if not storage_key or not storage_key.strip() or len(storage_key) > 255:
        raise AppError(404, "NOT_FOUND", not_found_message)

    if storage_key != storage_key.strip():
        raise AppError(404, "NOT_FOUND", not_found_message)

    unquoted = unquote(storage_key)
    if (
        "\x00" in storage_key
        or "\x00" in unquoted
        or "\\" in storage_key
        or "\\" in unquoted
        or ":" in storage_key
        or ":" in unquoted
        or ".." in storage_key
        or ".." in unquoted
    ):
        raise AppError(404, "NOT_FOUND", not_found_message)

    if any(seg in ("", ".", "..") for seg in storage_key.split("/")) or any(
        seg in ("", ".", "..") for seg in unquoted.split("/")
    ):
        raise AppError(404, "NOT_FOUND", not_found_message)

    rel_path = Path(storage_key)
    if rel_path.is_absolute() or rel_path.drive or rel_path.root or ".." in rel_path.parts:
        raise AppError(404, "NOT_FOUND", not_found_message)

    media_dir = get_media_dir().resolve()
    try:
        target_path = (media_dir / rel_path).resolve()
    except OSError as err:
        raise AppError(404, "NOT_FOUND", not_found_message) from err

    if not target_path.is_relative_to(media_dir):
        raise AppError(404, "NOT_FOUND", not_found_message)

    return target_path


def _verify_video_stream_access(db: Session, video: Video, user: User | None) -> None:
    course = video.course
    is_operator = user is not None and user.account_type == AccountType.OPERATOR
    is_owner_instructor = user is not None and course.instructor_id == user.id

    # 잠금 해제 예외 ③: 강좌 소유 강사 또는 운영자
    if is_operator or is_owner_instructor:
        return

    if video.video_type == "OT":
        if course.status == "PUBLISHED":
            return
        if course.status in ("EDITING", "OPERATOR_HIDDEN"):
            if user is None or user.active_mode == Role.INSTRUCTOR:
                raise AppError(403, "FORBIDDEN", VIDEO_FORBIDDEN_MESSAGE)
            enrollment = db.scalar(
                select(Enrollment).where(
                    Enrollment.learner_id == user.id,
                    Enrollment.course_id == course.id,
                )
            )
            if enrollment is None:
                raise AppError(403, "FORBIDDEN", VIDEO_FORBIDDEN_MESSAGE)
            return
        raise AppError(403, "FORBIDDEN", VIDEO_FORBIDDEN_MESSAGE)

    # LESSON 영상은 로그인 필수
    if user is None:
        raise AppError(401, "UNAUTHORIZED", "로그인이 필요합니다.")

    if course.status not in ("PUBLISHED", "EDITING", "OPERATOR_HIDDEN"):
        raise AppError(403, "FORBIDDEN", VIDEO_FORBIDDEN_MESSAGE)

    if user.active_mode == Role.INSTRUCTOR:
        raise AppError(403, "FORBIDDEN", VIDEO_FORBIDDEN_MESSAGE)

    enrollment = db.scalar(
        select(Enrollment).where(
            Enrollment.learner_id == user.id,
            Enrollment.course_id == course.id,
        )
    )
    if enrollment is None:
        raise AppError(403, "FORBIDDEN", VIDEO_FORBIDDEN_MESSAGE)

    # 잠금 해제 예외 ②: 최초 수료 이력 보유 수강생
    if enrollment.first_completed_at is not None or enrollment.status == "COMPLETED":
        return

    completed_video_ids = set(
        db.scalars(
            select(VideoProgress.video_id).where(
                VideoProgress.enrollment_id == enrollment.id,
                VideoProgress.is_completed.is_(True),
            )
        ).all()
    )

    # 잠금 해제 예외 ①: 이미 완료한 영상 재시청
    if video.id in completed_video_ids:
        return

    submitted_quiz_ids = set(
        db.scalars(
            select(QuizAttempt.quiz_id).where(
                QuizAttempt.enrollment_id == enrollment.id,
                QuizAttempt.quiz_id.is_not(None),
                QuizAttempt.status == "SUBMITTED",
            )
        ).all()
    )

    # 커리큘럼 순서대로 선행 학습 영상 및 단위 확인 퀴즈 완료 여부 검증
    active_lessons = [v for v in course.videos if v.is_active and v.video_type == "LESSON"]
    sectionless_lessons = sorted(
        (v for v in active_lessons if v.section_id is None),
        key=lambda v: (v.sort_order, v.id),
    )
    for lesson in sectionless_lessons:
        if lesson.id == video.id:
            return
        if lesson.id not in completed_video_ids:
            raise AppError(403, "FORBIDDEN", VIDEO_FORBIDDEN_MESSAGE)

    active_sections = sorted(
        (s for s in course.sections if s.is_active),
        key=lambda s: (s.sort_order, s.id),
    )
    active_unit_quizzes = [q for q in course.quizzes if q.is_active and q.quiz_type == "UNIT"]

    for section in active_sections:
        section_lessons = sorted(
            (v for v in active_lessons if v.section_id == section.id),
            key=lambda v: (v.sort_order, v.id),
        )
        for lesson in section_lessons:
            if lesson.id == video.id:
                return
            if lesson.id not in completed_video_ids:
                raise AppError(403, "FORBIDDEN", VIDEO_FORBIDDEN_MESSAGE)

        section_quizzes = sorted(
            (q for q in active_unit_quizzes if q.section_id == section.id),
            key=lambda q: q.id,
        )
        for quiz in section_quizzes:
            if quiz.id not in submitted_quiz_ids:
                raise AppError(403, "FORBIDDEN", VIDEO_FORBIDDEN_MESSAGE)

    raise AppError(404, "NOT_FOUND", VIDEO_NOT_FOUND_MESSAGE)


def _iter_file_range(file_path: Path, start: int, length: int) -> Iterator[bytes]:
    with file_path.open("rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            chunk = f.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _parse_byte_range(range_header: str, file_size: int) -> tuple[int, int] | None:
    cleaned = range_header.strip()
    if not cleaned.lower().startswith("bytes="):
        return None

    spec = cleaned[6:].strip()
    if not spec or "," in spec or "-" not in spec:
        return None

    start_str, end_str = spec.split("-", 1)
    start_str = start_str.strip()
    end_str = end_str.strip()

    if file_size <= 0:
        return None

    if start_str == "":
        if not (end_str.isascii() and end_str.isdigit()):
            return None
        suffix_length = int(end_str)
        if suffix_length <= 0:
            return None
        start = max(0, file_size - suffix_length)
        end = file_size - 1
        return start, end

    if not (start_str.isascii() and start_str.isdigit()):
        return None
    if end_str != "" and not (end_str.isascii() and end_str.isdigit()):
        return None

    start = int(start_str)
    end = int(end_str) if end_str != "" else file_size - 1

    if start < 0 or start >= file_size or end < start:
        return None

    end = min(end, file_size - 1)
    return start, end


def stream_video(db: Session, request: Request, video_id: int) -> Response:
    """[M-1] 영상 미디어 스트리밍 서빙 (HTTP Range 지원)."""
    user = resolve_optional_user(request, db)

    try:
        video = db.scalar(
            select(Video)
            .where(Video.id == video_id)
            .options(
                joinedload(Video.course).selectinload(Course.sections),
                joinedload(Video.course).selectinload(Course.videos),
                joinedload(Video.course).selectinload(Course.quizzes),
                joinedload(Video.section),
            )
        )
    except (ProgrammingError, OperationalError) as err:
        raise AppError(404, "NOT_FOUND", VIDEO_NOT_FOUND_MESSAGE) from err

    if video is None or not video.is_active:
        raise AppError(404, "NOT_FOUND", VIDEO_NOT_FOUND_MESSAGE)

    if video.video_type not in ("OT", "LESSON"):
        raise AppError(404, "NOT_FOUND", VIDEO_NOT_FOUND_MESSAGE)

    if video.section_id is not None and (video.section is None or not video.section.is_active):
        raise AppError(404, "NOT_FOUND", VIDEO_NOT_FOUND_MESSAGE)

    if video.course is None:
        raise AppError(404, "NOT_FOUND", VIDEO_NOT_FOUND_MESSAGE)

    _verify_video_stream_access(db, video, user)

    file_path = _resolve_safe_media_path(
        video.video_storage_key, not_found_message=VIDEO_NOT_FOUND_MESSAGE
    )
    if not file_path.is_file():
        raise AppError(404, "NOT_FOUND", VIDEO_NOT_FOUND_MESSAGE)

    file_size = file_path.stat().st_size
    range_header = request.headers.get("Range")

    if range_header is None or not range_header.strip():
        return StreamingResponse(
            content=_iter_file_range(file_path, 0, file_size),
            status_code=200,
            media_type="video/mp4",
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
            },
        )

    parsed_range = _parse_byte_range(range_header, file_size)
    if parsed_range is None:
        return JSONResponse(
            status_code=416,
            content=error_body(
                "RANGE_NOT_SATISFIABLE", "요청한 스트리밍 범위가 유효하지 않습니다."
            ),
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    start, end = parsed_range
    content_length = end - start + 1
    return StreamingResponse(
        content=_iter_file_range(file_path, start, content_length),
        status_code=206,
        media_type="video/mp4",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(content_length),
        },
    )


def serve_static_media(storage_key: str) -> Response:
    """[M-2] 정적 미디어(썸네일 · 프로필 사진) 서빙."""
    file_path = _resolve_safe_media_path(storage_key, not_found_message=IMAGE_NOT_FOUND_MESSAGE)

    ext = file_path.suffix.lower()
    content_type = IMAGE_CONTENT_TYPES.get(ext)
    if content_type is None:
        raise AppError(404, "NOT_FOUND", IMAGE_NOT_FOUND_MESSAGE)

    if not file_path.is_file():
        raise AppError(404, "NOT_FOUND", IMAGE_NOT_FOUND_MESSAGE)

    content = file_path.read_bytes()
    return Response(
        content=content,
        status_code=200,
        media_type=content_type,
        headers={"Content-Length": str(len(content))},
    )
