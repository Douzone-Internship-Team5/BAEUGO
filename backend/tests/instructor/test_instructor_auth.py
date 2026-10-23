from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import jwt
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import JWT_ALGORITHM
from app.models.course import Course, Section
from app.models.user import AccountType, Role, User, UserRole
from app.models.video import Video
from tests.conftest import auth_header

# B 도메인 전체 엔드포인트 목록 (B-1 ~ B-9, 총 19개 요청 형태)
B_ENDPOINTS = [
    # B-1: 내 강좌 목록
    ("GET", "/api/v1/instructor/courses", None, None),
    # B-2: 강좌 편집용 상세
    ("GET", "/api/v1/instructor/courses/1", None, None),
    # B-3: 강좌 생성
    ("POST", "/api/v1/instructor/courses", {"title": "강좌"}, None),
    # B-3: 강좌 수정
    ("PATCH", "/api/v1/instructor/courses/1", {"title": "수정"}, None),
    # B-3: 강좌 삭제
    ("DELETE", "/api/v1/instructor/courses/1", None, None),
    # B-4: 강좌 상태 변경
    ("PUT", "/api/v1/instructor/courses/1/status", None, {"status": "PUBLISHED"}),
    # B-5: 섹션 생성
    ("POST", "/api/v1/instructor/sections", None, {"courseId": 1, "title": "섹션"}),
    # B-5: 섹션 수정
    ("PATCH", "/api/v1/instructor/sections/1", None, {"title": "수정"}),
    # B-5: 섹션 삭제
    ("DELETE", "/api/v1/instructor/sections/1", None, None),
    # B-6: 영상 업로드
    (
        "POST",
        "/api/v1/instructor/videos",
        {"courseId": 1, "videoType": "OT", "title": "영상"},
        None,
    ),
    # B-6: 영상 수정
    ("PATCH", "/api/v1/instructor/videos/1", {"title": "수정"}, None),
    # B-6: 영상 삭제
    ("DELETE", "/api/v1/instructor/videos/1", None, None),
    # B-7: 대본 조회
    ("GET", "/api/v1/instructor/videos/1/script", None, None),
    # B-7: 대본 수정
    ("PUT", "/api/v1/instructor/videos/1/script", None, {"segments": []}),
    # B-7: 대본 재생성
    ("POST", "/api/v1/instructor/videos/1/script/regenerate", None, None),
    # B-8: 퀴즈 조회
    ("GET", "/api/v1/instructor/courses/1/quiz", None, None),
    # B-8: 퀴즈 수정
    ("PUT", "/api/v1/instructor/courses/1/quiz", None, {"questions": []}),
    # B-8: 퀴즈 재생성
    ("POST", "/api/v1/instructor/courses/1/quiz/regenerate?sectionId=1", None, None),
    # B-9: 강사 통계
    ("GET", "/api/v1/instructor/stats", None, None),
]


def _dispatch_request(
    client: TestClient,
    method: str,
    path: str,
    form_data: dict | None = None,
    json_data: dict | None = None,
    headers: dict | None = None,
):
    if method == "GET":
        return client.get(path, headers=headers)
    if method == "POST":
        return client.post(path, data=form_data, json=json_data, headers=headers)
    if method == "PATCH":
        return client.patch(path, data=form_data, json=json_data, headers=headers)
    if method == "PUT":
        return client.put(path, data=form_data, json=json_data, headers=headers)
    if method == "DELETE":
        return client.delete(path, headers=headers)
    raise ValueError(f"지원하지 않는 HTTP 메서드: {method}")


# ==============================================================================
# 1. [401 UNAUTHORIZED] 비인증 및 토큰 유효성 검증
# ==============================================================================
def test_b_endpoints_unauthorized_without_token(client: TestClient):
    """토큰 없이 B 엔드포인트 전체 호출 시 401 UNAUTHORIZED ('로그인이 필요합니다.') 반환 검증."""
    for method, path, form_data, json_data in B_ENDPOINTS:
        res = _dispatch_request(
            client,
            method,
            path,
            form_data=form_data,
            json_data=json_data,
            headers={"Authorization": "NONE"},
        )
        assert res.status_code == 401, f"Expected 401 for {method} {path}, got {res.status_code}"
        err = res.json()["error"]
        assert err["code"] == "UNAUTHORIZED"
        assert err["message"] == "로그인이 필요합니다."


