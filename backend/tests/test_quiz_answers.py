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
from app.services import admin_users, course_service, goals, learner_quiz, learning
from tests.conftest import auth_header
from tests.factories import enroll, make_video
from tests.test_learner_quiz import build_course, complete_video


def freeze_clock(monkeypatch, at="2026-10-27T12:00:00+09:00"):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime.fromisoformat(at)
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


@pytest.mark.parametrize("activity_type", ["VIDEO", "QUIZ"])
def test_old_session_cannot_be_reused_with_today_timestamps(
    client, db, get_user, monkeypatch, activity_type
):
    freeze_clock(monkeypatch)
    learner, _, _, video, unit, _, enrollment, attempt = prepare_attempt(db, get_user)
    started = datetime(2026, 10, 21, 10, tzinfo=SEOUL_TZ)
    db.add(
        StudyActivitySession(
            enrollment_id=enrollment.id,
            activity_type=activity_type,
            video_id=video.id if activity_type == "VIDEO" else None,
            quiz_attempt_id=attempt.attempt_id if activity_type == "QUIZ" else None,
            session_key="old-session",
            study_date=started.date(),
            started_at=started,
            ended_at=started + timedelta(seconds=60),
            active_seconds=60,
        )
    )
    db.commit()
    body = answer_body(attempt, activity={"sessionKey": "old-session"})
    if activity_type == "QUIZ":
        response = post_answer(client, learner, unit.id, body)
    else:
        response = client.put(
            f"/api/v1/videos/{video.id}/progress",
            headers=auth_header(learner.id),
            json={
                "lastPositionSeconds": 60,
                "watchedRange": [0, 60],
                "activitySession": body["activitySession"],
            },
        )
    assert response.status_code == 400
    assert db.scalar(select(func.count()).select_from(AttendanceDay)) == 0
    assert db.scalar(select(func.count()).select_from(QuestionAnswer)) == 0
    session = db.scalar(select(StudyActivitySession))
    assert session.active_seconds == 60
    assert session.study_date == started.date()


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
        "yesterday",
        "six_days_ago",
        "old_start_today_end",
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
    elif invalid_case == "future_time":
        activity.update(startedAt="2026-10-27T13:00:00+09:00", endedAt="2026-10-27T13:01:00+09:00")
    elif invalid_case == "yesterday":
        activity.update(startedAt="2026-10-26T10:00:00+09:00", endedAt="2026-10-26T10:01:00+09:00")
    elif invalid_case == "six_days_ago":
        activity.update(startedAt="2026-10-21T10:00:00+09:00", endedAt="2026-10-21T10:01:00+09:00")
    else:
        activity["startedAt"] = "2026-10-21T10:00:00+09:00"
    response = post_answer(client, learner, unit.id, body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    if invalid_case in ("choice_zero", "choice_five"):
        assert response.json()["error"]["message"] == "선택한 보기 번호는 1~4 사이여야 합니다."
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
    freeze_clock(monkeypatch, "2026-10-26T23:59:00+09:00")
    learner, _, _, _, unit, _, _, attempt = prepare_attempt(db, get_user, question_count=3)
    activity = {
        "sessionKey": "midnight-quiz",
        "startedAt": "2026-10-26T23:58:00+09:00",
        "endedAt": "2026-10-26T23:59:00+09:00",
        "activeSeconds": 30,
    }
    response = post_answer(client, learner, unit.id, answer_body(attempt, activity=activity))
    assert response.status_code == 200
    freeze_clock(monkeypatch)
    activity.update(endedAt="2026-10-27T00:01:00+09:00", activeSeconds=90)
    for index in (1, 2):
        body = answer_body(attempt, index, activity=activity)
        assert post_answer(client, learner, unit.id, body).status_code == 200
    assert post_answer(client, learner, unit.id, body).status_code == 409
    previous = db.get(AttendanceDay, (learner.id, date(2026, 10, 26)), populate_existing=True)
    current = db.get(AttendanceDay, (learner.id, date(2026, 10, 27)), populate_existing=True)
    assert (previous.active_seconds, current.active_seconds) == (60, 30)
    assert db.scalar(select(func.count()).select_from(StudyActivitySession)) == 1


def test_completed_course_retake_keeps_completion_and_updates_wrong_notes(
    client, db, get_user, monkeypatch
):
    freeze_clock(monkeypatch)
    learner, course, section, video, unit, final, enrollment, attempt = prepare_attempt(
        db, get_user, question_count=1
    )
    headers = auth_header(learner.id)
    assert post_answer(client, learner, unit.id, answer_body(attempt)).status_code == 200
    final_attempt = learner_quiz.get_course_attempt(db, learner.id, course.id, "FINAL", None)
    assert post_answer(client, learner, final.id, answer_body(final_attempt)).status_code == 200
    db.refresh(enrollment)
    first_at, current_at = enrollment.first_completed_at, enrollment.current_completed_at

    retake = learner_quiz.get_course_attempt(db, learner.id, course.id, "FINAL", None)
    assert retake.attempt_no == 2
    wrong = post_answer(client, learner, final.id, answer_body(retake, choice=2))
    assert wrong.status_code == 200
    assert wrong.json()["data"]["isCourseCompleted"]
    assert wrong.json()["data"]["questionRecordStatus"] == "REVIEW_NEEDED"
    completion = client.get(f"/api/v1/courses/{course.id}/completion", headers=headers)
    assert completion.json()["data"]["isCompleted"]
    assert completion.json()["data"]["unresolvedWrongCount"] == 1
    notes = client.get("/api/v1/me/quiz-notes?status=REVIEW_NEEDED", headers=headers)
    assert notes.json()["data"]["counts"]["reviewNeeded"] == 1
    listed = client.get("/api/v1/me/enrollments?status=COMPLETED", headers=headers)
    assert listed.json()["data"]["enrollments"][0]["courseId"] == course.id
    assert admin_users.build_learning_summary(db, learner).completed_count == 1

    response = client.put(
        f"/api/v1/videos/{video.id}/progress",
        headers=headers,
        json={
            "lastPositionSeconds": 60,
            "watchedRange": [0, 60],
            "activitySession": answer_body(retake, activity={"sessionKey": "replay"})[
                "activitySession"
            ],
        },
    )
    assert response.status_code == 200
    course_service.recalculate_enrollments_progress(course, db)
    db.commit()
    db.refresh(enrollment)
    assert enrollment.status == "COMPLETED"
    assert enrollment.first_completed_at == first_at
    assert enrollment.current_completed_at == current_at
    stat = db.get(CourseDailyStat, (course.id, date(2026, 10, 27)), populate_existing=True)
    assert (stat.first_answer_cnt, stat.first_correct_cnt, stat.first_submitted_cnt) == (2, 2, 2)

    # 새 학습 영상으로 재학습이 필요해지는 경우는 수료 후 복습과 구분한다.
    added = make_video(db, course, section=section, sort_order=2)
    db.commit()
    db.expire(course, ["videos"])
    course_service.recalculate_enrollments_progress(course, db)
    db.commit()
    db.refresh(enrollment)
    assert enrollment.status == "IN_PROGRESS"
    assert enrollment.current_completed_at is None
    assert enrollment.first_completed_at == first_at
    complete_video(db, enrollment, added)
    assert not learner_quiz.get_completion(db, learner.id, course.id).is_completed
    review = learner_quiz.get_course_attempt(db, learner.id, course.id, "WRONG_REVIEW", None)
    corrected = post_answer(client, learner, 0, answer_body(review))
    assert corrected.json()["data"]["isCourseCompleted"]
    assert corrected.json()["data"]["questionRecordStatus"] == "CORRECT"


def test_quiz_validation_messages_and_openapi_constraints(client, db, get_user):
    learner, _, _, _, unit, _, _, attempt = prepare_attempt(db, get_user)
    body = answer_body(attempt)
    for choice in (0, 5, "5"):
        body["selectedChoiceNo"] = choice
        response = post_answer(client, learner, unit.id, body)
        assert response.status_code == 400
        assert response.json()["error"]["message"] == "선택한 보기 번호는 1~4 사이여야 합니다."
    response = client.get("/api/v1/me/quiz-notes?status=INVALID", headers=auth_header(learner.id))
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "유효하지 않은 복습 상태입니다."
    schema = client.get("/openapi.json").json()
    choice_schema = schema["components"]["schemas"]["QuizAnswerRequest"]["properties"][
        "selectedChoiceNo"
    ]
    assert (choice_schema["minimum"], choice_schema["maximum"]) == (1, 4)
    params = schema["paths"]["/api/v1/me/quiz-notes"]["get"]["parameters"]
    status_schema = next(param["schema"] for param in params if param["name"] == "status")
    assert status_schema["enum"] == ["ALL", "CORRECT", "REVIEW_NEEDED"]
