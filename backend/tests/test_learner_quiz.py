from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.errors import AppError
from app.core.timezone import SEOUL_TZ
from app.models import CourseDailyStat, Question, QuestionAnswer, QuizAttempt, VideoProgress
from app.schemas.learner_quiz import QuizAnswerRequest
from app.services import learner_quiz
from tests.conftest import auth_header
from tests.factories import enroll, make_course, make_quiz, make_section, make_video


def build_course(db, get_user, *, question_count=2):
    learner = get_user("learner2@baeugo.test")
    course = make_course(db, get_user("instructor1@baeugo.test"))
    section = make_section(db, course)
    video = make_video(db, course, section=section)
    unit = make_quiz(db, course, section=section, question_count=question_count)
    final = make_quiz(db, course)
    enrollment = enroll(db, learner, course)
    db.commit()
    return learner, course, section, video, unit, final, enrollment


def complete_video(db, enrollment, video):
    db.add(
        VideoProgress(
            enrollment_id=enrollment.id,
            video_id=video.id,
            is_completed=True,
            watched_seconds=video.duration_seconds,
            last_position_seconds=video.duration_seconds,
        )
    )
    db.commit()


def answer_request(attempt, index=0, choice=1):
    now = datetime.now(SEOUL_TZ)
    return QuizAnswerRequest(
        attempt_id=attempt.attempt_id,
        attempt_question_id=attempt.questions[index].attempt_question_id,
        selected_choice_no=choice,
        activity_session=dict(
            session_key=f"quiz-{attempt.attempt_id}-{index}",
            started_at=now - timedelta(seconds=10),
            ended_at=now,
            active_seconds=10,
        ),
    )


def test_unit_contract_lock_and_resume(client, db, get_user):
    learner, course, section, video, unit, final, enrollment = build_course(db, get_user)
    path = f"/api/v1/sections/{section.id}/quiz"
    headers = auth_header(learner.id)
    assert client.get(path, headers=headers).status_code == 403
    complete_video(db, enrollment, video)
    first = client.get(path, headers=headers)
    assert first.status_code == 200
    data = first.json()["data"]
    assert data["nextOrderNo"] == 1
    assert data["totalQuestions"] == 2
    assert "correctChoiceNo" not in data["questions"][0]
    assert "explanation" not in data["questions"][0]
    assert len(data["questions"][0]["choices"]) == 4
    assert client.get(path, headers=headers).json()["data"]["attemptId"] == data["attemptId"]
    assert db.scalar(select(func.count()).select_from(QuizAttempt)) == 1
    schema = client.get("/openapi.json").json()
    assert "/api/v1/quizzes/{id}/answers" not in schema["paths"]
    assert "/api/v1/me/today" in schema["paths"]


def test_cancel_ownership_and_new_attempt(client, db, get_user):
    learner, course, section, video, unit, final, enrollment = build_course(db, get_user)
    complete_video(db, enrollment, video)
    attempt = learner_quiz.get_unit_attempt(db, learner.id, section.id)
    path = f"/api/v1/quiz-attempts/{attempt.attempt_id}/cancel"
    assert client.post(path).status_code == 401
    instructor_headers = auth_header(get_user("instructor1@baeugo.test").id)
    assert client.post(path, headers=instructor_headers).status_code == 403
    # 서비스에서도 다른 사용자 소유의 응시를 노출하지 않는다.
    with pytest.raises(AppError) as error:
        learner_quiz.cancel_attempt(db, get_user("learner1@baeugo.test").id, attempt.attempt_id)
    assert error.value.status_code == 404
    assert client.post(path, headers=auth_header(learner.id)).status_code == 200
    assert client.post(path, headers=auth_header(learner.id)).status_code == 409
    resumed = learner_quiz.get_unit_attempt(db, learner.id, section.id)
    assert resumed.attempt_id != attempt.attempt_id
    assert resumed.attempt_no == 2


def test_grading_resume_duplicate_and_stats(db, get_user):
    learner, course, section, video, unit, final, enrollment = build_course(db, get_user)
    complete_video(db, enrollment, video)
    attempt = learner_quiz.get_unit_attempt(db, learner.id, section.id)
    body = answer_request(attempt, choice=2)
    result = learner_quiz.grade_answer(db, learner.id, unit.id, body)
    assert not result.is_correct
    assert result.next_order_no == 2
    # Q-2 공통 집계 연결 전 채점 부분만 검사한다. 실제 HTTP 제출은 아직 비공개다.
    db.commit()
    resumed = learner_quiz.get_unit_attempt(db, learner.id, section.id)
    assert resumed.attempt_id == attempt.attempt_id
    assert resumed.next_order_no == 2
    with pytest.raises(AppError) as error:
        learner_quiz.grade_answer(db, learner.id, unit.id, body)
    assert error.value.status_code == 409
    assert db.scalar(select(func.count()).select_from(QuestionAnswer)) == 1
    result = learner_quiz.grade_answer(db, learner.id, unit.id, answer_request(attempt, index=1))
    db.commit()
    assert result.attempt_status == "SUBMITTED"
    assert result.next_order_no is None
    stat = db.get(CourseDailyStat, (course.id, datetime.now(SEOUL_TZ).date()))
    assert (stat.first_answer_cnt, stat.first_correct_cnt, stat.first_submitted_cnt) == (2, 1, 1)
    retry = learner_quiz.get_unit_attempt(db, learner.id, section.id)
    learner_quiz.grade_answer(db, learner.id, unit.id, answer_request(retry))
    db.commit()
    db.refresh(stat)
    assert stat.first_answer_cnt == 2


