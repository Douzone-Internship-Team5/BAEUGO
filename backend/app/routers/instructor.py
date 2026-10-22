from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile
from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from app.core.auth import (
    get_current_instructor,
    verify_course_ownership,
    verify_section_ownership,
    verify_video_ownership,
)
from app.core.database import DbSession
from app.core.errors import AppError
from app.models.category import Category, Topic
from app.models.course import (
    Course,
    CourseDailyStat,
    Question,
    QuestionChoice,
    Quiz,
    Section,
    Video,
    VideoScript,
)
from app.models.learning import AttemptQuestion, Enrollment, QuestionAnswer
from app.models.user import User
from app.schemas.common import DataResponse
from app.schemas.instructor import (
    CourseCounts,
    CourseCreateRequest,
    CourseDeleteResponseData,
    CourseMutationResponseData,
    CourseStatusUpdateRequest,
    CourseStatusUpdateResponseData,
    CourseUpdateRequest,
    DailyStatItem,
    CourseStatItem,
    InstructorCourseDetail,
    InstructorCourseItem,
    InstructorCourseListResponseData,
    InstructorSectionDetail,
    InstructorStatsResponseData,
    InstructorUnitQuizSummary,
    InstructorVideoSummary,
    ProcessingInfo,
    PublishCheck,
    QuestionItem,
    QuizDetailResponseData,
    QuizRegenerateResponseData,
    QuizUpdateRequest,
    ScriptRegenerateResponseData,
    ScriptResponseData,
    ScriptSegment,
    ScriptUpdateRequest,
    SectionCreateRequest,
    SectionResponseData,
    SectionUpdateRequest,
    StatsComparison,
    StatsPeriod,
    StatsSummary,
    VideoResponseData,
)
from app.services.course_service import (
    calculate_course_processing,
    recalculate_enrollments_progress,
    validate_publish_readiness,
)
from app.services.storage import (
    get_thumbnail_url,
    save_upload_file,
)

router = APIRouter(prefix="/instructor", tags=["instructor"])


