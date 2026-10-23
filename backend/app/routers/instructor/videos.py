from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, Form, Request, UploadFile
from sqlalchemy import delete, func, select

from app.core.auth import (
    InstructorUser,
    verify_course_ownership,
    verify_section_ownership,
    verify_video_ownership,
)
from app.core.database import DbSession
from app.core.errors import AppError
from app.models.course import Quiz, Video, VideoScript
from app.schemas.common import DataResponse
from app.schemas.instructor import (
    ScriptRegenerateResponseData,
    ScriptResponseData,
    ScriptSegment,
    ScriptUpdateRequest,
    VideoDeleteResponseData,
    VideoResponseData,
)
from app.services.storage import (
    extract_video_duration,
    extract_video_thumbnail,
    get_media_dir,
    save_upload_file,
    validate_thumbnail_file,
    validate_video_file,
)

router = APIRouter(tags=["instructor-videos"])


# ==========================================
# [B-6] 영상 관리 (OT 포함)
# ==========================================
@router.post("/videos", response_model=DataResponse[VideoResponseData], status_code=201)
def upload_video(
    db: DbSession,
    current_user: InstructorUser,
    course_id: Annotated[int, Form(alias="courseId")],
    video_type: Annotated[str, Form(alias="videoType")],
    title: Annotated[str, Form(...)],
    file: Annotated[UploadFile, File(...)],
    section_id: Annotated[int | None, Form(alias="sectionId")] = None,
    thumbnail: Annotated[UploadFile | None, File()] = None,
):
    course = verify_course_ownership(course_id, current_user.id, db)
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(
            409, "CONFLICT", "DRAFT 또는 EDITING 상태인 강좌에서만 영상을 편집할 수 있습니다."
        )

    if video_type not in ("OT", "LESSON"):
        raise AppError(400, "VALIDATION_ERROR", "영상 유형은 OT 또는 LESSON이어야 합니다.")

    if not title or not title.strip() or len(title) > 200:
        raise AppError(400, "VALIDATION_ERROR", "영상 제목은 1~200자 이내로 입력해 주세요.")

    if video_type == "LESSON" and section_id is None:
        raise AppError(400, "VALIDATION_ERROR", "학습 영상(LESSON)은 sectionId가 필수입니다.")

    validate_video_file(file)
    if thumbnail is not None:
        validate_thumbnail_file(thumbnail)

    if section_id is not None:
        verify_section_ownership(section_id, current_user.id, db, course_id=course.id)

    # OT는 강좌당 활성 1개만 유지: 새 OT 등록 시 기존 OT is_active = False
    if video_type == "OT":
        section_id = None
        existing_ots = db.scalars(
            select(Video).where(
                Video.course_id == course.id,
                Video.video_type == "OT",
                Video.is_active.is_(True),
            )
        ).all()
        for v in existing_ots:
            v.is_active = False
        db.flush()
        sort_order = 0
    else:
        max_order = db.scalar(
            select(func.coalesce(func.max(Video.sort_order), 0)).where(
                Video.section_id == section_id, Video.is_active.is_(True)
            )
        )
        sort_order = (max_order or 0) + 1

    # 파일 저장 및 실제 영상 재생 시간 추출 (실패 시 기본 360초 폴백)
    video_key = save_upload_file(file, f"courses/{course.id}/videos", "video")
    media_dir = get_media_dir()
    extracted_duration = extract_video_duration(media_dir / video_key)
    duration_seconds = extracted_duration if extracted_duration is not None else 360

    thumb_key = None
    if thumbnail is not None:
        thumb_key = save_upload_file(thumbnail, f"courses/{course.id}/videos", "vthumb")
    else:
        # 미입력 시 FFmpeg로 첫 프레임 추출하여 videos.thumbnail_storage_key 저장
        auto_thumb_name = f"vthumb_{uuid4().hex[:8]}.jpg"
        rel_key = f"courses/{course.id}/videos/{auto_thumb_name}"
        auto_thumb_path = media_dir / rel_key
        if extract_video_thumbnail(media_dir / video_key, auto_thumb_path):
            thumb_key = rel_key

    video = Video(
        course_id=course.id,
        section_id=section_id,
        video_type=video_type,
        title=title,
        duration_seconds=duration_seconds,
        sort_order=sort_order,
        video_storage_key=video_key,
        thumbnail_storage_key=thumb_key,
        stt_status="PENDING",
        transcript_reviewed_at=None,
        is_active=True,
    )
    db.add(video)

    # 섹션 단위 퀴즈 자동 생성 및 재생성 연동
    if video_type == "LESSON" and section_id is not None:
        unit_quiz = db.scalar(
            select(Quiz).where(
                Quiz.section_id == section_id,
                Quiz.quiz_type == "UNIT",
                Quiz.is_active.is_(True),
            )
        )
        if unit_quiz:
            unit_quiz.ai_status = "PENDING"
            unit_quiz.reviewed_at = None
        else:
            db.add(
                Quiz(
                    course_id=course.id,
                    section_id=section_id,
                    quiz_type="UNIT",
                    ai_status="PENDING",
                    reviewed_at=None,
                    is_active=True,
                )
            )

    db.commit()
    db.refresh(video)

    return DataResponse(
        data=VideoResponseData(
            video_id=video.id,
            course_id=video.course_id,
            section_id=video.section_id,
            video_type=video.video_type,
            title=video.title,
            duration_seconds=video.duration_seconds,
            sort_order=video.sort_order,
            stt_status=video.stt_status,
            transcript_reviewed_at=video.transcript_reviewed_at,
            is_active=video.is_active,
        )
    )