def test_final_review_and_completion(db, get_user):
    learner, course, section, video, unit, final, enrollment = build_course(
        db, get_user, question_count=1
    )
    with pytest.raises(AppError) as error:
        learner_quiz.get_course_attempt(db, learner.id, course.id, "FINAL", None)
    assert error.value.status_code == 403
    complete_video(db, enrollment, video)
    attempt = learner_quiz.get_unit_attempt(db, learner.id, section.id)
    learner_quiz.grade_answer(db, learner.id, unit.id, answer_request(attempt))
    db.commit()
    attempt = learner_quiz.get_course_attempt(db, learner.id, course.id, "FINAL", None)
    result = learner_quiz.grade_answer(db, learner.id, final.id, answer_request(attempt, choice=2))
    db.commit()
    assert result.has_unresolved_wrong_after_final
    assert not result.is_course_completed
    completion = learner_quiz.get_completion(db, learner.id, course.id)
    assert completion.progress_percent == 100
    assert completion.redirect_target == "LIBRARY_QUIZ_NOTES"
    review = learner_quiz.get_course_attempt(db, learner.id, course.id, "WRONG_REVIEW", None)
    assert review.quiz_id is None
    assert review.questions[0].origin == "WRONG_REVIEW"
    learner_quiz.grade_answer(db, learner.id, 0, answer_request(review, choice=2))
    db.commit()
    # 복습에서 또 틀려도 새 복습을 만들 수 있다. 횟수 제한/FINAL 연결 컬럼 없음.
    second = learner_quiz.get_course_attempt(db, learner.id, course.id, "WRONG_REVIEW", None)
    assert second.attempt_id != review.attempt_id
    result = learner_quiz.grade_answer(db, learner.id, 0, answer_request(second))
    db.commit()
    assert result.is_course_completed
    completion = learner_quiz.get_completion(db, learner.id, course.id)
    timestamp = completion.first_completed_at
    assert completion.is_completed
    again = learner_quiz.get_completion(db, learner.id, course.id)
    assert again.first_completed_at == timestamp
    assert again.current_completed_at == completion.current_completed_at


def test_fixed_questions_and_inactive_notes(db, get_user):
    learner, course, section, video, unit, final, enrollment = build_course(db, get_user)
    complete_video(db, enrollment, video)
    attempt = learner_quiz.get_unit_attempt(db, learner.id, section.id)
    learner_quiz.grade_answer(db, learner.id, unit.id, answer_request(attempt, choice=2))
    question = db.get(Question, attempt.questions[0].question_id)
    question.is_active = False
    question.order_no = -question.id
    db.commit()
    resumed = learner_quiz.get_unit_attempt(db, learner.id, section.id)
    assert [item.question_id for item in resumed.questions] == [
        item.question_id for item in attempt.questions
    ]
    notes = learner_quiz.get_quiz_notes(db, learner.id, "REVIEW_NEEDED")
    assert notes.counts.all == 1
    assert notes.quiz_notes[0].question_id == question.id
    with pytest.raises(AppError) as error:
        learner_quiz.get_course_attempt(db, learner.id, course.id, "WRONG_REVIEW", question.id)
    assert error.value.status_code == 404


def test_wrong_attempt_and_path_are_rejected(db, get_user):
    learner, course, section, video, unit, final, enrollment = build_course(db, get_user)
    complete_video(db, enrollment, video)
    attempt = learner_quiz.get_unit_attempt(db, learner.id, section.id)
    body = answer_request(attempt)
    with pytest.raises(AppError) as error:
        learner_quiz.grade_answer(db, learner.id, final.id, body)
    assert error.value.status_code == 404
    body.attempt_question_id = 999999
    with pytest.raises(AppError) as error:
        learner_quiz.grade_answer(db, learner.id, unit.id, body)
    assert error.value.status_code == 404
    assert db.scalar(select(func.count()).select_from(QuestionAnswer)) == 0


