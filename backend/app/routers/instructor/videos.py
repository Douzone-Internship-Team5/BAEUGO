from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import delete, func, select

from app.core.auth import (
    get_current_instructor,
    verify_course_ownership,
    verify_section_ownership,
    verify_video_ownership,
)
from app.core.database import DbSession
from app.core.errors import AppError
from app.models.course import Quiz, Video, VideoScript
from app.models.user import User
from app.schemas.common import DataResponse
from app.schemas.instructor import (
    ScriptRegenerateResponseData,
    ScriptResponseData,
    ScriptSegment,
    ScriptUpdateRequest,
    VideoResponseData,
)
from app.services.storage import save_upload_file

router = APIRouter(tags=["instructor-videos"])


# ==========================================
# [B-6] 영상 관리 (OT 포함)
# ==========================================
@router.post("/videos", response_model=DataResponse[VideoResponseData], status_code=201)
def upload_video(
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
    courseId: Annotated[int, Form(...)],
    videoType: Annotated[str, Form(...)],
    title: Annotated[str, Form(...)],
    file: Annotated[UploadFile, File(...)],
    sectionId: Annotated[int | None, Form()] = None,
    thumbnail: Annotated[UploadFile | None, File()] = None,
):
    course = verify_course_ownership(courseId, current_user.id, db)
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(409, "CONFLICT", "DRAFT 또는 EDITING 상태인 강좌에서만 영상을 편집할 수 있습니다.")

    if videoType not in ("OT", "LESSON"):
        raise AppError(400, "VALIDATION_ERROR", "영상 유형은 OT 또는 LESSON이어야 합니다.")

    if videoType == "LESSON" and sectionId is None:
        raise AppError(400, "VALIDATION_ERROR", "학습 영상(LESSON)은 sectionId가 필수입니다.")

    # 영상 파일 최대 200MB 검증
    MAX_VIDEO_SIZE = 200 * 1024 * 1024
    if file.size and file.size > MAX_VIDEO_SIZE:
        raise AppError(400, "INVALID_FILE", "재생할 수 없거나 200MB를 초과한 영상 파일입니다.")

    if sectionId is not None:
        verify_section_ownership(sectionId, current_user.id, db)

    # OT는 강좌당 활성 1개만 유지: 새 OT 등록 시 기존 OT is_active = False
    if videoType == "OT":
        sectionId = None
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
                Video.section_id == sectionId, Video.is_active.is_(True)
            )
        )
        sort_order = (max_order or 0) + 1

    # 파일 저장
    video_key = save_upload_file(file, f"courses/{course.id}/videos", "video")
    thumb_key = None
    if thumbnail:
        thumb_key = save_upload_file(thumbnail, f"courses/{course.id}/videos", "vthumb")

    video = Video(
        course_id=course.id,
        section_id=sectionId,
        video_type=videoType,
        title=title,
        duration_seconds=360,  # 기본/모의 재생 시간
        sort_order=sort_order,
        video_storage_key=video_key,
        thumbnail_storage_key=thumb_key,
        stt_status="PENDING",
        transcript_reviewed_at=None,
        is_active=True,
    )
    db.add(video)

    # 섹션 단위 퀴즈 재생성 연동
    if videoType == "LESSON" and sectionId is not None:
        unit_quiz = db.scalar(
            select(Quiz).where(
                Quiz.section_id == sectionId,
                Quiz.quiz_type == "UNIT",
                Quiz.is_active.is_(True),
            )
        )
        if unit_quiz:
            unit_quiz.ai_status = "PENDING"
            unit_quiz.reviewed_at = None

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


