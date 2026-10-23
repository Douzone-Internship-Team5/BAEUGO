from datetime import time, timedelta

import pytest
from sqlalchemy import select

from app.core.timezone import today_in_seoul
from app.models import (
    AttendanceDay,
    InstructorTopic,
    LearnerProfile,
    LearnerTopicInterest,
    LearningGoal,
    LearningTimeSlot,
    Role,
    Topic,
    UserRole,
)
from tests.conftest import auth_header

ONBOARDING_URL = "/api/v1/users/me/onboarding"
TIME_SLOT_MESSAGE = "종료 시각은 시작 시각보다 늦어야 하며 중복 시간대를 등록할 수 없습니다."


def learner_payload(**overrides) -> dict:
    payload = {
        "categoryId": 1,
        "currentStatus": "EMPLOYED",
        "interestTopicIds": [11, 12],
        "dailyGoalMinutes": 20,
    }
    payload.update(overrides)
    return payload


def instructor_payload(**overrides) -> dict:
    payload = {"categoryId": 6, "lectureTopicIds": [61, 62], "bio": "10년 차 사무 자동화 실무자"}
    payload.update(overrides)
    return payload


def put_onboarding(client, user, payload):
    return client.put(ONBOARDING_URL, json=payload, headers=auth_header(user.id))


def give_mode(db, user, role: Role) -> None:
    if not user.has_role(role):
        db.add(UserRole(user_id=user.id, role=role))
    user.active_mode = role
    db.flush()
    db.expire(user, ["roles"])


def validation_fields(response) -> dict:
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    return error["fields"]


@pytest.fixture
def new_learner(db, get_user):
    user = get_user("learner1@baeugo.test")
    give_mode(db, user, Role.LEARNER)
    return user


@pytest.fixture
def new_instructor(db, get_user):
    user = get_user("learner1@baeugo.test")
    give_mode(db, user, Role.INSTRUCTOR)
    return user


@pytest.fixture
def learner(get_user):
    return get_user("learner2@baeugo.test")


# ---------- 성공 ----------


def test_learner_first_onboarding_matches_spec(client, db, new_learner):
    payload = learner_payload(
        timeSlots=[{"startTime": "08:00:00", "endTime": "09:00:00", "sortOrder": 1}]
    )

    response = put_onboarding(client, new_learner, payload)

    assert response.status_code == 200
    data = response.json()["data"]
    slot_id = data["onboarding"]["timeSlots"][0]["id"]
    assert data == {
        "activeMode": "LEARNER",
        "onboarded": True,
        "onboarding": {
            "currentStatus": "EMPLOYED",
            "categoryId": 1,
            "jobCategory": "PLANNING_PM",
            "interestTopicIds": [11, 12],
            "dailyGoalMinutes": 20,
            "timeZone": "Asia/Seoul",
            "timeSlots": [
                {"id": slot_id, "startTime": "08:00:00", "endTime": "09:00:00", "sortOrder": 1}
            ],
        },
    }
    assert db.get(LearnerProfile, new_learner.id).target_category_id == 1
    assert db.get(LearningGoal, new_learner.id).daily_target_minutes == 20


def test_instructor_first_onboarding_matches_spec(client, db, new_instructor):
    response = put_onboarding(client, new_instructor, instructor_payload())

    assert response.status_code == 200
    assert response.json()["data"] == {
        "activeMode": "INSTRUCTOR",
        "onboarded": True,
        "onboarding": {
            "categoryId": 6,
            "jobCategory": "OFFICE_PRODUCTIVITY",
            "lectureTopicIds": [61, 62],
            "bio": "10년 차 사무 자동화 실무자",
        },
    }
    topic_ids = db.scalars(
        select(InstructorTopic.topic_id).where(InstructorTopic.instructor_id == new_instructor.id)
    ).all()
    assert sorted(topic_ids) == [61, 62]


def test_learner_onboarding_update_replaces_previous_values(client, db, learner):
    payload = learner_payload(
        categoryId=3, currentStatus="CAREER_CHANGE", interestTopicIds=[31, 33], dailyGoalMinutes=40
    )

    response = put_onboarding(client, learner, payload)

    onboarding = response.json()["data"]["onboarding"]
    assert onboarding["categoryId"] == 3
    assert onboarding["jobCategory"] == "DATA_ANALYSIS"
    assert onboarding["currentStatus"] == "CAREER_CHANGE"
    assert onboarding["interestTopicIds"] == [31, 33]
    assert onboarding["dailyGoalMinutes"] == 40
    saved_topic_ids = db.scalars(
        select(LearnerTopicInterest.topic_id).where(LearnerTopicInterest.learner_id == learner.id)
    ).all()
    assert sorted(saved_topic_ids) == [31, 33]