def test_inactive_curriculum_and_other_enrollments(db, get_user):
    learner, course, section, video, unit, final, enrollment = build_course(
        db, get_user, question_count=1
    )
    other = enroll(db, get_user("learner1@baeugo.test"), course)
    other.status = "COMPLETED"
    complete_video(db, enrollment, video)
    attempt = learner_quiz.get_unit_attempt(db, learner.id, section.id)
    learner_quiz.grade_answer(db, learner.id, unit.id, answer_request(attempt))
    db.commit()
    attempt = learner_quiz.get_course_attempt(db, learner.id, course.id, "FINAL", None)
    learner_quiz.grade_answer(db, learner.id, final.id, answer_request(attempt))
    db.commit()
    db.refresh(other)
    assert other.status == "COMPLETED"
    hidden = make_section(db, course, sort_order=2, is_active=False)
    make_video(db, course, section=hidden)
    make_quiz(db, course, section=hidden)
    db.commit()
    assert learner_quiz.get_completion(db, learner.id, course.id).is_completed


def test_grading_rollback_is_atomic(db, get_user):
    learner, course, section, video, unit, final, enrollment = build_course(db, get_user)
    complete_video(db, enrollment, video)
    attempt = learner_quiz.get_unit_attempt(db, learner.id, section.id)
    with db.begin_nested() as transaction:
        learner_quiz.grade_answer(db, learner.id, unit.id, answer_request(attempt))
        # 향후 활동 시간 저장 실패 시 같은 트랜잭션 전체를 되돌릴 수 있어야 한다.
        transaction.rollback()
    assert db.scalar(select(func.count()).select_from(QuestionAnswer)) == 0
    assert db.get(CourseDailyStat, (course.id, datetime.now(SEOUL_TZ).date())) is None


def test_single_review_scope_and_cross_enrollment_isolation(db, get_user):
    learner, course, section, video, unit, final, enrollment = build_course(db, get_user)
    complete_video(db, enrollment, video)
    attempt = learner_quiz.get_unit_attempt(db, learner.id, section.id)
    for index in range(2):
        learner_quiz.grade_answer(db, learner.id, unit.id, answer_request(attempt, index, choice=2))
        db.commit()
    question_id = attempt.questions[0].question_id
    single = learner_quiz.get_course_attempt(db, learner.id, course.id, "WRONG_REVIEW", question_id)
    repeated = learner_quiz.get_course_attempt(
        db, learner.id, course.id, "WRONG_REVIEW", question_id
    )
    assert repeated.attempt_id == single.attempt_id
    assert single.total_questions == 1
    all_wrong = learner_quiz.get_course_attempt(db, learner.id, course.id, "WRONG_REVIEW", None)
    assert all_wrong.total_questions == 2
    assert all_wrong.attempt_id != single.attempt_id
    other = get_user("learner1@baeugo.test")
    enroll(db, other, course)
    db.commit()
    assert learner_quiz.get_quiz_notes(db, other.id, "ALL").counts.all == 0
    with pytest.raises(AppError) as error:
        learner_quiz.get_course_attempt(db, other.id, course.id, "WRONG_REVIEW", question_id)
    assert error.value.status_code == 404
    with pytest.raises(AppError) as error:
        learner_quiz.grade_answer(db, other.id, 0, answer_request(single))
    assert error.value.status_code == 404


def test_invalid_queries_and_empty_curriculum(client, db, get_user):
    learner = get_user("learner2@baeugo.test")
    course = make_course(db, get_user("instructor1@baeugo.test"))
    enroll(db, learner, course)
    db.commit()
    headers = auth_header(learner.id)
    for query in (
        "attemptType=BAD",
        "attemptType=FINAL&questionId=1",
        "attemptType=WRONG_REVIEW&questionId=0",
    ):
        response = client.get(f"/api/v1/courses/{course.id}/quiz-attempt?{query}", headers=headers)
        assert response.status_code == 400
    assert client.get("/api/v1/me/quiz-notes?status=BAD", headers=headers).status_code == 400
    completion = learner_quiz.get_completion(db, learner.id, course.id)
    assert not completion.is_completed
    assert completion.progress_percent == 0


def test_cancelled_attempt_cannot_accept_answer(db, get_user):
    learner, course, section, video, unit, final, enrollment = build_course(db, get_user)
    complete_video(db, enrollment, video)
    attempt = learner_quiz.get_unit_attempt(db, learner.id, section.id)
    learner_quiz.cancel_attempt(db, learner.id, attempt.attempt_id)
    with pytest.raises(AppError) as error:
        learner_quiz.grade_answer(db, learner.id, unit.id, answer_request(attempt))
    assert error.value.status_code == 409
    assert db.scalar(select(func.count()).select_from(QuestionAnswer)) == 0
