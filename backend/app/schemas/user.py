from datetime import time

from pydantic import Field, field_validator
from pydantic_core import PydanticCustomError

from app.models.user import Role
from app.schemas.common import CamelModel


class RoleRequest(CamelModel):
    role: str

    @field_validator("role")
    @classmethod
    def check_role(cls, value: str) -> str:
        if value not in (Role.LEARNER, Role.INSTRUCTOR):
            raise PydanticCustomError("role", "LEARNER 또는 INSTRUCTOR만 선택할 수 있습니다.")
        return value


class RoleResponse(CamelModel):
    role: str
    active_mode: str
    roles: list[str]
    onboarded: bool


class TimeSlotInput(CamelModel):
    start_time: time
    end_time: time
    sort_order: int | None = Field(default=None, ge=1, le=32767)

    @field_validator("start_time", "end_time")
    @classmethod
    def reject_time_zone(cls, value: time) -> time:
        # DB 컬럼이 time(시간대 없음)이고, 시간대가 붙은 값과는 크기 비교도 할 수 없다.
        if value.tzinfo is not None:
            raise PydanticCustomError("time_zone", "시각은 HH:MM:SS 형식으로 입력해 주세요.")
        return value


class OnboardingRequest(CamelModel):
    """현재 모드(수강자/강사)에 따라 필요한 항목이 달라서, 필수 여부는 서비스에서 확인한다."""

    name: str | None = None
    category_id: int | None = None
    job_category: str | None = None
    current_status: str | None = None
    interest_topic_ids: list[int] | None = None
    daily_goal_minutes: int | None = None
    time_slots: list[TimeSlotInput] | None = None
    lecture_topic_ids: list[int] | None = None
    bio: str | None = None


class TimeSlotItem(CamelModel):
    id: int
    start_time: time
    end_time: time
    sort_order: int


class LearnerOnboarding(CamelModel):
    current_status: str
    category_id: int | None
    job_category: str | None
    interest_topic_ids: list[int]
    daily_goal_minutes: int | None
    time_zone: str | None
    time_slots: list[TimeSlotItem]


class InstructorOnboarding(CamelModel):
    category_id: int
    job_category: str
    lecture_topic_ids: list[int]
    bio: str


class OnboardingResponse(CamelModel):
    active_mode: str
    onboarded: bool
    onboarding: LearnerOnboarding | InstructorOnboarding | None
