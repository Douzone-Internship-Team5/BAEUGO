"""테스트용 강좌 데이터. 시드에는 강좌가 없어서 필요한 테스트에서 직접 만든다."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import (
    Course,
    Enrollment,
    Question,
    QuestionChoice,
    Quiz,
    Section,
    User,
    Video,
    VideoScript,
)


def make_course(
    db: Session,
    instructor: User,
    *,
    title: str = "실무자를 위한 엑셀 자동화",
    status: str = "PUBLISHED",
    category_id: int = 6,
    topic_id: int = 61,
    published_at: datetime | None = None,
    price_krw: int = 29000,
) -> Course:
    course = Course(
        instructor_id=instructor.id,
        title=title,
        description=f"{title} 강좌 소개",
        status=status,
        category_id=category_id,
        topic_id=topic_id,
        difficulty="BEGINNER",
        price_krw=price_krw,
        published_at=published_at or datetime(2026, 10, 21, 6, 0, tzinfo=UTC),
    )
    db.add(course)
    db.flush()
    return course


def make_section(
    db: Session, course: Course, *, title: str = "섹션 1", sort_order: int = 1, is_active=True
) -> Section:
    section = Section(course_id=course.id, title=title, sort_order=sort_order, is_active=is_active)
    db.add(section)
    db.flush()
    return section


def make_video(
    db: Session,
    course: Course,
    *,
    section: Section | None = None,
    video_type: str = "LESSON",
    title: str = "1강",
    sort_order: int = 1,
    duration_seconds: int = 360,
    is_active: bool = True,
    scripts: tuple[str, ...] = (),
) -> Video:
    video = Video(
        course_id=course.id,
        section_id=section.id if section else None,
        video_type=video_type,
        title=title,
        video_storage_key=f"courses/{course.id}/videos/{title}.mp4",
        duration_seconds=duration_seconds,
        sort_order=sort_order,
        stt_status="COMPLETED",
        is_active=is_active,
    )
    db.add(video)
    db.flush()
    db.add_all(
        VideoScript(
            video_id=video.id,
            seq=seq,
            start_ms=(seq - 1) * 1000,
            end_ms=seq * 1000,
            content=content,
        )
        for seq, content in enumerate(scripts, start=1)
    )
    db.flush()
    return video


def make_quiz(
    db: Session,
    course: Course,
    *,
    section: Section | None = None,
    question_count: int = 1,
    inactive_question_count: int = 0,
    is_active: bool = True,
) -> Quiz:
    is_unit = section is not None
    quiz = Quiz(
        course_id=course.id,
        section_id=section.id if section else None,
        quiz_type="UNIT" if is_unit else "FINAL",
        ai_status="COMPLETED" if is_unit else None,
        is_active=is_active,
    )
    db.add(quiz)
    db.flush()
    for order_no in range(1, question_count + inactive_question_count + 1):
        is_active_question = order_no <= question_count
        question = Question(
            quiz_id=quiz.id,
            # 비활성 문항은 명세 규칙대로 order_no를 음수로 옮겨 둔다.
            order_no=order_no if is_active_question else -order_no,
            content=f"문제 {order_no}",
            correct_choice_no=1,
            explanation=f"해설 {order_no}",
            generated_by="AI" if is_unit else "INSTRUCTOR",
            is_active=is_active_question,
        )
        db.add(question)
        db.flush()
        db.add_all(
            QuestionChoice(question_id=question.id, choice_no=number, content=f"보기 {number}")
            for number in range(1, 5)
        )
    db.flush()
    return quiz


def enroll(
    db: Session,
    learner: User,
    course: Course,
    *,
    status: str = "IN_PROGRESS",
    last_studied_at: datetime | None = None,
) -> Enrollment:
    enrollment = Enrollment(
        learner_id=learner.id, course_id=course.id, status=status, last_studied_at=last_studied_at
    )
    db.add(enrollment)
    db.flush()
    return enrollment
