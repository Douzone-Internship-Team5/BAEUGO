from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError
from app.core.timezone import SEOUL_TZ
from app.models.course import Course, Section
from app.models.learning import Enrollment, VideoProgress
from app.models.quiz import QuestionRecord, Quiz, QuizAttempt
from app.models.video import Video
from app.schemas.learner_quiz import CompletionData


def load_enrolled_course(
    db: Session, learner_id: int, course_id: int, *, for_update: bool = False
) -> tuple[Course, Enrollment]:
    # 강사 편집과 동일하게 Course -> Enrollment 순서로 잠근다.
    query = select(Course).where(Course.id == course_id)
    if for_update:
        query = query.with_for_update()
    course = db.scalar(
        query.options(
            selectinload(Course.sections),
            selectinload(Course.videos),
            selectinload(Course.quizzes).selectinload(Quiz.questions),
        ).execution_options(populate_existing=True)
    )
    enrollment_query = select(Enrollment).where(
        Enrollment.course_id == course_id, Enrollment.learner_id == learner_id
    )
    if for_update:
        enrollment_query = enrollment_query.with_for_update()
    enrollment = db.scalar(enrollment_query.execution_options(populate_existing=True))
    if course is None or enrollment is None:
        raise AppError(404, "NOT_FOUND", "해당 강좌의 수강 내역이 존재하지 않습니다.")
    if course.status not in ("PUBLISHED", "EDITING", "OPERATOR_HIDDEN"):
        raise AppError(403, "FORBIDDEN", "현재 학습할 수 없는 강좌입니다.")
    return course, enrollment


def active_curriculum(course: Course) -> tuple[list[Section], list[Video], list[Quiz]]:
    sections = sorted(
        (section for section in course.sections if section.is_active),
        key=lambda section: (section.sort_order, section.id),
    )
    section_ids = {section.id for section in sections}
    videos = sorted(
        (
            video
            for video in course.videos
            if video.is_active and video.video_type == "LESSON" and video.section_id in section_ids
        ),
        key=lambda video: (video.sort_order, video.id),
    )
    quizzes = [
        quiz
        for quiz in course.quizzes
        if quiz.is_active
        and (
            (quiz.quiz_type == "UNIT" and quiz.section_id in section_ids)
            or (quiz.quiz_type == "FINAL" and quiz.section_id is None)
        )
    ]
    return sections, videos, quizzes


def completed_item_ids(db: Session, enrollment_id: int) -> tuple[set[int], set[int]]:
    videos = set(
        db.scalars(
            select(VideoProgress.video_id).where(
                VideoProgress.enrollment_id == enrollment_id,
                VideoProgress.is_completed.is_(True),
            )
        )
    )
    quizzes = set(
        db.scalars(
            select(QuizAttempt.quiz_id).where(
                QuizAttempt.enrollment_id == enrollment_id,
                QuizAttempt.status == "SUBMITTED",
                QuizAttempt.quiz_id.is_not(None),
            )
        )
    )
    return videos, quizzes


def unresolved_question_ids(db: Session, course: Course, enrollment_id: int) -> set[int]:
    _, _, quizzes = active_curriculum(course)
    active_ids = {
        question.id for quiz in quizzes for question in quiz.questions if question.is_active
    }
    return set(
        db.scalars(
            select(QuestionRecord.question_id).where(
                QuestionRecord.enrollment_id == enrollment_id,
                QuestionRecord.status == "REVIEW_NEEDED",
                QuestionRecord.question_id.in_(active_ids),
            )
        )
    )


def update_completion(db: Session, course: Course, enrollment: Enrollment) -> CompletionData:
    """해당 수강 등록만 판정한다. 호출자가 잠금과 트랜잭션 완료를 담당한다."""
    _, videos, quizzes = active_curriculum(course)
    completed_videos, submitted_quizzes = completed_item_ids(db, enrollment.id)
    total = len(videos) + len(quizzes)
    completed = sum(video.id in completed_videos for video in videos) + sum(
        quiz.id in submitted_quizzes for quiz in quizzes
    )
    unresolved = len(unresolved_question_ids(db, course, enrollment.id))
    all_items_completed = total > 0 and completed == total
    is_completed = all_items_completed and unresolved == 0
    if is_completed:
        now = datetime.now(SEOUL_TZ)
        enrollment.status = "COMPLETED"
        enrollment.first_completed_at = enrollment.first_completed_at or now
        enrollment.current_completed_at = enrollment.current_completed_at or now
    else:
        enrollment.status = "IN_PROGRESS"
        enrollment.current_completed_at = None

    if is_completed:
        message, redirect = "강좌 수료가 완료되었습니다.", None
    elif all_items_completed and unresolved:
        message = f"남은 오답 {unresolved}개를 보관함에서 해결하면 수료가 완료됩니다."
        redirect = "LIBRARY_QUIZ_NOTES"
    else:
        message, redirect = "모든 학습 영상과 퀴즈를 완료해 주세요.", None
    return CompletionData(
        course_id=course.id,
        enrollment_id=enrollment.id,
        is_completed=is_completed,
        status=enrollment.status,
        progress_percent=(completed * 100 // total if total else 0),
        unresolved_wrong_count=unresolved,
        first_completed_at=enrollment.first_completed_at,
        current_completed_at=enrollment.current_completed_at,
        message=message,
        redirect_target=redirect,
    )