def test_instructor_onboarding_update_replaces_previous_values(client, db, get_user):
    instructor = get_user("instructor1@baeugo.test")

    response = put_onboarding(
        client, instructor, instructor_payload(categoryId=4, lectureTopicIds=[43], bio="Git 강의")
    )

    assert response.json()["data"]["onboarding"] == {
        "categoryId": 4,
        "jobCategory": "DEV_IT",
        "lectureTopicIds": [43],
        "bio": "Git 강의",
    }


def test_name_is_trimmed_and_updated(client, learner):
    response = put_onboarding(client, learner, learner_payload(name="  새 이름  "))

    assert response.status_code == 200
    assert learner.name == "새 이름"


def test_name_is_kept_when_not_sent(client, learner):
    put_onboarding(client, learner, learner_payload())

    assert learner.name == "김배우"


def test_duplicate_topic_ids_are_saved_once(client, learner):
    response = put_onboarding(client, learner, learner_payload(interestTopicIds=[11, 12, 11]))

    assert response.json()["data"]["onboarding"]["interestTopicIds"] == [11, 12]


def test_job_category_code_is_accepted_instead_of_id(client, learner):
    payload = learner_payload(jobCategory="MARKETING", interestTopicIds=[21])
    del payload["categoryId"]

    response = put_onboarding(client, learner, payload)

    assert response.status_code == 200
    assert response.json()["data"]["onboarding"]["categoryId"] == 2


def test_matching_category_id_and_code_are_accepted(client, learner):
    response = put_onboarding(client, learner, learner_payload(jobCategory="PLANNING_PM"))

    assert response.status_code == 200


def test_dual_role_member_saves_only_current_mode(client, db, learner):
    give_mode(db, learner, Role.INSTRUCTOR)
    learner_profile_before = db.get(LearnerProfile, learner.id).current_status

    response = put_onboarding(
        client, learner, {**instructor_payload(), "currentStatus": "JOB_SEEKER"}
    )

    assert response.json()["data"]["activeMode"] == "INSTRUCTOR"
    assert db.get(LearnerProfile, learner.id).current_status == learner_profile_before
    assert learner.instructor_profile is not None


def test_learner_mode_ignores_instructor_fields(client, learner):
    response = put_onboarding(client, learner, learner_payload(bio="1", lectureTopicIds=[999]))

    assert response.status_code == 200
    assert learner.instructor_profile is None


# ---------- 선호 학습 시간대 ----------


def saved_slots(db, learner_id: int) -> list[tuple[time, time, int]]:
    slots = db.scalars(
        select(LearningTimeSlot)
        .where(LearningTimeSlot.learner_id == learner_id)
        .order_by(LearningTimeSlot.start_time)
    )
    return [(slot.start_time, slot.end_time, slot.sort_order) for slot in slots]


def test_time_slots_are_replaced(client, db, learner):
    put_onboarding(
        client, learner, learner_payload(timeSlots=[{"startTime": "08:00", "endTime": "09:00"}])
    )

    put_onboarding(
        client,
        learner,
        learner_payload(
            timeSlots=[
                {"startTime": "21:00", "endTime": "22:00"},
                {"startTime": "07:00", "endTime": "07:30"},
            ]
        ),
    )

    assert saved_slots(db, learner.id) == [
        (time(7, 0), time(7, 30), 2),
        (time(21, 0), time(22, 0), 1),
    ]


def test_time_slots_are_kept_when_not_sent(client, db, learner):
    put_onboarding(
        client, learner, learner_payload(timeSlots=[{"startTime": "08:00", "endTime": "09:00"}])
    )

    response = put_onboarding(client, learner, learner_payload())

    assert len(response.json()["data"]["onboarding"]["timeSlots"]) == 1
    assert saved_slots(db, learner.id) == [(time(8, 0), time(9, 0), 1)]


def test_empty_time_slots_clear_all(client, db, learner):
    put_onboarding(
        client, learner, learner_payload(timeSlots=[{"startTime": "08:00", "endTime": "09:00"}])
    )

    response = put_onboarding(client, learner, learner_payload(timeSlots=[]))

    assert response.json()["data"]["onboarding"]["timeSlots"] == []
    assert saved_slots(db, learner.id) == []


