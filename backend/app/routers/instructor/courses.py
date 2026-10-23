from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request, UploadFile
from sqlalchemy import desc, func, select
from sqlalchemy.orm import selectinload

from app.core.auth import (
    InstructorUser,
    verify_course_ownership,
)
from app.core.database import DbSession
from app.core.errors import AppError
from app.models.category import Category, Topic
from app.models.course import Course, Question, Quiz, Section
from app.schemas.common import DataResponse
from app.schemas.instructor import (
    CourseCounts,
    CourseDeleteResponseData,
    CourseMutationResponseData,
    CourseStatusUpdateRequest,
    CourseStatusUpdateResponseData,
    InstructorCourseDetail,
    InstructorCourseItem,
    InstructorCourseListResponseData,
    InstructorSectionDetail,
    InstructorUnitQuizSummary,
    InstructorVideoSummary,
    ProcessingInfo,
    PublishCheck,
)
from app.services.course_service import (
    calculate_course_processing,
    recalculate_enrollments_progress,
    validate_publish_readiness,
)
from app.services.storage import (
    get_thumbnail_url,
    save_upload_file,
    validate_thumbnail_file,
)

router = APIRouter(tags=["instructor-courses"])

VALID_DIFFICULTIES = {"BEGINNER", "INTERMEDIATE", "ADVANCED"}


def _parse_int_field(val: Any, field_name: str) -> int:
    if isinstance(val, bool) or val is None:
        raise AppError(400, "VALIDATION_ERROR", f"{field_name} 값이 올바르지 않습니다.")
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        s = val.strip()
        if not s:
            raise AppError(400, "VALIDATION_ERROR", f"{field_name} 값이 올바르지 않습니다.")
        try:
            return int(s)
        except ValueError as err:
            raise AppError(
                400, "VALIDATION_ERROR", f"{field_name} 값이 올바르지 않습니다."
            ) from err
    raise AppError(400, "VALIDATION_ERROR", f"{field_name} 값이 올바르지 않습니다.")


