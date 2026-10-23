# 새 모델 파일을 만들면 여기에서 import해야 Alembic이 테이블을 인식한다.
from app.models.attendance import AttendanceDay
from app.models.base import Base
from app.models.category import Category, Topic
from app.models.goal import LearningGoal, LearningTimeSlot
from app.models.profile import (
    InstructorProfile,
    InstructorTopic,
    LearnerProfile,
    LearnerTopicInterest,
)
from app.models.user import AccountType, Role, User, UserRole

__all__ = [
    "AccountType",
    "AttendanceDay",
    "Base",
    "Category",
    "InstructorProfile",
    "InstructorTopic",
    "LearnerProfile",
    "LearnerTopicInterest",
    "LearningGoal",
    "LearningTimeSlot",
    "Role",
    "Topic",
    "User",
    "UserRole",
]
