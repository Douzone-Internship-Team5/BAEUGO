from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course, Video


def test_feed_ot_all_cases(client: TestClient, db_session: Session):
    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="피드용 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="PUBLISHED",
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(course)
    db_session.flush()

    ot_vid = Video(
        course_id=course.id,
        video_type="OT",
        title="피드 무료 OT",
        duration_seconds=45,
        video_storage_key="test/ot_feed.mp4",
        stt_status="COMPLETED",
        view_count=120,
        like_count=15,
        is_active=True,
    )
    db_session.add(ot_vid)
    db_session.commit()

    # [성공 1] 정상 로그인 헤더 전달 시 OT 목록 및 커서 페이징 반환
    res = client.get(
        "/api/v1/feed/ot?size=5",
        headers={"Authorization": "Bearer valid_token"},
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert "items" in data
    assert any(item["videoId"] == ot_vid.id for item in data["items"])
    assert "hasNext" in data

    # [성공 2] 다음 페이지 커서 전달 시 정상 동작 확인
    next_cursor = data.get("nextCursor")
    if next_cursor:
        res_page2 = client.get(
            f"/api/v1/feed/ot?size=5&cursor={next_cursor}",
            headers={"Authorization": "Bearer valid_token"},
        )
        assert res_page2.status_code == 200

    # [실패 1] Authorization 헤더 누락 시 401 UNAUTHORIZED
    res_no_auth = client.get("/api/v1/feed/ot")
    assert res_no_auth.status_code == 401
    assert res_no_auth.json()["error"]["code"] == "UNAUTHORIZED"

    # [실패 2] 유효하지 않은 토큰(invalid) -> 401 UNAUTHORIZED
    res_inv_auth = client.get(
        "/api/v1/feed/ot",
        headers={"Authorization": "Bearer invalid"},
    )
    assert res_inv_auth.status_code == 401
    assert res_inv_auth.json()["error"]["code"] == "UNAUTHORIZED"

    # [실패 3] size < 1 경계값 유효성 검사 실패 -> 400 VALIDATION_ERROR
    res_bad_size = client.get(
        "/api/v1/feed/ot?size=0",
        headers={"Authorization": "Bearer valid_token"},
    )
    assert res_bad_size.status_code == 400
    assert res_bad_size.json()["error"]["code"] == "VALIDATION_ERROR"
