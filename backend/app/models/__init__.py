# 새 모델 파일을 만들면 여기에서 import해야 Alembic이 테이블을 인식한다.
from app.models.attendance import AttendanceDay
from app.models.base import Base, TimestampMixin
from app.models.category import Category, Topic
from app.models.course import Course, Section
from app.models.goal import LearningGoal, LearningTimeSlot
from app.models.learning import Enrollment, StudyActivitySession, VideoProgress
from app.models.profile import (
    InstructorProfile,
    InstructorTopic,
    LearnerProfile,
    LearnerTopicInterest,
)
from app.models.quiz import (
    AttemptQuestion,
    Question,
    QuestionAnswer,
    QuestionChoice,
    QuestionRecord,
    Quiz,
    QuizAttempt,
)
from app.models.stat import CourseDailyStat
from app.models.user import AccountType, Role, User, UserRole
from app.models.video import Video, VideoLike, VideoScript, VideoView

__all__ = [
    "AccountType",
    "AttemptQuestion",
    "AttendanceDay",
    "Base",
    "Category",
    "Course",
    "CourseDailyStat",
    "Enrollment",
    "InstructorProfile",
    "InstructorTopic",
    "LearnerProfile",
    "LearnerTopicInterest",
    "Question",
    "QuestionAnswer",
    "QuestionChoice",
    "QuestionRecord",
    "Quiz",
    "QuizAttempt",
    "LearningGoal",
    "LearningTimeSlot",
    "Role",
    "Section",
    "StudyActivitySession",
    "TimestampMixin",
    "Topic",
    "User",
    "UserRole",
    "Video",
    "VideoLike",
    "VideoProgress",
    "VideoScript",
    "VideoView",
]
