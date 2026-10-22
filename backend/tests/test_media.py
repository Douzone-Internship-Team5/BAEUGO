from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course, Video
from app.services.storage import get_media_dir


def test_media_file_serving(client: TestClient):
    media_dir = get_media_dir()
    test_file_path = media_dir / "test_thumb.png"
    test_file_path.write_bytes(b"dummy image bytes")

    try:
        # [성공] 이미지 서빙 정상 동작
        res = client.get("/api/v1/media/test_thumb.png")
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/png"
        assert res.content == b"dummy image bytes"

        # [실패 1] 디렉터리 경로 탐색 (..) 차단 -> 404 NOT_FOUND
        res_traversal = client.get("/api/v1/media/..%2Fsecret.png")
        assert res_traversal.status_code == 404
        assert res_traversal.json()["error"]["code"] == "NOT_FOUND"

        # [실패 2] 존재하지 않는 미디어 파일 요청 -> 404 NOT_FOUND
        res_no_file = client.get("/api/v1/media/non_existent_file.png")
        assert res_no_file.status_code == 404
        assert res_no_file.json()["error"]["code"] == "NOT_FOUND"
    finally:
        if test_file_path.exists():
            test_file_path.unlink()


def test_video_streaming(client: TestClient, db_session: Session):
    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="스트리밍 테스트 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="DRAFT",
    )
    db_session.add(course)
    db_session.flush()

    media_dir = get_media_dir()
    vid_file_path = media_dir / "test_stream.mp4"
    vid_file_path.write_bytes(b"dummy mp4 video bytes")

    active_vid = Video(
        course_id=course.id,
        video_type="OT",
        title="활성 영상",
        duration_seconds=60,
        video_storage_key="test_stream.mp4",
        is_active=True,
    )
    inactive_vid = Video(
        course_id=course.id,
        video_type="OT",
        title="비활성 영상",
        duration_seconds=60,
        video_storage_key="test_stream.mp4",
        is_active=False,
    )
    no_file_vid = Video(
        course_id=course.id,
        video_type="OT",
        title="파일없는 영상",
        duration_seconds=60,
        video_storage_key="ghost_file.mp4",
        is_active=True,
    )
    db_session.add_all([active_vid, inactive_vid, no_file_vid])
    db_session.commit()

    try:
        # [성공] 활성 비디오 스트리밍 정상 동작
        res = client.get(f"/api/v1/videos/{active_vid.id}/stream")
        assert res.status_code == 200
        assert res.headers["content-type"] == "video/mp4"
        assert res.content == b"dummy mp4 video bytes"

        # [실패 1] 존재하지 않는 videoId -> 404 NOT_FOUND
        res_no_id = client.get("/api/v1/videos/999999/stream")
        assert res_no_id.status_code == 404
        assert res_no_id.json()["error"]["code"] == "NOT_FOUND"

        # [실패 2] 비활성화(is_active=False)된 영상 스트리밍 -> 404 NOT_FOUND
        res_inactive = client.get(f"/api/v1/videos/{inactive_vid.id}/stream")
        assert res_inactive.status_code == 404
        assert res_inactive.json()["error"]["code"] == "NOT_FOUND"

        # [실패 3] 활성 영상이지만 디스크에 실제 파일 부재 -> 404 NOT_FOUND
        res_no_file = client.get(f"/api/v1/videos/{no_file_vid.id}/stream")
        assert res_no_file.status_code == 404
        assert res_no_file.json()["error"]["code"] == "NOT_FOUND"
    finally:
        if vid_file_path.exists():
            vid_file_path.unlink()