# ==========================================
# [B-1] 내 강좌 목록 조회
# ==========================================
@router.get("/courses", response_model=DataResponse[InstructorCourseListResponseData])
def list_my_courses(
    db: DbSession,
    current_user: InstructorUser,
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

    # 2. 강좌 목록 쿼리 (N+1 방지 eager loading 적용)
    query = (
        select(Course)
        .where(Course.instructor_id == current_user.id)
        .options(
            selectinload(Course.sections),
            selectinload(Course.videos),
            selectinload(Course.quizzes),
            selectinload(Course.enrollments),
        )
    )
    if status is not None:
        query = query.where(Course.status == status)

    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    courses = db.scalars(
        query.order_by(desc(Course.id)).offset((page - 1) * size).limit(size)
    ).all()

    items: list[InstructorCourseItem] = []
    for c in courses:
        active_sec_ids = {s.id for s in c.sections if s.is_active}
        section_cnt = len(active_sec_ids)
        video_cnt = sum(
            1
            for v in c.videos
            if v.is_active
            and v.video_type == "LESSON"
            and (v.section_id is None or v.section_id in active_sec_ids)
        )
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
@router.get("/courses/{course_id}", response_model=DataResponse[InstructorCourseDetail])
def get_course_detail_for_edit(
    course_id: int,
    db: DbSession,
    current_user: InstructorUser,
):
    course = db.scalar(
        select(Course)
        .where(Course.id == course_id, Course.instructor_id == current_user.id)
        .options(
            selectinload(Course.videos),
            selectinload(Course.sections).selectinload(Section.videos),
            selectinload(Course.quizzes)
            .selectinload(Quiz.questions)
            .selectinload(Question.choices),
        )
    )
    if not course:
        raise AppError(404, "NOT_FOUND", "존재하지 않거나 접근 권한이 없는 강좌입니다.")

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
    for s in sorted(course.sections, key=lambda x: (x.sort_order, x.id)):
        if not s.is_active:
            continue

        lesson_videos: list[InstructorVideoSummary] = []
        for v in sorted(s.videos, key=lambda x: (x.sort_order, x.id)):
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

        unit_quiz_obj = next(
            (
                q
                for q in course.quizzes
                if q.section_id == s.id and q.quiz_type == "UNIT" and q.is_active
            ),
            None,
        )
        unit_quiz_dto = None
        if unit_quiz_obj:
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
    final_quiz_obj = next(
        (q for q in course.quizzes if q.quiz_type == "FINAL" and q.is_active), None
    )
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
async def create_course(
    request: Request,
    db: DbSession,
    current_user: InstructorUser,
):
    content_type = request.headers.get("content-type", "")
    thumbnail: UploadFile | None = None

    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception as err:
            raise AppError(400, "VALIDATION_ERROR", "입력값을 확인해 주세요.") from err
        if not isinstance(body, dict):
            raise AppError(400, "VALIDATION_ERROR", "입력값을 확인해 주세요.")
        title_raw = body.get("title")
        description_raw = body.get("description")
        category_raw = body.get("categoryId", body.get("category_id"))
        topic_raw = body.get("topicId", body.get("topic_id"))
        difficulty_raw = body.get("difficulty")
        price_raw = body.get("priceKrw", body.get("price_krw", 0))
    else:
        form = await request.form()
        title_raw = form.get("title")
        description_raw = form.get("description")
        category_raw = form.get("categoryId", form.get("category_id"))
        topic_raw = form.get("topicId", form.get("topic_id"))
        difficulty_raw = form.get("difficulty")
        price_raw = form.get("priceKrw", form.get("price_krw", 0))
        thumb_val = form.get("thumbnail")
        if isinstance(thumb_val, UploadFile) or hasattr(thumb_val, "filename"):
            thumbnail = thumb_val  # type: ignore[assignment]

    if (
        title_raw is None
        or description_raw is None
        or category_raw is None
        or topic_raw is None
        or difficulty_raw is None
    ):
        raise AppError(400, "VALIDATION_ERROR", "입력값을 확인해 주세요.")

    if not isinstance(title_raw, str) or not title_raw.strip() or len(title_raw) > 200:
        raise AppError(400, "VALIDATION_ERROR", "강좌 제목은 1~200자 이내로 입력해 주세요.")
    if not isinstance(description_raw, str) or not description_raw.strip():
        raise AppError(400, "VALIDATION_ERROR", "강좌 소개를 입력해 주세요.")
    if not isinstance(difficulty_raw, str) or difficulty_raw not in VALID_DIFFICULTIES:
        raise AppError(400, "VALIDATION_ERROR", "유효하지 않은 난이도입니다.")

    category_id = _parse_int_field(category_raw, "categoryId")
    topic_id = _parse_int_field(topic_raw, "topicId")
    price_krw = (
        0 if (price_raw is None or price_raw == "") else _parse_int_field(price_raw, "priceKrw")
    )

    # category_id 및 topic_id 소속/활성 검증
    category = db.get(Category, category_id)
    topic = db.get(Topic, topic_id)
    if (
        not category
        or not category.is_active
        or not topic
        or not topic.is_active
        or topic.category_id != category_id
    ):
        raise AppError(
            400,
            "VALIDATION_ERROR",
            "선택한 주제가 해당 직무 카테고리에 속하지 않거나 가격 값이 올바르지 않습니다.",
        )

    if price_krw < 0:
        raise AppError(
            400,
            "VALIDATION_ERROR",
            "선택한 주제가 해당 직무 카테고리에 속하지 않거나 가격 값이 올바르지 않습니다.",
        )

    if thumbnail is not None:
        validate_thumbnail_file(thumbnail)

    course = Course(
        instructor_id=current_user.id,
        category_id=category_id,
        topic_id=topic_id,
        title=title_raw,
        description=description_raw,
        difficulty=difficulty_raw,
        price_krw=price_krw,
        status="DRAFT",
    )
    db.add(course)
    db.flush()

    if thumbnail is not None:
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


@router.patch("/courses/{course_id}", response_model=DataResponse[CourseMutationResponseData])
async def update_course(
    course_id: int,
    request: Request,
    db: DbSession,
    current_user: InstructorUser,
):
    course = verify_course_ownership(course_id, current_user.id, db)

    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(
            409,
            "CONFLICT",
            "수정은 DRAFT·EDITING 상태에서만 가능하며, "
            "삭제는 공개된 적 없는 DRAFT 강좌만 가능합니다.",
        )

    content_type = request.headers.get("content-type", "")
    thumbnail: UploadFile | None = None

    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception as err:
            raise AppError(400, "VALIDATION_ERROR", "입력값을 확인해 주세요.") from err
        if not isinstance(body, dict):
            raise AppError(400, "VALIDATION_ERROR", "입력값을 확인해 주세요.")
        title_raw = body.get("title")
        description_raw = body.get("description")
        category_raw = body.get("categoryId", body.get("category_id"))
        topic_raw = body.get("topicId", body.get("topic_id"))
        difficulty_raw = body.get("difficulty")
        price_raw = body.get("priceKrw", body.get("price_krw"))
    else:
        form = await request.form()
        title_raw = form.get("title")
        description_raw = form.get("description")
        category_raw = form.get("categoryId", form.get("category_id"))
        topic_raw = form.get("topicId", form.get("topic_id"))
        difficulty_raw = form.get("difficulty")
        price_raw = form.get("priceKrw", form.get("price_krw"))
        thumb_val = form.get("thumbnail")
        if isinstance(thumb_val, UploadFile) or hasattr(thumb_val, "filename"):
            thumbnail = thumb_val  # type: ignore[assignment]

    category_id = _parse_int_field(category_raw, "categoryId") if category_raw is not None else None
    topic_id = _parse_int_field(topic_raw, "topicId") if topic_raw is not None else None
    price_krw = _parse_int_field(price_raw, "priceKrw") if price_raw is not None else None

    if category_id is not None or topic_id is not None:
        target_category_id = category_id if category_id is not None else course.category_id
        target_topic_id = topic_id if topic_id is not None else course.topic_id
        category = db.get(Category, target_category_id)
        topic = db.get(Topic, target_topic_id)
        if (
            not category
            or not category.is_active
            or not topic
            or not topic.is_active
            or topic.category_id != target_category_id
        ):
            raise AppError(
                400,
                "VALIDATION_ERROR",
                "선택한 주제가 해당 직무 카테고리에 속하지 않거나 가격 값이 올바르지 않습니다.",
            )
        course.category_id = target_category_id
        course.topic_id = target_topic_id

    if title_raw is not None:
        if not isinstance(title_raw, str) or not title_raw.strip() or len(title_raw) > 200:
            raise AppError(400, "VALIDATION_ERROR", "강좌 제목은 1~200자 이내로 입력해 주세요.")
        course.title = title_raw

    if description_raw is not None:
        if not isinstance(description_raw, str) or not description_raw.strip():
            raise AppError(400, "VALIDATION_ERROR", "강좌 소개를 입력해 주세요.")
        course.description = description_raw

    if difficulty_raw is not None:
        if not isinstance(difficulty_raw, str) or difficulty_raw not in VALID_DIFFICULTIES:
            raise AppError(400, "VALIDATION_ERROR", "유효하지 않은 난이도입니다.")
        course.difficulty = difficulty_raw

    if price_krw is not None:
        if price_krw < 0:
            raise AppError(400, "VALIDATION_ERROR", "가격 값이 올바르지 않습니다.")
        course.price_krw = price_krw

    if thumbnail is not None:
        validate_thumbnail_file(thumbnail)
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


@router.delete("/courses/{course_id}", response_model=DataResponse[CourseDeleteResponseData])
def delete_course(
    course_id: int,
    db: DbSession,
    current_user: InstructorUser,
):
    course = verify_course_ownership(course_id, current_user.id, db)

    # 한 번도 공개된 적 없는 DRAFT 강좌만 물리 삭제 가능
    if course.status != "DRAFT" or course.published_at is not None:
        raise AppError(
            409,
            "CONFLICT",
            "수정은 DRAFT·EDITING 상태에서만 가능하며, "
            "삭제는 공개된 적 없는 DRAFT 강좌만 가능합니다.",
        )

    deleted_id = course.id
    db.delete(course)
    db.commit()

    return DataResponse(data=CourseDeleteResponseData(deleted_course_id=deleted_id, deleted=True))


# ==========================================
# [B-4] 강좌 공개 및 수정 시작
# ==========================================
@router.put(
    "/courses/{course_id}/status", response_model=DataResponse[CourseStatusUpdateResponseData]
)
def update_course_status(
    course_id: int,
    req: CourseStatusUpdateRequest,
    db: DbSession,
    current_user: InstructorUser,
):
    course = db.scalar(
        select(Course)
        .where(Course.id == course_id, Course.instructor_id == current_user.id)
        .options(
            selectinload(Course.videos),
            selectinload(Course.sections).selectinload(Section.videos),
            selectinload(Course.quizzes)
            .selectinload(Quiz.questions)
            .selectinload(Question.choices),
        )
    )
    if not course:
        raise AppError(404, "NOT_FOUND", "존재하지 않거나 접근 권한이 없는 강좌입니다.")

    if req.status == "PUBLISHED":
        if course.status not in ("DRAFT", "EDITING"):
            raise AppError(
                409,
                "CONFLICT",
                "현재 강좌 상태(OPERATOR_HIDDEN 등)에서는 요청한 상태로 전환할 수 없습니다.",
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

        now = datetime.now(UTC)
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
                "현재 강좌 상태(OPERATOR_HIDDEN 등)에서는 요청한 상태로 전환할 수 없습니다.",
            )

        now = datetime.now(UTC)
        course.status = "EDITING"
        course.closed_by_user_id = current_user.id
        course.closed_at = now
        course.closed_reason = None
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
