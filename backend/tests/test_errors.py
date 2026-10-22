from datetime import date, time
from typing import Literal

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import Field

from app.core.errors import register_error_handlers
from app.schemas.common import CamelModel

# 공통 검증 에러 핸들러만 확인하기 위한 테스트 전용 앱
probe_app = FastAPI()
register_error_handlers(probe_app)


class ProbeBody(CamelModel):
    name: str = Field(min_length=5, max_length=10)
    daily_goal_minutes: int = Field(ge=10, le=60)
    topic_ids: list[int] = Field(min_length=1)
    role: Literal["LEARNER", "INSTRUCTOR"]
    start_time: time | None = None
    study_date: date | None = None
    is_public: bool | None = None


@probe_app.post("/probe")
def probe(body: ProbeBody) -> dict:
    return {}


client = TestClient(probe_app)

VALID_BODY = {"name": "가나다라마", "dailyGoalMinutes": 20, "topicIds": [11], "role": "LEARNER"}


def error_fields(body) -> dict[str, str]:
    response = client.post("/probe", json=body)
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["message"] == "입력값을 확인해 주세요."
    return error["fields"]


def test_missing_fields_use_korean_reason():
    fields = error_fields({})

    assert fields == {
        "name": "필수 입력값입니다.",
        "dailyGoalMinutes": "필수 입력값입니다.",
        "topicIds": "필수 입력값입니다.",
        "role": "필수 입력값입니다.",
    }


def test_length_and_range_reasons_include_limits():
    too_short = error_fields({**VALID_BODY, "name": "가나", "dailyGoalMinutes": 5, "topicIds": []})
    too_long = error_fields({**VALID_BODY, "name": "가" * 11, "dailyGoalMinutes": 70})

    assert too_short == {
        "name": "5자 이상 입력해야 합니다.",
        "dailyGoalMinutes": "10 이상이어야 합니다.",
        "topicIds": "1개 이상이어야 합니다.",
    }
    assert too_long == {
        "name": "10자 이하로 입력해야 합니다.",
        "dailyGoalMinutes": "60 이하여야 합니다.",
    }


def test_type_reasons_are_korean():
    fields = error_fields(
        {
            "name": 123,
            "dailyGoalMinutes": "abc",
            "topicIds": "11",
            "role": "OPERATOR",
            "startTime": "25:99",
            "studyDate": "2026-13-40",
            "isPublic": "maybe",
        }
    )

    assert fields == {
        "name": "문자열이어야 합니다.",
        "dailyGoalMinutes": "정수여야 합니다.",
        "topicIds": "배열이어야 합니다.",
        "role": "허용되지 않는 값입니다. (허용값: 'LEARNER' or 'INSTRUCTOR')",
        "startTime": "시각 형식(HH:MM:SS)이 올바르지 않습니다.",
        "studyDate": "날짜 형식(YYYY-MM-DD)이 올바르지 않습니다.",
        "isPublic": "true 또는 false여야 합니다.",
    }


def test_list_item_location_is_joined_with_dot():
    fields = error_fields({**VALID_BODY, "topicIds": [11, "abc"]})

    assert fields == {"topicIds.1": "정수여야 합니다."}


def test_invalid_json_body():
    response = client.post(
        "/probe", content="{not json", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 400
    assert "JSON 형식이 올바르지 않습니다." in response.json()["error"]["fields"].values()
