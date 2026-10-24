from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import CamelModel
from app.schemas.user import InstructorOnboarding, LearnerOnboarding


class AdminUserItem(CamelModel):
    user_id: int
    email: str
    name: str
    account_type: str
    role: str | None
    active_mode: str | None
    is_active: bool


class LearningSummary(CamelModel):
    in_progress_count: int
    completed_count: int
    last_studied_at: datetime | None
    attendance_streak: int


# 키가 강좌 상태값(DRAFT 등) 그대로여야 해서 camelCase 변환을 하지 않는다.
class CourseStatusCount(BaseModel):
    DRAFT: int = 0
    PUBLISHED: int = 0
    EDITING: int = 0
    OPERATOR_HIDDEN: int = 0


class InstructorSummary(CamelModel):
    course_count: CourseStatusCount
    total_enrollments: int


class AdminUserDetail(CamelModel):
    user_id: int
    email: str
    name: str
    account_type: str
    role: str | None
    active_mode: str | None
    is_active: bool
    onboarding: LearnerOnboarding | InstructorOnboarding | None
    learning_summary: LearningSummary
    instructor_summary: InstructorSummary
