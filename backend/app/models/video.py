from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.course import Course, Section
    from app.models.user import User


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    course_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    video_type: Mapped[str] = mapped_column(String(10), nullable=False)  # OT, LESSON
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    video_storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    thumbnail_storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, default=0, server_default="0", nullable=False
    )
    stt_status: Mapped[str] = mapped_column(
        String(20), default="PENDING", server_default="PENDING", nullable=False
    )  # PENDING, PROCESSING, COMPLETED, FAILED
    transcript_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    view_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    like_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )

    course: Mapped[Course] = relationship("Course", back_populates="videos")
    section: Mapped[Section | None] = relationship("Section", back_populates="videos")
    scripts: Mapped[list[VideoScript]] = relationship(
        "VideoScript",
        back_populates="video",
        cascade="all, delete-orphan",
        order_by="VideoScript.seq",
    )


class VideoScript(Base):
    __tablename__ = "video_scripts"
    __table_args__ = (UniqueConstraint("video_id", "seq", name="uq_video_scripts_video_seq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    video: Mapped[Video] = relationship("Video", back_populates="scripts")


class VideoLike(Base):
    __tablename__ = "video_likes"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )

    user: Mapped[User] = relationship("User")
    video: Mapped[Video] = relationship("Video")


class VideoView(Base):
    __tablename__ = "video_views"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    view_date: Mapped[date] = mapped_column(Date, primary_key=True)
    viewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "Video",
    "VideoLike",
    "VideoScript",
    "VideoView",
]