def test_same_time_slot_can_be_sent_again(client, learner):
    payload = learner_payload(timeSlots=[{"startTime": "08:00", "endTime": "09:00"}])
    put_onboarding(client, learner, payload)

    response = put_onboarding(client, learner, payload)

    assert response.status_code == 200


def test_touching_time_slots_are_allowed(client, learner):
    payload = learner_payload(
        timeSlots=[
            {"startTime": "08:00", "endTime": "09:00"},
            {"startTime": "09:00", "endTime": "10:00"},
        ]
    )

    response = put_onboarding(client, learner, payload)

    assert response.status_code == 200


@pytest.mark.parametrize(
    "time_slots",
    [
        [{"startTime": "09:00", "endTime": "09:00"}],
        [{"startTime": "10:00", "endTime": "09:00"}],
        [
            {"startTime": "08:00", "endTime": "09:00"},
            {"startTime": "08:00", "endTime": "09:00"},
        ],
        [
            {"startTime": "08:00", "endTime": "09:00"},
            {"startTime": "08:30", "endTime": "09:30"},
        ],
        [
            {"startTime": "07:00", "endTime": "12:00"},
            {"startTime": "08:00", "endTime": "09:00"},
        ],
    ],
    ids=["same-start-end", "end-before-start", "exact-duplicate", "overlap", "contains"],
)
def test_invalid_time_slots_are_rejected(client, db, learner, time_slots):
    response = put_onboarding(client, learner, learner_payload(timeSlots=time_slots))

    assert validation_fields(response) == {"timeSlots": TIME_SLOT_MESSAGE}
    assert saved_slots(db, learner.id) == []


def test_time_slot_with_time_zone_is_rejected(client, learner):
    payload = learner_payload(timeSlots=[{"startTime": "08:00:00+09:00", "endTime": "09:00"}])

    fields = validation_fields(put_onboarding(client, learner, payload))

    assert fields == {"timeSlots.0.startTime": "시각은 HH:MM:SS 형식으로 입력해 주세요."}


# ---------- 오늘 출석 재계산 ----------


def add_attendance(db, learner_id, study_date, *, target_minutes, active_seconds):
    attendance = AttendanceDay(
        learner_id=learner_id,
        study_date=study_date,
        target_minutes_snapshot=target_minutes,
        active_seconds=active_seconds,
        is_attended=active_seconds >= target_minutes * 60,
    )
    db.add(attendance)
    db.flush()
    return attendance


@pytest.mark.parametrize(
    ("new_goal", "expected_attended"),
    [(30, False), (20, True), (10, True)],
)
def test_today_attendance_is_recalculated(client, db, learner, new_goal, expected_attended):
    today = add_attendance(
        db, learner.id, today_in_seoul(), target_minutes=20, active_seconds=25 * 60
    )

    put_onboarding(client, learner, learner_payload(dailyGoalMinutes=new_goal))

    assert today.target_minutes_snapshot == new_goal
    assert today.is_attended is expected_attended


def test_past_attendance_is_not_changed(client, db, learner):
    yesterday = add_attendance(
        db,
        learner.id,
        today_in_seoul() - timedelta(days=1),
        target_minutes=20,
        active_seconds=25 * 60,
    )

    put_onboarding(client, learner, learner_payload(dailyGoalMinutes=60))

    assert yesterday.target_minutes_snapshot == 20
    assert yesterday.is_attended is True


def test_today_attendance_is_not_created(client, db, learner):
    put_onboarding(client, learner, learner_payload(dailyGoalMinutes=30))

    assert db.get(AttendanceDay, (learner.id, today_in_seoul())) is None


# ---------- 에러 ----------


def test_member_without_role_gets_role_not_set(client, get_user):
    response = put_onboarding(client, get_user("learner1@baeugo.test"), learner_payload())

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "ROLE_NOT_SET",
        "message": "역할을 먼저 선택한 후 온보딩을 진행할 수 있습니다.",
    }


def test_operator_is_forbidden(client, get_user):
    response = put_onboarding(client, get_user("admin@baeugo.test"), learner_payload())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_onboarding_requires_login(client):
    response = client.put(ONBOARDING_URL, json=learner_payload())

    assert response.status_code == 401


def test_learner_required_fields(client, new_learner):
    fields = validation_fields(put_onboarding(client, new_learner, {}))

    assert fields == {
        "categoryId": "필수 입력값입니다.",
        "interestTopicIds": "주제를 1개 이상 선택해 주세요.",
        "currentStatus": "필수 입력값입니다.",
        "dailyGoalMinutes": "필수 입력값입니다.",
    }


