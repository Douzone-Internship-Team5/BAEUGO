from datetime import date, datetime, time, timedelta

from app.core.timezone import SEOUL_TZ
from app.models import AttendanceDay, LearningGoal, LearningTimeSlot, QuizAttempt, VideoProgress
from app.services import goals
from tests.conftest import auth_header
from tests.factories import enroll, make_course, make_quiz, make_section, make_video


def freeze_today(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 27, 8, 30, tzinfo=SEOUL_TZ)

    monkeypatch.setattr(goals, "datetime", FixedDatetime)
    return date(2026, 10, 27)


def test_today_goal_and_slot(client, db, get_user, monkeypatch):
    today = freeze_today(monkeypatch)
    learner = get_user("learner2@baeugo.test")
    goal = db.get(LearningGoal, learner.id)
    goal.daily_target_minutes = 20
    goal.time_slots.clear()
    db.flush()
    goal.time_slots.append(
        LearningTimeSlot(
            start_time=time(8),
            end_time=time(9),
            sort_order=1,
        )
    )
    db.add(
        AttendanceDay(
            learner_id=learner.id,
            study_date=today,
            target_minutes_snapshot=20,
            active_seconds=620,
            is_attended=False,
        )
    )
    db.commit()
    result = client.get("/api/v1/me/today", headers=auth_header(learner.id))
    assert result.status_code == 200
    data = result.json()["data"]
    assert data["studyDate"] == "2026-10-27"
    assert data["timeZone"] == "Asia/Seoul"
    assert data["goal"]["remainingSeconds"] == 580
    assert data["goal"]["achievementPercent"] == 51.6
    assert data["currentTimeSlot"]["isInPreferredSlot"]
    assert data["recommendedShortforms"] == []


def test_goal_missing_auth_and_validation(client, db, get_user):
    learner = get_user("learner2@baeugo.test")
    headers = auth_header(learner.id)
    assert client.get("/api/v1/me/today").status_code == 401
    instructor = get_user("instructor1@baeugo.test")
    assert client.get("/api/v1/me/today", headers=auth_header(instructor.id)).status_code == 403
    for params in ({"month": 0}, {"month": 13}, {"year": 0}, {"year": 10000}):
        response = client.get("/api/v1/me/attendance", params=params, headers=headers)
        assert response.status_code == 400
    db.delete(db.get(LearningGoal, learner.id))
    db.commit()
    assert client.get("/api/v1/me/today", headers=headers).status_code == 409
    assert client.get("/api/v1/me/attendance", headers=headers).status_code == 409


def test_calendar_snapshots_and_streaks(client, db, get_user, monkeypatch):
    today = freeze_today(monkeypatch)
    learner = get_user("learner2@baeugo.test")
    for offset, target, seconds in [(0, 20, 100), (1, 10, 600), (2, 30, 1800), (4, 20, 1200)]:
        db.add(
            AttendanceDay(
                learner_id=learner.id,
                study_date=today - timedelta(days=offset),
                target_minutes_snapshot=target,
                active_seconds=seconds,
                is_attended=seconds >= target * 60,
            )
        )
    db.add(
        AttendanceDay(
            learner_id=learner.id,
            study_date=today + timedelta(days=1),
            target_minutes_snapshot=20,
            active_seconds=1200,
            is_attended=True,
        )
    )
    db.commit()
    result = client.get("/api/v1/me/attendance", headers=auth_header(learner.id))
    assert result.status_code == 200
    data = result.json()["data"]
    assert data["streak"] == {
        "currentStreakDays": 2,
        "bestStreakDays": 2,
        "monthlyAttendedDays": 3,
    }
    assert len(data["calendarDays"]) == 4
    assert data["calendarDays"][-2]["targetMinutesSnapshot"] == 10
    assert data["calendarDays"][-1]["targetMinutesSnapshot"] == 20
    future = client.get("/api/v1/me/attendance?year=9999&month=12", headers=auth_header(learner.id))
    assert future.status_code == 200
    assert future.json()["data"]["calendarDays"] == []


def test_continue_recommendation_stops_at_unit_quiz(db, get_user, monkeypatch):
    freeze_today(monkeypatch)
    learner = get_user("learner2@baeugo.test")
    course = make_course(db, get_user("instructor1@baeugo.test"))
    first_section = make_section(db, course)
    second_section = make_section(db, course, sort_order=2)
    first = make_video(db, course, section=first_section)
    second = make_video(db, course, section=second_section)
    quiz = make_quiz(db, course, section=first_section)
    enrollment = enroll(db, learner, course)
    db.add(
        VideoProgress(
            enrollment_id=enrollment.id,
            video_id=first.id,
            is_completed=True,
            watched_seconds=360,
            last_position_seconds=360,
        )
    )
    db.commit()
    assert goals.get_today(db, learner.id).recommended_shortforms == []
    db.add(
        QuizAttempt(
            enrollment_id=enrollment.id,
            quiz_id=quiz.id,
            attempt_type="UNIT",
            status="SUBMITTED",
        )
    )
    db.commit()
    result = goals.get_today(db, learner.id)
    assert result.recommended_shortforms[0].video_id == second.id
    assert result.recommended_shortforms[0].recommendation_type == "CONTINUE_LESSON"


def test_ot_recommendation_ranking_and_remaining_time(db, get_user, monkeypatch):
    today = freeze_today(monkeypatch)
    learner = get_user("learner2@baeugo.test")
    instructor = get_user("instructor1@baeugo.test")
    course = make_course(db, instructor)
    unpopular = make_video(db, course, video_type="OT", duration_seconds=60)
    popular = make_video(db, course, video_type="OT", duration_seconds=60, sort_order=2)
    popular.view_count = 20
    too_long = make_video(db, course, video_type="OT", duration_seconds=1800, sort_order=3)
    too_long.view_count = 999
    hidden = make_course(db, instructor, status="DRAFT")
    make_video(db, hidden, video_type="OT", duration_seconds=10)
    db.commit()
    result = goals.get_today(db, learner.id)
    assert [item.video_id for item in result.recommended_shortforms] == [popular.id, unpopular.id]
    goal = db.get(LearningGoal, learner.id)
    db.add(
        AttendanceDay(
            learner_id=learner.id,
            study_date=today,
            target_minutes_snapshot=goal.daily_target_minutes,
            active_seconds=goal.daily_target_minutes * 60,
            is_attended=True,
        )
    )
    db.commit()
    result = goals.get_today(db, learner.id)
    assert result.goal.remaining_seconds == 0
    assert result.recommended_shortforms == []
