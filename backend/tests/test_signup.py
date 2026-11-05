from __future__ import annotations

import uuid

import pytest

from app.core.security import decode_access_token

SIGNUP_URL = "/api/v1/auth/signup"
LOGIN_URL = "/api/v1/auth/login"
ME_URL = "/api/v1/users/me"

VALID_PASSWORD = "password123"
PASSWORD_RULE_MESSAGE = "비밀번호는 8자 이상 영문과 숫자를 포함해야 합니다."
PASSWORD_TOO_LONG_MESSAGE = "비밀번호는 72바이트(영문 72자, 한글 24자) 이하여야 합니다."


def signup(client, **data):
    return client.post(SIGNUP_URL, json=data)


# 1. 수강자(LEARNER) 회원가입 성공
def test_signup_learner_success(client, get_user):
    email = "new_learner@baeugo.test"

    response = signup(client, email=email, password=VALID_PASSWORD, role="LEARNER")

    assert response.status_code == 201
    data = response.json()["data"]
    assert set(data.keys()) == {"accessToken", "user"}

    user = data["user"]
    assert user["email"] == email
    assert user["role"] == "LEARNER"
    assert user["activeMode"] == "LEARNER"
    assert user["roles"] == ["LEARNER"]
    assert user["accountType"] == "MEMBER"
    assert user["onboarded"] is False
    assert user["name"] == "new_learner"  # 이메일 ID 기본값 사용
    assert decode_access_token(data["accessToken"]) == user["id"]

    # DB 저장 확인
    db_user = get_user(email)
    assert db_user.id == user["id"]
    assert db_user.active_mode == "LEARNER"
    assert [r.role for r in db_user.roles] == ["LEARNER"]


# 2. 강사(INSTRUCTOR) 회원가입 성공
def test_signup_instructor_success(client, get_user):
    email = "new_instructor@baeugo.test"

    response = signup(client, email=email, password=VALID_PASSWORD, role="INSTRUCTOR")

    assert response.status_code == 201
    user = response.json()["data"]["user"]

    assert user["email"] == email
    assert user["role"] == "INSTRUCTOR"
    assert user["activeMode"] == "INSTRUCTOR"
    assert user["roles"] == ["INSTRUCTOR"]
    assert user["onboarded"] is False

    db_user = get_user(email)
    assert db_user.active_mode == "INSTRUCTOR"
    assert [r.role for r in db_user.roles] == ["INSTRUCTOR"]


# 3. 역할 미선택 회원가입 성공 (이메일과 비밀번호만 입력)
def test_signup_without_role_success(client, get_user):
    email = "no_role_user@baeugo.test"

    response = signup(client, email=email, password=VALID_PASSWORD)

    assert response.status_code == 201
    user = response.json()["data"]["user"]

    assert user["email"] == email
    assert user["role"] is None
    assert user["activeMode"] is None
    assert user["roles"] == []
    assert user["onboarded"] is False


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
    response = signup(client, email=email, password=VALID_PASSWORD, role=input_role)

    assert response.status_code == 201
    user = response.json()["data"]["user"]
    assert user["role"] == expected_role
    assert user["activeMode"] == expected_role
    assert user["roles"] == [expected_role]


# 5. 빈 문자열("") 또는 공백 역할 전달 시 역할 미선택(None)으로 정상 처리
@pytest.mark.parametrize("empty_role", ["", "   "])
def test_signup_empty_role_treated_as_none(client, empty_role):
    email = f"empty_role_{len(empty_role)}@baeugo.test"
    res = signup(client, email=email, password=VALID_PASSWORD, role=empty_role)

    assert res.status_code == 201
    user = res.json()["data"]["user"]
    assert user["role"] is None
    assert user["activeMode"] is None
    assert user["roles"] == []


# 6. 이메일 공백 제거 및 소문자 정규화
def test_signup_trims_and_lowercases_email(client):
    email_with_spaces = "  Spaces.Case@Baeugo.TEST  "
    expected_email = "spaces.case@baeugo.test"

    response = signup(client, email=email_with_spaces, password=VALID_PASSWORD)

    assert response.status_code == 201
    assert response.json()["data"]["user"]["email"] == expected_email


# 7. 가입 직후 동일 이메일/비밀번호로 로그인 성공
def test_signup_and_immediate_login(client):
    email = "immediate_login@baeugo.test"

    signup_res = signup(client, email=email, password=VALID_PASSWORD, role="LEARNER")
    assert signup_res.status_code == 201

    login_res = client.post(LOGIN_URL, json={"email": email, "password": VALID_PASSWORD})
    assert login_res.status_code == 200
    login_data = login_res.json()["data"]
    assert login_data["user"] == signup_res.json()["data"]["user"]