@router.patch("/videos/{video_id}", response_model=DataResponse[VideoResponseData])
async def update_or_replace_video(
    video_id: int,
    request: Request,
    db: DbSession,
    current_user: InstructorUser,
):
    video = verify_video_ownership(video_id, current_user.id, db)
    course = video.course
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(
            409, "CONFLICT", "DRAFT 또는 EDITING 상태인 강좌에서만 영상을 편집할 수 있습니다."
        )

    content_type = request.headers.get("content-type", "")
    title: str | None = None
    sort_order: int | None = None
    file: UploadFile | None = None
    thumbnail: UploadFile | None = None

    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception as err:
            raise AppError(400, "VALIDATION_ERROR", "입력값을 확인해 주세요.") from err
        if not isinstance(body, dict):
            raise AppError(400, "VALIDATION_ERROR", "입력값을 확인해 주세요.")
        if "title" in body and body["title"] is not None:
            if not isinstance(body["title"], str):
                raise AppError(400, "VALIDATION_ERROR", "영상 제목 형식이 올바르지 않습니다.")
            title = body["title"]
        raw_sort = body.get("sortOrder", body.get("sort_order"))
        if raw_sort is not None:
            if isinstance(raw_sort, bool):
                raise AppError(400, "VALIDATION_ERROR", "정렬 순서 값이 올바르지 않습니다.")
            if isinstance(raw_sort, int):
                sort_order = raw_sort
            elif isinstance(raw_sort, str):
                try:
                    sort_order = int(raw_sort.strip())
                except ValueError as err:
                    raise AppError(
                        400, "VALIDATION_ERROR", "정렬 순서 값이 올바르지 않습니다."
                    ) from err
            else:
                raise AppError(400, "VALIDATION_ERROR", "정렬 순서 값이 올바르지 않습니다.")
    else:
        form = await request.form()
        if "title" in form and form.get("title") is not None:
            title_val = form.get("title")
            if isinstance(title_val, str):
                title = title_val
        raw_sort = form.get("sortOrder", form.get("sort_order"))
        if raw_sort is not None and raw_sort != "":
            try:
                sort_order = int(str(raw_sort).strip())
            except (ValueError, TypeError) as err:
                raise AppError(
                    400, "VALIDATION_ERROR", "정렬 순서 값이 올바르지 않습니다."
                ) from err
        file_val = form.get("file")
        if isinstance(file_val, UploadFile) or hasattr(file_val, "filename"):
            file = file_val  # type: ignore[assignment]
        thumb_val = form.get("thumbnail")
        if isinstance(thumb_val, UploadFile) or hasattr(thumb_val, "filename"):
            thumbnail = thumb_val  # type: ignore[assignment]

    if title is not None:
        if not title.strip() or len(title) > 200:
            raise AppError(400, "VALIDATION_ERROR", "영상 제목은 1~200자 이내로 입력해 주세요.")

    if sort_order is not None:
        if (video.video_type == "LESSON" and sort_order < 1) or sort_order < 0:
            raise AppError(400, "VALIDATION_ERROR", "정렬 순서(sortOrder) 값이 올바르지 않습니다.")

    if file is not None:
        validate_video_file(file)
    if thumbnail is not None:
        validate_thumbnail_file(thumbnail)

    # 파일이 제공된 경우: 새 videoId 행 생성 규칙 적용!
    if file is not None:
        video.is_active = False

        new_video_key = save_upload_file(file, f"courses/{course.id}/videos", "video")
        media_dir = get_media_dir()
        extracted_duration = extract_video_duration(media_dir / new_video_key)
        new_duration = (
            extracted_duration
            if extracted_duration is not None
            else (video.duration_seconds or 360)
        )

        new_thumb_key = video.thumbnail_storage_key
        if thumbnail is not None:
            new_thumb_key = save_upload_file(thumbnail, f"courses/{course.id}/videos", "vthumb")
        else:
            auto_thumb_name = f"vthumb_{uuid4().hex[:8]}.jpg"
            rel_key = f"courses/{course.id}/videos/{auto_thumb_name}"
            auto_thumb_path = media_dir / rel_key
            if extract_video_thumbnail(media_dir / new_video_key, auto_thumb_path):
                new_thumb_key = rel_key

        new_video = Video(
            course_id=video.course_id,
            section_id=video.section_id,
            video_type=video.video_type,
            title=title if title is not None else video.title,
            duration_seconds=new_duration,
            sort_order=sort_order if sort_order is not None else video.sort_order,
            video_storage_key=new_video_key,
            thumbnail_storage_key=new_thumb_key,
            stt_status="PENDING",
            transcript_reviewed_at=None,
            is_active=True,
        )
        db.add(new_video)

        # 단위 퀴즈 자동 생성 및 재생성 연동
        if video.video_type == "LESSON" and video.section_id is not None:
            unit_quiz = db.scalar(
                select(Quiz).where(
                    Quiz.section_id == video.section_id,
                    Quiz.quiz_type == "UNIT",
                    Quiz.is_active.is_(True),
                )
            )
            if unit_quiz:
                unit_quiz.ai_status = "PENDING"
                unit_quiz.reviewed_at = None
            else:
                db.add(
                    Quiz(
                        course_id=course.id,
                        section_id=video.section_id,
                        quiz_type="UNIT",
                        ai_status="PENDING",
                        reviewed_at=None,
                        is_active=True,
                    )
                )

        db.commit()
        db.refresh(new_video)

        return DataResponse(
            data=VideoResponseData(
                video_id=new_video.id,
                course_id=new_video.course_id,
                section_id=new_video.section_id,
                video_type=new_video.video_type,
                title=new_video.title,
                duration_seconds=new_video.duration_seconds,
                sort_order=new_video.sort_order,
                stt_status=new_video.stt_status,
                transcript_reviewed_at=new_video.transcript_reviewed_at,
                is_active=new_video.is_active,
            )
        )

    # 메타데이터만 수정하는 경우
    if title is not None:
        video.title = title
    if sort_order is not None:
        video.sort_order = sort_order
    if thumbnail is not None:
        video.thumbnail_storage_key = save_upload_file(
            thumbnail, f"courses/{course.id}/videos", "vthumb"
        )

    db.commit()
    db.refresh(video)

    return DataResponse(
        data=VideoResponseData(
            video_id=video.id,
            course_id=video.course_id,
            section_id=video.section_id,
            video_type=video.video_type,
            title=video.title,
            duration_seconds=video.duration_seconds,
            sort_order=video.sort_order,
            stt_status=video.stt_status,
            transcript_reviewed_at=video.transcript_reviewed_at,
            is_active=video.is_active,
        )
    )


