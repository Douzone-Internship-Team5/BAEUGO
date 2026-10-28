from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.errors import AppError
from app.core.timezone import SEOUL_TZ
from app.models import (
    AttendanceDay,
    CourseDailyStat,
    LearningGoal,
    QuestionAnswer,
    QuestionRecord,
    QuizAttempt,
    Role,
    StudyActivitySession,
    UserRole,
)
from app.services import goals, learner_quiz, learning
from tests.conftest import auth_header
from tests.factories import enroll
from tests.test_learner_quiz import build_course, complete_video


def freeze_clock(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 10, 27, 12, tzinfo=SEOUL_TZ)
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    for module in (learner_quiz, learning, goals):
        monkeypatch.setattr(module, "datetime", FixedDatetime)


def prepare_attempt(db, get_user, *, question_count=2):
    learner, course, section, video, unit, final, enrollment = build_course(
        db, get_user, question_count=question_count
    )
    complete_video(db, enrollment, video)
    attempt = learner_quiz.get_unit_attempt(db, learner.id, section.id)
    return learner, course, section, video, unit, final, enrollment, attempt


def answer_body(attempt, index=0, *, choice=1, activity=None):
    now = learner_quiz.datetime.now(SEOUL_TZ)
    session = {
        "sessionKey": f"quiz-{attempt.attempt_id}-{index}",
        "startedAt": (now - timedelta(seconds=60)).isoformat(),
        "endedAt": now.isoformat(),
        "activeSeconds": 60,
    }
    if activity is not None:
        session.update(activity)
    return {
        "attemptId": attempt.attempt_id,
        "attemptQuestionId": attempt.questions[index].attempt_question_id,
        "selectedChoiceNo": choice,
        "activitySession": session,
    }


def post_answer(client, learner, quiz_id, body):
    return client.post(
        f"/api/v1/quizzes/{quiz_id}/answers", json=body, headers=auth_header(learner.id)
    )


def test_q2_contract_cumulative_activity_duplicate_and_stats(client, db, get_user, monkeypatch):
    freeze_clock(monkeypatch)
    learner, course, section, _, unit, _, enrollment, attempt = prepare_attempt(db, get_user)
    goal = db.get(LearningGoal, learner.id)
    goal.daily_target_minutes = 1
    db.commit()
    activity = {
        "sessionKey": "quiz-cumulative",
        "startedAt": "2026-10-27T10:00:00+09:00",
        "endedAt": "2026-10-27T10:01:00+09:00",
        "activeSeconds": 60,
    }
    body = answer_body(attempt, activity=activity)
    response = post_answer(client, learner, unit.id, body)
    assert response.status_code == 200
    result = response.json()["data"]
    assert set(result) == {
        "answerId",
        "attemptQuestionId",
        "questionId",
        "selectedChoiceNo",
        "isCorrect",
        "correctChoiceNo",
        "explanation",
        "questionRecordStatus",
        "attemptStatus",
        "nextOrderNo",
        "hasUnresolvedWrongAfterFinal",
        "isCourseCompleted",
    }
    assert result["isCorrect"] is True
    assert result["attemptStatus"] == "IN_PROGRESS"
    assert result["nextOrderNo"] == 2
    session = db.scalar(select(StudyActivitySession))
    assert session.activity_type == "QUIZ"
    assert session.video_id is None
    assert session.quiz_attempt_id == attempt.attempt_id
    assert session.enrollment_id == enrollment.id
    assert session.active_seconds == 60
    resumed = client.get(f"/api/v1/sections/{section.id}/quiz", headers=auth_header(learner.id))
    assert resumed.json()["data"]["nextOrderNo"] == 2
    # 중복 제출은 출석 및 통계까지 재처리하지 않고 409로 거부한다.
    assert post_answer(client, learner, unit.id, body).status_code == 409
    goal = db.get(LearningGoal, learner.id)
    goal.daily_target_minutes = 20
    db.commit()
    activity.update(endedAt="2026-10-27T10:01:30+09:00", activeSeconds=90)
    response = post_answer(client, learner, unit.id, answer_body(attempt, 1, activity=activity))
    assert response.status_code == 200
    assert response.json()["data"]["attemptStatus"] == "SUBMITTED"
    assert response.json()["data"]["nextOrderNo"] is None
    assert db.scalar(select(func.count()).select_from(StudyActivitySession)) == 1
    attendance = db.get(AttendanceDay, (learner.id, date(2026, 10, 27)), populate_existing=True)
    assert attendance.active_seconds == 90
    assert attendance.target_minutes_snapshot == 1
    assert attendance.is_attended
    stat = db.get(CourseDailyStat, (course.id, date(2026, 10, 27)), populate_existing=True)
    assert (stat.first_answer_cnt, stat.first_correct_cnt, stat.first_submitted_cnt) == (2, 2, 1)
    assert db.scalar(select(func.count()).select_from(QuestionAnswer)) == 2


