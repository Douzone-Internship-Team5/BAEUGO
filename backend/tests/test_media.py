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

        # [M-1 권한 검증] LESSON 영상은 비로그인/미수강 학습자 접근 시 403 FORBIDDEN, 담당 강사/수강생은 200 OK
        from app.models.learning import Enrollment

        lesson_vid = Video(
            course_id=course.id,
            video_type="LESSON",
            title="유료 학습 영상",
            duration_seconds=120,
            video_storage_key="test_stream.mp4",
            is_active=True,
        )
        db_session.add(lesson_vid)
        db_session.commit()

        # 비로그인 요청 -> 403 FORBIDDEN
        res_lesson_unauth = client.get(f"/api/v1/videos/{lesson_vid.id}/stream")
        assert res_lesson_unauth.status_code == 403
        assert res_lesson_unauth.json()["error"]["code"] == "FORBIDDEN"

        # 미수강 학습자(id=2) 요청 -> 403 FORBIDDEN
        res_lesson_unenrolled = client.get(
            f"/api/v1/videos/{lesson_vid.id}/stream",
            headers={"Authorization": "Bearer learner"},
        )
        assert res_lesson_unenrolled.status_code == 403
        assert res_lesson_unenrolled.json()["error"]["code"] == "FORBIDDEN"

        # 타 강사(id=99) 요청 -> 403 FORBIDDEN
        from app.models.user import User

        db_session.add(
            User(
                id=99,
                email="inst99@baeugo.test",
                password_hash="$2b$12$mockhash",
                name="타강사",
                account_type="MEMBER",
                active_mode="INSTRUCTOR",
                is_active=True,
            )
        )
        db_session.commit()
        res_other_inst = client.get(
            f"/api/v1/videos/{lesson_vid.id}/stream",
            headers={"Authorization": "Bearer user_99"},
        )
        assert res_other_inst.status_code == 403

        # 운영자(id=1) 요청 -> 200 OK
        res_operator = client.get(
            f"/api/v1/videos/{lesson_vid.id}/stream",
            headers={"Authorization": "Bearer operator"},
        )
        assert res_operator.status_code == 200

        # 담당 강사(id=6) 요청 -> 200 OK
        res_lesson_inst = client.get(
            f"/api/v1/videos/{lesson_vid.id}/stream",
            headers={"Authorization": "Bearer instructor"},
        )
        assert res_lesson_inst.status_code == 200

        # 수강 등록 후 학습자(id=2) 요청 -> 200 OK
        db_session.add(Enrollment(course_id=course.id, learner_id=2, status="IN_PROGRESS"))
        db_session.commit()
        res_lesson_enrolled = client.get(
            f"/api/v1/videos/{lesson_vid.id}/stream",
            headers={"Authorization": "Bearer learner"},
        )
        assert res_lesson_enrolled.status_code == 200
    finally:
        if vid_file_path.exists():
            vid_file_path.unlink()