# 8. 유효한 비밀번호(8자 이상 영문+숫자, 72바이트 이하) 허용
@pytest.mark.parametrize(
    "valid_pw",
    [
        "password123",
        "한글비밀번호1234",
        "a1" * 36,  # 72바이트 (ASCII)
        "가" * 22 + "a1",  # 72바이트 (한글 66바이트 + 영문·숫자 2바이트 = 68바이트 <= 72바이트)
    ],
    ids=["normal", "korean-with-alnum", "ascii-72-bytes", "korean-72-bytes"],
)
def test_signup_valid_password_up_to_72_bytes(client, valid_pw):
    email = f"pw_test_{uuid.uuid4().hex[:8]}@baeugo.test"
    res = signup(client, email=email, password=valid_pw, role="LEARNER")

    assert res.status_code == 201
    assert res.json()["data"]["user"]["email"] == email

    # 해당 비밀번호로 즉시 로그인 가능 확인
    login_res = client.post(LOGIN_URL, json={"email": email, "password": valid_pw})
    assert login_res.status_code == 200


# 8-1. 비밀번호 복잡도 규칙 위반 실패 (8자 이상 영문+숫자 미충족 시 400 VALIDATION_ERROR)
@pytest.mark.parametrize(
    "invalid_pw",
    [
        "abc1234",  # 7자 (8자 미만)
        "abcdefgh",  # 숫자 없음
        "12345678",  # 영문 없음
        "1",  # 1글자
        "!@#$%^&*()_+",  # 특수문자만
    ],
    ids=["too-short", "no-digit", "no-letter", "one-char", "special-chars-only"],
)
def test_signup_password_rule_violation_fails(client, invalid_pw):
    email = f"pw_rule_{uuid.uuid4().hex[:8]}@baeugo.test"
    res = signup(client, email=email, password=invalid_pw, role="LEARNER")

    assert res.status_code == 400
    error = res.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["message"] == PASSWORD_RULE_MESSAGE
    assert error["fields"] == {"password": PASSWORD_RULE_MESSAGE}


# 8-2. 72바이트 초과 비밀번호 실패 (400 VALIDATION_ERROR)
@pytest.mark.parametrize(
    "long_pw",
    [
        "a1" * 36 + "b",  # 73바이트 (ASCII)
        "가" * 24 + "a1",  # 74바이트 (한글 72바이트 + 영문·숫자 2바이트)
    ],
    ids=["ascii-73-bytes", "korean-74-bytes"],
)
def test_signup_password_over_72_bytes_fails(client, long_pw):
    email = f"pw_long_{uuid.uuid4().hex[:8]}@baeugo.test"
    res = signup(client, email=email, password=long_pw, role="LEARNER")

    assert res.status_code == 400
    error = res.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["message"] == PASSWORD_TOO_LONG_MESSAGE
    assert error["fields"] == {"password": PASSWORD_TOO_LONG_MESSAGE}


# 9. 중복 이메일 가입 실패 (409 CONFLICT)
def test_signup_duplicate_email_fails(client):
    response = signup(client, email="learner1@baeugo.test", password=VALID_PASSWORD)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "CONFLICT"
    assert error["fields"]["email"] == "이미 가입된 이메일입니다."


# 10. 동일 이메일 연속 가입 시 두 번째 가입 실패 (409 CONFLICT)
def test_signup_consecutive_duplicate_fails(client):
    email = "duplicate_test@baeugo.test"
    res1 = signup(client, email=email, password=VALID_PASSWORD)
    assert res1.status_code == 201

    res2 = signup(client, email=email, password="otherpassword123")
    assert res2.status_code == 409
    assert res2.json()["error"]["code"] == "CONFLICT"


# 11. 이메일 미입력 실패 (400 VALIDATION_ERROR)
def test_signup_missing_email_fails(client):
    response = client.post(SIGNUP_URL, json={"password": VALID_PASSWORD})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "email" in response.json()["error"]["fields"]


# 12. id/userId 등 임의의 별칭 필드로 전송 시 email 누락으로 실패 (400 VALIDATION_ERROR)
def test_signup_alias_field_not_supported(client):
    response = client.post(SIGNUP_URL, json={"id": "alias@baeugo.test", "password": VALID_PASSWORD})

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
    response = signup(client, email="not-an-email", password=VALID_PASSWORD)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["fields"]["email"] == "유효한 이메일 형식이 아닙니다."


# 16. 수강자/강사 외 유효하지 않은 역할 선택 실패 (400 VALIDATION_ERROR)
@pytest.mark.parametrize("invalid_role", ["OPERATOR", "ADMIN", "SUPERUSER", "OTHER"])
def test_signup_invalid_role_fails(client, invalid_role):
    response = signup(
        client, email="invalid_role@baeugo.test", password=VALID_PASSWORD, role=invalid_role
    )

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
        wrong_url,
        json={"email": "path_test@baeugo.test", "password": VALID_PASSWORD, "role": "LEARNER"},
    )
    assert response.status_code == 404


# 18. 가입 토큰으로 /users/me 호출 정상 동작
def test_signup_token_allows_me_query(client):
    email = "flow_user@baeugo.test"
    res = signup(client, email=email, password=VALID_PASSWORD, role="LEARNER")
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
    res = signup(client, email=email, password=VALID_PASSWORD)
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
    res = signup(client, email="race_condition@baeugo.test", password=VALID_PASSWORD)

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"