def test_q2_auth_ownership_and_question_membership(client, db, get_user):
    learner, _, _, _, unit, final, _, attempt = prepare_attempt(db, get_user)
    path = f"/api/v1/quizzes/{unit.id}/answers"
    body = answer_body(attempt)
    assert client.post(path, json=body).status_code == 401
    for email in ("instructor1@baeugo.test", "admin@baeugo.test"):
        response = client.post(path, json=body, headers=auth_header(get_user(email).id))
        assert response.status_code == 403
    other = get_user("learner1@baeugo.test")
    other.active_mode = Role.LEARNER
    db.add(UserRole(user_id=other.id, role=Role.LEARNER))
    db.commit()
    db.expire(other, ["roles"])
    assert post_answer(client, other, unit.id, body).status_code == 404
    assert post_answer(client, learner, final.id, body).status_code == 404
    assert post_answer(client, learner, 0, body).status_code == 404
    for field in ("attemptId", "attemptQuestionId"):
        invalid = dict(body, **{field: 999999})
        assert post_answer(client, learner, unit.id, invalid).status_code == 404
    assert db.scalar(select(func.count()).select_from(QuestionAnswer)) == 0
    assert db.scalar(select(func.count()).select_from(StudyActivitySession)) == 0


@pytest.mark.parametrize(
    "invalid_case",
    [
        "choice_zero",
        "choice_five",
        "missing_activity",
        "blank_key",
        "long_key",
        "negative_seconds",
        "reversed_time",
        "excess_seconds",
        "future_time",
    ],
)
def test_q2_invalid_input_does_not_persist_partial_results(
    client, db, get_user, monkeypatch, invalid_case
):
    freeze_clock(monkeypatch)
    learner, _, _, _, unit, _, _, attempt = prepare_attempt(db, get_user)
    body = answer_body(attempt)
    activity = body["activitySession"]
    if invalid_case == "choice_zero":
        body["selectedChoiceNo"] = 0
    elif invalid_case == "choice_five":
        body["selectedChoiceNo"] = 5
    elif invalid_case == "missing_activity":
        del body["activitySession"]
    elif invalid_case == "blank_key":
        activity["sessionKey"] = "   "
    elif invalid_case == "long_key":
        activity["sessionKey"] = "x" * 65
    elif invalid_case == "negative_seconds":
        activity["activeSeconds"] = -1
    elif invalid_case == "reversed_time":
        activity["endedAt"] = "2026-10-27T10:00:00+09:00"
    elif invalid_case == "excess_seconds":
        activity["activeSeconds"] = 1200
    else:
        activity.update(startedAt="2026-10-27T13:00:00+09:00", endedAt="2026-10-27T13:01:00+09:00")
    response = post_answer(client, learner, unit.id, body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    for model in (
        QuestionAnswer,
        QuestionRecord,
        StudyActivitySession,
        AttendanceDay,
        CourseDailyStat,
    ):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    assert db.get(QuizAttempt, attempt.attempt_id, populate_existing=True).status == "IN_PROGRESS"


@pytest.mark.parametrize("binding", ["video", "other_attempt", "other_enrollment"])
def test_q2_session_key_cannot_cross_activity_ownership(client, db, get_user, binding):
    learner, course, _, video, unit, _, enrollment, attempt = prepare_attempt(db, get_user)
    now = datetime.now(SEOUL_TZ)
    bound_enrollment_id = enrollment.id
    bound_attempt_id = attempt.attempt_id
    if binding == "other_enrollment":
        bound_enrollment_id = enroll(db, get_user("learner1@baeugo.test"), course).id
    if binding != "video":
        previous = QuizAttempt(
            enrollment_id=bound_enrollment_id, quiz_id=unit.id, attempt_type="UNIT"
        )
        db.add(previous)
        db.flush()
        bound_attempt_id = previous.id
    db.add(
        StudyActivitySession(
            enrollment_id=bound_enrollment_id,
            activity_type="VIDEO" if binding == "video" else "QUIZ",
            video_id=video.id if binding == "video" else None,
            quiz_attempt_id=None if binding == "video" else bound_attempt_id,
            session_key="already-bound",
            study_date=now.date(),
            started_at=now - timedelta(seconds=60),
            ended_at=now,
            active_seconds=60,
        )
    )
    db.commit()
    body = answer_body(attempt, activity={"sessionKey": "already-bound"})
    assert post_answer(client, learner, unit.id, body).status_code == 400
    assert db.scalar(select(func.count()).select_from(QuestionAnswer)) == 0
    assert db.scalar(select(func.count()).select_from(StudyActivitySession)) == 1
    assert db.scalar(select(func.count()).select_from(AttendanceDay)) == 0


def test_q2_cancelled_attempt_cannot_write_activity(client, db, get_user):
    learner, _, _, _, unit, _, _, attempt = prepare_attempt(db, get_user)
    cancelled = client.post(
        f"/api/v1/quiz-attempts/{attempt.attempt_id}/cancel", headers=auth_header(learner.id)
    )
    assert cancelled.status_code == 200
    assert post_answer(client, learner, unit.id, answer_body(attempt)).status_code == 409
    assert db.scalar(select(func.count()).select_from(StudyActivitySession)) == 0


def test_q2_final_wrong_review_completion_and_first_attempt_stats(client, db, get_user):
    learner, course, _, _, unit, final, enrollment, unit_attempt = prepare_attempt(
        db, get_user, question_count=1
    )
    assert post_answer(client, learner, unit.id, answer_body(unit_attempt)).status_code == 200
    final_attempt = learner_quiz.get_course_attempt(db, learner.id, course.id, "FINAL", None)
    wrong = post_answer(client, learner, final.id, answer_body(final_attempt, choice=2))
    assert wrong.status_code == 200
    assert wrong.json()["data"]["hasUnresolvedWrongAfterFinal"]
    assert not wrong.json()["data"]["isCourseCompleted"]
    review = learner_quiz.get_course_attempt(db, learner.id, course.id, "WRONG_REVIEW", None)
    body = answer_body(review)
    assert post_answer(client, learner, final.id, body).status_code == 404
    corrected = post_answer(client, learner, 0, body)
    assert corrected.status_code == 200
    assert corrected.json()["data"]["questionRecordStatus"] == "CORRECT"
    assert corrected.json()["data"]["isCourseCompleted"]
    db.refresh(enrollment)
    assert enrollment.status == "COMPLETED"
    assert enrollment.first_completed_at is not None
    assert post_answer(client, learner, 0, body).status_code == 409
    stat = db.get(
        CourseDailyStat, (course.id, datetime.now(SEOUL_TZ).date()), populate_existing=True
    )
    assert (stat.first_answer_cnt, stat.first_correct_cnt, stat.first_submitted_cnt) == (2, 1, 2)
    assert db.scalar(select(func.count()).select_from(QuestionAnswer)) == 3


def test_q2_failure_after_activity_write_rolls_back_all_results(client, db, get_user, monkeypatch):
    learner, course, _, _, unit, final, enrollment, unit_attempt = prepare_attempt(
        db, get_user, question_count=1
    )
    assert post_answer(client, learner, unit.id, answer_body(unit_attempt)).status_code == 200
    attempt = learner_quiz.get_course_attempt(db, learner.id, course.id, "FINAL", None)
    today = datetime.now(SEOUL_TZ).date()
    attendance = db.get(AttendanceDay, (learner.id, today), populate_existing=True)
    previous_seconds = attendance.active_seconds
    original = learner_quiz.record_learning_activity

    def fail_after_activity(*args, **kwargs):
        original(*args, **kwargs)
        raise AppError(503, "TEST_FAILURE", "저장 실패 재현")

    monkeypatch.setattr(learner_quiz, "record_learning_activity", fail_after_activity)
    body = answer_body(attempt)
    assert post_answer(client, learner, final.id, body).status_code == 503
    db.refresh(enrollment)
    db.refresh(attendance)
    assert enrollment.status == "IN_PROGRESS"
    assert enrollment.first_completed_at is None
    assert enrollment.current_completed_at is None
    assert attendance.active_seconds == previous_seconds
    assert db.get(QuizAttempt, attempt.attempt_id, populate_existing=True).status == "IN_PROGRESS"
    for model in (QuestionAnswer, QuestionRecord, StudyActivitySession):
        assert db.scalar(select(func.count()).select_from(model)) == 1
    stat = db.get(CourseDailyStat, (course.id, today), populate_existing=True)
    assert (stat.first_answer_cnt, stat.first_correct_cnt, stat.first_submitted_cnt) == (1, 1, 1)
    monkeypatch.setattr(learner_quiz, "record_learning_activity", original)
    retry = post_answer(client, learner, final.id, body)
    assert retry.status_code == 200
    assert retry.json()["data"]["isCourseCompleted"]


def test_q2_and_c7_share_overlap_deduplication_and_goals(client, db, get_user, monkeypatch):
    freeze_clock(monkeypatch)
    learner, _, _, video, unit, _, _, attempt = prepare_attempt(db, get_user)
    activity = {
        "sessionKey": "video-window",
        "startedAt": "2026-10-27T10:00:00+09:00",
        "endedAt": "2026-10-27T10:01:00+09:00",
        "activeSeconds": 60,
    }
    video_body = {"lastPositionSeconds": 60, "watchedRange": [0, 60], "activitySession": activity}
    path = f"/api/v1/videos/{video.id}/progress"
    headers = auth_header(learner.id)
    assert client.put(path, json=video_body, headers=headers).status_code == 200
    quiz_activity = dict(
        activity,
        sessionKey="quiz-window",
        startedAt="2026-10-27T10:00:30+09:00",
        endedAt="2026-10-27T10:01:30+09:00",
    )
    body = answer_body(attempt, activity=quiz_activity)
    assert post_answer(client, learner, unit.id, body).status_code == 200
    attendance = db.get(AttendanceDay, (learner.id, date(2026, 10, 27)), populate_existing=True)
    assert attendance.active_seconds == 90
    quiz_activity.update(endedAt="2026-10-27T10:02:00+09:00", activeSeconds=90)
    body = answer_body(attempt, 1, activity=quiz_activity)
    assert post_answer(client, learner, unit.id, body).status_code == 200
    repeated_video = client.put(path, json=video_body, headers=headers)
    assert repeated_video.json()["data"]["todayActiveSeconds"] == 120
    # 퀴즈용 키를 영상 진도 저장에 재사용할 수도 없다.
    activity["sessionKey"] = "quiz-window"
    assert client.put(path, json=video_body, headers=headers).status_code == 400
    today = client.get("/api/v1/me/today", headers=headers).json()["data"]
    assert today["goal"]["activeSeconds"] == 120
    response = client.get("/api/v1/me/attendance?year=2026&month=10", headers=headers)
    calendar = response.json()["data"]
    assert calendar["calendarDays"][0]["activeSeconds"] == 120


def test_q2_midnight_cumulative_activity_and_duplicate_submission(
    client, db, get_user, monkeypatch
):
    freeze_clock(monkeypatch)
    learner, _, _, _, unit, _, _, attempt = prepare_attempt(db, get_user, question_count=3)
    activity = {
        "sessionKey": "midnight-quiz",
        "startedAt": "2026-10-26T23:58:00+09:00",
        "endedAt": "2026-10-26T23:59:00+09:00",
        "activeSeconds": 30,
    }
    response = post_answer(client, learner, unit.id, answer_body(attempt, activity=activity))
    assert response.status_code == 200
    activity.update(endedAt="2026-10-27T00:01:00+09:00", activeSeconds=90)
    for index in (1, 2):
        body = answer_body(attempt, index, activity=activity)
        assert post_answer(client, learner, unit.id, body).status_code == 200
    assert post_answer(client, learner, unit.id, body).status_code == 409
    previous = db.get(AttendanceDay, (learner.id, date(2026, 10, 26)), populate_existing=True)
    current = db.get(AttendanceDay, (learner.id, date(2026, 10, 27)), populate_existing=True)
    assert (previous.active_seconds, current.active_seconds) == (60, 30)
    assert db.scalar(select(func.count()).select_from(StudyActivitySession)) == 1
