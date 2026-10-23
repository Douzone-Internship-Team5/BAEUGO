from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import CamelModel
from app.schemas.feed import (
    FeedCourseInfo,
    FeedInstructorInfo,
    FeedOtItem,
)
from app.schemas.search import (
    CategorySimple,
    CourseSearchItem,
    TopicSimple,
)


# ==========================================
# [B-1] 강사 강좌 목록
# ==========================================
class ProcessingInfo(CamelModel):
    completed: int
    total: int


class InstructorCourseItem(CamelModel):
    course_id: int
    title: str
    thumbnail_storage_key: str | None = None
    thumbnail_url: str | None = None
    category_id: int
    topic_id: int
    difficulty: str
    price_krw: int
    status: str
    closed_reason: str | None = None
    published_at: datetime | None = None
    section_count: int
    video_count: int
    enrollment_count: int
    processing: ProcessingInfo


class CourseCounts(BaseModel):
    DRAFT: int = 0
    PUBLISHED: int = 0
    EDITING: int = 0
    OPERATOR_HIDDEN: int = 0


class InstructorCourseListResponseData(CamelModel):
    counts: CourseCounts
    items: list[InstructorCourseItem]
    page: int
    size: int
    total: int


# ==========================================
# [B-2] 강좌 편집용 상세
# ==========================================
class InstructorVideoSummary(CamelModel):
    video_id: int
    video_type: str
    title: str
    duration_seconds: int
    sort_order: int
    stt_status: str
    transcript_reviewed_at: datetime | None = None


class InstructorUnitQuizSummary(CamelModel):
    quiz_id: int
    quiz_type: str
    section_id: int | None = None
    ai_status: str | None = None
    reviewed_at: datetime | None = None
    question_count: int


class InstructorSectionDetail(CamelModel):
    section_id: int
    title: str
    sort_order: int
    videos: list[InstructorVideoSummary]
    unit_quiz: InstructorUnitQuizSummary | None = None


class PublishCheck(CamelModel):
    is_ready: bool
    missing: list[str]


class InstructorCourseDetail(CamelModel):
    course_id: int
    status: str
    closed_reason: str | None = None
    published_at: datetime | None = None
    title: str
    description: str
    category_id: int
    topic_id: int
    difficulty: str
    price_krw: int
    thumbnail_storage_key: str | None = None
    thumbnail_url: str | None = None
    total_lesson_duration_seconds: int
    ot_video: InstructorVideoSummary | None = None
    sections: list[InstructorSectionDetail]
    final_quiz: InstructorUnitQuizSummary | None = None
    publish_check: PublishCheck


# ==========================================
# [B-3] 강좌 생성 · 수정 · 삭제
# ==========================================
class CourseCreateRequest(CamelModel):
    title: str
    description: str
    category_id: int
    topic_id: int
    difficulty: str
    price_krw: int = 0


class CourseUpdateRequest(CamelModel):
    title: str | None = None
    description: str | None = None
    category_id: int | None = None
    topic_id: int | None = None
    difficulty: str | None = None
    price_krw: int | None = None


class CourseMutationResponseData(CamelModel):
    course_id: int
    status: str
    title: str
    category_id: int
    topic_id: int
    difficulty: str
    price_krw: int
    thumbnail_storage_key: str | None = None
    thumbnail_url: str | None = None


class CourseDeleteResponseData(CamelModel):
    deleted_course_id: int
    deleted: bool = True


# ==========================================
# [B-4] 강좌 상태 변경 (공개 / 수정 시작)
# ==========================================
class CourseStatusUpdateRequest(CamelModel):
    status: str


class CourseStatusUpdateResponseData(CamelModel):
    course_id: int
    status: str
    published_at: datetime | None = None
    closed_by_user_id: int | None = None
    closed_at: datetime | None = None


__all__ = [
    # B-1 ~ B-4 강사 강좌 관리
    "CourseCounts",
    "CourseCreateRequest",
    "CourseDeleteResponseData",
    "CourseMutationResponseData",
    "CourseStatusUpdateRequest",
    "CourseStatusUpdateResponseData",
    "CourseUpdateRequest",
    "InstructorCourseDetail",
    "InstructorCourseItem",
    "InstructorCourseListResponseData",
    "InstructorSectionDetail",
    "InstructorUnitQuizSummary",
    "InstructorVideoSummary",
    "ProcessingInfo",
    "PublishCheck",
    # B-10 무료 OT 피드 (re-export for backward compatibility)
    "FeedCourseInfo",
    "FeedInstructorInfo",
    "FeedOtItem",
    # B-11 강좌 검색 · 필터 · 추천 (re-export for backward compatibility)
    "CategorySimple",
    "CourseSearchItem",
    "TopicSimple",
]