@router.delete("/videos/{video_id}", response_model=DataResponse[VideoDeleteResponseData])
def delete_video(
    video_id: int,
    db: DbSession,
    current_user: InstructorUser,
):
    video = verify_video_ownership(video_id, current_user.id, db)
    course = video.course
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(
            409, "CONFLICT", "DRAFT 또는 EDITING 상태인 강좌에서만 영상을 편집할 수 있습니다."
        )

    video.is_active = False
    db.commit()

    return DataResponse(
        data=VideoDeleteResponseData(
            video_id=video.id,
            course_id=video.course_id,
            is_active=False,
        )
    )


# ==========================================
# [B-7] 대본 조회 · 수정 · STT 재시도
# ==========================================
@router.get("/videos/{video_id}/script", response_model=DataResponse[ScriptResponseData])
def get_video_script(
    video_id: int,
    db: DbSession,
    current_user: InstructorUser,
):
    video = verify_video_ownership(video_id, current_user.id, db)
    segments = [
        ScriptSegment(
            seq=s.seq,
            start_ms=s.start_ms,
            end_ms=s.end_ms,
            content=s.content,
        )
        for s in video.scripts
    ]

    return DataResponse(
        data=ScriptResponseData(
            video_id=video.id,
            stt_status=video.stt_status,
            transcript_reviewed_at=video.transcript_reviewed_at,
            segments=segments,
        )
    )


