from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.errors import AppError
from app.core.timezone import SEOUL_TZ, today_in_seoul
from app.models import (
    AccountType,
    AttendanceDay,
    Course,
    CourseDailyStat,
    Enrollment,
    LearningGoal,
    QuestionRecord,
    Quiz,
    QuizAttempt,
    Role,
    Section,
    StudyActivitySession,
    User,
    Video,
    VideoLike,
    VideoProgress,
    VideoView,
)
from app.schemas.learning import (
    CourseCurriculumQuiz,
    CourseCurriculumSection,
    CourseCurriculumVideo,
    CourseDetailEnrollment,
    CourseDetailInstructor,
    CourseDetailOtVideo,
    CourseDetailResponse,
    EnrollmentCounts,
    EnrollmentNextResponse,
    EnrollRequest,
    EnrollResponse,
    LikedVideoItem,
    LikedVideoListResponse,
    MyEnrollmentItem,
    MyEnrollmentListResponse,
    VideoDetailResponse,
    VideoLikeResponse,
    VideoNavigation,
    VideoProgressRequest,
    VideoProgressResponse,
    VideoProgressSummary,
    VideoSegmentItem,
    VideoViewRequest,
    VideoViewResponse,
)
from app.schemas.search import CategorySimple, TopicSimple
from app.services.course_service import recalculate_enrollments_progress
from app.services.storage import get_thumbnail_url, get_video_stream_url

DEFAULT_DAILY_GOAL_MINUTES = 20

COURSE_NOT_FOUND_MESSAGE = "존재하지 않는 강좌입니다."
COURSE_HIDDEN_MESSAGE = "현재 비공개 상태인 강좌입니다."
ENROLL_MODE_FORBIDDEN_MESSAGE = "수강자 모드에서만 강좌를 수강 신청할 수 있습니다."
ENROLL_CONFLICT_MESSAGE = "이미 수강 등록된 강좌이거나 현재 공개 중인 강좌가 아닙니다."
ENROLLMENT_NOT_FOUND_MESSAGE = "존재하지 않는 수강 내역입니다."
ENROLLMENT_FORBIDDEN_MESSAGE = "본인의 수강 내역만 조회할 수 있습니다."
VIDEO_NOT_FOUND_MESSAGE = "존재하지 않거나 비활성화된 영상입니다."
VIDEO_VIEW_NOT_FOUND_MESSAGE = "존재하지 않는 영상입니다."
VIDEO_LOCKED_MESSAGE = "수강 등록이 되어 있지 않거나 이전 학습 단계를 먼저 완료해야 합니다."
VIDEO_VIEW_MIN_SECONDS_MESSAGE = "실제 재생 시간이 3초 이상이어야 조회수가 집계됩니다."
VIDEO_PROGRESS_VALIDATION_MESSAGE = (
    "무료 OT 영상은 진도 저장 대상이 아니거나 시청 구간 값이 올바르지 않습니다."
)
VIDEO_PROGRESS_FORBIDDEN_MESSAGE = "수강 중인 강좌의 영상만 진도를 저장할 수 있습니다."
VIDEO_LIKE_FORBIDDEN_MESSAGE = "미수강 상태에서는 유료 학습 영상에 좋아요를 누를 수 없습니다."


@dataclass
class CurriculumItem:
    item_type: str  # "VIDEO", "UNIT_QUIZ", "FINAL_QUIZ"
    item_id: int
    section_id: int | None
    is_completed: bool
    is_locked: bool


def _is_learner_mode(user: User | None) -> bool:
    if user is None:
        return False
    return (
        user.account_type == AccountType.MEMBER
        and user.active_mode == Role.LEARNER
        and user.has_role(Role.LEARNER)
    )


def _is_operator(user: User | None) -> bool:
    if user is None:
        return False
    return user.account_type == AccountType.OPERATOR and user.active_mode is None


def _is_course_owner(user: User | None, course: Course) -> bool:
    if user is None:
        return False
    return user.id == course.instructor_id


def _load_course_with_curriculum(db: Session, course_id: int) -> Course | None:
    return db.scalar(
        select(Course)
        .where(Course.id == course_id)
        .options(
            joinedload(Course.instructor).joinedload(User.instructor_profile),
            joinedload(Course.category),
            joinedload(Course.topic),
            selectinload(Course.sections),
            selectinload(Course.videos),
            selectinload(Course.quizzes).selectinload(Quiz.questions),
        )
    )


def _extract_active_curriculum(
    course: Course,
) -> tuple[
    list[Section], dict[int, list[Video]], dict[int, Quiz | None], Quiz | None, Video | None
]:
    active_sections = sorted(
        (s for s in course.sections if s.is_active),
        key=lambda s: (s.sort_order, s.id),
    )
    active_section_ids = {s.id for s in active_sections}

    active_videos = sorted(
        (v for v in course.videos if v.is_active),
        key=lambda v: (v.sort_order, v.id),
    )
    ot_video = next(
        (v for v in active_videos if v.video_type == "OT" and v.section_id is None),
        next((v for v in active_videos if v.video_type == "OT"), None),
    )

    section_lessons: dict[int, list[Video]] = {s.id: [] for s in active_sections}
    for v in active_videos:
        if v.video_type == "LESSON" and v.section_id in active_section_ids:
            section_lessons[v.section_id].append(v)

    active_quizzes = [q for q in course.quizzes if q.is_active]
    section_unit_quizzes: dict[int, Quiz | None] = {}
    for s in active_sections:
        section_unit_quizzes[s.id] = next(
            (q for q in active_quizzes if q.quiz_type == "UNIT" and q.section_id == s.id),
            None,
        )

    final_quiz = next(
        (q for q in active_quizzes if q.quiz_type == "FINAL" and q.section_id is None),
        None,
    )

    return active_sections, section_lessons, section_unit_quizzes, final_quiz, ot_video


