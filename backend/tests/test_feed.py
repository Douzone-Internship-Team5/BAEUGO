import base64
import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.course import Course, Video
from tests.conftest import auth_header


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
        published_at=datetime.now(UTC),
    )
    db_session.add(course)
    db_session.flush()

    ot_vid = Video(
        course_id=course.id,
        video_type="OT",
        title="피드 무료 OT",
        duration_seconds=45,
        video_storage_key="test/ot_feed.mp4",
        thumbnail_storage_key="courses/1/ot_thumb.jpg",
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
        headers=auth_header(2),
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert "items" in data
    assert any(item["videoId"] == ot_vid.id for item in data["items"])
    assert "hasNext" in data

    target_item = next(item for item in data["items"] if item["videoId"] == ot_vid.id)
    assert target_item["videoUrl"] == f"/api/v1/videos/{ot_vid.id}/stream"
    assert target_item["thumbnailUrl"] == "/api/v1/media/courses/1/ot_thumb.jpg"

    # [M 엔드포인트 제거 검증] B 피드 응답 URL로 동일 백엔드에 직접 미디어 요청 시 404 NOT_FOUND
    res_direct_stream = client.get(target_item["videoUrl"])
    assert res_direct_stream.status_code == 404
    assert res_direct_stream.json()["error"]["code"] == "NOT_FOUND"

    res_direct_media = client.get(target_item["thumbnailUrl"])
    assert res_direct_media.status_code == 404
    assert res_direct_media.json()["error"]["code"] == "NOT_FOUND"

    # [성공 2] 다음 페이지 커서 전달 시 정상 동작 확인
    next_cursor = data.get("nextCursor")
    if next_cursor:
        res_page2 = client.get(
            f"/api/v1/feed/ot?size=5&cursor={next_cursor}",
            headers=auth_header(2),
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
        headers=auth_header(2),
    )
    assert res_bad_size.status_code == 400
    assert res_bad_size.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패 4] size > 100 경계값 유효성 검사 실패 -> 400 VALIDATION_ERROR
    res_bad_size_101 = client.get(
        "/api/v1/feed/ot?size=101",
        headers=auth_header(2),
    )
    assert res_bad_size_101.status_code == 400
    assert res_bad_size_101.json()["error"]["code"] == "VALIDATION_ERROR"

    # [성공 3] 잘못된 포맷/변조된 커서 전달 시 첫 번째 영상부터 안전하게 폴백
    res_bad_cursor = client.get(
        "/api/v1/feed/ot?size=5&cursor=invalid_base64_string!",
        headers=auth_header(2),
    )
    assert res_bad_cursor.status_code == 200
    assert len(res_bad_cursor.json()["data"]["items"]) > 0

    # [성공 4] {"videoId": ...} 커서 포맷 및 마지막 OT 도달 시 첫 OT로 순환 재생 검증
    ot_vid2 = Video(
        course_id=course.id,
        video_type="OT",
        title="피드 무료 OT 2",
        duration_seconds=30,
        video_storage_key="test/ot_feed2.mp4",
        stt_status="COMPLETED",
        view_count=10,
        like_count=1,
        is_active=True,
    )
    db_session.add(ot_vid2)
    db_session.commit()

    res_p1 = client.get(
        "/api/v1/feed/ot?size=1",
        headers=auth_header(2),
    )
    assert res_p1.status_code == 200
    p1_data = res_p1.json()["data"]
    first_id = p1_data["items"][0]["videoId"]
    assert p1_data["hasNext"] is True

    # videoId 전용 커서로 다음 영상 조회
    vid_cursor = base64.b64encode(json.dumps({"videoId": first_id}).encode()).decode()
    res_p2 = client.get(
        f"/api/v1/feed/ot?size=1&cursor={vid_cursor}",
        headers=auth_header(2),
    )
    assert res_p2.status_code == 200
    p2_data = res_p2.json()["data"]
    second_id = p2_data["items"][0]["videoId"]
    assert second_id != first_id

    # 마지막 OT 영상 이후 커서로 조회 시 첫 번째 OT로 순환(loop) 확인
    res_loop = client.get(
        f"/api/v1/feed/ot?size=1&cursor={p2_data['nextCursor']}",
        headers=auth_header(2),
    )
    assert res_loop.status_code == 200
    assert res_loop.json()["data"]["items"][0]["videoId"] == first_id


def test_feed_ot_empty(client: TestClient, db_session: Session):
    """활성 무료 OT 영상이 하나도 없을 때 빈 목록 반환 검증."""
    db_session.execute(delete(Video))
    db_session.commit()

    res = client.get("/api/v1/feed/ot?size=10", headers=auth_header(2))
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["items"] == []
    assert data["nextCursor"] is None
    assert data["hasNext"] is False