@router.put("/videos/{video_id}/script", response_model=DataResponse[ScriptResponseData])
def update_video_script(
    video_id: int,
    req: ScriptUpdateRequest,
    db: DbSession,
    current_user: InstructorUser,
):
    video = verify_video_ownership(video_id, current_user.id, db)
    course = video.course
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(
            409,
            "CONFLICT",
            "DRAFT 또는 EDITING 상태인 강좌에서만 대본을 수정하거나 재생성할 수 있습니다.",
        )

    seen_seqs: set[int] = set()
    for seg in req.segments:
        if seg.seq < 1:
            raise AppError(400, "VALIDATION_ERROR", "자막 순번(seq)은 1 이상이어야 합니다.")
        if seg.seq in seen_seqs:
            raise AppError(400, "VALIDATION_ERROR", "중복된 자막 순번(seq)이 존재합니다.")
        seen_seqs.add(seg.seq)
        if seg.start_ms < 0:
            raise AppError(
                400, "VALIDATION_ERROR", "자막 시작 시점(startMs)은 0 이상이어야 합니다."
            )
        if seg.end_ms <= seg.start_ms:
            raise AppError(
                400,
                "VALIDATION_ERROR",
                "자막 종료 시점(endMs)은 시작 시점(startMs)보다 커야 합니다.",
            )
        if not seg.content or not seg.content.strip():
            raise AppError(400, "VALIDATION_ERROR", "자막 내용(content)은 비어 있을 수 없습니다.")

    sorted_segments = sorted(req.segments, key=lambda x: x.seq)

    # 기존 자막 삭제 후 교체
    db.execute(delete(VideoScript).where(VideoScript.video_id == video.id))
    db.flush()
    for seg in sorted_segments:
        db.add(
            VideoScript(
                video_id=video.id,
                seq=seg.seq,
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                content=seg.content,
            )
        )

    now = datetime.now(UTC)
    video.stt_status = "COMPLETED"
    video.transcript_reviewed_at = now

    db.commit()
    db.refresh(video)

    return DataResponse(
        data=ScriptResponseData(
            video_id=video.id,
            stt_status=video.stt_status,
            transcript_reviewed_at=video.transcript_reviewed_at,
            segments=sorted_segments,
        )
    )


@router.post(
    "/videos/{video_id}/script/regenerate",
    response_model=DataResponse[ScriptRegenerateResponseData],
)
def regenerate_video_script(
    video_id: int,
    db: DbSession,
    current_user: InstructorUser,
):
    video = verify_video_ownership(video_id, current_user.id, db)
    course = video.course
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(
            409,
            "CONFLICT",
            "DRAFT 또는 EDITING 상태인 강좌에서만 대본을 수정하거나 재생성할 수 있습니다.",
        )

    video.stt_status = "PENDING"
    video.transcript_reviewed_at = None
    db.commit()

    return DataResponse(
        data=ScriptRegenerateResponseData(
            video_id=video.id,
            stt_status="PENDING",
            message="STT 대본 추출 작업이 등록되었습니다.",
        )
    )
