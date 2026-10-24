from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.errors import AppError
from app.models import Category, Course, Enrollment, Question, Quiz, User, Video
from app.schemas.admin import (
    AdminCourseDetail,
    AdminCourseInstructor,
    AdminCourseItem,
    AdminQuiz,
    AdminScriptSegment,
    AdminSection,
    AdminVideo,
    UnpublishResponse,
)
from app.schemas.common import PageData
from app.schemas.quiz import QuestionItem
from app.services.admin_users import LIKE_ESCAPE, escape_like
from app.services.storage import get_profile_image_url, get_thumbnail_url, get_video_stream_url

# 운영자는 공개 중이거나 직접 내린 강좌만 다룬다. 작성·수정 중인 강좌는 점검 대상이 아니다.
VISIBLE_STATUSES = ("PUBLISHED", "OPERATOR_HIDDEN")
SORTS = ("recent", "enrollments")
MAX_REASON_LENGTH = 500

LIST_FILTER_MESSAGE = "유효하지 않은 상태 또는 정렬 기준입니다."
COURSE_NOT_FOUND_MESSAGE = "존재하지 않는 강좌입니다."
REASON_MESSAGE = f"비공개 사유를 1~{MAX_REASON_LENGTH}자 이내로 입력해 주세요."


# ---------- OP-1 ----------


