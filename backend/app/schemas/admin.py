from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import CamelModel
from app.schemas.quiz import QuestionItem
from app.schemas.script import ScriptSegment
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


# ---------- OP-1 강좌 목록 ----------


class AdminCourseItem(CamelModel):
    course_id: int
    title: str
    instructor_id: int
    instructor_name: str
    category_id: int
    job_category: str
    status: str
    price_krw: int
    enrollment_count: int
    published_at: datetime | None
    closed_by_user_id: int | None
    closed_at: datetime | None
    closed_reason: str | None


# ---------- OP-2 강좌 상세 ----------


class AdminCourseInstructor(CamelModel):
    id: int
    name: str
    email: str
    bio: str | None
    profile_image_key: str | None
    profile_image_url: str | None


class AdminScriptSegment(ScriptSegment):
    id: int


class AdminVideo(CamelModel):
    video_id: int
    video_type: str
    title: str
    duration_seconds: int
    sort_order: int
    video_storage_key: str
    video_url: str
    thumbnail_storage_key: str | None
    thumbnail_url: str | None
    stt_status: str
    transcript_reviewed_at: datetime | None
    segments: list[AdminScriptSegment]


class AdminQuiz(CamelModel):
    quiz_id: int
    quiz_type: str
    section_id: int | None
    ai_status: str | None
    reviewed_by: int | None
    reviewed_at: datetime | None
    questions: list[QuestionItem]


class AdminSection(CamelModel):
    section_id: int
    title: str
    sort_order: int
    videos: list[AdminVideo]
    unit_quiz: AdminQuiz | None


class AdminCourseDetail(CamelModel):
    course_id: int
    status: str
    title: str
    description: str
    category_id: int
    category_code: str
    category_name: str
    topic_id: int
    topic_name: str
    difficulty: str
    price_krw: int
    thumbnail_storage_key: str | None
    thumbnail_url: str | None
    published_at: datetime | None
    closed_at: datetime | None
    closed_reason: str | None
    closed_by_user_id: int | None
    instructor: AdminCourseInstructor
    total_lesson_duration_seconds: int
    total_lesson_count: int
    enrollment_count: int
    ot_video: AdminVideo | None
    sections: list[AdminSection]
    final_quiz: AdminQuiz | None


# ---------- OP-3 강좌 내리기 ----------


class UnpublishRequest(CamelModel):
    # 비어 있을 때도 명세 문구로 응답하려고 필수 여부는 서비스에서 확인한다.
    reason: str | None = None


class UnpublishResponse(CamelModel):
    course_id: int
    status: str
    closed_by_user_id: int
    closed_reason: str
    closed_at: datetime