def _compute_curriculum_items(
    active_sections: list[Section],
    section_lessons: dict[int, list[Video]],
    section_unit_quizzes: dict[int, Quiz | None],
    final_quiz: Quiz | None,
    completed_video_ids: set[int],
    submitted_quiz_ids: set[int],
    *,
    is_enrolled: bool,
    unlock_all: bool,
) -> list[CurriculumItem]:
    raw_items: list[tuple[str, int, int | None, bool]] = []
    for s in active_sections:
        for v in section_lessons.get(s.id, []):
            raw_items.append(("VIDEO", v.id, s.id, v.id in completed_video_ids))
        uq = section_unit_quizzes.get(s.id)
        if uq is not None:
            raw_items.append(("UNIT_QUIZ", uq.id, s.id, uq.id in submitted_quiz_ids))

    if final_quiz is not None:
        raw_items.append(("FINAL_QUIZ", final_quiz.id, None, final_quiz.id in submitted_quiz_ids))

    result: list[CurriculumItem] = []
    first_incomplete_seen = False
    for item_type, item_id, section_id, is_completed in raw_items:
        if not is_enrolled and not unlock_all:
            is_locked = True
        elif unlock_all or is_completed:
            is_locked = False
        elif not first_incomplete_seen:
            is_locked = False
            first_incomplete_seen = True
        else:
            is_locked = True

        result.append(
            CurriculumItem(
                item_type=item_type,
                item_id=item_id,
                section_id=section_id,
                is_completed=is_completed,
                is_locked=is_locked,
            )
        )
    return result


def _get_enrollment_progress_data(
    db: Session,
    enrollment_id: int,
    active_lesson_ids: set[int],
    active_quiz_ids: set[int],
    active_question_ids: set[int],
) -> tuple[dict[int, VideoProgress], set[int], set[int], int]:
    video_progress_map: dict[int, VideoProgress] = {}
    completed_video_ids: set[int] = set()
    if active_lesson_ids:
        vps = db.scalars(
            select(VideoProgress).where(
                VideoProgress.enrollment_id == enrollment_id,
                VideoProgress.video_id.in_(active_lesson_ids),
            )
        ).all()
        for vp in vps:
            video_progress_map[vp.video_id] = vp
            if vp.is_completed:
                completed_video_ids.add(vp.video_id)

    submitted_quiz_ids: set[int] = set()
    if active_quiz_ids:
        q_ids = db.scalars(
            select(func.distinct(QuizAttempt.quiz_id)).where(
                QuizAttempt.enrollment_id == enrollment_id,
                QuizAttempt.quiz_id.in_(active_quiz_ids),
                QuizAttempt.status == "SUBMITTED",
            )
        ).all()
        submitted_quiz_ids = {qid for qid in q_ids if qid is not None}

    unresolved_wrong_count = 0
    if active_question_ids:
        unresolved_wrong_count = (
            db.scalar(
                select(func.count(QuestionRecord.id)).where(
                    QuestionRecord.enrollment_id == enrollment_id,
                    QuestionRecord.question_id.in_(active_question_ids),
                    QuestionRecord.status == "REVIEW_NEEDED",
                )
            )
            or 0
        )

    return video_progress_map, completed_video_ids, submitted_quiz_ids, unresolved_wrong_count


# ==========================================
# [C-1] 강좌 상세 · 학습 현황 조회
# ==========================================
def get_course_detail_for_learner(
    db: Session, course_id: int, current_user: User | None
) -> CourseDetailResponse:
    course = _load_course_with_curriculum(db, course_id)
    if course is None:
        raise AppError(404, "NOT_FOUND", COURSE_NOT_FOUND_MESSAGE)

    enrollment: Enrollment | None = None
    if current_user is not None:
        enrollment = db.scalar(
            select(Enrollment).where(
                Enrollment.learner_id == current_user.id,
                Enrollment.course_id == course.id,
            )
        )

    if course.status != "PUBLISHED":
        can_access_hidden = course.status in ("EDITING", "OPERATOR_HIDDEN") and (
            enrollment is not None
            or _is_course_owner(current_user, course)
            or _is_operator(current_user)
        )
        if not can_access_hidden:
            raise AppError(403, "FORBIDDEN", COURSE_HIDDEN_MESSAGE)

    active_sections, section_lessons, section_unit_quizzes, final_quiz, ot_video = (
        _extract_active_curriculum(course)
    )

    all_lessons = [v for s in active_sections for v in section_lessons.get(s.id, [])]
    all_quizzes = [q for s in active_sections if (q := section_unit_quizzes.get(s.id)) is not None]
    if final_quiz is not None:
        all_quizzes.append(final_quiz)

    active_lesson_ids = {v.id for v in all_lessons}
    active_quiz_ids = {q.id for q in all_quizzes}
    active_question_ids = {
        quest.id
        for q in all_quizzes
        for quest in q.questions
        if quest.is_active and quest.order_no > 0
    }

    completed_video_ids: set[int] = set()
    submitted_quiz_ids: set[int] = set()
    if enrollment is not None:
        _, completed_video_ids, submitted_quiz_ids, _ = _get_enrollment_progress_data(
            db, enrollment.id, active_lesson_ids, active_quiz_ids, active_question_ids
        )

    total_items = len(all_lessons) + len(all_quizzes)
    completed_items = len(completed_video_ids) + len(submitted_quiz_ids)
    progress_percent = (
        min(100, round(completed_items * 100 / total_items)) if total_items > 0 else 0
    )

    unlock_all = enrollment is not None and (
        enrollment.first_completed_at is not None or enrollment.status == "COMPLETED"
    )
    curriculum_items = _compute_curriculum_items(
        active_sections,
        section_lessons,
        section_unit_quizzes,
        final_quiz,
        completed_video_ids,
        submitted_quiz_ids,
        is_enrolled=enrollment is not None,
        unlock_all=unlock_all,
    )
    item_map = {(ci.item_type, ci.item_id): ci for ci in curriculum_items}

    total_lesson_duration = sum(v.duration_seconds for v in all_lessons)
    total_lesson_count = len(all_lessons)

    goal_minutes = DEFAULT_DAILY_GOAL_MINUTES
    if current_user is not None and current_user.learning_goal is not None:
        if current_user.learning_goal.daily_target_minutes > 0:
            goal_minutes = current_user.learning_goal.daily_target_minutes

    estimated_days = (
        max(1, math.ceil(total_lesson_duration / (goal_minutes * 60)))
        if total_lesson_duration > 0
        else 0
    )

    is_free = course.price_krw == 0
    if enrollment is not None:
        enrollment_dto = CourseDetailEnrollment(
            is_enrolled=True,
            enrollment_id=enrollment.id,
            status=enrollment.status,
            progress_percent=progress_percent,
            action_button="CONTINUE",
        )
    else:
        enrollment_dto = CourseDetailEnrollment(
            is_enrolled=False,
            enrollment_id=None,
            status=None,
            progress_percent=0,
            action_button="START_FREE" if is_free else "PURCHASE",
        )

    ot_dto = (
        CourseDetailOtVideo(
            video_id=ot_video.id,
            title=ot_video.title,
            video_storage_key=ot_video.video_storage_key,
            video_url=get_video_stream_url(ot_video.id),
            duration_seconds=ot_video.duration_seconds,
        )
        if ot_video is not None
        else None
    )

    sections_dto: list[CourseCurriculumSection] = []
    for s in active_sections:
        videos_dto: list[CourseCurriculumVideo] = []
        for v in section_lessons.get(s.id, []):
            ci = item_map[("VIDEO", v.id)]
            videos_dto.append(
                CourseCurriculumVideo(
                    video_id=v.id,
                    title=v.title,
                    duration_seconds=v.duration_seconds,
                    sort_order=v.sort_order,
                    is_completed=ci.is_completed,
                    is_locked=ci.is_locked,
                )
            )
        uq = section_unit_quizzes.get(s.id)
        uq_dto = None
        if uq is not None:
            ci_q = item_map[("UNIT_QUIZ", uq.id)]
            uq_dto = CourseCurriculumQuiz(
                quiz_id=uq.id,
                is_completed=ci_q.is_completed,
                is_locked=ci_q.is_locked,
            )
        sections_dto.append(
            CourseCurriculumSection(
                section_id=s.id,
                title=s.title,
                sort_order=s.sort_order,
                videos=videos_dto,
                unit_quiz=uq_dto,
            )
        )

    final_quiz_dto = None
    if final_quiz is not None:
        ci_fq = item_map[("FINAL_QUIZ", final_quiz.id)]
        final_quiz_dto = CourseCurriculumQuiz(
            quiz_id=final_quiz.id,
            is_completed=ci_fq.is_completed,
            is_locked=ci_fq.is_locked,
        )

    instructor = course.instructor
    inst_profile = instructor.instructor_profile if instructor else None

    return CourseDetailResponse(
        course_id=course.id,
        status=course.status,
        category=CategorySimple(
            id=course.category.id,
            code=course.category.code,
            name=course.category.name,
        ),
        topic=TopicSimple(
            id=course.topic.id,
            name=course.topic.name,
        ),
        title=course.title,
        description=course.description,
        difficulty=course.difficulty,
        price_krw=course.price_krw,
        is_free=is_free,
        thumbnail_storage_key=course.thumbnail_storage_key,
        thumbnail_url=get_thumbnail_url(course.thumbnail_storage_key),
        instructor=CourseDetailInstructor(
            id=instructor.id,
            name=instructor.name,
            bio=inst_profile.bio if inst_profile else None,
        ),
        total_lesson_duration_seconds=total_lesson_duration,
        total_lesson_count=total_lesson_count,
        estimated_completion_days=estimated_days,
        ot_video=ot_dto,
        enrollment=enrollment_dto,
        sections=sections_dto,
        final_quiz=final_quiz_dto,
    )


