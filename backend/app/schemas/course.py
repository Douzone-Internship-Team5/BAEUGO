from __future__ import annotations

from app.schemas.common import CamelModel


# ==========================================
# [B-10] 무료 OT 숏폼 피드
# ==========================================
class FeedCourseInfo(CamelModel):
    course_id: int
    title: str
    description: str
    category_name: str
    difficulty: str
    price_krw: int
    is_enrolled: bool


class FeedInstructorInfo(CamelModel):
    id: int
    name: str
    bio: str | None = None
    profile_image_url: str | None = None


class FeedOtItem(CamelModel):
    video_id: int
    video_url: str
    video_storage_key: str
    thumbnail_url: str | None = None
    thumbnail_storage_key: str | None = None
    title: str
    duration_seconds: int
    view_count: int
    like_count: int
    is_liked: bool
    course: FeedCourseInfo
    instructor: FeedInstructorInfo


# ==========================================
# [B-11] 강좌 검색 · 필터 · 추천
# ==========================================
class CategorySimple(CamelModel):
    id: int
    code: str
    name: str


class TopicSimple(CamelModel):
    id: int
    name: str


class CourseSearchItem(CamelModel):
    course_id: int
    title: str
    thumbnail_storage_key: str | None = None
    thumbnail_url: str | None = None
    instructor_id: int
    instructor_name: str
    category: CategorySimple
    topic: TopicSimple
    difficulty: str
    price_krw: int
    is_free: bool
    enrollment_count: int
