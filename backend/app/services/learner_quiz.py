from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.errors import AppError
from app.core.timezone import SEOUL_TZ
from app.models.course import Section
from app.models.learning import Enrollment
from app.models.quiz import (
    AttemptQuestion,
    Question,
    QuestionAnswer,
    QuestionRecord,
    Quiz,
    QuizAttempt,
)
from app.models.stat import CourseDailyStat
from app.schemas.learner_quiz import (
    AttemptChoice,
    AttemptQuestionData,
    CancelAttemptData,
    QuizAnswerData,
    QuizAnswerRequest,
    QuizAttemptData,
    QuizNote,
    QuizNoteCounts,
    QuizNotesData,
)
from app.services.learning_activity import lock_learning_activity, record_learning_activity
from app.services.learning_completion import (
    active_curriculum,
    completed_item_ids,
    load_enrolled_course,
    unresolved_question_ids,
    update_completion,
)
from app.services.storage import get_thumbnail_url


def _attempt_query():
    return select(QuizAttempt).options(
        selectinload(QuizAttempt.attempt_questions)
        .joinedload(AttemptQuestion.question)
        .selectinload(Question.choices),
        selectinload(QuizAttempt.attempt_questions).joinedload(AttemptQuestion.answer),
    )


def _attempt_data(attempt: QuizAttempt) -> QuizAttemptData:
    questions = sorted(attempt.attempt_questions, key=lambda item: item.order_no)
    next_order = next((item.order_no for item in questions if item.answer is None), None)
    return QuizAttemptData(
        attempt_id=attempt.id,
        quiz_id=attempt.quiz_id,
        quiz_type=attempt.attempt_type if attempt.quiz_id is not None else None,
        attempt_type=attempt.attempt_type,
        attempt_no=attempt.attempt_no,
        status=attempt.status,
        total_questions=len(questions),
        next_order_no=next_order,
        questions=[
            AttemptQuestionData(
                attempt_question_id=item.id,
                question_id=item.question_id,
                order_no=item.order_no,
                origin=item.origin,
                content=item.question.content,
                choices=[
                    AttemptChoice(choice_no=choice.choice_no, content=choice.content)
                    for choice in item.question.choices
                ],
            )
            for item in questions
        ],
    )


def _get_or_create_attempt(
    db: Session,
    enrollment: Enrollment,
    quiz: Quiz | None,
    questions: list[Question],
    *,
    question_id: int | None = None,
) -> QuizAttemptData:
    attempt_type = quiz.quiz_type if quiz else "WRONG_REVIEW"
    quiz_id = quiz.id if quiz else None
    scope = (
        QuizAttempt.enrollment_id == enrollment.id,
        QuizAttempt.attempt_type == attempt_type,
        QuizAttempt.quiz_id == quiz_id,
    )
    candidates = db.scalars(
        _attempt_query()
        .where(*scope, QuizAttempt.status == "IN_PROGRESS")
        .order_by(QuizAttempt.id.desc())
    ).all()
    requested_ids = {question.id for question in questions}
    for attempt in candidates:
        fixed_ids = {item.question_id for item in attempt.attempt_questions}
        if quiz is not None or (
            (question_id is not None and fixed_ids == {question_id})
            or (question_id is None and requested_ids and requested_ids <= fixed_ids)
        ):
            return _attempt_data(attempt)
    if not questions:
        raise AppError(404, "NOT_FOUND", "복습할 미해결 오답 문항이 없거나 출제할 문항이 없습니다.")

    attempt_no = (db.scalar(select(func.max(QuizAttempt.attempt_no)).where(*scope)) or 0) + 1
    attempt = QuizAttempt(
        enrollment_id=enrollment.id,
        quiz_id=quiz_id,
        attempt_type=attempt_type,
        attempt_no=attempt_no,
        status="IN_PROGRESS",
    )
    db.add(attempt)
    db.flush()
    db.add_all(
        AttemptQuestion(
            attempt_id=attempt.id,
            question_id=question.id,
            order_no=order,
            origin="BASE" if quiz else "WRONG_REVIEW",
        )
        for order, question in enumerate(questions, start=1)
    )
    db.flush()
    attempt = db.scalar(
        _attempt_query()
        .where(QuizAttempt.id == attempt.id)
        .execution_options(populate_existing=True)
    )
    return _attempt_data(attempt)