# ==========================================
# [C-2] 수강 등록 (목업 결제 · 무료)
# ==========================================
def enroll_course(
    db: Session, course_id: int, current_user: User, body: EnrollRequest | None
) -> EnrollResponse:
    if not _is_learner_mode(current_user):
        raise AppError(403, "FORBIDDEN", ENROLL_MODE_FORBIDDEN_MESSAGE)

    access_source = (
        body.access_source
        if (body is not None and body.access_source is not None)
        else "DEMO_GRANT"
    )
    if access_source != "DEMO_GRANT":
        raise AppError(400, "VALIDATION_ERROR", "유효하지 않은 수강 권한 획득 경로입니다.")

    course = db.get(Course, course_id, with_for_update=True, populate_existing=True)
    if course is None:
        raise AppError(404, "NOT_FOUND", COURSE_NOT_FOUND_MESSAGE)

    if course.status != "PUBLISHED":
        raise AppError(409, "CONFLICT", ENROLL_CONFLICT_MESSAGE)

    existing_enrollment = db.scalar(
        select(Enrollment).where(
            Enrollment.learner_id == current_user.id,
            Enrollment.course_id == course.id,
        )
    )
    if existing_enrollment is not None:
        raise AppError(409, "CONFLICT", ENROLL_CONFLICT_MESSAGE)

    course_full = _load_course_with_curriculum(db, course.id)
    assert course_full is not None
    active_sections, section_lessons, section_unit_quizzes, final_quiz, _ = (
        _extract_active_curriculum(course_full)
    )

    all_lessons = [v for s in active_sections for v in section_lessons.get(s.id, [])]
    first_video_id = all_lessons[0].id if all_lessons else None

    active_quiz_count = sum(
        1 for s in active_sections if section_unit_quizzes.get(s.id) is not None
    ) + (1 if final_quiz is not None else 0)

    now = datetime.now(SEOUL_TZ)
    enrollment = Enrollment(
        learner_id=current_user.id,
        course_id=course.id,
        access_source=access_source,
        price_snapshot_krw=course.price_krw,
        status="IN_PROGRESS",
        enrolled_at=now,
    )
    db.add(enrollment)
    db.flush()

    today = today_in_seoul()
    stat_stmt = insert(CourseDailyStat).values(
        course_id=course.id,
        start_date=today,
        lesson_views=0,
        new_students=1,
        expected_revenue=course.price_krw,
        available_quiz_cnt=active_quiz_count,
        first_submitted_cnt=0,
        first_answer_cnt=0,
        first_correct_cnt=0,
    )
    db.execute(
        stat_stmt.on_conflict_do_update(
            index_elements=["course_id", "start_date"],
            set_={
                "new_students": CourseDailyStat.new_students + 1,
                "expected_revenue": CourseDailyStat.expected_revenue + course.price_krw,
                "available_quiz_cnt": CourseDailyStat.available_quiz_cnt + active_quiz_count,
            },
        )
    )

    db.commit()
    db.refresh(enrollment)

    return EnrollResponse(
        enrollment_id=enrollment.id,
        learner_id=enrollment.learner_id,
        course_id=enrollment.course_id,
        access_source=enrollment.access_source,
        price_snapshot_krw=enrollment.price_snapshot_krw,
        status=enrollment.status,
        enrolled_at=enrollment.enrolled_at,
        first_video_id=first_video_id,
    )


