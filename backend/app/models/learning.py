from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
# Re-exports for backward compatibility
from app.models.quiz import (
    AttemptQuestion,
    QuestionAnswer,
    QuestionRecord,
    QuizAttempt,
)
from app.models.video import VideoLike, VideoView

if TYPE_CHECKING:
    from app.models.course import Course
    from app.models.video import Video
    from app.models.user import User


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("learner_id", "course_id", name="uq_enrollments_course_learner"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    learner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    course_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    access_source: Mapped[str] = mapped_column(
        String(20), default="DEMO_GRANT", server_default="DEMO_GRANT", nullable=False
    )
    price_snapshot_krw: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), default="IN_PROGRESS", server_default="IN_PROGRESS", nullable=False
    )  # IN_PROGRESS, COMPLETED
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    first_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_studied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    course: Mapped[Course] = relationship("Course", back_populates="enrollments")
    learner: Mapped[User] = relationship("User", foreign_keys=[learner_id])
    video_progresses: Mapped[list[VideoProgress]] = relationship(
        "VideoProgress", back_populates="enrollment", cascade="all, delete-orphan"
    )
    quiz_attempts: Mapped[list[QuizAttempt]] = relationship(
        "QuizAttempt", back_populates="enrollment", cascade="all, delete-orphan"
    )
    question_records: Mapped[list[QuestionRecord]] = relationship(
        "QuestionRecord", back_populates="enrollment", cascade="all, delete-orphan"
    )


class VideoProgress(Base):
    __tablename__ = "video_progress"

    enrollment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("enrollments.id", ondelete="CASCADE"), primary_key=True
    )
    video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    watched_ranges: Mapped[Any] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    watched_seconds: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    last_position_seconds: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    is_completed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    enrollment: Mapped[Enrollment] = relationship("Enrollment", back_populates="video_progresses")
    video: Mapped[Video] = relationship("Video")


class StudyActivitySession(Base):
    __tablename__ = "study_activity_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("enrollments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    activity_type: Mapped[str] = mapped_column(String(20), nullable=False)  # VIDEO, QUIZ
    video_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("videos.id", ondelete="CASCADE"), nullable=True, index=True
    )
    quiz_attempt_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("quiz_attempts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    session_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    study_date: Mapped[date] = mapped_column(Date, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active_seconds: Mapped[int] = mapped_column(Integer, nullable=False)

    enrollment: Mapped[Enrollment] = relationship("Enrollment")
    video: Mapped[Video | None] = relationship("Video")
    quiz_attempt: Mapped[QuizAttempt | None] = relationship("QuizAttempt")


__all__ = [
    "Enrollment",
    "StudyActivitySession",
    "VideoProgress",
    # re-exports for backward compatibility
    "AttemptQuestion",
    "QuestionAnswer",
    "QuestionRecord",
    "QuizAttempt",
    "VideoLike",
    "VideoView",
]