def get_unit_attempt(db: Session, learner_id: int, section_id: int) -> QuizAttemptData:
    section = db.get(Section, section_id)
    if section is None:
        raise AppError(404, "NOT_FOUND", "존재하지 않는 섹션 또는 퀴즈입니다.")
    course, enrollment = load_enrolled_course(db, learner_id, section.course_id, for_update=True)
    sections, videos, quizzes = active_curriculum(course)
    if section_id not in {item.id for item in sections}:
        raise AppError(404, "NOT_FOUND", "존재하지 않는 섹션 또는 퀴즈입니다.")
    quiz = next(
        (item for item in quizzes if item.quiz_type == "UNIT" and item.section_id == section_id),
        None,
    )
    if quiz is None:
        raise AppError(404, "NOT_FOUND", "존재하지 않는 섹션 또는 퀴즈입니다.")
    completed, _ = completed_item_ids(db, enrollment.id)
    if any(video.id not in completed for video in videos if video.section_id == section_id):
        raise AppError(
            403,
            "FORBIDDEN",
            "섹션 내 모든 학습 영상을 완료해야 단위 확인 퀴즈에 응시할 수 있습니다.",
        )
    questions = sorted(
        (question for question in quiz.questions if question.is_active),
        key=lambda question: (question.order_no, question.id),
    )
    result = _get_or_create_attempt(db, enrollment, quiz, questions)
    db.commit()
    return result


def get_course_attempt(
    db: Session, learner_id: int, course_id: int, attempt_type: str, question_id: int | None
) -> QuizAttemptData:
    if attempt_type not in ("FINAL", "WRONG_REVIEW") or (
        attempt_type == "FINAL" and question_id is not None
    ):
        raise AppError(400, "VALIDATION_ERROR", "응시 유형과 복습 문항을 확인해 주세요.")
    course, enrollment = load_enrolled_course(db, learner_id, course_id, for_update=True)
    _, videos, quizzes = active_curriculum(course)
    if attempt_type == "FINAL":
        completed, submitted = completed_item_ids(db, enrollment.id)
        if any(video.id not in completed for video in videos) or any(
            quiz.id not in submitted for quiz in quizzes if quiz.quiz_type == "UNIT"
        ):
            raise AppError(
                403,
                "FORBIDDEN",
                "앞선 모든 섹션 학습과 단위 퀴즈를 완료해야 "
                "코스 종합 퀴즈에 응시할 수 있습니다.",
            )
        quiz = next((item for item in quizzes if item.quiz_type == "FINAL"), None)
        if quiz is None:
            raise AppError(404, "NOT_FOUND", "존재하지 않는 퀴즈입니다.")
        questions = sorted(
            (question for question in quiz.questions if question.is_active),
            key=lambda question: (question.order_no, question.id),
        )
    else:
        quiz = None
        wrong_ids = unresolved_question_ids(db, course, enrollment.id)
        if question_id is not None:
            wrong_ids &= {question_id}
        if not wrong_ids:
            raise AppError(404, "NOT_FOUND", "복습할 미해결 오답 문항이 없습니다.")
        questions = list(
            db.scalars(
                select(Question)
                .where(Question.id.in_(wrong_ids))
                .order_by(Question.quiz_id, Question.order_no, Question.id)
            )
        )
    result = _get_or_create_attempt(db, enrollment, quiz, questions, question_id=question_id)
    db.commit()
    return result