# ==========================================
# [C-3] 내 강좌 목록 조회 (수강 중 · 수료)
# ==========================================
def list_my_enrollments(
    db: Session, current_user: User, status: str | None
) -> MyEnrollmentListResponse:
    filter_status = status if status is not None else "ALL"
    if filter_status not in ("ALL", "IN_PROGRESS", "COMPLETED"):
        raise AppError(400, "VALIDATION_ERROR", "유효하지 않은 수강 상태 필터입니다.")

    all_enrollments = db.scalars(
        select(Enrollment)
        .where(Enrollment.learner_id == current_user.id)
        .options(
            joinedload(Enrollment.course).selectinload(Course.sections),
            joinedload(Enrollment.course).selectinload(Course.videos),
            joinedload(Enrollment.course).selectinload(Course.quizzes).selectinload(Quiz.questions),
            selectinload(Enrollment.video_progresses),
            selectinload(Enrollment.quiz_attempts),
            selectinload(Enrollment.question_records),
        )
        .order_by(
            Enrollment.last_studied_at.desc().nulls_last(),
            Enrollment.enrolled_at.desc(),
            Enrollment.id.desc(),
        )
    ).all()

    in_progress_cnt = sum(1 for e in all_enrollments if e.status == "IN_PROGRESS")
    completed_cnt = sum(1 for e in all_enrollments if e.status == "COMPLETED")
    counts = EnrollmentCounts(
        all=len(all_enrollments),
        in_progress=in_progress_cnt,
        completed=completed_cnt,
    )

    filtered = (
        all_enrollments
        if filter_status == "ALL"
        else [e for e in all_enrollments if e.status == filter_status]
    )

    items: list[MyEnrollmentItem] = []
    for enr in filtered:
        course = enr.course
        active_sections, section_lessons, section_unit_quizzes, final_quiz, _ = (
            _extract_active_curriculum(course)
        )
        all_lessons = [v for s in active_sections for v in section_lessons.get(s.id, [])]
        all_quizzes = [
            q for s in active_sections if (q := section_unit_quizzes.get(s.id)) is not None
        ]
        if final_quiz is not None:
            all_quizzes.append(final_quiz)

        active_lesson_ids = {v.id for v in all_lessons}
        active_quiz_ids = {q.id for q in all_quizzes}
        active_question_ids = {
            quest.id
            for q in all_quizzes
            for quest in q.questions
            if quest.is_active and quest.order_no > 0
        }

        completed_video_ids = {
            vp.video_id
            for vp in enr.video_progresses
            if vp.is_completed and vp.video_id in active_lesson_ids
        }
        submitted_quiz_ids = {
            qa.quiz_id
            for qa in enr.quiz_attempts
            if qa.status == "SUBMITTED" and qa.quiz_id is not None and qa.quiz_id in active_quiz_ids
        }
        unresolved_wrong_count = sum(
            1
            for qr in enr.question_records
            if qr.status == "REVIEW_NEEDED" and qr.question_id in active_question_ids
        )

        total_items = len(all_lessons) + len(all_quizzes)
        completed_items = len(completed_video_ids) + len(submitted_quiz_ids)
        progress_percent = (
            min(100, round(completed_items * 100 / total_items)) if total_items > 0 else 0
        )
        participated_quiz_count = len(submitted_quiz_ids)

        if enr.status == "COMPLETED":
            status_message = "수료 완료"
        elif unresolved_wrong_count > 0:
            status_message = f"오답 {unresolved_wrong_count}개 남았어요"
        elif progress_percent == 0:
            status_message = "학습 시작 전"
        else:
            status_message = f"진도율 {progress_percent}%"

        items.append(
            MyEnrollmentItem(
                enrollment_id=enr.id,
                course_id=course.id,
                title=course.title,
                thumbnail_storage_key=course.thumbnail_storage_key,
                thumbnail_url=get_thumbnail_url(course.thumbnail_storage_key),
                status=enr.status,
                progress_percent=progress_percent,
                unresolved_wrong_count=unresolved_wrong_count,
                status_message=status_message,
                participated_quiz_count=participated_quiz_count,
                enrolled_at=enr.enrolled_at,
                last_studied_at=enr.last_studied_at,
            )
        )

    return MyEnrollmentListResponse(counts=counts, enrollments=items)


# ==========================================
# [C-4] 이어가기 (첫 미완료 항목 조회)
# ==========================================
def get_next_learning_item(
    db: Session, enrollment_id: int, current_user: User
) -> EnrollmentNextResponse:
    if not _is_learner_mode(current_user):
        raise AppError(403, "FORBIDDEN", ENROLLMENT_FORBIDDEN_MESSAGE)

    enrollment = db.get(Enrollment, enrollment_id)
    if enrollment is None:
        raise AppError(404, "NOT_FOUND", ENROLLMENT_NOT_FOUND_MESSAGE)

    if enrollment.learner_id != current_user.id:
        raise AppError(403, "FORBIDDEN", ENROLLMENT_FORBIDDEN_MESSAGE)

    course = _load_course_with_curriculum(db, enrollment.course_id)
    if course is None:
        raise AppError(404, "NOT_FOUND", COURSE_NOT_FOUND_MESSAGE)

    active_sections, section_lessons, section_unit_quizzes, final_quiz, _ = (
        _extract_active_curriculum(course)
    )
    all_lessons = [v for s in active_sections for v in section_lessons.get(s.id, [])]
    all_quizzes = [q for s in active_sections if (q := section_unit_quizzes.get(s.id)) is not None]
    if final_quiz is not None:
        all_quizzes.append(final_quiz)

    active_lesson_ids = {v.id for v in all_lessons}
    active_quiz_ids = {q.id for q in all_quizzes}
    active_question_ids = {
        quest.id
        for q in all_quizzes
        for quest in q.questions
        if quest.is_active and quest.order_no > 0
    }

    video_progress_map, completed_video_ids, submitted_quiz_ids, unresolved_wrong_count = (
        _get_enrollment_progress_data(
            db, enrollment.id, active_lesson_ids, active_quiz_ids, active_question_ids
        )
    )

    if enrollment.status == "COMPLETED":
        if all_lessons:
            first_lesson = all_lessons[0]
            vp = video_progress_map.get(first_lesson.id)
            return EnrollmentNextResponse(
                enrollment_id=enrollment.id,
                course_id=course.id,
                next_item_type="VIDEO",
                section_id=first_lesson.section_id,
                video_id=first_lesson.id,
                quiz_id=None,
                last_position_seconds=vp.last_position_seconds if vp else 0,
            )
        return EnrollmentNextResponse(
            enrollment_id=enrollment.id,
            course_id=course.id,
            next_item_type=None,
            section_id=None,
            video_id=None,
            quiz_id=None,
            last_position_seconds=None,
        )

    for s in active_sections:
        for v in section_lessons.get(s.id, []):
            if v.id not in completed_video_ids:
                vp = video_progress_map.get(v.id)
                return EnrollmentNextResponse(
                    enrollment_id=enrollment.id,
                    course_id=course.id,
                    next_item_type="VIDEO",
                    section_id=s.id,
                    video_id=v.id,
                    quiz_id=None,
                    last_position_seconds=vp.last_position_seconds if vp else 0,
                )
        uq = section_unit_quizzes.get(s.id)
        if uq is not None and uq.id not in submitted_quiz_ids:
            return EnrollmentNextResponse(
                enrollment_id=enrollment.id,
                course_id=course.id,
                next_item_type="UNIT_QUIZ",
                section_id=s.id,
                video_id=None,
                quiz_id=uq.id,
                last_position_seconds=None,
            )

    if final_quiz is not None and final_quiz.id not in submitted_quiz_ids:
        return EnrollmentNextResponse(
            enrollment_id=enrollment.id,
            course_id=course.id,
            next_item_type="FINAL_QUIZ",
            section_id=None,
            video_id=None,
            quiz_id=final_quiz.id,
            last_position_seconds=None,
        )

    if unresolved_wrong_count > 0:
        return EnrollmentNextResponse(
            enrollment_id=enrollment.id,
            course_id=course.id,
            next_item_type="WRONG_REVIEW",
            section_id=None,
            video_id=None,
            quiz_id=None,
            last_position_seconds=None,
        )

    if all_lessons:
        first_lesson = all_lessons[0]
        vp = video_progress_map.get(first_lesson.id)
        return EnrollmentNextResponse(
            enrollment_id=enrollment.id,
            course_id=course.id,
            next_item_type="VIDEO",
            section_id=first_lesson.section_id,
            video_id=first_lesson.id,
            quiz_id=None,
            last_position_seconds=vp.last_position_seconds if vp else 0,
        )

    return EnrollmentNextResponse(
        enrollment_id=enrollment.id,
        course_id=course.id,
        next_item_type=None,
        section_id=None,
        video_id=None,
        quiz_id=None,
        last_position_seconds=None,
    )


