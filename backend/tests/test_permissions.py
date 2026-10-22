from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth import InstructorUser, LearnerUser, OperatorUser
from app.core.database import get_db
from app.core.errors import register_error_handlers
from app.models import Role, UserRole
from tests.conftest import auth_header

# 권한 의존성만 확인하기 위한 테스트 전용 앱
probe_app = FastAPI()
register_error_handlers(probe_app)


@probe_app.get("/operator-only")
def operator_only(user: OperatorUser) -> dict:
    return {"id": user.id}


@probe_app.get("/learner-only")
def learner_only(user: LearnerUser) -> dict:
    return {"id": user.id}


@probe_app.get("/instructor-only")
def instructor_only(user: InstructorUser) -> dict:
    return {"id": user.id}


@pytest.fixture
def probe_client(db) -> Generator[TestClient, None, None]:
    probe_app.dependency_overrides[get_db] = lambda: db
    with TestClient(probe_app) as test_client:
        yield test_client
    probe_app.dependency_overrides.clear()


def request_as(probe_client, get_user, email: str, path: str):
    return probe_client.get(path, headers=auth_header(get_user(email).id))


def assert_forbidden(response) -> None:
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_operator_passes_operator_only(probe_client, get_user):
    response = request_as(probe_client, get_user, "admin@baeugo.test", "/operator-only")

    assert response.status_code == 200
    assert response.json() == {"id": get_user("admin@baeugo.test").id}


@pytest.mark.parametrize(
    "email", ["learner1@baeugo.test", "learner2@baeugo.test", "instructor1@baeugo.test"]
)
def test_member_is_forbidden_from_operator_only(probe_client, get_user, email):
    assert_forbidden(request_as(probe_client, get_user, email, "/operator-only"))


def test_operator_only_without_token_is_unauthorized(probe_client):
    response = probe_client.get("/operator-only")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_learner_mode_passes_learner_only(probe_client, get_user):
    response = request_as(probe_client, get_user, "learner2@baeugo.test", "/learner-only")

    assert response.status_code == 200


def test_instructor_mode_passes_instructor_only(probe_client, get_user):
    response = request_as(probe_client, get_user, "instructor1@baeugo.test", "/instructor-only")

    assert response.status_code == 200


def test_learner_is_forbidden_from_instructor_only(probe_client, get_user):
    response = request_as(probe_client, get_user, "learner2@baeugo.test", "/instructor-only")

    assert_forbidden(response)
    assert response.json()["error"]["message"] == "강사 모드에서만 이용할 수 있습니다."


def test_member_without_role_is_forbidden_from_mode_only(probe_client, get_user):
    assert_forbidden(request_as(probe_client, get_user, "learner1@baeugo.test", "/learner-only"))


@pytest.mark.parametrize("path", ["/learner-only", "/instructor-only"])
def test_operator_is_forbidden_from_mode_only(probe_client, get_user, path):
    assert_forbidden(request_as(probe_client, get_user, "admin@baeugo.test", path))


def test_role_owner_in_other_mode_is_forbidden(probe_client, db, get_user):
    learner = get_user("learner2@baeugo.test")
    db.add(UserRole(user_id=learner.id, role=Role.INSTRUCTOR))
    db.flush()
    db.refresh(learner)

    instructor_response = request_as(probe_client, get_user, learner.email, "/instructor-only")
    learner_response = request_as(probe_client, get_user, learner.email, "/learner-only")

    assert_forbidden(instructor_response)
    assert learner_response.status_code == 200


def test_active_mode_without_owned_role_is_forbidden(probe_client, db, get_user):
    learner = get_user("learner2@baeugo.test")
    learner.active_mode = Role.INSTRUCTOR
    db.flush()

    response = request_as(probe_client, get_user, learner.email, "/instructor-only")

    assert_forbidden(response)