# ==========================================
# [B-1] 내 강좌 목록 조회
# ==========================================
@router.get("/courses", response_model=DataResponse[InstructorCourseListResponseData])
def list_my_courses(
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
    status: str | None = Query(None, description="강좌 상태 필터"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    valid_statuses = {"DRAFT", "PUBLISHED", "EDITING", "OPERATOR_HIDDEN"}
    if status is not None and status not in valid_statuses:
        raise AppError(400, "VALIDATION_ERROR", "유효하지 않은 강좌 상태 필터입니다.")

    # 1. 상태별 전체 강좌 수 집계
    status_counts_query = (
        select(Course.status, func.count(Course.id))
        .where(Course.instructor_id == current_user.id)
        .group_by(Course.status)
    )
    raw_counts = db.execute(status_counts_query).all()
    count_map = {s: cnt for s, cnt in raw_counts}
    counts = CourseCounts(
        DRAFT=count_map.get("DRAFT", 0),
        PUBLISHED=count_map.get("PUBLISHED", 0),
        EDITING=count_map.get("EDITING", 0),
        OPERATOR_HIDDEN=count_map.get("OPERATOR_HIDDEN", 0),
    )

    # 2. 강좌 목록 쿼리
    query = select(Course).where(Course.instructor_id == current_user.id)
    if status is not None:
        query = query.where(Course.status == status)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    courses = db.scalars(query.order_by(desc(Course.id)).offset((page - 1) * size).limit(size)).all()

    items: list[InstructorCourseItem] = []
    for c in courses:
        section_cnt = sum(1 for s in c.sections if s.is_active)
        video_cnt = sum(1 for v in c.videos if v.is_active and v.video_type == "LESSON")
        enrollment_cnt = len(c.enrollments)
        completed_proc, total_proc = calculate_course_processing(c)

        items.append(
            InstructorCourseItem(
                course_id=c.id,
                title=c.title,
                thumbnail_storage_key=c.thumbnail_storage_key,
                thumbnail_url=get_thumbnail_url(c.thumbnail_storage_key),
                category_id=c.category_id,
                topic_id=c.topic_id,
                difficulty=c.difficulty,
                price_krw=c.price_krw,
                status=c.status,
                closed_reason=c.closed_reason,
                published_at=c.published_at,
                section_count=section_cnt,
                video_count=video_cnt,
                enrollment_count=enrollment_cnt,
                processing=ProcessingInfo(completed=completed_proc, total=total_proc),
            )
        )

    return DataResponse(
        data=InstructorCourseListResponseData(
            counts=counts,
            items=items,
            page=page,
            size=size,
            total=total,
        )
    )


# ==========================================
# [B-2] 강좌 편집용 상세 조회
# ==========================================
@router.get("/courses/{courseId}", response_model=DataResponse[InstructorCourseDetail])
def get_course_detail_for_edit(
    courseId: int,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
):
    course = verify_course_ownership(courseId, current_user.id, db)

    # 활성 OT 영상
    ot_video_obj = next((v for v in course.videos if v.video_type == "OT" and v.is_active), None)
    ot_video_dto = (
        InstructorVideoSummary(
            video_id=ot_video_obj.id,
            video_type=ot_video_obj.video_type,
            title=ot_video_obj.title,
            duration_seconds=ot_video_obj.duration_seconds,
            sort_order=ot_video_obj.sort_order,
            stt_status=ot_video_obj.stt_status,
            transcript_reviewed_at=ot_video_obj.transcript_reviewed_at,
        )
        if ot_video_obj
        else None
    )

    # 섹션 및 영상 목록
    sections_dto: list[InstructorSectionDetail] = []
    total_lesson_duration = 0
    for s in course.sections:
        if not s.is_active:
            continue

        lesson_videos: list[InstructorVideoSummary] = []
        for v in s.videos:
            if not v.is_active or v.video_type != "LESSON":
                continue
            total_lesson_duration += v.duration_seconds
            lesson_videos.append(
                InstructorVideoSummary(
                    video_id=v.id,
                    video_type=v.video_type,
                    title=v.title,
                    duration_seconds=v.duration_seconds,
                    sort_order=v.sort_order,
                    stt_status=v.stt_status,
                    transcript_reviewed_at=v.transcript_reviewed_at,
                )
            )

        unit_quiz_obj = s.unit_quiz
        unit_quiz_dto = None
        if unit_quiz_obj and unit_quiz_obj.is_active:
            active_q_count = sum(1 for q in unit_quiz_obj.questions if q.is_active)
            unit_quiz_dto = InstructorUnitQuizSummary(
                quiz_id=unit_quiz_obj.id,
                quiz_type=unit_quiz_obj.quiz_type,
                section_id=unit_quiz_obj.section_id,
                ai_status=unit_quiz_obj.ai_status,
                reviewed_at=unit_quiz_obj.reviewed_at,
                question_count=active_q_count,
            )

        sections_dto.append(
            InstructorSectionDetail(
                section_id=s.id,
                title=s.title,
                sort_order=s.sort_order,
                videos=lesson_videos,
                unit_quiz=unit_quiz_dto,
            )
        )

    # 종합 퀴즈
    final_quiz_obj = next((q for q in course.quizzes if q.quiz_type == "FINAL" and q.is_active), None)
    final_quiz_dto = None
    if final_quiz_obj:
        active_q_count = sum(1 for q in final_quiz_obj.questions if q.is_active)
        final_quiz_dto = InstructorUnitQuizSummary(
            quiz_id=final_quiz_obj.id,
            quiz_type=final_quiz_obj.quiz_type,
            section_id=None,
            ai_status=final_quiz_obj.ai_status,
            reviewed_at=final_quiz_obj.reviewed_at,
            question_count=active_q_count,
        )

    # 6대 공개 조건 검증
    is_ready, missing = validate_publish_readiness(course, db)

    return DataResponse(
        data=InstructorCourseDetail(
            course_id=course.id,
            status=course.status,
            closed_reason=course.closed_reason,
            published_at=course.published_at,
            title=course.title,
            description=course.description,
            category_id=course.category_id,
            topic_id=course.topic_id,
            difficulty=course.difficulty,
            price_krw=course.price_krw,
            thumbnail_storage_key=course.thumbnail_storage_key,
            thumbnail_url=get_thumbnail_url(course.thumbnail_storage_key),
            total_lesson_duration_seconds=total_lesson_duration,
            ot_video=ot_video_dto,
            sections=sections_dto,
            final_quiz=final_quiz_dto,
            publish_check=PublishCheck(is_ready=is_ready, missing=missing),
        )
    )


# ==========================================
# [B-3] 강좌 생성 · 수정 · 초안 삭제
# ==========================================
@router.post("/courses", response_model=DataResponse[CourseMutationResponseData], status_code=201)
def create_course(
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
    title: Annotated[str, Form(...)],
    description: Annotated[str, Form(...)],
    categoryId: Annotated[int, Form(...)],
    topicId: Annotated[int, Form(...)],
    difficulty: Annotated[str, Form(...)],
    priceKrw: Annotated[int, Form()] = 0,
    thumbnail: Annotated[UploadFile | None, File()] = None,
):
    # topicId가 categoryId에 속하는지 검증
    topic = db.get(Topic, topicId)
    if not topic or topic.category_id != categoryId:
        raise AppError(400, "VALIDATION_ERROR", "선택한 주제가 해당 직무 카테고리에 속하지 않거나 가격 값이 올바르지 않습니다.")

    if priceKrw < 0:
        raise AppError(400, "VALIDATION_ERROR", "선택한 주제가 해당 직무 카테고리에 속하지 않거나 가격 값이 올바르지 않습니다.")

    course = Course(
        instructor_id=current_user.id,
        category_id=categoryId,
        topic_id=topicId,
        title=title,
        description=description,
        difficulty=difficulty,
        price_krw=priceKrw,
        status="DRAFT",
    )
    db.add(course)
    db.flush()

    if thumbnail:
        storage_key = save_upload_file(thumbnail, f"courses/{course.id}", "thumb")
        course.thumbnail_storage_key = storage_key

    db.commit()
    db.refresh(course)

    return DataResponse(
        data=CourseMutationResponseData(
            course_id=course.id,
            status=course.status,
            title=course.title,
            category_id=course.category_id,
            topic_id=course.topic_id,
            difficulty=course.difficulty,
            price_krw=course.price_krw,
            thumbnail_storage_key=course.thumbnail_storage_key,
            thumbnail_url=get_thumbnail_url(course.thumbnail_storage_key),
        )
    )


@router.patch("/courses/{courseId}", response_model=DataResponse[CourseMutationResponseData])
def update_course(
    courseId: int,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
    title: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
    categoryId: Annotated[int | None, Form()] = None,
    topicId: Annotated[int | None, Form()] = None,
    difficulty: Annotated[str | None, Form()] = None,
    priceKrw: Annotated[int | None, Form()] = None,
    thumbnail: Annotated[UploadFile | None, File()] = None,
):
    course = verify_course_ownership(courseId, current_user.id, db)

    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(
            409,
            "CONFLICT",
            "수정은 DRAFT·EDITING 상태에서만 가능하며, 삭제는 공개된 적 없는 DRAFT 강좌만 가능합니다.",
        )

    target_category_id = categoryId or course.category_id
    if topicId is not None:
        topic = db.get(Topic, topicId)
        if not topic or topic.category_id != target_category_id:
            raise AppError(
                400,
                "VALIDATION_ERROR",
                "선택한 주제가 해당 직무 카테고리에 속하지 않거나 가격 값이 올바르지 않습니다.",
            )
        course.topic_id = topicId

    if categoryId is not None:
        course.category_id = categoryId
    if title is not None:
        course.title = title
    if description is not None:
        course.description = description
    if difficulty is not None:
        course.difficulty = difficulty
    if priceKrw is not None:
        if priceKrw < 0:
            raise AppError(400, "VALIDATION_ERROR", "가격 값이 올바르지 않습니다.")
        course.price_krw = priceKrw

    if thumbnail:
        storage_key = save_upload_file(thumbnail, f"courses/{course.id}", "thumb")
        course.thumbnail_storage_key = storage_key

    db.commit()
    db.refresh(course)

    return DataResponse(
        data=CourseMutationResponseData(
            course_id=course.id,
            status=course.status,
            title=course.title,
            category_id=course.category_id,
            topic_id=course.topic_id,
            difficulty=course.difficulty,
            price_krw=course.price_krw,
            thumbnail_storage_key=course.thumbnail_storage_key,
            thumbnail_url=get_thumbnail_url(course.thumbnail_storage_key),
        )
    )


@router.delete("/courses/{courseId}", response_model=DataResponse[CourseDeleteResponseData])
def delete_course(
    courseId: int,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
):
    course = verify_course_ownership(courseId, current_user.id, db)

    # 한 번도 공개된 적 없는 DRAFT 강좌만 물리 삭제 가능
    if course.status != "DRAFT" or course.published_at is not None:
        raise AppError(
            409,
            "CONFLICT",
            "수정은 DRAFT·EDITING 상태에서만 가능하며, 삭제는 공개된 적 없는 DRAFT 강좌만 가능합니다.",
        )

    deleted_id = course.id
    db.delete(course)
    db.commit()

    return DataResponse(data=CourseDeleteResponseData(deleted_course_id=deleted_id, deleted=True))


# ==========================================
# [B-4] 강좌 공개 및 수정 시작
# ==========================================
@router.put("/courses/{courseId}/status", response_model=DataResponse[CourseStatusUpdateResponseData])
def update_course_status(
    courseId: int,
    req: CourseStatusUpdateRequest,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
):
    course = verify_course_ownership(courseId, current_user.id, db)

    if req.status == "PUBLISHED":
        if course.status not in ("DRAFT", "EDITING"):
            raise AppError(
                409,
                "CONFLICT",
                "현재 강좌 상태에서는 요청한 상태로 전환할 수 없습니다.",
            )

        # 6대 공개 조건 검증
        is_ready, missing = validate_publish_readiness(course, db)
        if not is_ready:
            raise AppError(
                409,
                "PUBLISH_NOT_READY",
                "강좌 공개 조건을 충족하지 못했습니다.",
                missing=missing,
            )

        now = datetime.now(timezone.utc)
        course.status = "PUBLISHED"
        course.published_at = now
        course.closed_by_user_id = None
        course.closed_at = None
        course.closed_reason = None

        # 재공개 시 기존 수강생 이수율 재계산
        recalculate_enrollments_progress(course, db)
        db.commit()
        db.refresh(course)

        return DataResponse(
            data=CourseStatusUpdateResponseData(
                course_id=course.id,
                status="PUBLISHED",
                published_at=course.published_at,
                closed_at=None,
            )
        )

    elif req.status == "EDITING":
        if course.status != "PUBLISHED":
            raise AppError(
                409,
                "CONFLICT",
                "현재 강좌 상태에서는 요청한 상태로 전환할 수 없습니다.",
            )

        now = datetime.now(timezone.utc)
        course.status = "EDITING"
        course.closed_by_user_id = current_user.id
        course.closed_at = now
        db.commit()
        db.refresh(course)

        return DataResponse(
            data=CourseStatusUpdateResponseData(
                course_id=course.id,
                status="EDITING",
                published_at=course.published_at,
                closed_by_user_id=course.closed_by_user_id,
                closed_at=course.closed_at,
            )
        )

    else:
        raise AppError(400, "VALIDATION_ERROR", "유효하지 않은 목표 상태값입니다.")


# ==========================================
# [B-5] 섹션 관리
# ==========================================
@router.post("/sections", response_model=DataResponse[SectionResponseData], status_code=201)
def create_section(
    req: SectionCreateRequest,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
):
    course = verify_course_ownership(req.course_id, current_user.id, db)
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(409, "CONFLICT", "DRAFT 또는 EDITING 상태인 강좌에서만 섹션을 편집할 수 있습니다.")

    # sort_order 자동 배정 (마지막 + 1)
    max_order = db.scalar(
        select(func.coalesce(func.max(Section.sort_order), 0)).where(Section.course_id == course.id)
    )
    new_order = (max_order or 0) + 1

    section = Section(
        course_id=course.id,
        title=req.title,
        sort_order=new_order,
        is_active=True,
    )
    db.add(section)
    db.commit()
    db.refresh(section)

    return DataResponse(
        data=SectionResponseData(
            section_id=section.id,
            course_id=section.course_id,
            title=section.title,
            sort_order=section.sort_order,
            is_active=section.is_active,
        )
    )


@router.patch("/sections/{sectionId}", response_model=DataResponse[SectionResponseData])
def update_section(
    sectionId: int,
    req: SectionUpdateRequest,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
):
    section = verify_section_ownership(sectionId, current_user.id, db)
    course = section.course
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(409, "CONFLICT", "DRAFT 또는 EDITING 상태인 강좌에서만 섹션을 편집할 수 있습니다.")

    if req.title is not None:
        section.title = req.title
    if req.sort_order is not None:
        section.sort_order = req.sort_order

    db.commit()
    db.refresh(section)

    return DataResponse(
        data=SectionResponseData(
            section_id=section.id,
            course_id=section.course_id,
            title=section.title,
            sort_order=section.sort_order,
            is_active=section.is_active,
        )
    )


@router.delete("/sections/{sectionId}", response_model=DataResponse[SectionResponseData])
def delete_section(
    sectionId: int,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
):
    section = verify_section_ownership(sectionId, current_user.id, db)
    course = section.course
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(409, "CONFLICT", "DRAFT 또는 EDITING 상태인 강좌에서만 섹션을 편집할 수 있습니다.")

    # 물리 삭제 금지: is_active = False 처리 (하위 영상 및 퀴즈도 비활성화)
    section.is_active = False
    for v in section.videos:
        v.is_active = False
    if section.unit_quiz:
        section.unit_quiz.is_active = False

    db.commit()
    return DataResponse(
        data=SectionResponseData(
            section_id=section.id,
            course_id=section.course_id,
            isActive=False,
        )
    )


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


# ==========================================
# [B-8] 단위 · 종합 퀴즈 조회 · 수정 · AI 재생성
# ==========================================
@router.get("/courses/{courseId}/quiz", response_model=DataResponse[QuizDetailResponseData])
def get_quiz_detail(
    courseId: int,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
    sectionId: int | None = Query(None, description="섹션 ID (단위 퀴즈 조회 시 전달)"),
):
    course = verify_course_ownership(courseId, current_user.id, db)

    if sectionId is not None:
        verify_section_ownership(sectionId, current_user.id, db)
        quiz = db.scalar(
            select(Quiz).where(
                Quiz.course_id == course.id,
                Quiz.section_id == sectionId,
                Quiz.quiz_type == "UNIT",
                Quiz.is_active.is_(True),
            )
        )
    else:
        quiz = db.scalar(
            select(Quiz).where(
                Quiz.course_id == course.id,
                Quiz.section_id.is_(None),
                Quiz.quiz_type == "FINAL",
                Quiz.is_active.is_(True),
            )
        )

    if not quiz:
        raise AppError(404, "NOT_FOUND", "강좌 또는 섹션을 찾을 수 없습니다.")

    active_questions = [q for q in quiz.questions if q.is_active]
    question_dtos: list[QuestionItem] = []
    for q in active_questions:
        choices = [c.content for c in sorted(q.choices, key=lambda x: x.choice_no)]
        question_dtos.append(
            QuestionItem(
                question_id=q.id,
                order_no=q.order_no,
                generated_by=q.generated_by,
                content=q.content,
                choices=choices,
                correct_choice_no=q.correct_choice_no,
                explanation=q.explanation,
            )
        )

    return DataResponse(
        data=QuizDetailResponseData(
            quiz_id=quiz.id,
            course_id=quiz.course_id,
            section_id=quiz.section_id,
            quiz_type=quiz.quiz_type,
            ai_status=quiz.ai_status,
            reviewed_by=quiz.reviewed_by,
            reviewed_at=quiz.reviewed_at,
            questions=question_dtos,
        )
    )


@router.put("/courses/{courseId}/quiz", response_model=DataResponse[QuizDetailResponseData])
def update_quiz(
    courseId: int,
    req: QuizUpdateRequest,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
    sectionId: int | None = Query(None),
):
    course = verify_course_ownership(courseId, current_user.id, db)
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(409, "CONFLICT", "DRAFT 또는 EDITING 상태인 강좌에서만 퀴즈를 수정하거나 재생성할 수 있습니다.")

    # 3~5문항, 문항당 보기 4개, 정답 1~4 검증
    if len(req.questions) < 3 or len(req.questions) > 5:
        raise AppError(
            400,
            "VALIDATION_ERROR",
            "퀴즈는 3~5문항이어야 하며, 각 문항의 보기(choices)는 정확히 4개, 정답 번호(correctChoiceNo)는 1~4여야 합니다.",
        )
    for q_item in req.questions:
        if len(q_item.choices) != 4 or q_item.correct_choice_no not in (1, 2, 3, 4):
            raise AppError(
                400,
                "VALIDATION_ERROR",
                "퀴즈는 3~5문항이어야 하며, 각 문항의 보기(choices)는 정확히 4개, 정답 번호(correctChoiceNo)는 1~4여야 합니다.",
            )

    # 퀴즈 조회 또는 생성
    if sectionId is not None:
        verify_section_ownership(sectionId, current_user.id, db)
        quiz = db.scalar(
            select(Quiz).where(
                Quiz.course_id == course.id,
                Quiz.section_id == sectionId,
                Quiz.quiz_type == "UNIT",
                Quiz.is_active.is_(True),
            )
        )
        if not quiz:
            quiz = Quiz(
                course_id=course.id,
                section_id=sectionId,
                quiz_type="UNIT",
                ai_status="COMPLETED",
                is_active=True,
            )
            db.add(quiz)
            db.flush()
    else:
        quiz = db.scalar(
            select(Quiz).where(
                Quiz.course_id == course.id,
                Quiz.section_id.is_(None),
                Quiz.quiz_type == "FINAL",
                Quiz.is_active.is_(True),
            )
        )
        if not quiz:
            quiz = Quiz(
                course_id=course.id,
                section_id=None,
                quiz_type="FINAL",
                ai_status=None,
                is_active=True,
            )
            db.add(quiz)
            db.flush()

    # (치명적 제약조건 방어)
    # 기존 활성 문항 중 수강생 제출 이력(attempt_questions)이 있는 문항은 is_active=False 및 order_no = -id 로 음수화하여 보존!
    existing_questions = list(quiz.questions)
    for old_q in existing_questions:
        if not old_q.is_active:
            continue
        has_attempts = db.scalar(
            select(func.count(AttemptQuestion.id)).where(AttemptQuestion.question_id == old_q.id)
        )
        if has_attempts and has_attempts > 0:
            old_q.is_active = False
            old_q.order_no = -int(old_q.id)
        else:
            # 시도 이력이 없는 기존 문항은 삭제
            db.delete(old_q)
    db.flush()

    # 새 문항 삽입
    new_question_dtos: list[QuestionItem] = []
    for idx, q_item in enumerate(req.questions, start=1):
        new_q = Question(
            quiz_id=quiz.id,
            order_no=idx,
            generated_by=q_item.generated_by or ("AI" if quiz.quiz_type == "UNIT" else "INSTRUCTOR"),
            content=q_item.content,
            explanation=q_item.explanation,
            correct_choice_no=q_item.correct_choice_no,
            is_active=True,
        )
        db.add(new_q)
        db.flush()

        for c_idx, choice_text in enumerate(q_item.choices, start=1):
            db.add(
                QuestionChoice(
                    question_id=new_q.id,
                    choice_no=c_idx,
                    content=choice_text,
                )
            )

        new_question_dtos.append(
            QuestionItem(
                question_id=new_q.id,
                order_no=idx,
                generated_by=new_q.generated_by,
                content=new_q.content,
                choices=q_item.choices,
                correct_choice_no=new_q.correct_choice_no,
                explanation=new_q.explanation,
            )
        )

    # 검수 시각 및 검수자 기록
    now = datetime.now(timezone.utc)
    quiz.reviewed_by = current_user.id
    quiz.reviewed_at = now
    if quiz.quiz_type == "UNIT":
        quiz.ai_status = "COMPLETED"

    db.commit()
    db.refresh(quiz)

    return DataResponse(
        data=QuizDetailResponseData(
            quiz_id=quiz.id,
            course_id=quiz.course_id,
            section_id=quiz.section_id,
            quiz_type=quiz.quiz_type,
            ai_status=quiz.ai_status,
            reviewed_by=quiz.reviewed_by,
            reviewed_at=quiz.reviewed_at,
            questions=new_question_dtos,
        )
    )


@router.post("/courses/{courseId}/quiz/regenerate", response_model=DataResponse[QuizRegenerateResponseData])
def regenerate_unit_quiz(
    courseId: int,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
    sectionId: int = Query(..., description="재생성할 단위 퀴즈의 섹션 ID"),
):
    course = verify_course_ownership(courseId, current_user.id, db)
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(409, "CONFLICT", "DRAFT 또는 EDITING 상태인 강좌에서만 퀴즈를 수정하거나 재생성할 수 있습니다.")

    verify_section_ownership(sectionId, current_user.id, db)
    quiz = db.scalar(
        select(Quiz).where(
            Quiz.course_id == course.id,
            Quiz.section_id == sectionId,
            Quiz.quiz_type == "UNIT",
            Quiz.is_active.is_(True),
        )
    )
    if not quiz:
        quiz = Quiz(
            course_id=course.id,
            section_id=sectionId,
            quiz_type="UNIT",
            ai_status="PENDING",
            reviewed_at=None,
            is_active=True,
        )
        db.add(quiz)
    else:
        quiz.ai_status = "PENDING"
        quiz.reviewed_at = None

    db.commit()
    db.refresh(quiz)

    return DataResponse(
        data=QuizRegenerateResponseData(
            quiz_id=quiz.id,
            section_id=quiz.section_id,
            quiz_type=quiz.quiz_type,
            ai_status="PENDING",
            message="단위 퀴즈 AI 생성 작업이 등록되었습니다.",
        )
    )


# ==========================================
# [B-9] 강사 통계 조회
# ==========================================
@router.get("/stats", response_model=DataResponse[InstructorStatsResponseData])
def get_instructor_stats(
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
    courseId: int | None = Query(None),
    fromDate: date | None = Query(None, alias="from"),
    toDate: date | None = Query(None, alias="to"),
    groupBy: str = Query("day"),
):
    if groupBy not in ("day", "course"):
        raise AppError(400, "VALIDATION_ERROR", "groupBy는 day 또는 course만 허용됩니다.")

    if courseId is not None:
        verify_course_ownership(courseId, current_user.id, db)

    # 날짜 기본값 (이번 달 1일 ~ 오늘)
    today = date.today()
    if toDate is None:
        toDate = today
    if fromDate is None:
        fromDate = date(toDate.year, toDate.month, 1)

    # 강사 소유 강좌 목록
    my_courses_query = select(Course).where(Course.instructor_id == current_user.id)
    if courseId is not None:
        my_courses_query = my_courses_query.where(Course.id == courseId)
    my_courses = db.scalars(my_courses_query).all()
    my_course_ids = [c.id for c in my_courses]

    published_course_count = sum(1 for c in my_courses if c.status == "PUBLISHED")

    # 당월 기간 stats 쿼리
    stats_query = select(CourseDailyStat).where(
        CourseDailyStat.course_id.in_(my_course_ids),
        CourseDailyStat.start_date >= fromDate,
        CourseDailyStat.start_date <= toDate,
    )
    stats_rows = db.scalars(stats_query).all() if my_course_ids else []

    lesson_views = sum(s.lesson_views for s in stats_rows)
    new_students = sum(s.new_students for s in stats_rows)
    expected_revenue = sum(s.expected_revenue for s in stats_rows)

    tot_avail_quiz = sum(s.available_quiz_cnt for s in stats_rows)
    tot_first_sub = sum(s.first_submitted_cnt for s in stats_rows)
    quiz_resp_rate = round(tot_first_sub / tot_avail_quiz * 100, 1) if tot_avail_quiz > 0 else None

    tot_first_ans = sum(s.first_answer_cnt for s in stats_rows)
    tot_first_corr = sum(s.first_correct_cnt for s in stats_rows)
    quiz_corr_rate = round(tot_first_corr / tot_first_ans * 100, 1) if tot_first_ans > 0 else None

    # 전월 동기 대비 비교 (comparison) 계산
    # 전월 기간 산출
    try:
        prev_from = (
            fromDate.replace(year=fromDate.year - 1, month=12)
            if fromDate.month == 1
            else fromDate.replace(month=fromDate.month - 1)
        )
        prev_to = (
            toDate.replace(year=toDate.year - 1, month=12)
            if toDate.month == 1
            else toDate.replace(month=toDate.month - 1)
        )
    except ValueError:
        prev_from = fromDate
        prev_to = toDate

    prev_stats_query = select(CourseDailyStat).where(
        CourseDailyStat.course_id.in_(my_course_ids),
        CourseDailyStat.start_date >= prev_from,
        CourseDailyStat.start_date <= prev_to,
    )
    prev_rows = db.scalars(prev_stats_query).all() if my_course_ids else []
    prev_lesson_views = sum(s.lesson_views for s in prev_rows)
    prev_new_students = sum(s.new_students for s in prev_rows)
    prev_expected_revenue = sum(s.expected_revenue for s in prev_rows)

    views_diff_pct = (
        round((lesson_views - prev_lesson_views) / prev_lesson_views * 100, 1)
        if prev_lesson_views > 0
        else 0.0
    )
    students_diff_pct = (
        round((new_students - prev_new_students) / prev_new_students * 100, 1)
        if prev_new_students > 0
        else 0.0
    )
    revenue_diff_pct = (
        round((expected_revenue - prev_expected_revenue) / prev_expected_revenue * 100, 1)
        if prev_expected_revenue > 0
        else 0.0
    )

    comparison = StatsComparison(
        published_course_diff=0,
        lesson_views_diff_percent=views_diff_pct,
        new_students_diff_percent=students_diff_pct,
        expected_revenue_diff_percent=revenue_diff_pct,
        quiz_response_rate_diff_point=0.0,
        quiz_correct_rate_diff_point=0.0,
    )

    summary = StatsSummary(
        published_course_count=published_course_count,
        lesson_views=lesson_views,
        new_students=new_students,
        expected_revenue=expected_revenue,
        quiz_response_rate=quiz_resp_rate,
        quiz_correct_rate=quiz_corr_rate,
        comparison=comparison,
    )

    daily_stats: list[DailyStatItem] | None = None
    course_stats: list[CourseStatItem] | None = None

    if groupBy == "day":
        # 일별 집계
        date_map: dict[str, DailyStatItem] = {}
        for s in stats_rows:
            d_str = s.start_date.isoformat()
            if d_str not in date_map:
                date_map[d_str] = DailyStatItem(
                    date=d_str,
                    lesson_views=0,
                    new_students=0,
                    expected_revenue=0,
                    available_quiz_cnt=0,
                    first_submitted_cnt=0,
                    first_answer_cnt=0,
                    first_correct_cnt=0,
                )
            item = date_map[d_str]
            item.lesson_views += s.lesson_views
            item.new_students += s.new_students
            item.expected_revenue += s.expected_revenue
            item.available_quiz_cnt += s.available_quiz_cnt
            item.first_submitted_cnt += s.first_submitted_cnt
            item.first_answer_cnt += s.first_answer_cnt
            item.first_correct_cnt += s.first_correct_cnt
        daily_stats = sorted(date_map.values(), key=lambda x: x.date)

    elif groupBy == "course":
        # 강좌별 집계
        course_map: dict[int, CourseStatItem] = {}
        c_title_map = {c.id: c.title for c in my_courses}
        c_avail_map: dict[int, int] = {}
        c_sub_map: dict[int, int] = {}
        c_ans_map: dict[int, int] = {}
        c_corr_map: dict[int, int] = {}

        for s in stats_rows:
            cid = s.course_id
            if cid not in course_map:
                course_map[cid] = CourseStatItem(
                    course_id=cid,
                    title=c_title_map.get(cid, ""),
                    lesson_views=0,
                    new_students=0,
                    expected_revenue=0,
                    quiz_response_rate=None,
                    quiz_correct_rate=None,
                )
                c_avail_map[cid] = 0
                c_sub_map[cid] = 0
                c_ans_map[cid] = 0
                c_corr_map[cid] = 0

            c_item = course_map[cid]
            c_item.lesson_views += s.lesson_views
            c_item.new_students += s.new_students
            c_item.expected_revenue += s.expected_revenue
            c_avail_map[cid] += s.available_quiz_cnt
            c_sub_map[cid] += s.first_submitted_cnt
            c_ans_map[cid] += s.first_answer_cnt
            c_corr_map[cid] += s.first_correct_cnt

        for cid, c_item in course_map.items():
            avail = c_avail_map[cid]
            sub = c_sub_map[cid]
            ans = c_ans_map[cid]
            corr = c_corr_map[cid]
            c_item.quiz_response_rate = round(sub / avail * 100, 1) if avail > 0 else None
            c_item.quiz_correct_rate = round(corr / ans * 100, 1) if ans > 0 else None

        course_stats = list(course_map.values())

    return DataResponse(
        data=InstructorStatsResponseData(
            period=StatsPeriod(
                from_date=fromDate.isoformat(),
                to_date=toDate.isoformat(),
                group_by=groupBy,
            ),
            summary=summary,
            daily_stats=daily_stats,
            course_stats=course_stats,
        )
    )
