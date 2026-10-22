from datetime import date, datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course, CourseDailyStat


def test_b9_instructor_stats_all_cases(client: TestClient, db_session: Session):
    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="통계 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=10000,
        status="PUBLISHED",
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(course)
    db_session.flush()

    today = date.today()
    stat1 = CourseDailyStat(
        course_id=course.id,
        start_date=today,
        lesson_views=100,
        new_students=10,
        expected_revenue=100000,
        available_quiz_cnt=20,
        first_submitted_cnt=18,
        first_answer_cnt=50,
        first_correct_cnt=40,
    )
    db_session.add(stat1)
    db_session.commit()

    # [성공 1] groupBy=day 조회
    res_day = client.get(
        f"/api/v1/instructor/stats?courseId={course.id}&groupBy=day&from={today.isoformat()}&to={today.isoformat()}"
    )
    assert res_day.status_code == 200
    day_data = res_day.json()["data"]
    assert day_data["summary"]["lessonViews"] == 100
    assert day_data["summary"]["newStudents"] == 10
    assert day_data["summary"]["quizResponseRate"] == 90.0  # 18/20 * 100
    assert day_data["summary"]["quizCorrectRate"] == 80.0   # 40/50 * 100
    assert len(day_data["dailyStats"]) == 1

    # [성공 2] groupBy=course 조회
    res_course = client.get(
        f"/api/v1/instructor/stats?groupBy=course&from={today.isoformat()}&to={today.isoformat()}"
    )
    assert res_course.status_code == 200
    course_data = res_course.json()["data"]
    assert course_data["courseStats"] is not None
    assert any(cs["courseId"] == course.id for cs in course_data["courseStats"])

    # [실패 1] 잘못된 groupBy 값 전달 -> 400 VALIDATION_ERROR
    res_bad_group = client.get("/api/v1/instructor/stats?groupBy=month")
    assert res_bad_group.status_code == 400
    assert res_bad_group.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패 2] 타인/미존재 강좌 ID 통계 요청 -> 404 NOT_FOUND
    res_stats_404 = client.get("/api/v1/instructor/stats?courseId=999999")
    assert res_stats_404.status_code == 404