# ==========================================
# [C-5] 영상 정보 및 잠금 확인
# ==========================================
def get_video_detail(
    db: Session,
    video_id: int,
    current_user: User | None,
    *,
    has_auth_header: bool = False,
) -> VideoDetailResponse:
    video = db.scalar(
        select(Video)
        .where(Video.id == video_id)
        .options(
            joinedload(Video.section),
            selectinload(Video.scripts),
        )
    )
    if video is None or not video.is_active:
        raise AppError(404, "NOT_FOUND", VIDEO_NOT_FOUND_MESSAGE)

    if video.video_type == "LESSON" and (video.section is None or not video.section.is_active):
        raise AppError(404, "NOT_FOUND", VIDEO_NOT_FOUND_MESSAGE)

    course = _load_course_with_curriculum(db, video.course_id)
    if course is None:
        raise AppError(404, "NOT_FOUND", VIDEO_NOT_FOUND_MESSAGE)

    if video.video_type == "LESSON" and current_user is None:
        if has_auth_header:
            raise AppError(401, "UNAUTHORIZED", "유효하지 않거나 만료된 토큰입니다.")
        raise AppError(401, "UNAUTHORIZED", "로그인이 필요합니다.")

    if video.video_type == "OT" and has_auth_header and current_user is None:
        raise AppError(401, "UNAUTHORIZED", "유효하지 않거나 만료된 토큰입니다.")

    enrollment: Enrollment | None = None
    if current_user is not None:
        enrollment = db.scalar(
            select(Enrollment).where(
                Enrollment.learner_id == current_user.id,
                Enrollment.course_id == course.id,
            )
        )

    is_owner = _is_course_owner(current_user, course)
    is_op = _is_operator(current_user)

    if course.status != "PUBLISHED":
        can_access_course = (
            is_owner
            or is_op
            or (course.status in ("EDITING", "OPERATOR_HIDDEN") and enrollment is not None)
        )
        if not can_access_course:
            raise AppError(403, "FORBIDDEN", VIDEO_LOCKED_MESSAGE)

    active_sections, section_lessons, section_unit_quizzes, final_quiz, _ = (
        _extract_active_curriculum(course)
    )
    all_lessons = [v for s in active_sections for v in section_lessons.get(s.id, [])]
    all_quizzes = [q for s in active_sections if (q := section_unit_quizzes.get(s.id)) is not None]
    if final_quiz is not None:
        all_quizzes.append(final_quiz)

    active_lesson_ids = {v.id for v in all_lessons}
    active_quiz_ids = {q.id for q in all_quizzes}
    active_question_ids = {
        quest.id
        for q in all_quizzes
        for quest in q.questions
        if quest.is_active and quest.order_no > 0
    }

    video_progress_map: dict[int, VideoProgress] = {}
    completed_video_ids: set[int] = set()
    submitted_quiz_ids: set[int] = set()
    if enrollment is not None:
        video_progress_map, completed_video_ids, submitted_quiz_ids, _ = (
            _get_enrollment_progress_data(
                db, enrollment.id, active_lesson_ids, active_quiz_ids, active_question_ids
            )
        )

    unlock_all = (
        is_owner
        or is_op
        or (
            enrollment is not None
            and (enrollment.first_completed_at is not None or enrollment.status == "COMPLETED")
        )
    )

    curriculum_items = _compute_curriculum_items(
        active_sections,
        section_lessons,
        section_unit_quizzes,
        final_quiz,
        completed_video_ids,
        submitted_quiz_ids,
        is_enrolled=enrollment is not None,
        unlock_all=unlock_all,
    )
    item_map = {(ci.item_type, ci.item_id): ci for ci in curriculum_items}

    if video.video_type == "LESSON":
        if not (is_owner or is_op):
            if enrollment is None or not _is_learner_mode(current_user):
                raise AppError(403, "FORBIDDEN", VIDEO_LOCKED_MESSAGE)
            ci_self = item_map.get(("VIDEO", video.id))
            if ci_self is None or ci_self.is_locked:
                raise AppError(403, "FORBIDDEN", VIDEO_LOCKED_MESSAGE)

    is_liked = False
    if current_user is not None:
        like_row = db.get(VideoLike, (current_user.id, video.id))
        is_liked = like_row is not None

    vp = video_progress_map.get(video.id)
    progress_dto = VideoProgressSummary(
        watched_seconds=vp.watched_seconds if vp else 0,
        last_position_seconds=vp.last_position_seconds if vp else 0,
        is_completed=vp.is_completed if vp else False,
    )

    segments_dto = [
        VideoSegmentItem(
            id=s.id,
            seq=s.seq,
            start_ms=s.start_ms,
            end_ms=s.end_ms,
            content=s.content,
        )
        for s in sorted(video.scripts, key=lambda x: x.seq)
    ]

    # Navigation 계산
    prev_video_id: int | None = None
    next_item_type: str | None = None
    next_item_id: int | None = None
    is_next_unlocked = False

    if video.video_type == "OT":
        if curriculum_items:
            first_ci = curriculum_items[0]
            next_item_type = first_ci.item_type
            next_item_id = first_ci.item_id
            is_next_unlocked = not first_ci.is_locked
    else:
        for idx, l_vid in enumerate(all_lessons):
            if l_vid.id == video.id and idx > 0:
                prev_video_id = all_lessons[idx - 1].id
                break

        for idx, ci in enumerate(curriculum_items):
            if ci.item_type == "VIDEO" and ci.item_id == video.id:
                if idx + 1 < len(curriculum_items):
                    next_ci = curriculum_items[idx + 1]
                    next_item_type = next_ci.item_type
                    next_item_id = next_ci.item_id
                    is_next_unlocked = not next_ci.is_locked
                break

    return VideoDetailResponse(
        video_id=video.id,
        course_id=course.id,
        course_title=course.title,
        section_id=video.section_id,
        section_title=video.section.title if video.section else None,
        video_type=video.video_type,
        title=video.title,
        video_storage_key=video.video_storage_key,
        video_url=get_video_stream_url(video.id),
        thumbnail_storage_key=video.thumbnail_storage_key,
        thumbnail_url=get_thumbnail_url(video.thumbnail_storage_key),
        duration_seconds=video.duration_seconds,
        sort_order=video.sort_order,
        view_count=video.view_count,
        like_count=video.like_count,
        is_liked=is_liked,
        progress=progress_dto,
        segments=segments_dto,
        navigation=VideoNavigation(
            prev_video_id=prev_video_id,
            next_item_type=next_item_type,
            next_item_id=next_item_id,
            is_next_unlocked=is_next_unlocked,
        ),
    )