def test_instructor_required_fields(client, new_instructor):
    fields = validation_fields(put_onboarding(client, new_instructor, {}))

    assert fields == {
        "categoryId": "필수 입력값입니다.",
        "lectureTopicIds": "주제를 1개 이상 선택해 주세요.",
        "bio": "필수 입력값입니다.",
    }


def test_invalid_current_status(client, learner):
    fields = validation_fields(put_onboarding(client, learner, learner_payload(currentStatus="X")))

    assert fields == {
        "currentStatus": "허용되지 않는 값입니다. (허용값: JOB_SEEKER, EMPLOYED, CAREER_CHANGE)"
    }


@pytest.mark.parametrize("minutes", [0, 15, 70])
def test_daily_goal_minutes_must_be_a_choice(client, learner, minutes):
    response = put_onboarding(client, learner, learner_payload(dailyGoalMinutes=minutes))

    assert validation_fields(response) == {
        "dailyGoalMinutes": "10, 20, 30, 40, 50, 60 중 하나여야 합니다."
    }


def test_topic_from_other_category_is_rejected(client, learner):
    response = put_onboarding(client, learner, learner_payload(interestTopicIds=[11, 21]))

    assert validation_fields(response) == {
        "interestTopicIds": "선택한 직무에 속하지 않는 주제가 포함되어 있습니다."
    }


def test_lecture_topic_from_other_category_is_rejected(client, new_instructor):
    response = put_onboarding(client, new_instructor, instructor_payload(lectureTopicIds=[11]))

    assert validation_fields(response) == {
        "lectureTopicIds": "선택한 직무에 속하지 않는 주제가 포함되어 있습니다."
    }


def test_unknown_topic_is_rejected(client, learner):
    response = put_onboarding(client, learner, learner_payload(interestTopicIds=[11, 999]))

    assert validation_fields(response) == {
        "interestTopicIds": "존재하지 않는 주제가 포함되어 있습니다."
    }


def test_inactive_topic_is_rejected(client, db, learner):
    db.get(Topic, 12).is_active = False
    db.flush()

    response = put_onboarding(client, learner, learner_payload(interestTopicIds=[12]))

    assert validation_fields(response) == {
        "interestTopicIds": "존재하지 않는 주제가 포함되어 있습니다."
    }


def test_empty_topic_list_is_rejected(client, learner):
    response = put_onboarding(client, learner, learner_payload(interestTopicIds=[]))

    assert validation_fields(response) == {"interestTopicIds": "주제를 1개 이상 선택해 주세요."}


def test_unknown_category_id_is_rejected(client, learner):
    response = put_onboarding(client, learner, learner_payload(categoryId=999))

    assert validation_fields(response) == {"categoryId": "존재하지 않는 직무입니다."}


def test_unknown_job_category_code_is_rejected(client, learner):
    payload = learner_payload(jobCategory="UNKNOWN")
    del payload["categoryId"]

    assert validation_fields(put_onboarding(client, learner, payload)) == {
        "jobCategory": "존재하지 않는 직무입니다."
    }


def test_mismatched_category_id_and_code_are_rejected(client, learner):
    response = put_onboarding(client, learner, learner_payload(jobCategory="MARKETING"))

    assert validation_fields(response) == {
        "jobCategory": "categoryId와 jobCategory가 서로 다른 직무입니다."
    }


@pytest.mark.parametrize("bio", ["1234", "   abcd   ", ""])
def test_short_bio_is_rejected(client, new_instructor, bio):
    response = put_onboarding(client, new_instructor, instructor_payload(bio=bio))

    assert validation_fields(response) == {"bio": "강사 약력은 5자 이상 입력해야 합니다."}


@pytest.mark.parametrize("name", ["", "   ", "가" * 51])
def test_invalid_name_is_rejected(client, learner, name):
    response = put_onboarding(client, learner, learner_payload(name=name))

    assert validation_fields(response) == {"name": "이름은 1~50자로 입력해 주세요."}


def test_wrong_type_uses_common_validation_message(client, learner):
    response = put_onboarding(client, learner, learner_payload(interestTopicIds="11"))

    assert validation_fields(response) == {"interestTopicIds": "배열이어야 합니다."}


def test_failed_validation_saves_nothing(client, db, learner):
    response = put_onboarding(
        client, learner, learner_payload(name="바뀐 이름", categoryId=3, dailyGoalMinutes=15)
    )

    assert response.status_code == 400
    assert learner.name == "김배우"
    assert db.get(LearnerProfile, learner.id).target_category_id == 1
    assert db.get(LearningGoal, learner.id).daily_target_minutes == 20
