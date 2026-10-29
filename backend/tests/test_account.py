import pytest
from sqlalchemy import delete

from app.models import LearningGoal, Role
from tests.conftest import auth_header
from tests.test_onboarding import give_mode

ME_URL = "/api/v1/users/me"
PASSWORD_URL = "/api/v1/users/me/password"
PAYMENT_URL = "/api/v1/users/me/payment-method"
SETTLEMENT_URL = "/api/v1/users/me/settlement-account"
LOGIN_URL = "/api/v1/auth/login"
SEED_PASSWORD = "baeugo1234"

PASSWORD_RULE_MESSAGE = "새 비밀번호는 8자 이상 영문과 숫자를 포함해야 합니다."
PASSWORD_TOO_LONG_MESSAGE = "새 비밀번호는 72바이트(영문 72자, 한글 24자) 이하여야 합니다."
SAME_PASSWORD_MESSAGE = "새 비밀번호는 현재 비밀번호와 달라야 합니다."
PAYMENT_LABEL_MESSAGE = "결제수단 표시값은 1~100자 이내여야 합니다."
SETTLEMENT_LABEL_MESSAGE = "정산계좌 표시값은 1~100자 이내여야 합니다."
ONBOARDING_REQUIRED_MESSAGE = "온보딩을 먼저 완료해 주세요."


def get_me(client, user):
    response = client.get(ME_URL, headers=auth_header(user.id))
    assert response.status_code == 200
    return response.json()["data"]


def login(client, email, password):
    return client.post(LOGIN_URL, json={"email": email, "password": password})


def assert_error(response, status_code, code, message):
    assert response.status_code == status_code
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message"] == message
    return error


# ---------- A-5 내 정보 조회 ----------


def test_me_learner_matches_spec(client, get_user):
    data = get_me(client, get_user("learner2@baeugo.test"))

    assert data == {
        "id": 2,
        "email": "learner2@baeugo.test",
        "name": "김배우",
        "accountType": "MEMBER",
        "role": "LEARNER",
        "activeMode": "LEARNER",
        "roles": ["LEARNER"],
        "onboarded": True,
        "profileImageKey": None,
        "profileImageUrl": None,
        "demoPaymentLabel": "신한카드 (1234)",
        "onboarding": {
            "currentStatus": "EMPLOYED",
            "categoryId": 1,
            "jobCategory": "PLANNING_PM",
            "interestTopicIds": [11],
            "dailyGoalMinutes": 20,
            "timeZone": "Asia/Seoul",
            "timeSlots": [],
        },
    }


def test_me_instructor_matches_spec(client, get_user):
    data = get_me(client, get_user("instructor1@baeugo.test"))

    assert data["demoSettlementLabel"] == "신한은행 (5678) 이하늘"
    assert "demoPaymentLabel" not in data
    assert data["onboarding"] == {
        "categoryId": 6,
        "jobCategory": "OFFICE_PRODUCTIVITY",
        "lectureTopicIds": [61],
        "bio": "10년 차 사무 자동화 실무자",
    }


def test_me_basic_fields_match_login_response(client, get_user):
    user = get_user("learner2@baeugo.test")
    login_user = login(client, user.email, SEED_PASSWORD).json()["data"]["user"]

    data = get_me(client, user)

    assert {key: data[key] for key in login_user} == login_user


def test_me_profile_image_url_from_key(client, db, get_user):
    user = get_user("learner2@baeugo.test")
    user.profile_image_key = "profiles/2.png"
    db.flush()

    data = get_me(client, user)

    assert data["profileImageKey"] == "profiles/2.png"
    assert data["profileImageUrl"] == "/api/v1/media/profiles/2.png"


def test_me_instructor_without_label_returns_null(client, get_user):
    data = get_me(client, get_user("instructor2@baeugo.test"))

    assert data["demoSettlementLabel"] is None
    assert "demoPaymentLabel" not in data


def test_me_label_follows_current_mode_for_dual_role(client, db, get_user):
    user = get_user("instructor1@baeugo.test")
    give_mode(db, user, Role.LEARNER)

    data = get_me(client, user)

    assert data["activeMode"] == "LEARNER"
    assert data["demoPaymentLabel"] is None
    assert "demoSettlementLabel" not in data
    assert data["onboarding"] is None


def test_me_without_role(client, get_user):
    data = get_me(client, get_user("learner1@baeugo.test"))

    assert data["role"] is None
    assert data["activeMode"] is None
    assert data["roles"] == []
    assert data["onboarded"] is False
    assert data["onboarding"] is None
    assert "demoPaymentLabel" not in data
    assert "demoSettlementLabel" not in data


def test_me_onboarding_is_null_before_onboarding_completes(client, db, get_user):
    user = get_user("learner2@baeugo.test")
    # 프로필은 있지만 학습 목표가 없으면 온보딩 미완료다.
    db.execute(delete(LearningGoal).where(LearningGoal.learner_id == user.id))
    db.flush()
    db.expire(user, ["learning_goal"])

    data = get_me(client, user)

    assert data["onboarded"] is False
    assert data["onboarding"] is None
    assert data["demoPaymentLabel"] == "신한카드 (1234)"