# ==========================================
# [C-6] 영상 조회수 집계 (3초 · 하루 1회)
# ==========================================
def record_video_view(
    db: Session, video_id: int, current_user: User, body: VideoViewRequest
) -> VideoViewResponse:
    if body.played_seconds < 3:
        raise AppError(400, "VALIDATION_ERROR", VIDEO_VIEW_MIN_SECONDS_MESSAGE)

    video = db.get(Video, video_id, with_for_update=True, populate_existing=True)
    if video is None or not video.is_active:
        raise AppError(404, "NOT_FOUND", VIDEO_VIEW_NOT_FOUND_MESSAGE)

    if video.video_type == "LESSON":
        section = db.get(Section, video.section_id) if video.section_id is not None else None
        if section is None or not section.is_active:
            raise AppError(404, "NOT_FOUND", VIDEO_VIEW_NOT_FOUND_MESSAGE)

    view_date = today_in_seoul()
    view_stmt = (
        insert(VideoView)
        .values(
            user_id=current_user.id,
            video_id=video.id,
            view_date=view_date,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "video_id", "view_date"])
        .returning(VideoView.user_id)
    )
    inserted_user_id = db.scalar(view_stmt)
    is_incremented = inserted_user_id is not None

    if is_incremented:
        video.view_count = (video.view_count or 0) + 1
        if video.video_type == "LESSON":
            stat_stmt = insert(CourseDailyStat).values(
                course_id=video.course_id,
                start_date=view_date,
                lesson_views=1,
                new_students=0,
                expected_revenue=0,
                available_quiz_cnt=0,
                first_submitted_cnt=0,
                first_answer_cnt=0,
                first_correct_cnt=0,
            )
            db.execute(
                stat_stmt.on_conflict_do_update(
                    index_elements=["course_id", "start_date"],
                    set_={"lesson_views": CourseDailyStat.lesson_views + 1},
                )
            )

    db.commit()
    db.refresh(video)

    return VideoViewResponse(
        video_id=video.id,
        view_date=view_date.isoformat(),
        is_incremented=is_incremented,
        view_count=video.view_count,
    )


# ==========================================
# [C-7] 학습 진도 · 시청 시간 저장
# ==========================================
def merge_watched_ranges(
    existing_ranges: list[list[int]] | None, new_range: list[int]
) -> list[list[int]]:
    intervals: list[tuple[int, int]] = []
    if existing_ranges:
        for item in existing_ranges:
            if isinstance(item, list | tuple) and len(item) == 2:
                s, e = int(item[0]), int(item[1])
                if e > s >= 0:
                    intervals.append((s, e))
    intervals.append((int(new_range[0]), int(new_range[1])))
    intervals.sort(key=lambda x: (x[0], x[1]))

    merged: list[list[int]] = []
    for s, e in intervals:
        if not merged or s > merged[-1][1]:
            merged.append([s, e])
        else:
            merged[-1][1] = max(merged[-1][1], e)
    return merged


def _ensure_seoul_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=SEOUL_TZ)
    return dt.astimezone(SEOUL_TZ)


def _compute_deduplicated_active_seconds(sessions: list[StudyActivitySession]) -> int:
    """동일 날짜 학습 활동 세션에서 동시(중복) 시간 구간을 제외하고 인정 학습 시간을 합산한다."""
    if not sessions:
        return 0

    normalized: list[tuple[datetime, datetime, int]] = []
    for s in sessions:
        st = _ensure_seoul_aware(s.started_at)
        et = _ensure_seoul_aware(s.ended_at)
        act_sec = max(0, int(s.active_seconds or 0))
        normalized.append((st, et, act_sec))

    normalized.sort(key=lambda x: (x[0], x[1]))

    # 엄격히 겹치는(next_start < comp_end) 세션들을 연결 요소로 묶어 동시 시청 중복 합산 방지
    components: list[tuple[datetime, datetime, int, int, int]] = []
    for st, et, act_sec in normalized:
        if not components or st >= components[-1][1]:
            components.append((st, et, act_sec, act_sec, 1))
        else:
            c_st, c_et, c_sum, c_max, c_cnt = components[-1]
            components[-1] = (
                c_st,
                max(c_et, et),
                c_sum + act_sec,
                max(c_max, act_sec),
                c_cnt + 1,
            )

    total = 0
    for c_st, c_et, c_sum, c_max, c_cnt in components:
        if c_cnt == 1:
            total += c_sum
        else:
            window_seconds = max(0, int((c_et - c_st).total_seconds()))
            total += min(c_sum, max(c_max, window_seconds))
    return total


