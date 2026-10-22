from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import JWT_ALGORITHM, decode_access_token
from tests.conftest import auth_header

LOGIN_URL = "/api/v1/auth/login"
LOGOUT_URL = "/api/v1/auth/logout"
SEED_PASSWORD = "baeugo1234"


def login(client, email: str, password: str = SEED_PASSWORD):
    return client.post(LOGIN_URL, json={"email": email, "password": password})


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        (
            "learner1@baeugo.test",
            {"accountType": "MEMBER", "role": None, "activeMode": None, "roles": []},
        ),
        (
            "learner2@baeugo.test",
            {
                "accountType": "MEMBER",
                "role": "LEARNER",
                "activeMode": "LEARNER",
                "roles": ["LEARNER"],
            },
        ),
        (
            "instructor1@baeugo.test",
            {
                "accountType": "MEMBER",
                "role": "INSTRUCTOR",
                "activeMode": "INSTRUCTOR",
                "roles": ["INSTRUCTOR"],
            },
        ),
        (
            "admin@baeugo.test",
            {"accountType": "OPERATOR", "role": None, "activeMode": None, "roles": []},
        ),
    ],
)
def test_login_returns_token_and_account_state(client, get_user, email, expected):
    response = login(client, email)

    assert response.status_code == 200
    data = response.json()["data"]
    user = data["user"]
    assert decode_access_token(data["accessToken"]) == get_user(email).id
    assert user["email"] == email
    assert {key: user[key] for key in expected} == expected


@pytest.mark.parametrize(
    ("email", "onboarded"),
    [
        ("learner1@baeugo.test", False),
        ("learner2@baeugo.test", True),
        ("instructor1@baeugo.test", True),
        ("instructor2@baeugo.test", True),
        ("admin@baeugo.test", True),
    ],
)
def test_login_onboarded_flag(client, email, onboarded):
    response = login(client, email)

    assert response.json()["data"]["user"]["onboarded"] is onboarded


def test_login_ignores_email_case_and_spaces(client):
    response = login(client, "  Learner2@Baeugo.TEST ")

    assert response.status_code == 200


def test_login_with_wrong_password_fails(client):
    response = login(client, "learner2@baeugo.test", "wrong-password")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_login_with_unknown_email_fails(client):
    response = login(client, "nobody@baeugo.test")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_login_with_inactive_account_fails(client, db, get_user):
    get_user("learner2@baeugo.test").is_active = False
    db.flush()

    response = login(client, "learner2@baeugo.test")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_login_with_deleted_account_fails(client, db, get_user):
    get_user("learner2@baeugo.test").deleted_at = datetime.now(UTC)
    db.flush()

    response = login(client, "learner2@baeugo.test")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_login_with_invalid_email_format_fails(client):
    response = login(client, "not-an-email")

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["fields"]["email"] == "유효한 이메일 형식이 아닙니다."


def test_login_without_password_fails(client):
    response = client.post(LOGIN_URL, json={"email": "learner2@baeugo.test"})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["fields"] == {"password": "필수 입력값입니다."}


def test_logout_with_valid_token_returns_no_content(client, get_user):
    user = get_user("learner2@baeugo.test")

    response = client.post(LOGOUT_URL, headers=auth_header(user.id))

    assert response.status_code == 204
    assert response.content == b""


def test_logout_without_token_fails(client):
    response = client.post(LOGOUT_URL)

    assert response.status_code == 401
    assert response.json()["error"] == {"code": "UNAUTHORIZED", "message": "로그인이 필요합니다."}


def test_logout_with_malformed_token_fails(client):
    response = client.post(LOGOUT_URL, headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "유효하지 않거나 만료된 토큰입니다."


def test_logout_with_expired_token_fails(client, get_user):
    user = get_user("learner2@baeugo.test")
    expired_token = jwt.encode(
        {"sub": str(user.id), "exp": datetime.now(UTC) - timedelta(minutes=1)},
        get_settings().jwt_secret_key,
        JWT_ALGORITHM,
    )

    response = client.post(LOGOUT_URL, headers={"Authorization": f"Bearer {expired_token}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_token_for_missing_user_is_rejected(client):
    response = client.post(LOGOUT_URL, headers=auth_header(999_999))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_token_is_rejected_after_account_is_deleted(client, db, get_user):
    user = get_user("learner2@baeugo.test")
    headers = auth_header(user.id)
    user.deleted_at = datetime.now(UTC)
    db.flush()

    response = client.post(LOGOUT_URL, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_token_is_rejected_after_account_is_deactivated(client, db, get_user):
    user = get_user("learner2@baeugo.test")
    headers = auth_header(user.id)
    user.is_active = False
    db.flush()

    response = client.post(LOGOUT_URL, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
