from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course, Section, Video
from app.models.user import User


def test_instructor_unauthorized_and_forbidden(client: TestClient):
    # [401 UNAUTHORIZED] 비인증 / 잘못된 토큰 전달
    res_no_auth = client.get(
        "/api/v1/instructor/courses", headers={"Authorization": "Bearer invalid"}
    )
    assert res_no_auth.status_code == 401
    assert res_no_auth.json()["error"]["code"] == "UNAUTHORIZED"

    res_post_unauth = client.post(
        "/api/v1/instructor/courses",
        data={"title": "강좌", "categoryId": 1, "topicId": 11, "difficulty": "BEGINNER"},
        headers={"Authorization": "NONE"},
    )
    assert res_post_unauth.status_code == 401
    assert res_post_unauth.json()["error"]["code"] == "UNAUTHORIZED"

    # [403 FORBIDDEN] 수강생 권한(active_mode=LEARNER)으로 강사 API 전체 접근 시 차단
    headers_learner = {"Authorization": "Bearer learner"}
    endpoints_for_instructor = [
        ("GET", "/api/v1/instructor/courses", None, None),
        ("POST", "/api/v1/instructor/courses", {"title": "강좌"}, None),
        ("PATCH", "/api/v1/instructor/courses/1", {"title": "수정"}, None),
        ("DELETE", "/api/v1/instructor/courses/1", None, None),
        ("PUT", "/api/v1/instructor/courses/1/status", None, {"status": "PUBLISHED"}),
        ("POST", "/api/v1/instructor/sections", None, {"courseId": 1, "title": "섹션"}),
        ("PATCH", "/api/v1/instructor/sections/1", None, {"title": "수정"}),
        ("DELETE", "/api/v1/instructor/sections/1", None, None),
        (
            "POST",
            "/api/v1/instructor/videos",
            {"courseId": 1, "videoType": "OT", "title": "영상"},
            None,
        ),
        ("PATCH", "/api/v1/instructor/videos/1", {"title": "수정"}, None),
        ("DELETE", "/api/v1/instructor/videos/1", None, None),
        ("GET", "/api/v1/instructor/videos/1/script", None, None),
        ("PUT", "/api/v1/instructor/videos/1/script", None, {"segments": []}),
        ("POST", "/api/v1/instructor/videos/1/script/regenerate", None, None),
        ("GET", "/api/v1/instructor/courses/1/quiz", None, None),
        ("PUT", "/api/v1/instructor/courses/1/quiz", None, {"questions": []}),
        ("POST", "/api/v1/instructor/courses/1/quiz/regenerate?sectionId=1", None, None),
        ("GET", "/api/v1/instructor/stats", None, None),
    ]

    for method, path, form_data, json_data in endpoints_for_instructor:
        if method == "GET":
            r = client.get(path, headers=headers_learner)
        elif method == "POST":
            r = client.post(path, data=form_data, json=json_data, headers=headers_learner)
        elif method == "PATCH":
            r = client.patch(path, data=form_data, json=json_data, headers=headers_learner)
        elif method == "PUT":
            r = client.put(path, data=form_data, json=json_data, headers=headers_learner)
        elif method == "DELETE":
            r = client.delete(path, headers=headers_learner)
        assert r.status_code == 403, f"Expected 403 for {method} {path}, got {r.status_code}"
        assert r.json()["error"]["code"] == "FORBIDDEN"


def test_instructor_inactive_account(client: TestClient, db_session: Session):
    # [401 INVALID_CREDENTIALS] 비활성화된 계정 차단
    inst_user = db_session.get(User, 6)
    if inst_user:
        inst_user.is_active = False
        db_session.commit()

        res_inactive = client.get("/api/v1/instructor/courses")
        assert res_inactive.status_code == 401
        assert res_inactive.json()["error"]["code"] == "UNAUTHORIZED"

        # 복구
        inst_user.is_active = True
        db_session.commit()


def test_instructor_ownership_security_404(client: TestClient, db_session: Session):
    # [보안 규정 404 NOT_FOUND] 타인 강좌, 섹션, 영상에 접근 시
    # 403이 아닌 404를 반환하여 리소스 존재 여부를 은닉
    other_user = User(
        id=99,
        email="other99@baeugo.test",
        password_hash="mock",
        name="타강사",
        account_type="MEMBER",
        active_mode="INSTRUCTOR",
        is_active=True,
    )
    db_session.add(other_user)
    db_session.flush()

    other_course = Course(
        instructor_id=99,
        category_id=1,
        topic_id=11,
        title="타인 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="DRAFT",
    )
    db_session.add(other_course)
    db_session.flush()

    other_sec = Section(course_id=other_course.id, title="타인 섹션", sort_order=1, is_active=True)
    db_session.add(other_sec)
    db_session.flush()

    other_vid = Video(
        course_id=other_course.id,
        section_id=other_sec.id,
        video_type="LESSON",
        title="타인 영상",
        duration_seconds=60,
        video_storage_key="test/other.mp4",
        is_active=True,
    )
    db_session.add(other_vid)
    db_session.commit()

    # 상세 조회 404
    r_detail = client.get(f"/api/v1/instructor/courses/{other_course.id}")
    assert r_detail.status_code == 404

    # 수정/삭제 404
    r_patch = client.patch(f"/api/v1/instructor/courses/{other_course.id}", data={"title": "수정"})
    assert r_patch.status_code == 404

    r_del = client.delete(f"/api/v1/instructor/courses/{other_course.id}")
    assert r_del.status_code == 404

    # 섹션 수정/삭제 404
    r_sec_patch = client.patch(
        f"/api/v1/instructor/sections/{other_sec.id}", json={"title": "수정"}
    )
    assert r_sec_patch.status_code == 404

    r_sec_del = client.delete(f"/api/v1/instructor/sections/{other_sec.id}")
    assert r_sec_del.status_code == 404

    # 영상 수정/삭제 404
    r_vid_patch = client.patch(f"/api/v1/instructor/videos/{other_vid.id}", data={"title": "수정"})
    assert r_vid_patch.status_code == 404

    r_vid_del = client.delete(f"/api/v1/instructor/videos/{other_vid.id}")
    assert r_vid_del.status_code == 404
