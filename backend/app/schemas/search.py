from __future__ import annotations

from app.schemas.common import CamelModel


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


__all__ = [
    "CategorySimple",
    "CourseSearchItem",
    "TopicSimple",
]