def test_me_operator(client, get_user):
    user = get_user("admin@baeugo.test")
    login_user = login(client, user.email, SEED_PASSWORD).json()["data"]["user"]

    data = get_me(client, user)

    assert data == {
        "id": user.id,
        "email": "admin@baeugo.test",
        "name": "운영자",
        "accountType": "OPERATOR",
        "role": None,
        "activeMode": None,
        "roles": [],
        "onboarded": login_user["onboarded"],
        "profileImageKey": None,
        "profileImageUrl": None,
        "onboarding": None,
    }
    assert data["onboarded"] is True


def test_me_requires_login(client):
    response = client.get(ME_URL)

    assert_error(response, 401, "UNAUTHORIZED", "로그인이 필요합니다.")


# ---------- A-7 비밀번호 변경 ----------


def change_password(client, user, body):
    return client.put(PASSWORD_URL, json=body, headers=auth_header(user.id))


def test_change_password_then_login_with_new_password(client, get_user):
    user = get_user("learner2@baeugo.test")

    response = change_password(
        client, user, {"currentPassword": SEED_PASSWORD, "newPassword": "newpass123"}
    )

    assert response.status_code == 204
    assert response.content == b""
    assert user.password_hash.startswith("$2")
    assert "newpass123" not in user.password_hash
    assert login(client, user.email, "newpass123").status_code == 200
    assert_error(
        login(client, user.email, SEED_PASSWORD),
        401,
        "INVALID_CREDENTIALS",
        "이메일 또는 비밀번호가 일치하지 않습니다.",
    )


def test_operator_can_change_password(client, get_user):
    user = get_user("admin@baeugo.test")

    response = change_password(
        client, user, {"currentPassword": SEED_PASSWORD, "newPassword": "admin2026"}
    )

    assert response.status_code == 204
    assert login(client, user.email, "admin2026").status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"currentPassword": "wrong1234", "newPassword": "newpass123"},
        {"currentPassword": "", "newPassword": "newpass123"},
        {"newPassword": "newpass123"},
    ],
    ids=["wrong", "empty", "missing"],
)
def test_wrong_current_password(client, get_user, body):
    user = get_user("learner2@baeugo.test")
    original_hash = user.password_hash

    response = change_password(client, user, body)

    assert_error(response, 400, "WRONG_PASSWORD", "현재 비밀번호가 일치하지 않습니다.")
    assert user.password_hash == original_hash


@pytest.mark.parametrize(
    "new_password",
    ["abc1234", "abcdefgh", "12345678", "", None],
    ids=["too-short", "no-digit", "no-letter", "empty", "null"],
)
def test_new_password_rule_violation(client, get_user, new_password):
    user = get_user("learner2@baeugo.test")
    original_hash = user.password_hash

    response = change_password(
        client, user, {"currentPassword": SEED_PASSWORD, "newPassword": new_password}
    )

    error = assert_error(response, 400, "VALIDATION_ERROR", PASSWORD_RULE_MESSAGE)
    assert "fields" not in error
    assert user.password_hash == original_hash


def test_new_password_missing(client, get_user):
    response = change_password(
        client, get_user("learner2@baeugo.test"), {"currentPassword": SEED_PASSWORD}
    )

    assert_error(response, 400, "VALIDATION_ERROR", PASSWORD_RULE_MESSAGE)


@pytest.mark.parametrize(
    "new_password",
    ["a1" * 36, "가" * 22 + "a1"],
    ids=["ascii-72-bytes", "korean-72-bytes"],
)
def test_new_password_up_to_72_bytes_is_allowed(client, get_user, new_password):
    user = get_user("learner2@baeugo.test")

    response = change_password(
        client, user, {"currentPassword": SEED_PASSWORD, "newPassword": new_password}
    )

    assert response.status_code == 204
    assert login(client, user.email, new_password).status_code == 200


@pytest.mark.parametrize(
    "new_password",
    ["a1" * 36 + "b", "가" * 24 + "a1", "a1" * 128],
    ids=["ascii-73-bytes", "korean-74-bytes", "256-chars"],
)
def test_new_password_over_72_bytes(client, get_user, new_password):
    user = get_user("learner2@baeugo.test")
    original_hash = user.password_hash

    response = change_password(
        client, user, {"currentPassword": SEED_PASSWORD, "newPassword": new_password}
    )

    error = assert_error(response, 400, "VALIDATION_ERROR", PASSWORD_TOO_LONG_MESSAGE)
    assert error["fields"] == {"newPassword": PASSWORD_TOO_LONG_MESSAGE}
    assert user.password_hash == original_hash


