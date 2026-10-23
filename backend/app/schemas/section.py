from __future__ import annotations

from app.schemas.common import CamelModel


# ==========================================
# [B-5] 섹션 관리
# ==========================================
class SectionCreateRequest(CamelModel):
    course_id: int
    title: str


class SectionUpdateRequest(CamelModel):
    title: str | None = None
    sort_order: int | None = None


class SectionResponseData(CamelModel):
    section_id: int
    course_id: int
    title: str
    sort_order: int
    is_active: bool


class SectionDeleteResponseData(CamelModel):
    section_id: int
    course_id: int
    is_active: bool = False


__all__ = [
    "SectionCreateRequest",
    "SectionDeleteResponseData",
    "SectionResponseData",
    "SectionUpdateRequest",
]
