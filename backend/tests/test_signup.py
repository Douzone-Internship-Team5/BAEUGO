from __future__ import annotations

import uuid

import pytest

from app.core.security import decode_access_token

SIGNUP_URL = "/api/v1/auth/signup"
LOGIN_URL = "/api/v1/auth/login"
ME_URL = "/api/v1/users/me"


def signup(client, **data):
    return client.post(SIGNUP_URL, json=data)


# 1. 수강자(LEARNER) 회원가입 성공
def test_signup_learner_success(client, get_user):
    email = "new_learner@baeugo.test"
    password = "simplepassword"

    response = signup(client, email=email, password=password, role="LEARNER")

    assert response.status_code == 201
    data = response.json()["data"]

    # 응답 스키마 검증
    assert data["email"] == email
    assert data["role"] == "LEARNER"
    assert data["activeMode"] == "LEARNER"
    assert data["roles"] == ["LEARNER"]
    assert data["accountType"] == "MEMBER"
    assert data["onboarded"] is False
    assert data["name"] == "new_learner"  # 이메일 ID 기본값 사용
    assert "accessToken" in data
    assert decode_access_token(data["accessToken"]) == data["id"]
    assert data["user"]["email"] == email
    assert data["user"]["role"] == "LEARNER"

    # DB 저장 확인
    db_user = get_user(email)
    assert db_user.id == data["id"]
    assert db_user.active_mode == "LEARNER"
    assert [r.role for r in db_user.roles] == ["LEARNER"]


# 2. 강사(INSTRUCTOR) 회원가입 성공
def test_signup_instructor_success(client, get_user):
    email = "new_instructor@baeugo.test"
    password = "pw"

    response = signup(client, email=email, password=password, role="INSTRUCTOR")

    assert response.status_code == 201
    data = response.json()["data"]

    assert data["email"] == email
    assert data["role"] == "INSTRUCTOR"
    assert data["activeMode"] == "INSTRUCTOR"
    assert data["roles"] == ["INSTRUCTOR"]
    assert data["onboarded"] is False

    db_user = get_user(email)
    assert db_user.active_mode == "INSTRUCTOR"
    assert [r.role for r in db_user.roles] == ["INSTRUCTOR"]


# 3. 역할 미선택 회원가입 성공 (이메일과 비밀번호만 입력)
def test_signup_without_role_success(client, get_user):
    email = "no_role_user@baeugo.test"
    password = "123"

    response = signup(client, email=email, password=password)

    assert response.status_code == 201
    data = response.json()["data"]

    assert data["email"] == email
    assert data["role"] is None
    assert data["activeMode"] is None
    assert data["roles"] == []
    assert data["onboarded"] is False


# 4. 프론트엔드 대소문자 무관 role 전달 처리 (LEARNER, learner, INSTRUCTOR, instructor)
@pytest.mark.parametrize(
    ("input_role", "expected_role"),
    [
        ("LEARNER", "LEARNER"),
        ("learner", "LEARNER"),
        ("INSTRUCTOR", "INSTRUCTOR"),
        ("instructor", "INSTRUCTOR"),
    ],
)
def test_signup_role_case_handling(client, input_role, expected_role):
    email = f"user_{expected_role}_{input_role.lower()}@baeugo.test"
    response = signup(client, email=email, password="pw", role=input_role)

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["role"] == expected_role
    assert data["activeMode"] == expected_role
    assert data["roles"] == [expected_role]


# 5. 빈 문자열("") 또는 공백 역할 전달 시 역할 미선택(None)으로 정상 처리
@pytest.mark.parametrize("empty_role", ["", "   "])
def test_signup_empty_role_treated_as_none(client, empty_role):
    email = f"empty_role_{len(empty_role)}@baeugo.test"
    res = signup(client, email=email, password="pw", role=empty_role)

    assert res.status_code == 201
    data = res.json()["data"]
    assert data["role"] is None
    assert data["activeMode"] is None
    assert data["roles"] == []


# 6. 이메일 공백 제거 및 소문자 정규화
def test_signup_trims_and_lowercases_email(client):
    email_with_spaces = "  Spaces.Case@Baeugo.TEST  "
    expected_email = "spaces.case@baeugo.test"

    response = signup(client, email=email_with_spaces, password="pw")

    assert response.status_code == 201
    assert response.json()["data"]["email"] == expected_email


# 7. 가입 직후 동일 이메일/비밀번호로 로그인 성공
def test_signup_and_immediate_login(client):
    email = "immediate_login@baeugo.test"
    password = "password123"

    signup_res = signup(client, email=email, password=password, role="LEARNER")
    assert signup_res.status_code == 201

    login_res = client.post(LOGIN_URL, json={"email": email, "password": password})
    assert login_res.status_code == 200
    login_data = login_res.json()["data"]
    assert login_data["user"]["email"] == email
    assert login_data["user"]["role"] == "LEARNER"