def test_new_password_same_as_current(client, get_user):
    user = get_user("learner2@baeugo.test")

    response = change_password(
        client, user, {"currentPassword": SEED_PASSWORD, "newPassword": SEED_PASSWORD}
    )

    error = assert_error(response, 400, "VALIDATION_ERROR", SAME_PASSWORD_MESSAGE)
    assert error["fields"] == {"newPassword": SAME_PASSWORD_MESSAGE}


def test_change_password_requires_login(client):
    response = client.put(
        PASSWORD_URL, json={"currentPassword": SEED_PASSWORD, "newPassword": "newpass123"}
    )

    assert_error(response, 401, "UNAUTHORIZED", "로그인이 필요합니다.")


# ---------- A-8 결제 방식 / A-9 정산 계좌 ----------

LABEL_CASES = {
    "payment": {
        "url": PAYMENT_URL,
        "mode": Role.LEARNER,
        "user": "learner2@baeugo.test",
        "saved": "신한카드 (1234)",
        "no_label_user": None,
        "message": PAYMENT_LABEL_MESSAGE,
        "forbidden": "수강자 모드에서만 이용할 수 있습니다.",
        "other_mode_user": "instructor1@baeugo.test",
        "me_key": "demoPaymentLabel",
    },
    "settlement": {
        "url": SETTLEMENT_URL,
        "mode": Role.INSTRUCTOR,
        "user": "instructor1@baeugo.test",
        "saved": "신한은행 (5678) 이하늘",
        "no_label_user": "instructor2@baeugo.test",
        "message": SETTLEMENT_LABEL_MESSAGE,
        "forbidden": "강사 모드에서만 이용할 수 있습니다.",
        "other_mode_user": "learner2@baeugo.test",
        "me_key": "demoSettlementLabel",
    },
}


@pytest.fixture(params=list(LABEL_CASES))
def case(request):
    return LABEL_CASES[request.param]


def get_label(client, case, user):
    return client.get(case["url"], headers=auth_header(user.id))


def put_label(client, case, user, body):
    return client.put(case["url"], json=body, headers=auth_header(user.id))


def test_get_label(client, get_user, case):
    response = get_label(client, case, get_user(case["user"]))

    assert response.status_code == 200
    assert response.json() == {"data": {"label": case["saved"]}}


def test_get_label_null_when_not_saved(client, get_user):
    response = get_label(client, LABEL_CASES["settlement"], get_user("instructor2@baeugo.test"))

    assert response.json() == {"data": {"label": None}}


def test_get_label_null_without_profile(client, db, get_user, case):
    user = get_user("learner1@baeugo.test")
    give_mode(db, user, case["mode"])

    response = get_label(client, case, user)

    assert response.status_code == 200
    assert response.json() == {"data": {"label": None}}


def test_put_label_trims_and_saves(client, get_user, case):
    user = get_user(case["user"])

    response = put_label(client, case, user, {"label": "  국민카드 (9876)  "})

    assert response.status_code == 200
    assert response.json() == {"data": {"label": "국민카드 (9876)"}}
    assert get_label(client, case, user).json()["data"]["label"] == "국민카드 (9876)"
    assert get_me(client, user)[case["me_key"]] == "국민카드 (9876)"


def test_put_label_accepts_100_characters(client, get_user, case):
    response = put_label(client, case, get_user(case["user"]), {"label": "가" * 100})

    assert response.status_code == 200
    assert response.json()["data"]["label"] == "가" * 100


@pytest.mark.parametrize(
    "body",
    [{"label": ""}, {"label": "   "}, {"label": None}, {}, {"label": "가" * 101}],
    ids=["empty", "blank", "null", "missing", "too-long"],
)
def test_put_label_validation(client, get_user, case, body):
    user = get_user(case["user"])

    response = put_label(client, case, user, body)

    assert_error(response, 400, "VALIDATION_ERROR", case["message"])
    assert get_label(client, case, user).json()["data"]["label"] == case["saved"]


def test_put_label_without_profile_requires_onboarding(client, db, get_user, case):
    user = get_user("learner1@baeugo.test")
    give_mode(db, user, case["mode"])

    response = put_label(client, case, user, {"label": "국민카드 (9876)"})

    assert_error(response, 409, "ONBOARDING_REQUIRED", ONBOARDING_REQUIRED_MESSAGE)


@pytest.mark.parametrize("method", ["get", "put"])
def test_label_forbidden_in_other_mode(client, get_user, case, method):
    user = get_user(case["other_mode_user"])

    response = client.request(
        method.upper(), case["url"], json={"label": "x"}, headers=auth_header(user.id)
    )

    assert_error(response, 403, "FORBIDDEN", case["forbidden"])


def test_label_forbidden_for_operator(client, get_user, case):
    response = get_label(client, case, get_user("admin@baeugo.test"))

    assert_error(response, 403, "FORBIDDEN", case["forbidden"])


def test_label_requires_login(client, case):
    assert_error(client.get(case["url"]), 401, "UNAUTHORIZED", "로그인이 필요합니다.")