def _owned_attempt(db: Session, learner_id: int, attempt_id: int):
    enrollment = db.scalar(
        select(Enrollment)
        .join(QuizAttempt, QuizAttempt.enrollment_id == Enrollment.id)
        .where(QuizAttempt.id == attempt_id, Enrollment.learner_id == learner_id)
    )
    if enrollment is None:
        raise AppError(404, "NOT_FOUND", "존재하지 않는 응시 세션입니다.")
    course, enrollment = load_enrolled_course(db, learner_id, enrollment.course_id, for_update=True)
    attempt = db.scalar(
        _attempt_query()
        .where(QuizAttempt.id == attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if attempt is None:
        raise AppError(404, "NOT_FOUND", "존재하지 않는 응시 세션입니다.")
    return course, enrollment, attempt


def cancel_attempt(db: Session, learner_id: int, attempt_id: int) -> CancelAttemptData:
    _, _, attempt = _owned_attempt(db, learner_id, attempt_id)
    if attempt.status != "IN_PROGRESS":
        raise AppError(409, "CONFLICT", "이미 제출 완료되었거나 취소된 세션입니다.")
    attempt.status = "CANCELLED"
    db.commit()
    return CancelAttemptData(attempt_id=attempt.id, status=attempt.status)


def submit_answer(
    db: Session, learner_id: int, quiz_id: int, body: QuizAnswerRequest
) -> QuizAnswerData:
    """답안·오답 상태·통계·수료·학습 활동·출석을 하나의 트랜잭션으로 저장한다."""
    try:
        lock_learning_activity(db, learner_id)
        now = datetime.now(SEOUL_TZ)
        result = grade_answer(db, learner_id, quiz_id, body, now=now)
        attempt = db.get(QuizAttempt, body.attempt_id)
        assert attempt is not None
        activity = body.activity_session
        record_learning_activity(
            db,
            learner_id=learner_id,
            enrollment_id=attempt.enrollment_id,
            activity_type="QUIZ",
            session_key=activity.session_key,
            started_at=activity.started_at,
            ended_at=activity.ended_at,
            active_seconds=activity.active_seconds,
            now=now,
            quiz_attempt_id=attempt.id,
        )
        db.commit()
        return result
    except Exception:
        # 활동 저장 또는 commit 실패 시 채점 결과까지 함께 되돌린 뒤 원래 오류를 전달한다.
        db.rollback()
        raise


def grade_answer(
    db: Session,
    learner_id: int,
    quiz_id: int,
    body: QuizAnswerRequest,
    *,
    now: datetime | None = None,
) -> QuizAnswerData:
    """채점과 수료 판정만 수행하며, 활동 저장 및 트랜잭션 완료는 submit_answer가 담당한다."""
    course, enrollment, attempt = _owned_attempt(db, learner_id, body.attempt_id)
    if quiz_id != (attempt.quiz_id if attempt.quiz_id is not None else 0):
        raise AppError(404, "NOT_FOUND", "응시 세션의 퀴즈와 일치하지 않습니다.")
    if attempt.status != "IN_PROGRESS":
        raise AppError(409, "CONFLICT", "이미 제출 완료되었거나 취소된 세션입니다.")
    items = sorted(attempt.attempt_questions, key=lambda item: item.order_no)
    item = next((item for item in items if item.id == body.attempt_question_id), None)
    if item is None:
        raise AppError(404, "NOT_FOUND", "응시 세션에 포함되지 않은 문항입니다.")
    if item.answer is not None:
        raise AppError(409, "CONFLICT", "이미 답안을 제출한 문항입니다.")
    question = item.question
    if body.selected_choice_no not in {choice.choice_no for choice in question.choices}:
        raise AppError(400, "VALIDATION_ERROR", "선택한 보기 번호는 1~4 사이여야 합니다.")
    now = now or datetime.now(SEOUL_TZ)
    is_correct = body.selected_choice_no == question.correct_choice_no
    answer = QuestionAnswer(
        attempt_question_id=item.id,
        selected_choice_no=body.selected_choice_no,
        is_correct=is_correct,
        answered_at=now,
    )
    db.add(answer)
    db.flush()
    record = db.scalar(
        select(QuestionRecord).where(
            QuestionRecord.enrollment_id == enrollment.id, QuestionRecord.question_id == question.id
        )
    )
    if record is None:
        record = QuestionRecord(enrollment_id=enrollment.id, question_id=question.id, wrong_count=0)
        db.add(record)
    record.status = "CORRECT" if is_correct else "REVIEW_NEEDED"
    record.last_answer_id = answer.id
    record.wrong_count += int(not is_correct)
    record.resolved_at = now if is_correct else None
    remaining = [entry for entry in items if entry.id != item.id and entry.answer is None]
    if not remaining:
        attempt.status = "SUBMITTED"
        attempt.submitted_at = now
    enrollment.last_studied_at = now
    if attempt.attempt_no == 1 and attempt.attempt_type in ("UNIT", "FINAL"):
        values = dict(
            first_answer_cnt=1,
            first_correct_cnt=int(is_correct),
            first_submitted_cnt=int(not remaining),
        )
        db.execute(
            insert(CourseDailyStat)
            .values(course_id=course.id, start_date=now.date(), **values)
            .on_conflict_do_update(
                index_elements=["course_id", "start_date"],
                set_={key: getattr(CourseDailyStat, key) + value for key, value in values.items()},
            )
        )
    db.flush()
    completion = update_completion(db, course, enrollment)
    return QuizAnswerData(
        answer_id=answer.id,
        attempt_question_id=item.id,
        question_id=question.id,
        selected_choice_no=answer.selected_choice_no,
        is_correct=is_correct,
        correct_choice_no=question.correct_choice_no,
        explanation=question.explanation,
        question_record_status=record.status,
        attempt_status=attempt.status,
        next_order_no=remaining[0].order_no if remaining else None,
        has_unresolved_wrong_after_final=(
            attempt.attempt_type == "FINAL"
            and not remaining
            and completion.unresolved_wrong_count > 0
        ),
        is_course_completed=completion.is_completed,
    )


def get_completion(db: Session, learner_id: int, course_id: int):
    course, enrollment = load_enrolled_course(db, learner_id, course_id, for_update=True)
    result = update_completion(db, course, enrollment)
    db.commit()
    return result


def get_quiz_notes(db: Session, learner_id: int, status: str) -> QuizNotesData:
    if status not in ("ALL", "CORRECT", "REVIEW_NEEDED"):
        raise AppError(400, "VALIDATION_ERROR", "유효하지 않은 복습 상태입니다.")
    records = list(
        db.scalars(
            select(QuestionRecord)
            .join(Enrollment)
            .where(Enrollment.learner_id == learner_id)
            .options(
                joinedload(QuestionRecord.enrollment).joinedload(Enrollment.course),
                joinedload(QuestionRecord.question).joinedload(Question.quiz),
                joinedload(QuestionRecord.last_answer),
            )
            .order_by(QuestionRecord.id.desc())
        )
    )
    return QuizNotesData(
        counts=QuizNoteCounts(
            all=len(records),
            correct=sum(record.status == "CORRECT" for record in records),
            review_needed=sum(record.status == "REVIEW_NEEDED" for record in records),
        ),
        quiz_notes=[
            QuizNote(
                record_id=record.id,
                enrollment_id=record.enrollment_id,
                course_id=record.enrollment.course_id,
                course_title=record.enrollment.course.title,
                thumbnail_storage_key=record.enrollment.course.thumbnail_storage_key,
                thumbnail_url=get_thumbnail_url(record.enrollment.course.thumbnail_storage_key),
                quiz_id=record.question.quiz_id,
                quiz_type=record.question.quiz.quiz_type,
                question_id=record.question_id,
                question_content=record.question.content,
                status=record.status,
                wrong_count=record.wrong_count,
                last_answer_id=record.last_answer_id,
                last_answered_at=record.last_answer.answered_at,
                resolved_at=record.resolved_at,
            )
            for record in records
            if status == "ALL" or record.status == status
        ],
    )
