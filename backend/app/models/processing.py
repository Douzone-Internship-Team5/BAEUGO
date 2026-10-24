from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProcessingJob(Base):
    """실행별 이력. id는 이전 작업의 늦은 저장을 차단하는 토큰으로도 사용한다."""

    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    video_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    section_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sections.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(10))  # VIDEO, QUIZ
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    input_snapshot: Mapped[dict | None] = mapped_column(JSON)
    artifacts: Mapped[dict | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