# 8. 단순 비밀번호 허용 (복잡도/글자수 제한 없음)
@pytest.mark.parametrize(
    "simple_pw",
    [
        "1",  # 1글자
        "!@#$%^&*()_+",  # 특수문자
        "한글비밀번호1234",  # 한글 비밀번호
        "very-secure-and-clean-password",
    ],
    ids=["one-char", "special-chars", "korean-chars", "normal"],
)
def test_signup_simple_password_no_complexity_restriction(client, simple_pw):
    email = f"pw_test_{uuid.uuid4().hex[:8]}@baeugo.test"
    res = signup(client, email=email, password=simple_pw, role="LEARNER")

    assert res.status_code == 201
    assert res.json()["data"]["email"] == email

    # 해당 비밀번호로 즉시 로그인 가능 확인
    login_res = client.post(LOGIN_URL, json={"email": email, "password": simple_pw})
    assert login_res.status_code == 200


# 9. 중복 이메일 가입 실패 (409 CONFLICT)
def test_signup_duplicate_email_fails(client):
    response = signup(client, email="learner1@baeugo.test", password="newpassword")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "CONFLICT"
    assert error["fields"]["email"] == "이미 가입된 이메일입니다."


# 10. 동일 이메일 연속 가입 시 두 번째 가입 실패 (409 CONFLICT)
def test_signup_consecutive_duplicate_fails(client):
    email = "duplicate_test@baeugo.test"
    res1 = signup(client, email=email, password="123")
    assert res1.status_code == 201

    res2 = signup(client, email=email, password="456")
    assert res2.status_code == 409
    assert res2.json()["error"]["code"] == "CONFLICT"


# 11. 이메일 미입력 실패 (400 VALIDATION_ERROR)
def test_signup_missing_email_fails(client):
    response = client.post(SIGNUP_URL, json={"password": "123"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "email" in response.json()["error"]["fields"]


# 12. id/userId 등 임의의 별칭 필드로 전송 시 email 누락으로 실패 (400 VALIDATION_ERROR)
def test_signup_alias_field_not_supported(client):
    response = client.post(SIGNUP_URL, json={"id": "alias@baeugo.test", "password": "123"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "email" in response.json()["error"]["fields"]


# 13. 비밀번호 미입력 실패 (400 VALIDATION_ERROR)
def test_signup_missing_password_fails(client):
    response = client.post(SIGNUP_URL, json={"email": "valid@baeugo.test"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "password" in response.json()["error"]["fields"]


# 14. 비밀번호 빈 문자열 실패 (400 VALIDATION_ERROR)
def test_signup_empty_password_fails(client):
    response = client.post(SIGNUP_URL, json={"email": "valid@baeugo.test", "password": ""})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "password" in response.json()["error"]["fields"]


# 15. 유효하지 않은 이메일 형식 실패 (400 VALIDATION_ERROR)
def test_signup_invalid_email_format_fails(client):
    response = signup(client, email="not-an-email", password="123")

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["fields"]["email"] == "유효한 이메일 형식이 아닙니다."


# 16. 수강자/강사 외 유효하지 않은 역할 선택 실패 (400 VALIDATION_ERROR)
@pytest.mark.parametrize("invalid_role", ["OPERATOR", "ADMIN", "SUPERUSER", "OTHER"])
def test_signup_invalid_role_fails(client, invalid_role):
    response = signup(client, email="invalid_role@baeugo.test", password="123", role=invalid_role)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "role" in error["fields"]


# 17. 회원가입 경로는 /auth/signup 하나로 단일화 (다른 별칭 경로는 404/405)
@pytest.mark.parametrize(
    "wrong_url",
    [
        "/api/v1/auth/register",
        "/api/v1/users/signup",
        "/api/v1/users/register",
    ],
)
def test_extra_routes_not_registered(client, wrong_url):
    response = client.post(
        wrong_url, json={"email": "path_test@baeugo.test", "password": "123", "role": "LEARNER"}
    )
    assert response.status_code == 404


# 18. 가입 토큰으로 /users/me 호출 정상 동작
def test_signup_token_allows_me_query(client):
    email = "flow_user@baeugo.test"
    res = signup(client, email=email, password="123", role="LEARNER")
    assert res.status_code == 201

    token = res.json()["data"]["accessToken"]
    me_res = client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})

    assert me_res.status_code == 200
    me_data = me_res.json()["data"]
    assert me_data["email"] == email
    assert me_data["role"] == "LEARNER"


# 19. 역할 없이 가입한 후 /me/role 엔드포인트로 역할 사후 선택 가능 여부
def test_signup_without_role_can_select_role_later(client):
    email = "later_role@baeugo.test"
    res = signup(client, email=email, password="123")
    assert res.status_code == 201

    token = res.json()["data"]["accessToken"]
    role_res = client.post(
        "/api/v1/users/me/role",
        headers={"Authorization": f"Bearer {token}"},
        json={"role": "INSTRUCTOR"},
    )

    assert role_res.status_code == 200
    assert role_res.json()["data"]["role"] == "INSTRUCTOR"
    assert role_res.json()["data"]["activeMode"] == "INSTRUCTOR"


# 20. DB 동시성 충돌 (IntegrityError) 시 409 CONFLICT 반환
def test_signup_integrity_error_handled_as_conflict(client, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    def fake_commit(*args, **kwargs):
        raise IntegrityError("mock unique violation", orig=Exception("unique key"), params={})

    monkeypatch.setattr("sqlalchemy.orm.Session.commit", fake_commit)
    res = signup(client, email="race_condition@baeugo.test", password="pw")

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"
