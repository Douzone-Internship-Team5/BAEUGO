from sqlalchemy import delete, select

from app.models import LearningGoal, UserRole
from tests.conftest import auth_header

ROLE_URL = "/api/v1/users/me/role"


def select_role(client, user, role):
    return client.post(ROLE_URL, json={"role": role}, headers=auth_header(user.id))


def owned_roles(db, user_id: int) -> list[str]:
    return sorted(db.scalars(select(UserRole.role).where(UserRole.user_id == user_id)))


def test_first_learner_selection_grants_role_and_mode(client, db, get_user):
    user = get_user("learner1@baeugo.test")

    response = select_role(client, user, "LEARNER")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "role": "LEARNER",
        "activeMode": "LEARNER",
        "roles": ["LEARNER"],
        "onboarded": False,
    }
    assert owned_roles(db, user.id) == ["LEARNER"]
    assert user.active_mode == "LEARNER"


def test_first_instructor_selection_grants_role_and_mode(client, db, get_user):
    user = get_user("learner1@baeugo.test")

    response = select_role(client, user, "INSTRUCTOR")

    assert response.json()["data"] == {
        "role": "INSTRUCTOR",
        "activeMode": "INSTRUCTOR",
        "roles": ["INSTRUCTOR"],
        "onboarded": False,
    }
    assert owned_roles(db, user.id) == ["INSTRUCTOR"]


def test_second_role_is_added_and_becomes_active(client, db, get_user):
    learner = get_user("learner2@baeugo.test")

    response = select_role(client, learner, "INSTRUCTOR")

    assert response.json()["data"] == {
        "role": "INSTRUCTOR",
        "activeMode": "INSTRUCTOR",
        "roles": ["LEARNER", "INSTRUCTOR"],
        "onboarded": False,
    }
    assert owned_roles(db, learner.id) == ["INSTRUCTOR", "LEARNER"]


def test_owned_role_only_switches_mode(client, db, get_user):
    learner = get_user("learner2@baeugo.test")
    select_role(client, learner, "INSTRUCTOR")

    response = select_role(client, learner, "LEARNER")

    assert response.json()["data"] == {
        "role": "LEARNER",
        "activeMode": "LEARNER",
        "roles": ["LEARNER", "INSTRUCTOR"],
        "onboarded": True,
    }
    assert owned_roles(db, learner.id) == ["INSTRUCTOR", "LEARNER"]


def test_selecting_same_role_twice_keeps_single_row(client, db, get_user):
    learner = get_user("learner2@baeugo.test")

    select_role(client, learner, "LEARNER")
    response = select_role(client, learner, "LEARNER")

    assert response.status_code == 200
    assert owned_roles(db, learner.id) == ["LEARNER"]


def test_learner_without_learning_goal_is_not_onboarded(client, db, get_user):
    learner = get_user("learner2@baeugo.test")
    db.execute(delete(LearningGoal).where(LearningGoal.learner_id == learner.id))
    db.expire_all()

    response = select_role(client, learner, "LEARNER")

    assert response.json()["data"]["onboarded"] is False


def test_operator_cannot_select_role(client, db, get_user):
    operator = get_user("admin@baeugo.test")

    response = select_role(client, operator, "LEARNER")

    assert response.status_code == 403
    assert response.json()["error"] == {
        "code": "FORBIDDEN",
        "message": "운영자 계정은 역할을 선택할 수 없습니다.",
    }
    assert owned_roles(db, operator.id) == []
    assert operator.active_mode is None


def test_invalid_role_value_is_rejected(client, get_user):
    response = select_role(client, get_user("learner1@baeugo.test"), "OPERATOR")

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["fields"] == {"role": "LEARNER 또는 INSTRUCTOR만 선택할 수 있습니다."}


def test_missing_role_is_rejected(client, get_user):
    user = get_user("learner1@baeugo.test")

    response = client.post(ROLE_URL, json={}, headers=auth_header(user.id))

    assert response.status_code == 400
    assert response.json()["error"]["fields"] == {"role": "필수 입력값입니다."}


def test_role_selection_requires_login(client):
    response = client.post(ROLE_URL, json={"role": "LEARNER"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
