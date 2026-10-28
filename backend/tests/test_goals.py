from datetime import date, datetime, time, timedelta

from app.core.timezone import SEOUL_TZ
from app.models import (
    AttendanceDay,
    LearningGoal,
    LearningTimeSlot,
    QuizAttempt,
    VideoProgress,
    VideoView,
)
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


def test_ot_recommendations_limit_and_replace_viewed_items(client, db, get_user, monkeypatch):
    today = freeze_today(monkeypatch)
    learner = get_user("learner2@baeugo.test")
    instructor = get_user("instructor1@baeugo.test")
    course = make_course(db, instructor)
    section = make_section(db, course)
    lesson = make_video(db, course, section=section, duration_seconds=60)
    enroll(db, learner, course)
    ots = []
    for index in range(12):
        other_course = make_course(db, instructor)
        ot = make_video(db, other_course, video_type="OT", duration_seconds=60)
        ot.view_count = 100 - index * 5
        ots.append(ot)
    # 타인의 조회 기록으로 현재 사용자의 추천을 제외해서는 안 된다.
    db.add(VideoView(user_id=instructor.id, video_id=ots[0].id, view_date=today))
    # 과거에 본 영상도 미시청 추천에서는 제외한다.
    db.add(VideoView(user_id=learner.id, video_id=ots[1].id, view_date=today - timedelta(days=5)))
    db.commit()

    def recommended_ids():
        response = client.get("/api/v1/me/today", headers=auth_header(learner.id))
        assert response.status_code == 200
        items = response.json()["data"]["recommendedShortforms"]
        assert [item["videoId"] for item in items if item["videoType"] == "LESSON"] == [lesson.id]
        return [item["videoId"] for item in items if item["videoType"] == "OT"]

    expected = [ot.id for ot in ots if ot.id != ots[1].id]
    assert recommended_ids() == expected[:10]
    viewed = client.post(
        f"/api/v1/videos/{ots[0].id}/views",
        json={"playedSeconds": 3},
        headers=auth_header(learner.id),
    )
    assert viewed.status_code == 200
    assert recommended_ids() == expected[1:11]
    for ot in ots[2:]:
        db.add(VideoView(user_id=learner.id, video_id=ot.id, view_date=today))
    db.commit()
    assert recommended_ids() == []


def test_continue_completion_days_use_remaining_study_but_ot_uses_full_course(
    db, get_user, monkeypatch
):
    freeze_today(monkeypatch)
    learner = get_user("learner2@baeugo.test")
    db.get(LearningGoal, learner.id).daily_target_minutes = 20
    course = make_course(db, get_user("instructor1@baeugo.test"))
    section = make_section(db, course)
    completed = make_video(db, course, section=section, duration_seconds=3000)
    next_video = make_video(db, course, section=section, duration_seconds=3000, sort_order=2)
    ot = make_video(db, course, video_type="OT", duration_seconds=60)
    enrollment = enroll(db, learner, course)
    db.add_all(
        [
            VideoProgress(
                enrollment_id=enrollment.id,
                video_id=completed.id,
                is_completed=True,
                watched_seconds=2700,
            ),
            VideoProgress(
                enrollment_id=enrollment.id,
                video_id=next_video.id,
                is_completed=False,
                watched_seconds=1800,
                last_position_seconds=2900,
            ),
        ]
    )
    db.commit()
    items = {item.video_id: item for item in goals.get_today(db, learner.id).recommended_shortforms}
    # 총 100분 중 완료 50분, 미완료 영상의 실제 시청 30분을 제외하면 20분이다.
    assert items[next_video.id].estimated_completion_days == 1
    assert items[ot.id].estimated_completion_days == 5
    progress = db.get(VideoProgress, (enrollment.id, next_video.id))
    progress.watched_seconds = 1799
    db.commit()
    items = {item.video_id: item for item in goals.get_today(db, learner.id).recommended_shortforms}
    assert items[next_video.id].estimated_completion_days == 2
