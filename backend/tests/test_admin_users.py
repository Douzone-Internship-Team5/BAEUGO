from datetime import UTC, datetime, timedelta

import pytest

from app.core.timezone import today_in_seoul
from app.models import AttendanceDay, Role, UserRole
from tests.conftest import auth_header
from tests.factories import enroll, make_course

USERS_URL = "/api/v1/admin/users"
MEMBER_EMAILS = [
    "learner1@baeugo.test",
    "learner2@baeugo.test",
    "instructor1@baeugo.test",
    "instructor2@baeugo.test",
]
ALL_MEMBERS = "ALL_MEMBERS"
EMPTY_INSTRUCTOR_SUMMARY = {
    "courseCount": {"DRAFT": 0, "PUBLISHED": 0, "EDITING": 0, "OPERATOR_HIDDEN": 0},
    "totalEnrollments": 0,
}


@pytest.fixture
def operator_headers(get_user) -> dict[str, str]:
    return auth_header(get_user("admin@baeugo.test").id)


@pytest.fixture
def members_newest_first(get_user) -> list[str]:
    # 시드가 계정 id를 고정값으로 넣으므로, 넣은 순서가 아니라 실제 id로 기대 순서를 만든다.
    return sorted(MEMBER_EMAILS, key=lambda email: get_user(email).id, reverse=True)


def list_users(client, headers, **params):
    return client.get(USERS_URL, params=params, headers=headers)


def emails(response) -> list[str]:
    return [item["email"] for item in response.json()["data"]["items"]]


# ---------- OP-4 회원 목록 ----------


def test_list_returns_only_members_newest_first(client, operator_headers, members_newest_first):
    response = list_users(client, operator_headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert (data["page"], data["size"], data["total"]) == (1, 20, 4)
    assert emails(response) == members_newest_first


def test_list_item_matches_spec(client, operator_headers, get_user):
    learner = get_user("learner2@baeugo.test")

    response = list_users(client, operator_headers, q="learner2")

    assert response.json()["data"]["items"] == [
        {
            "userId": learner.id,
            "email": "learner2@baeugo.test",
            "name": "김배우",
            "accountType": "MEMBER",
            "role": "LEARNER",
            "activeMode": "LEARNER",
            "isActive": True,
        }
    ]


def test_member_without_role_has_null_role(client, operator_headers):
    item = list_users(client, operator_headers, q="learner1").json()["data"]["items"][0]

    assert (item["role"], item["activeMode"]) == (None, None)


def test_inactive_member_is_listed(client, db, operator_headers, get_user):
    get_user("learner1@baeugo.test").is_active = False
    db.flush()

    item = list_users(client, operator_headers, q="learner1").json()["data"]["items"][0]

    assert item["isActive"] is False


@pytest.mark.parametrize(
    ("role", "expected_prefix"),
    [("LEARNER", "learner2"), ("INSTRUCTOR", "instructor")],
)
def test_role_filter(client, operator_headers, members_newest_first, role, expected_prefix):
    expected = [email for email in members_newest_first if email.startswith(expected_prefix)]

    response = list_users(client, operator_headers, role=role)

    assert emails(response) == expected
    assert response.json()["data"]["total"] == len(expected)


def test_member_with_both_roles_appears_in_both_filters(client, db, operator_headers, get_user):
    db.add(UserRole(user_id=get_user("learner2@baeugo.test").id, role=Role.INSTRUCTOR))
    db.flush()

    learners = emails(list_users(client, operator_headers, role="LEARNER"))
    instructors = emails(list_users(client, operator_headers, role="INSTRUCTOR"))

    assert "learner2@baeugo.test" in learners
    assert "learner2@baeugo.test" in instructors


def test_invalid_role_filter_is_rejected(client, operator_headers):
    response = list_users(client, operator_headers, role="OPERATOR")

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["message"] == "유효하지 않은 역할 필터 값입니다."


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("김배", ["learner2@baeugo.test"]),
        ("INSTRUCTOR1@", ["instructor1@baeugo.test"]),
        ("baeugo.test", ALL_MEMBERS),
        ("   ", ALL_MEMBERS),
        ("admin", []),
        ("없는사람", []),
        ("%", []),
        ("_", []),
    ],
)
def test_search_by_name_or_email(client, operator_headers, members_newest_first, q, expected):
    if expected == ALL_MEMBERS:
        expected = members_newest_first

    response = list_users(client, operator_headers, q=q)

    assert response.status_code == 200
    assert emails(response) == expected


def test_search_combined_with_role_filter(client, operator_headers):
    response = list_users(client, operator_headers, q="instructor2", role="INSTRUCTOR")

    assert emails(response) == ["instructor2@baeugo.test"]


@pytest.mark.parametrize("page", [1, 2, 3])
def test_pagination(client, operator_headers, members_newest_first, page):
    response = list_users(client, operator_headers, page=page, size=2)

    data = response.json()["data"]
    assert emails(response) == members_newest_first[(page - 1) * 2 : page * 2]
    assert (data["page"], data["size"], data["total"]) == (page, 2, 4)


@pytest.mark.parametrize(
    "params",
    [{"page": 0}, {"size": 0}, {"size": 101}, {"q": "가" * 101}, {"page": "abc"}],
)
def test_invalid_paging_or_query_is_rejected(client, operator_headers, params):
    response = list_users(client, operator_headers, **params)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------- OP-5 회원 상세 ----------


def get_detail(client, headers, user_id):
    return client.get(f"{USERS_URL}/{user_id}", headers=headers)