@router.patch("/videos/{videoId}", response_model=DataResponse[VideoResponseData])
def update_or_replace_video(
    videoId: int,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
    title: Annotated[str | None, Form()] = None,
    sortOrder: Annotated[int | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
    thumbnail: Annotated[UploadFile | None, File()] = None,
):
    video = verify_video_ownership(videoId, current_user.id, db)
    course = video.course
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(409, "CONFLICT", "DRAFT 또는 EDITING 상태인 강좌에서만 영상을 편집할 수 있습니다.")

    # 파일이 제공된 경우: 새 videoId 행 생성 규칙 적용!
    if file is not None:
        video.is_active = False

        new_video_key = save_upload_file(file, f"courses/{course.id}/videos", "video")
        new_thumb_key = video.thumbnail_storage_key
        if thumbnail is not None:
            new_thumb_key = save_upload_file(thumbnail, f"courses/{course.id}/videos", "vthumb")

        new_video = Video(
            course_id=video.course_id,
            section_id=video.section_id,
            video_type=video.video_type,
            title=title or video.title,
            duration_seconds=video.duration_seconds,
            sort_order=sortOrder if sortOrder is not None else video.sort_order,
            video_storage_key=new_video_key,
            thumbnail_storage_key=new_thumb_key,
            stt_status="PENDING",
            transcript_reviewed_at=None,
            is_active=True,
        )
        db.add(new_video)

        # 단위 퀴즈 재생성 연동
        if video.section_id:
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
    if sortOrder is not None:
        video.sort_order = sortOrder
    if thumbnail is not None:
        video.thumbnail_storage_key = save_upload_file(thumbnail, f"courses/{course.id}/videos", "vthumb")

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


@router.delete("/videos/{videoId}", response_model=DataResponse[VideoResponseData])
def delete_video(
    videoId: int,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
):
    video = verify_video_ownership(videoId, current_user.id, db)
    course = video.course
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(409, "CONFLICT", "DRAFT 또는 EDITING 상태인 강좌에서만 영상을 편집할 수 있습니다.")

    video.is_active = False
    db.commit()

    return DataResponse(
        data=VideoResponseData(
            video_id=video.id,
            course_id=video.course_id,
            is_active=False,
        )
    )


# ==========================================
# [B-7] 대본 조회 · 수정 · STT 재시도
# ==========================================
@router.get("/videos/{videoId}/script", response_model=DataResponse[ScriptResponseData])
def get_video_script(
    videoId: int,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
):
    video = verify_video_ownership(videoId, current_user.id, db)
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


@router.put("/videos/{videoId}/script", response_model=DataResponse[ScriptResponseData])
def update_video_script(
    videoId: int,
    req: ScriptUpdateRequest,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
):
    video = verify_video_ownership(videoId, current_user.id, db)
    course = video.course
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(409, "CONFLICT", "DRAFT 또는 EDITING 상태인 강좌에서만 대본을 수정하거나 재생성할 수 있습니다.")

    # end_ms > start_ms 검증
    for seg in req.segments:
        if seg.end_ms <= seg.start_ms:
            raise AppError(400, "VALIDATION_ERROR", "자막 종료 시점(endMs)은 시작 시점(startMs)보다 커야 합니다.")

    # 기존 자막 삭제 후 교체
    db.execute(delete(VideoScript).where(VideoScript.video_id == video.id))
    for seg in req.segments:
        db.add(
            VideoScript(
                video_id=video.id,
                seq=seg.seq,
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                content=seg.content,
            )
        )

    now = datetime.now(timezone.utc)
    video.stt_status = "COMPLETED"
    video.transcript_reviewed_at = now

    db.commit()
    db.refresh(video)

    return DataResponse(
        data=ScriptResponseData(
            video_id=video.id,
            stt_status=video.stt_status,
            transcript_reviewed_at=video.transcript_reviewed_at,
            segments=req.segments,
        )
    )


@router.post("/videos/{videoId}/script/regenerate", response_model=DataResponse[ScriptRegenerateResponseData])
def regenerate_video_script(
    videoId: int,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
):
    video = verify_video_ownership(videoId, current_user.id, db)
    course = video.course
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(409, "CONFLICT", "DRAFT 또는 EDITING 상태인 강좌에서만 대본을 수정하거나 재생성할 수 있습니다.")

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
