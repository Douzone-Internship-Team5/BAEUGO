from datetime import datetime

from pydantic import Field

from app.schemas.common import CamelModel


class PlaybackUrlData(CamelModel):
    video_id: int
    playback_url: str = Field(description="해당 영상에만 사용할 수 있는 5분 유효 재생 URL")
    expires_at: datetime = Field(
        description="만료 전 재발급 후 재생 위치를 유지해 교체해야 하는 시각",
    )