def test_learner_detail_matches_spec(client, operator_headers, get_user):
    learner = get_user("learner2@baeugo.test")

    response = get_detail(client, operator_headers, learner.id)

    assert response.status_code == 200
    assert response.json()["data"] == {
        "userId": learner.id,
        "email": "learner2@baeugo.test",
        "name": "김배우",
        "accountType": "MEMBER",
        "role": "LEARNER",
        "activeMode": "LEARNER",
        "isActive": True,
        "onboarding": {
            "currentStatus": "EMPLOYED",
            "categoryId": 1,
            "jobCategory": "PLANNING_PM",
            "interestTopicIds": [11],
            "dailyGoalMinutes": 20,
            "timeZone": "Asia/Seoul",
            "timeSlots": [],
        },
        "learningSummary": {
            "inProgressCount": 0,
            "completedCount": 0,
            "lastStudiedAt": None,
            "attendanceStreak": 0,
        },
        "instructorSummary": EMPTY_INSTRUCTOR_SUMMARY,
    }


def test_instructor_detail_uses_instructor_onboarding(client, operator_headers, get_user):
    instructor = get_user("instructor1@baeugo.test")

    data = get_detail(client, operator_headers, instructor.id).json()["data"]

    assert data["role"] == "INSTRUCTOR"
    assert data["onboarding"] == {
        "categoryId": 6,
        "jobCategory": "OFFICE_PRODUCTIVITY",
        "lectureTopicIds": [61],
        "bio": "10년 차 사무 자동화 실무자",
    }
    assert data["instructorSummary"] == EMPTY_INSTRUCTOR_SUMMARY


def test_member_without_role_has_no_onboarding(client, operator_headers, get_user):
    data = get_detail(client, operator_headers, get_user("learner1@baeugo.test").id).json()["data"]

    assert (data["role"], data["activeMode"], data["onboarding"]) == (None, None, None)


def test_detail_includes_attendance_streak(client, db, operator_headers, get_user):
    learner = get_user("learner2@baeugo.test")
    today = today_in_seoul()
    for days_ago in (0, 1, 2):
        db.add(
            AttendanceDay(
                learner_id=learner.id,
                study_date=today - timedelta(days=days_ago),
                target_minutes_snapshot=20,
                active_seconds=1200,
                is_attended=True,
            )
        )
    db.flush()

    data = get_detail(client, operator_headers, learner.id).json()["data"]

    assert data["learningSummary"]["attendanceStreak"] == 3


def test_operator_account_is_not_found(client, operator_headers, get_user):
    response = get_detail(client, operator_headers, get_user("admin@baeugo.test").id)

    assert response.status_code == 404
    assert response.json()["error"] == {"code": "NOT_FOUND", "message": "존재하지 않는 회원입니다."}


def test_unknown_user_is_not_found(client, operator_headers):
    response = get_detail(client, operator_headers, 999_999)

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "존재하지 않는 회원입니다."


def test_non_numeric_user_id_is_rejected(client, operator_headers):
    response = get_detail(client, operator_headers, "abc")

    assert response.status_code == 400


# ---------- 권한 ----------


@pytest.mark.parametrize(
    "email", ["learner1@baeugo.test", "learner2@baeugo.test", "instructor1@baeugo.test"]
)
@pytest.mark.parametrize("path", ["", "/1"])
def test_members_are_forbidden(client, get_user, email, path):
    response = client.get(f"{USERS_URL}{path}", headers=auth_header(get_user(email).id))

    assert response.status_code == 403
    assert response.json()["error"] == {"code": "FORBIDDEN", "message": "운영자 권한이 필요합니다."}


@pytest.mark.parametrize("path", ["", "/1"])
def test_login_is_required(client, path):
    response = client.get(f"{USERS_URL}{path}")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


# ---------- OP-5 학습·강사 요약 ----------


def test_learning_summary_counts_enrollments(client, db, operator_headers, get_user):
    learner = get_user("learner2@baeugo.test")
    instructor = get_user("instructor2@baeugo.test")
    latest = datetime(2026, 10, 23, 8, 30, tzinfo=UTC)
    first, second, third = (make_course(db, instructor, title=f"강좌 {n}") for n in range(3))
    enroll(db, learner, first, last_studied_at=latest)
    enroll(db, learner, second)
    enroll(db, learner, third, status="COMPLETED", last_studied_at=latest - timedelta(days=2))

    summary = get_detail(client, operator_headers, learner.id).json()["data"]["learningSummary"]

    assert datetime.fromisoformat(summary.pop("lastStudiedAt")) == latest
    assert summary == {"inProgressCount": 2, "completedCount": 1, "attendanceStreak": 0}


def test_instructor_summary_counts_courses_and_enrollments(client, db, operator_headers, get_user):
    instructor = get_user("instructor1@baeugo.test")
    published = [make_course(db, instructor, title=f"공개 {n}") for n in range(2)]
    make_course(db, instructor, title="작성 중", status="DRAFT")
    make_course(db, instructor, title="운영자 비공개", status="OPERATOR_HIDDEN")
    make_course(db, get_user("instructor2@baeugo.test"), title="다른 강사 강좌")
    enroll(db, get_user("learner1@baeugo.test"), published[0])
    enroll(db, get_user("learner2@baeugo.test"), published[0])
    enroll(db, get_user("learner2@baeugo.test"), published[1])

    data = get_detail(client, operator_headers, instructor.id).json()["data"]

    assert data["instructorSummary"] == {
        "courseCount": {"DRAFT": 1, "PUBLISHED": 2, "EDITING": 0, "OPERATOR_HIDDEN": 1},
        "totalEnrollments": 3,
    }
