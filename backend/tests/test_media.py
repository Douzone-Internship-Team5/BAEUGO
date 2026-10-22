from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course, Video
from app.models.learning import Enrollment
from app.models.user import User
from app.services.storage import get_media_dir
from tests.conftest import auth_header


def test_media_file_serving(client: TestClient):
    media_dir = get_media_dir()
    test_file_png = media_dir / "test_thumb.png"
    test_file_webp = media_dir / "test_thumb.webp"
    test_file_jpg = media_dir / "test_thumb.jpg"

    test_file_png.write_bytes(b"dummy png bytes")
    test_file_webp.write_bytes(b"dummy webp bytes")
    test_file_jpg.write_bytes(b"dummy jpg bytes")

    try:
        # [성공 1] PNG 이미지 서빙 정상 동작 (image/png)
        res_png = client.get("/api/v1/media/test_thumb.png")
        assert res_png.status_code == 200
        assert res_png.headers["content-type"] == "image/png"
        assert res_png.content == b"dummy png bytes"

        # [성공 2] WEBP 이미지 서빙 정상 동작 (image/webp)
        res_webp = client.get("/api/v1/media/test_thumb.webp")
        assert res_webp.status_code == 200
        assert res_webp.headers["content-type"] == "image/webp"

        # [성공 3] JPG 이미지 서빙 정상 동작 (image/jpeg)
        res_jpg = client.get("/api/v1/media/test_thumb.jpg")
        assert res_jpg.status_code == 200
        assert res_jpg.headers["content-type"] == "image/jpeg"

        # [실패 1] 디렉터리 경로 탐색 (..) 차단 -> 404 NOT_FOUND
        res_traversal = client.get("/api/v1/media/..%2Fsecret.png")
        assert res_traversal.status_code == 404
        assert res_traversal.json()["error"]["code"] == "NOT_FOUND"

        # [실패 2] 존재하지 않는 미디어 파일 요청 -> 404 NOT_FOUND
        res_no_file = client.get("/api/v1/media/non_existent_file.png")
        assert res_no_file.status_code == 404
        assert res_no_file.json()["error"]["code"] == "NOT_FOUND"
    finally:
        for f in (test_file_png, test_file_webp, test_file_jpg):
            if f.exists():
                f.unlink()


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
        title="활성 무료 OT 영상",
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
        # [성공 1] 비로그인 상태에서도 무료 OT 영상 스트리밍은 누구나 시청 가능 (200 OK)
        res_ot_unauth = client.get(f"/api/v1/videos/{active_vid.id}/stream")
        assert res_ot_unauth.status_code == 200
        assert res_ot_unauth.headers["content-type"] == "video/mp4"
        assert res_ot_unauth.content == b"dummy mp4 video bytes"

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

        # [M-1 권한 검증] LESSON 영상은 비로그인/미수강 학습자 접근 시 403 FORBIDDEN,
        # 담당 강사/수강생은 200 OK
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
            headers=auth_header(2),
        )
        assert res_lesson_unenrolled.status_code == 403
        assert res_lesson_unenrolled.json()["error"]["code"] == "FORBIDDEN"

        # 타 강사(id=99) 요청 -> 403 FORBIDDEN
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
            headers=auth_header(99),
        )
        assert res_other_inst.status_code == 403

        # 운영자(id=1) 요청 -> 200 OK
        res_operator = client.get(
            f"/api/v1/videos/{lesson_vid.id}/stream",
            headers=auth_header(1),
        )
        assert res_operator.status_code == 200

        # 담당 강사(id=6) 요청 -> 200 OK
        res_lesson_inst = client.get(
            f"/api/v1/videos/{lesson_vid.id}/stream",
            headers=auth_header(6),
        )
        assert res_lesson_inst.status_code == 200

        # 수강 등록 후 학습자(id=2) 요청 -> 200 OK
        db_session.add(Enrollment(course_id=course.id, learner_id=2, status="IN_PROGRESS"))
        db_session.commit()
        res_lesson_enrolled = client.get(
            f"/api/v1/videos/{lesson_vid.id}/stream",
            headers=auth_header(2),
        )
        assert res_lesson_enrolled.status_code == 200
    finally:
        if vid_file_path.exists():
            vid_file_path.unlink()