def _get_learner_day_sessions(
    db: Session, learner_id: int, study_date: date
) -> list[StudyActivitySession]:
    return list(
        db.scalars(
            select(StudyActivitySession)
            .join(Enrollment, Enrollment.id == StudyActivitySession.enrollment_id)
            .where(
                Enrollment.learner_id == learner_id,
                StudyActivitySession.study_date == study_date,
            )
        ).all()
    )


def save_video_progress(
    db: Session, video_id: int, current_user: User, body: VideoProgressRequest
) -> VideoProgressResponse:
    video = db.get(Video, video_id)
    if video is None or not video.is_active:
        raise AppError(404, "NOT_FOUND", VIDEO_NOT_FOUND_MESSAGE)

    if video.video_type != "LESSON":
        raise AppError(400, "VALIDATION_ERROR", VIDEO_PROGRESS_VALIDATION_MESSAGE)

    if video.section_id is not None:
        section = db.get(Section, video.section_id)
        if section is None or not section.is_active:
            raise AppError(404, "NOT_FOUND", VIDEO_NOT_FOUND_MESSAGE)

    # 동일 수강자의 동시 요청 직렬화를 위해 User 및 Enrollment 행 잠금
    db.get(User, current_user.id, with_for_update=True)
    enrollment = db.scalar(
        select(Enrollment)
        .where(
            Enrollment.learner_id == current_user.id,
            Enrollment.course_id == video.course_id,
        )
        .with_for_update()
    )
    if enrollment is None:
        raise AppError(403, "FORBIDDEN", VIDEO_PROGRESS_FORBIDDEN_MESSAGE)

    duration = video.duration_seconds
    if body.last_position_seconds < 0 or body.last_position_seconds > duration:
        raise AppError(400, "VALIDATION_ERROR", VIDEO_PROGRESS_VALIDATION_MESSAGE)

    if len(body.watched_range) != 2:
        raise AppError(400, "VALIDATION_ERROR", VIDEO_PROGRESS_VALIDATION_MESSAGE)

    range_start, range_end = body.watched_range[0], body.watched_range[1]
    if range_start < 0 or range_end <= range_start or range_end > duration:
        raise AppError(400, "VALIDATION_ERROR", VIDEO_PROGRESS_VALIDATION_MESSAGE)

    act = body.activity_session
    session_key = act.session_key.strip() if act.session_key else ""
    started_at = _ensure_seoul_aware(act.started_at)
    ended_at = _ensure_seoul_aware(act.ended_at)
    if not session_key or len(session_key) > 64 or act.active_seconds < 0 or ended_at < started_at:
        raise AppError(400, "VALIDATION_ERROR", VIDEO_PROGRESS_VALIDATION_MESSAGE)

    now = datetime.now(SEOUL_TZ)
    db.execute(
        insert(VideoProgress)
        .values(
            enrollment_id=enrollment.id,
            video_id=video.id,
            watched_ranges=[],
            watched_seconds=0,
            last_position_seconds=0,
            is_completed=False,
            completed_at=None,
        )
        .on_conflict_do_nothing(index_elements=["enrollment_id", "video_id"])
    )
    vp = db.get(
        VideoProgress,
        (enrollment.id, video.id),
        with_for_update=True,
        populate_existing=True,
    )
    assert vp is not None

    merged_ranges = merge_watched_ranges(vp.watched_ranges, [range_start, range_end])
    watched_seconds = min(duration, sum(e - s for s, e in merged_ranges))
    vp.watched_ranges = merged_ranges
    vp.watched_seconds = watched_seconds
    vp.last_position_seconds = body.last_position_seconds

    required_seconds = math.ceil(duration * 9 / 10) if duration > 0 else 0
    newly_completed = False
    if watched_seconds >= required_seconds and not vp.is_completed:
        vp.is_completed = True
        vp.completed_at = now
        newly_completed = True

    remaining_for_completion = 0 if vp.is_completed else max(0, required_seconds - watched_seconds)

    watched_ratio_percent = round(watched_seconds / duration * 100, 1) if duration > 0 else 0.0
    enrollment.last_studied_at = now
    db.flush()

    if newly_completed:
        course = _load_course_with_curriculum(db, video.course_id)
        if course is not None:
            recalculate_enrollments_progress(course, db)

    # 학습 활동 세션(study_activity_sessions) 및 당일 출석(attendance_days) 갱신
    study_date = today_in_seoul()
    goal = db.get(LearningGoal, current_user.id)
    target_minutes = (
        goal.daily_target_minutes
        if (goal is not None and goal.daily_target_minutes > 0)
        else DEFAULT_DAILY_GOAL_MINUTES
    )

    prev_sessions = _get_learner_day_sessions(db, current_user.id, study_date)
    prev_sessions_seconds = _compute_deduplicated_active_seconds(prev_sessions)

    attendance = db.get(
        AttendanceDay,
        (current_user.id, study_date),
        with_for_update=True,
        populate_existing=True,
    )
    base_untracked_seconds = (
        max(0, (attendance.active_seconds or 0) - prev_sessions_seconds)
        if attendance is not None
        else 0
    )

    existing_session = db.scalar(
        select(StudyActivitySession)
        .where(StudyActivitySession.session_key == session_key)
        .with_for_update()
    )
    if existing_session is None:
        db.add(
            StudyActivitySession(
                enrollment_id=enrollment.id,
                activity_type="VIDEO",
                video_id=video.id,
                quiz_attempt_id=None,
                session_key=session_key,
                study_date=study_date,
                started_at=started_at,
                ended_at=ended_at,
                active_seconds=act.active_seconds,
            )
        )
        db.flush()
    else:
        if existing_session.enrollment_id != enrollment.id:
            raise AppError(400, "VALIDATION_ERROR", VIDEO_PROGRESS_VALIDATION_MESSAGE)
        if act.active_seconds > existing_session.active_seconds:
            existing_session.active_seconds = act.active_seconds
        existing_ended = _ensure_seoul_aware(existing_session.ended_at)
        if ended_at > existing_ended:
            existing_session.ended_at = ended_at
        db.flush()

    updated_sessions = _get_learner_day_sessions(db, current_user.id, study_date)
    new_sessions_seconds = _compute_deduplicated_active_seconds(updated_sessions)
    total_active_seconds = base_untracked_seconds + new_sessions_seconds

    if attendance is None:
        db.execute(
            insert(AttendanceDay)
            .values(
                learner_id=current_user.id,
                study_date=study_date,
                target_minutes_snapshot=target_minutes,
                active_seconds=total_active_seconds,
                is_attended=(total_active_seconds >= target_minutes * 60),
            )
            .on_conflict_do_update(
                index_elements=["learner_id", "study_date"],
                set_={
                    "active_seconds": total_active_seconds,
                    "is_attended": (total_active_seconds >= target_minutes * 60),
                },
            )
        )
        attendance = db.get(
            AttendanceDay,
            (current_user.id, study_date),
            populate_existing=True,
        )
        assert attendance is not None
    else:
        attendance.active_seconds = total_active_seconds
        attendance.is_attended = (
            attendance.active_seconds >= attendance.target_minutes_snapshot * 60
        )

    db.commit()
    db.refresh(vp)
    db.refresh(attendance)

    return VideoProgressResponse(
        enrollment_id=enrollment.id,
        video_id=video.id,
        watched_seconds=vp.watched_seconds,
        duration_seconds=duration,
        watched_ratio_percent=watched_ratio_percent,
        last_position_seconds=vp.last_position_seconds,
        is_completed=vp.is_completed,
        completed_at=vp.completed_at,
        remaining_seconds_for_completion=remaining_for_completion,
        today_active_seconds=attendance.active_seconds,
        today_is_attended=attendance.is_attended,
    )


