from datetime import UTC, date, datetime

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
        published_at=datetime.now(UTC),
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
    assert day_data["summary"]["quizCorrectRate"] == 80.0  # 40/50 * 100
    assert len(day_data["dailyStats"]) == 1

    # [성공 2] groupBy=course 조회
    res_course = client.get(
        f"/api/v1/instructor/stats?groupBy=course&from={today.isoformat()}&to={today.isoformat()}"
    )
    assert res_course.status_code == 200
    course_data = res_course.json()["data"]
    assert course_data["courseStats"] is not None
    assert any(cs["courseId"] == course.id for cs in course_data["courseStats"])

    # [성공 2-1] from, to 날짜 파라미터 생략 시 기본값(당월 1일 ~ 오늘) 자동 적용 검증
    res_default_dates = client.get(f"/api/v1/instructor/stats?courseId={course.id}")
    assert res_default_dates.status_code == 200
    assert res_default_dates.json()["data"]["period"]["from"] is not None
    assert res_default_dates.json()["data"]["period"]["to"] is not None

    # [성공 2-2] 해당 기간 통계 데이터가 전혀 없는 경우 0 및 빈 배열 반환
    res_empty_period = client.get(
        f"/api/v1/instructor/stats?courseId={course.id}&from=2020-01-01&to=2020-01-31"
    )
    assert res_empty_period.status_code == 200
    empty_data = res_empty_period.json()["data"]
    assert empty_data["summary"]["lessonViews"] == 0
    assert empty_data["summary"]["newStudents"] == 0
    assert empty_data["summary"]["expectedRevenue"] == 0
    assert empty_data["dailyStats"] == []

    # [실패 1] 잘못된 groupBy 값 전달 -> 400 VALIDATION_ERROR
    res_bad_group = client.get("/api/v1/instructor/stats?groupBy=month")
    assert res_bad_group.status_code == 400
    assert res_bad_group.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패 2] 타인/미존재 강좌 ID 통계 요청 -> 404 NOT_FOUND
    res_stats_404 = client.get("/api/v1/instructor/stats?courseId=999999")
    assert res_stats_404.status_code == 404

    # [실패 3] from > to 역전 날짜 입력 시 -> 400 VALIDATION_ERROR
    res_rev_date = client.get("/api/v1/instructor/stats?from=2026-05-10&to=2026-05-01")
    assert res_rev_date.status_code == 400
    assert res_rev_date.json()["error"]["code"] == "VALIDATION_ERROR"

    # [성공 3] 31일 날짜(2026-03-31) 기준 전월 동기(2026-02-28) 대비 증감 및 무실적 강좌 포함 검증
    empty_course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="무실적 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="PUBLISHED",
        published_at=datetime(2026, 3, 15, tzinfo=UTC),
    )
    db_session.add(empty_course)
    db_session.flush()

    db_session.add_all(
        [
            CourseDailyStat(
                course_id=course.id,
                start_date=date(2026, 2, 20),
                lesson_views=50,
                new_students=5,
                expected_revenue=50000,
                available_quiz_cnt=20,
                first_submitted_cnt=10,  # 50%
                first_answer_cnt=50,
                first_correct_cnt=30,  # 60%
            ),
            CourseDailyStat(
                course_id=course.id,
                start_date=date(2026, 3, 25),
                lesson_views=100,
                new_students=10,
                expected_revenue=100000,
                available_quiz_cnt=20,
                first_submitted_cnt=16,  # 80% (+30.0%p)
                first_answer_cnt=50,
                first_correct_cnt=40,  # 80% (+20.0%p)
            ),
        ]
    )
    db_session.commit()

    res_march = client.get("/api/v1/instructor/stats?from=2026-03-01&to=2026-03-31")
    assert res_march.status_code == 200
    m_data = res_march.json()["data"]
    assert m_data["period"] == {"from": "2026-03-01", "to": "2026-03-31", "groupBy": "day"}
    comp = m_data["summary"]["comparison"]
    assert comp["publishedCourseDiff"] == 2
    assert comp["lessonViewsDiffPercent"] == 100.0
    assert comp["quizResponseRateDiffPoint"] == 30.0
    assert comp["quizCorrectRateDiffPoint"] == 20.0
    assert m_data["dailyStats"] is not None
    assert m_data["courseStats"] is not None
    assert any(cs["courseId"] == empty_course.id for cs in m_data["courseStats"])
