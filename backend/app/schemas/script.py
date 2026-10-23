from __future__ import annotations

from datetime import datetime

from app.schemas.common import CamelModel


# ==========================================
# [B-7] 영상 대본
# ==========================================
class ScriptSegment(CamelModel):
    seq: int
    start_ms: int
    end_ms: int
    content: str


class ScriptUpdateRequest(CamelModel):
    segments: list[ScriptSegment]


class ScriptResponseData(CamelModel):
    video_id: int
    stt_status: str
    transcript_reviewed_at: datetime | None = None
    segments: list[ScriptSegment]


class ScriptRegenerateResponseData(CamelModel):
    video_id: int
    stt_status: str
    message: str


__all__ = [
    "ScriptRegenerateResponseData",
    "ScriptResponseData",
    "ScriptSegment",
    "ScriptUpdateRequest",
]
