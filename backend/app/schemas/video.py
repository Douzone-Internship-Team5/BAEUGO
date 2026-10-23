from __future__ import annotations

from datetime import datetime

from app.schemas.common import CamelModel
from app.schemas.script import (
    ScriptRegenerateResponseData,
    ScriptResponseData,
    ScriptSegment,
    ScriptUpdateRequest,
)


# ==========================================
# [B-6] 영상 관리
# ==========================================
class VideoResponseData(CamelModel):
    video_id: int
    course_id: int
    section_id: int | None = None
    video_type: str
    title: str
    duration_seconds: int
    sort_order: int
    stt_status: str
    transcript_reviewed_at: datetime | None = None
    is_active: bool


class VideoDeleteResponseData(CamelModel):
    video_id: int
    course_id: int
    is_active: bool = False


__all__ = [
    # B-6 영상 관리
    "VideoDeleteResponseData",
    "VideoResponseData",
    # B-7 영상 대본 (re-export)
    "ScriptRegenerateResponseData",
    "ScriptResponseData",
    "ScriptSegment",
    "ScriptUpdateRequest",
]