def list_courses(
    db: Session,
    *,
    status: str | None,
    sort: str | None,
    q: str | None,
    category_id: int | None,
    job_category: str | None,
    page: int,
    size: int,
) -> PageData[AdminCourseItem]:
    if status is not None and status not in VISIBLE_STATUSES:
        raise AppError(
            400,
            "VALIDATION_ERROR",
            LIST_FILTER_MESSAGE,
            fields={"status": "PUBLISHED 또는 OPERATOR_HIDDEN만 사용할 수 있습니다."},
        )
    sort = sort or "recent"
    if sort not in SORTS:
        raise AppError(
            400,
            "VALIDATION_ERROR",
            LIST_FILTER_MESSAGE,
            fields={"sort": "recent 또는 enrollments만 사용할 수 있습니다."},
        )
    check_category_filter(db, category_id, job_category)

    enrollment_counts = (
        select(Enrollment.course_id, func.count().label("count"))
        .group_by(Enrollment.course_id)
        .subquery()
    )
    enrollment_count = func.coalesce(enrollment_counts.c.count, 0)
    query = (
        select(Course, User.name, Category.code, enrollment_count)
        .join(User, User.id == Course.instructor_id)
        .join(Category, Category.id == Course.category_id)
        .outerjoin(enrollment_counts, enrollment_counts.c.course_id == Course.id)
        .where(Course.status.in_([status] if status else VISIBLE_STATUSES))
    )
    keyword = q.strip() if q else ""
    if keyword:
        pattern = f"%{escape_like(keyword)}%"
        query = query.where(
            or_(
                Course.title.ilike(pattern, escape=LIKE_ESCAPE),
                User.name.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    if category_id is not None:
        query = query.where(Course.category_id == category_id)
    elif job_category is not None:
        query = query.where(Category.code == job_category)

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    recent_first = (Course.published_at.desc().nulls_last(), Course.id.desc())
    order_by = recent_first if sort == "recent" else (enrollment_count.desc(), *recent_first)
    rows = db.execute(query.order_by(*order_by).offset((page - 1) * size).limit(size)).all()

    items = [
        AdminCourseItem(
            course_id=course.id,
            title=course.title,
            instructor_id=course.instructor_id,
            instructor_name=instructor_name,
            category_id=course.category_id,
            job_category=category_code,
            status=course.status,
            price_krw=course.price_krw,
            enrollment_count=count,
            published_at=course.published_at,
            closed_by_user_id=course.closed_by_user_id,
            closed_at=course.closed_at,
            closed_reason=course.closed_reason,
        )
        for course, instructor_name, category_code, count in rows
    ]
    return PageData(items=items, page=page, size=size, total=total)


def check_category_filter(db: Session, category_id: int | None, job_category: str | None) -> None:
    """categoryId가 우선이다. 둘 다 오면 같은 직무를 가리켜야 한다."""
    if category_id is None or job_category is None:
        return
    category = db.get(Category, category_id)
    if category is not None and category.code != job_category:
        raise AppError(
            400,
            "VALIDATION_ERROR",
            LIST_FILTER_MESSAGE,
            fields={"jobCategory": "categoryId와 jobCategory가 서로 다른 직무입니다."},
        )


# ---------- OP-2 ----------


def get_course_detail(db: Session, course_id: int) -> AdminCourseDetail:
    course = db.scalar(
        select(Course)
        .where(Course.id == course_id)
        .options(
            joinedload(Course.instructor).joinedload(User.instructor_profile),
            joinedload(Course.category),
            joinedload(Course.topic),
            selectinload(Course.sections),
            selectinload(Course.videos).selectinload(Video.scripts),
            selectinload(Course.quizzes)
            .selectinload(Quiz.questions)
            .selectinload(Question.choices),
        )
    )
    if course is None or course.status not in VISIBLE_STATUSES:
        raise AppError(404, "NOT_FOUND", COURSE_NOT_FOUND_MESSAGE)

    active_sections = sorted(
        (section for section in course.sections if section.is_active),
        key=lambda section: (section.sort_order, section.id),
    )
    active_videos = sorted(
        (video for video in course.videos if video.is_active),
        key=lambda video: (video.sort_order, video.id),
    )
    active_quizzes = [quiz for quiz in course.quizzes if quiz.is_active]
    active_section_ids = {section.id for section in active_sections}
    lessons = [
        video
        for video in active_videos
        if video.video_type == "LESSON" and video.section_id in active_section_ids
    ]
    ot_video = next((video for video in active_videos if video.video_type == "OT"), None)
    final_quiz = next((quiz for quiz in active_quizzes if quiz.quiz_type == "FINAL"), None)

    sections = [
        AdminSection(
            section_id=section.id,
            title=section.title,
            sort_order=section.sort_order,
            videos=[build_video(video) for video in lessons if video.section_id == section.id],
            unit_quiz=build_quiz(
                next(
                    (
                        quiz
                        for quiz in active_quizzes
                        if quiz.quiz_type == "UNIT" and quiz.section_id == section.id
                    ),
                    None,
                )
            ),
        )
        for section in active_sections
    ]

    return AdminCourseDetail(
        course_id=course.id,
        status=course.status,
        title=course.title,
        description=course.description,
        category_id=course.category_id,
        category_code=course.category.code,
        category_name=course.category.name,
        topic_id=course.topic_id,
        topic_name=course.topic.name,
        difficulty=course.difficulty,
        price_krw=course.price_krw,
        thumbnail_storage_key=course.thumbnail_storage_key,
        thumbnail_url=get_thumbnail_url(course.thumbnail_storage_key),
        published_at=course.published_at,
        closed_at=course.closed_at,
        closed_reason=course.closed_reason,
        closed_by_user_id=course.closed_by_user_id,
        instructor=build_instructor(course.instructor),
        total_lesson_duration_seconds=sum(video.duration_seconds for video in lessons),
        total_lesson_count=len(lessons),
        enrollment_count=count_enrollments(db, course.id),
        ot_video=build_video(ot_video) if ot_video else None,
        sections=sections,
        final_quiz=build_quiz(final_quiz),
    )


def build_instructor(instructor: User) -> AdminCourseInstructor:
    profile = instructor.instructor_profile
    return AdminCourseInstructor(
        id=instructor.id,
        name=instructor.name,
        email=instructor.email,
        bio=profile.bio if profile else None,
        profile_image_key=instructor.profile_image_key,
        profile_image_url=get_profile_image_url(instructor.profile_image_key),
    )


def build_video(video: Video) -> AdminVideo:
    return AdminVideo(
        video_id=video.id,
        video_type=video.video_type,
        title=video.title,
        duration_seconds=video.duration_seconds,
        sort_order=video.sort_order,
        video_storage_key=video.video_storage_key,
        video_url=get_video_stream_url(video.id),
        thumbnail_storage_key=video.thumbnail_storage_key,
        thumbnail_url=get_thumbnail_url(video.thumbnail_storage_key),
        stt_status=video.stt_status,
        transcript_reviewed_at=video.transcript_reviewed_at,
        segments=[
            AdminScriptSegment(
                id=script.id,
                seq=script.seq,
                start_ms=script.start_ms,
                end_ms=script.end_ms,
                content=script.content,
            )
            for script in video.scripts
        ],
    )


def build_quiz(quiz: Quiz | None) -> AdminQuiz | None:
    if quiz is None:
        return None
    return AdminQuiz(
        quiz_id=quiz.id,
        quiz_type=quiz.quiz_type,
        section_id=quiz.section_id,
        ai_status=quiz.ai_status,
        reviewed_by=quiz.reviewed_by,
        reviewed_at=quiz.reviewed_at,
        questions=[
            QuestionItem(
                question_id=question.id,
                order_no=question.order_no,
                generated_by=question.generated_by,
                content=question.content,
                choices=[choice.content for choice in question.choices],
                correct_choice_no=question.correct_choice_no,
                explanation=question.explanation,
            )
            for question in quiz.questions
            if question.is_active
        ],
    )


def count_enrollments(db: Session, course_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(Enrollment).where(Enrollment.course_id == course_id)
    )


# ---------- OP-3 ----------


def unpublish_course(
    db: Session, operator: User, course_id: int, reason: str | None
) -> UnpublishResponse:
    reason_text = reason.strip() if reason else ""
    if not 1 <= len(reason_text) <= MAX_REASON_LENGTH:
        raise AppError(400, "VALIDATION_ERROR", REASON_MESSAGE, fields={"reason": REASON_MESSAGE})

    # 강사의 수정 시작(B-4)과 동시에 들어와도 상태가 어긋나지 않게 행을 잠그고 확인한다.
    course = db.get(Course, course_id, with_for_update=True, populate_existing=True)
    if course is None:
        raise AppError(404, "NOT_FOUND", COURSE_NOT_FOUND_MESSAGE)
    if course.status != "PUBLISHED":
        raise AppError(
            409, "NOT_PUBLISHED", "현재 공개 중인(PUBLISHED) 강좌만 비공개 처리할 수 있습니다."
        )

    course.status = "OPERATOR_HIDDEN"
    course.closed_by_user_id = operator.id
    course.closed_at = datetime.now(UTC)
    course.closed_reason = reason_text
    db.commit()

    return UnpublishResponse(
        course_id=course.id,
        status=course.status,
        closed_by_user_id=course.closed_by_user_id,
        closed_reason=course.closed_reason,
        closed_at=course.closed_at,
    )