def test_b_endpoints_unauthorized_with_invalid_or_expired_tokens(client: TestClient):
    """잘못된 토큰, 만료된 토큰, 존재하지 않는 사용자 ID 토큰 전달 시 401 검증."""
    # 1. 잘못된 형식의 토큰 (not-a-jwt)
    res_malformed = client.get(
        "/api/v1/instructor/courses", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert res_malformed.status_code == 401
    assert res_malformed.json()["error"]["code"] == "UNAUTHORIZED"
    assert res_malformed.json()["error"]["message"] == "유효하지 않거나 만료된 토큰입니다."

    # 2. 만료된 토큰
    expired_token = jwt.encode(
        {"sub": "6", "exp": datetime.now(UTC) - timedelta(minutes=1)},
        get_settings().jwt_secret_key,
        JWT_ALGORITHM,
    )
    res_expired = client.get(
        "/api/v1/instructor/courses", headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert res_expired.status_code == 401
    assert res_expired.json()["error"]["code"] == "UNAUTHORIZED"
    assert res_expired.json()["error"]["message"] == "유효하지 않거나 만료된 토큰입니다."

    # 3. 존재하지 않는 사용자 ID 토큰 (sub: 999999)
    res_missing_user = client.get("/api/v1/instructor/courses", headers=auth_header(999_999))
    assert res_missing_user.status_code == 401
    assert res_missing_user.json()["error"]["code"] == "UNAUTHORIZED"
    assert res_missing_user.json()["error"]["message"] == "유효하지 않거나 만료된 토큰입니다."

    # 4. 숫자 아닌 sub 토큰
    non_numeric_token = jwt.encode(
        {"sub": "invalid-sub", "exp": datetime.now(UTC) + timedelta(minutes=10)},
        get_settings().jwt_secret_key,
        JWT_ALGORITHM,
    )
    res_non_num = client.get(
        "/api/v1/instructor/courses", headers={"Authorization": f"Bearer {non_numeric_token}"}
    )
    assert res_non_num.status_code == 401
    assert res_non_num.json()["error"]["code"] == "UNAUTHORIZED"
    assert res_non_num.json()["error"]["message"] == "유효하지 않거나 만료된 토큰입니다."


def test_b_endpoints_unauthorized_with_inactive_or_deleted_account(
    client: TestClient, db_session: Session
):
    """비활성화(is_active=False) 또는 탈퇴/삭제(deleted_at)된 계정의 접근 차단 검증."""
    inst_user = db_session.get(User, 6)
    assert inst_user is not None

    # 1. 비활성화된 계정 차단 (is_active=False)
    inst_user.is_active = False
    db_session.flush()

    res_inactive = client.get("/api/v1/instructor/courses")
    assert res_inactive.status_code == 401
    assert res_inactive.json()["error"]["code"] == "UNAUTHORIZED"
    assert res_inactive.json()["error"]["message"] == "유효하지 않거나 만료된 토큰입니다."

    # 2. 복구 후 탈퇴/삭제된 계정 차단 (deleted_at 설정)
    inst_user.is_active = True
    inst_user.deleted_at = datetime.now(UTC)
    db_session.flush()

    res_deleted = client.get("/api/v1/instructor/courses")
    assert res_deleted.status_code == 401
    assert res_deleted.json()["error"]["code"] == "UNAUTHORIZED"
    assert res_deleted.json()["error"]["message"] == "유효하지 않거나 만료된 토큰입니다."

    # 계정 원복
    inst_user.deleted_at = None
    db_session.commit()


# ==============================================================================
# 2. [403 FORBIDDEN] 권한 및 역할 모드 검증
# ==============================================================================
def test_b_endpoints_forbidden_for_learner(client: TestClient):
    """수강생(learner2@baeugo.test)으로 B 도메인 19개 전체 엔드포인트 접근 시 403 차단 검증."""
    headers_learner = {"Authorization": "Bearer learner"}
    for method, path, form_data, json_data in B_ENDPOINTS:
        res = _dispatch_request(
            client,
            method,
            path,
            form_data=form_data,
            json_data=json_data,
            headers=headers_learner,
        )
        assert res.status_code == 403, f"Expected 403 for {method} {path}, got {res.status_code}"
        assert res.json()["error"]["code"] == "FORBIDDEN"
        assert res.json()["error"]["message"] == "강사 모드에서만 이용할 수 있습니다."


def test_b_endpoints_forbidden_for_operator(client: TestClient):
    """운영자(admin)로 B 도메인 엔드포인트 접근 시 403 차단 검증 (오리진 원칙 준수)."""
    headers_operator = {"Authorization": "Bearer operator"}
    for method, path, form_data, json_data in B_ENDPOINTS:
        res = _dispatch_request(
            client,
            method,
            path,
            form_data=form_data,
            json_data=json_data,
            headers=headers_operator,
        )
        assert res.status_code == 403, f"Expected 403 for {method} {path}, got {res.status_code}"
        assert res.json()["error"]["code"] == "FORBIDDEN"
        assert res.json()["error"]["message"] == "강사 모드에서만 이용할 수 있습니다."


def test_b_endpoints_forbidden_for_member_without_role(client: TestClient):
    """역할이 부여되지 않은 일반 회원(learner1@baeugo.test, id=3)의 강사 API 접근 차단 검증."""
    headers_no_role = {"Authorization": "Bearer user_3"}
    res = client.get("/api/v1/instructor/courses", headers=headers_no_role)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"
    assert res.json()["error"]["message"] == "강사 모드에서만 이용할 수 있습니다."


def test_b_endpoints_forbidden_for_role_owner_in_other_mode(
    client: TestClient, db_session: Session
):
    """강사 역할 보유자라도 수강자 모드(LEARNER)인 경우 접근 차단 검증."""
    learner = db_session.get(User, 2)
    assert learner is not None
    # learner2에게 INSTRUCTOR 역할 추가하지만 active_mode는 LEARNER 유지
    has_inst = any(r.role == Role.INSTRUCTOR for r in learner.roles)
    if not has_inst:
        db_session.add(UserRole(user_id=learner.id, role=Role.INSTRUCTOR))
        db_session.flush()
        db_session.refresh(learner)

    res = client.get("/api/v1/instructor/courses", headers={"Authorization": "Bearer user_2"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"
    assert res.json()["error"]["message"] == "강사 모드에서만 이용할 수 있습니다."


def test_b_endpoints_forbidden_for_active_mode_without_owned_role(
    client: TestClient, db_session: Session
):
    """active_mode는 INSTRUCTOR이지만 실제 역할(UserRole)이 없는 경우 차단 검증."""
    learner1 = db_session.get(User, 3)
    assert learner1 is not None
    learner1.active_mode = Role.INSTRUCTOR
    db_session.flush()

    res = client.get("/api/v1/instructor/courses", headers={"Authorization": "Bearer user_3"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"
    assert res.json()["error"]["message"] == "강사 모드에서만 이용할 수 있습니다."


# ==============================================================================
# 3. [404 NOT_FOUND] 타인 리소스 소유권 은닉 보안 규정 매트릭스 검증
# ==============================================================================
def test_b_endpoints_ownership_security_404_matrix(client: TestClient, db_session: Session):
    """타 강사 소유 리소스 조작 시 403이 아닌 404를 반환하여 존재 여부를 은닉."""
    # 1. 타 강사 계정 생성
    other_user = User(
        id=99,
        email="other99@baeugo.test",
        password_hash="mock",
        name="타강사",
        account_type=AccountType.MEMBER,
        active_mode=Role.INSTRUCTOR,
        is_active=True,
    )
    db_session.add(other_user)
    db_session.flush()

    # 2. 내 강좌 및 타 강사 강좌 준비
    my_course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="내 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="DRAFT",
    )
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
    db_session.add_all([my_course, other_course])
    db_session.flush()

    # 3. 섹션 및 영상 준비
    my_sec = Section(course_id=my_course.id, title="내 섹션", sort_order=1, is_active=True)
    other_sec = Section(course_id=other_course.id, title="타인 섹션", sort_order=1, is_active=True)
    db_session.add_all([my_sec, other_sec])
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

    # (1) B-2 강좌 상세 조회 404
    assert client.get(f"/api/v1/instructor/courses/{other_course.id}").status_code == 404

    # (2) B-3 강좌 수정 404
    assert (
        client.patch(
            f"/api/v1/instructor/courses/{other_course.id}", data={"title": "수정"}
        ).status_code
        == 404
    )

    # (3) B-3 강좌 삭제 404
    assert client.delete(f"/api/v1/instructor/courses/{other_course.id}").status_code == 404

    # (4) B-4 강좌 상태 변경 404
    assert (
        client.put(
            f"/api/v1/instructor/courses/{other_course.id}/status", json={"status": "PUBLISHED"}
        ).status_code
        == 404
    )

    # (5) B-5 섹션 생성 시 타인 강좌 ID 지정 404
    assert (
        client.post(
            "/api/v1/instructor/sections", json={"courseId": other_course.id, "title": "새섹션"}
        ).status_code
        == 404
    )

    # (6) B-5 타인 섹션 수정 404
    assert (
        client.patch(
            f"/api/v1/instructor/sections/{other_sec.id}", json={"title": "수정"}
        ).status_code
        == 404
    )

    # (7) B-5 타인 섹션 삭제 404
    assert client.delete(f"/api/v1/instructor/sections/{other_sec.id}").status_code == 404

    # (8) B-6 영상 업로드 시 타인 강좌 ID 지정 404
    dummy_file = ("dummy.mp4", io.BytesIO(b"fake video data"), "video/mp4")
    assert (
        client.post(
            "/api/v1/instructor/videos",
            data={"courseId": other_course.id, "videoType": "OT", "title": "영상"},
            files={"file": dummy_file},
        ).status_code
        == 404
    )

    # (9) B-6 영상 업로드 시 내 강좌이지만 타인 섹션 ID 지정 (크로스 인젝션 방어) 404
    dummy_file2 = ("dummy2.mp4", io.BytesIO(b"fake video data"), "video/mp4")
    assert (
        client.post(
            "/api/v1/instructor/videos",
            data={
                "courseId": my_course.id,
                "videoType": "LESSON",
                "title": "영상",
                "sectionId": other_sec.id,
            },
            files={"file": dummy_file2},
        ).status_code
        == 404
    )

    # (10) B-6 타인 영상 메타 수정 및 삭제 404
    assert (
        client.patch(
            f"/api/v1/instructor/videos/{other_vid.id}", data={"title": "수정"}
        ).status_code
        == 404
    )
    assert client.delete(f"/api/v1/instructor/videos/{other_vid.id}").status_code == 404

    # (11) B-7 타인 영상 대본 조회, 수정, 재생성 404
    assert client.get(f"/api/v1/instructor/videos/{other_vid.id}/script").status_code == 404
    assert (
        client.put(
            f"/api/v1/instructor/videos/{other_vid.id}/script",
            json={"segments": [{"seq": 1, "startMs": 0, "endMs": 1000, "content": "대본"}]},
        ).status_code
        == 404
    )
    assert (
        client.post(f"/api/v1/instructor/videos/{other_vid.id}/script/regenerate").status_code
        == 404
    )

    # (12) B-8 타인 강좌 퀴즈 조회, 수정, 재생성 404
    assert client.get(f"/api/v1/instructor/courses/{other_course.id}/quiz").status_code == 404
    assert (
        client.get(
            f"/api/v1/instructor/courses/{my_course.id}/quiz?sectionId={other_sec.id}"
        ).status_code
        == 404
    )
    valid_quiz_payload = {
        "questions": [
            {
                "orderNo": 1,
                "content": "문제 1",
                "choices": ["A", "B", "C", "D"],
                "correctChoiceNo": 1,
                "explanation": "해설 1",
            },
            {
                "orderNo": 2,
                "content": "문제 2",
                "choices": ["A", "B", "C", "D"],
                "correctChoiceNo": 2,
                "explanation": "해설 2",
            },
            {
                "orderNo": 3,
                "content": "문제 3",
                "choices": ["A", "B", "C", "D"],
                "correctChoiceNo": 3,
                "explanation": "해설 3",
            },
        ]
    }
    assert (
        client.put(
            f"/api/v1/instructor/courses/{other_course.id}/quiz", json=valid_quiz_payload
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/v1/instructor/courses/{my_course.id}/quiz?sectionId={other_sec.id}",
            json=valid_quiz_payload,
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/instructor/courses/{other_course.id}/quiz/regenerate?sectionId=1"
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/instructor/courses/{my_course.id}/quiz/regenerate?sectionId={other_sec.id}"
        ).status_code
        == 404
    )

    # (13) B-9 타인 강좌 ID로 통계 조회 시 404
    assert client.get(f"/api/v1/instructor/stats?courseId={other_course.id}").status_code == 404