# ==========================================
# [C-8] 영상 좋아요 등록 · 취소
# ==========================================
def _sync_video_like_count(db: Session, video: Video) -> int:
    actual_count = (
        db.scalar(select(func.count()).select_from(VideoLike).where(VideoLike.video_id == video.id))
        or 0
    )
    video.like_count = actual_count
    return actual_count


def like_video(db: Session, video_id: int, current_user: User) -> VideoLikeResponse:
    video = db.get(Video, video_id, with_for_update=True, populate_existing=True)
    if video is None or not video.is_active:
        raise AppError(404, "NOT_FOUND", VIDEO_NOT_FOUND_MESSAGE)

    if video.video_type == "LESSON":
        section = db.get(Section, video.section_id) if video.section_id is not None else None
        if section is None or not section.is_active:
            raise AppError(404, "NOT_FOUND", VIDEO_NOT_FOUND_MESSAGE)

        enrollment = db.scalar(
            select(Enrollment).where(
                Enrollment.learner_id == current_user.id,
                Enrollment.course_id == video.course_id,
            )
        )
        if enrollment is None:
            raise AppError(403, "FORBIDDEN", VIDEO_LIKE_FORBIDDEN_MESSAGE)

    db.execute(
        insert(VideoLike)
        .values(user_id=current_user.id, video_id=video.id)
        .on_conflict_do_nothing(index_elements=["user_id", "video_id"])
    )
    _sync_video_like_count(db, video)
    db.commit()
    db.refresh(video)

    return VideoLikeResponse(
        video_id=video.id,
        video_type=video.video_type,
        is_liked=True,
        like_count=video.like_count,
    )


def unlike_video(db: Session, video_id: int, current_user: User) -> VideoLikeResponse:
    video = db.get(Video, video_id, with_for_update=True, populate_existing=True)
    if video is None or not video.is_active:
        raise AppError(404, "NOT_FOUND", VIDEO_NOT_FOUND_MESSAGE)

    if video.video_type == "LESSON":
        section = db.get(Section, video.section_id) if video.section_id is not None else None
        if section is None or not section.is_active:
            raise AppError(404, "NOT_FOUND", VIDEO_NOT_FOUND_MESSAGE)

        enrollment = db.scalar(
            select(Enrollment).where(
                Enrollment.learner_id == current_user.id,
                Enrollment.course_id == video.course_id,
            )
        )
        if enrollment is None:
            raise AppError(403, "FORBIDDEN", VIDEO_LIKE_FORBIDDEN_MESSAGE)

    db.execute(
        delete(VideoLike).where(
            VideoLike.user_id == current_user.id,
            VideoLike.video_id == video.id,
        )
    )
    _sync_video_like_count(db, video)
    db.commit()
    db.refresh(video)

    return VideoLikeResponse(
        video_id=video.id,
        video_type=video.video_type,
        is_liked=False,
        like_count=video.like_count,
    )


# ==========================================
# [C-9] 내가 좋아요한 영상 목록 조회
# ==========================================
def list_my_likes(
    db: Session, current_user: User, video_type: str | None
) -> LikedVideoListResponse:
    filter_type = video_type if video_type is not None else "ALL"
    if filter_type not in ("ALL", "OT", "LESSON"):
        raise AppError(400, "VALIDATION_ERROR", "유효하지 않은 영상 유형 필터입니다.")

    query = (
        select(Video)
        .join(VideoLike, VideoLike.video_id == Video.id)
        .join(Course, Course.id == Video.course_id)
        .outerjoin(Section, Section.id == Video.section_id)
        .options(joinedload(Video.course), joinedload(Video.section))
        .where(
            VideoLike.user_id == current_user.id,
            Video.is_active.is_(True),
        )
        .order_by(Video.id.desc())
    )
    if filter_type in ("OT", "LESSON"):
        query = query.where(Video.video_type == filter_type)

    videos = db.scalars(query).all()
    items: list[LikedVideoItem] = []
    for v in videos:
        if v.video_type == "LESSON" and (v.section is None or not v.section.is_active):
            continue
        c = v.course
        items.append(
            LikedVideoItem(
                video_id=v.id,
                video_type=v.video_type,
                video_title=v.title,
                thumbnail_storage_key=v.thumbnail_storage_key,
                thumbnail_url=get_thumbnail_url(v.thumbnail_storage_key),
                duration_seconds=v.duration_seconds,
                course_id=c.id,
                course_title=c.title,
            )
        )

    return LikedVideoListResponse(
        total_count=len(items),
        liked_videos=items,
    )
