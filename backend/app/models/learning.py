from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.course import Course, Question, Video
    from app.models.user import User


class Enrollment(Base, TimestampMixin):
    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("course_id", "learner_id", name="uq_enrollments_course_learner"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    course_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    learner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="IN_PROGRESS", nullable=False
    )  # IN_PROGRESS, COMPLETED
    access_source: Mapped[str] = mapped_column(String(20), default="DEMO_GRANT", nullable=False)
    price_snapshot_krw: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    course: Mapped[Course] = relationship("Course", back_populates="enrollments")
    learner: Mapped[User] = relationship("User", foreign_keys=[learner_id])
    video_progresses: Mapped[list[VideoProgress]] = relationship(
        "VideoProgress", back_populates="enrollment", cascade="all, delete-orphan"
    )


class VideoProgress(Base, TimestampMixin):
    __tablename__ = "video_progress"
    __table_args__ = (
        UniqueConstraint("enrollment_id", "video_id", name="uq_video_progress_enrollment_video"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("enrollments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    watched_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_position_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    watched_ranges: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    enrollment: Mapped[Enrollment] = relationship("Enrollment", back_populates="video_progresses")
    video: Mapped[Video] = relationship("Video")


class VideoLike(Base):
    __tablename__ = "video_likes"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class QuizAttempt(Base, TimestampMixin):
    __tablename__ = "quiz_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    quiz_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("quizzes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    learner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # UNIT, FINAL, WRONG_REVIEW
    attempt_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="IN_PROGRESS", nullable=False
    )  # IN_PROGRESS, SUBMITTED, CANCELLED

    attempt_questions: Mapped[list[AttemptQuestion]] = relationship(
        "AttemptQuestion", back_populates="attempt", cascade="all, delete-orphan"
    )


class AttemptQuestion(Base, TimestampMixin):
    __tablename__ = "attempt_questions"
    __table_args__ = (
        UniqueConstraint("attempt_id", "order_no", name="uq_attempt_questions_attempt_order"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    attempt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_attempts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_no: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = mapped_column(String(20), default="BASE", nullable=False)

    attempt: Mapped[QuizAttempt] = relationship("QuizAttempt", back_populates="attempt_questions")
    question: Mapped[Question] = relationship("Question")
    answer: Mapped[QuestionAnswer | None] = relationship(
        "QuestionAnswer",
        back_populates="attempt_question",
        uselist=False,
        cascade="all, delete-orphan",
    )


class QuestionAnswer(Base, TimestampMixin):
    __tablename__ = "question_answers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    attempt_question_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("attempt_questions.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    selected_choice_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)

    attempt_question: Mapped[AttemptQuestion] = relationship(
        "AttemptQuestion", back_populates="answer"
    )


class QuestionRecord(Base, TimestampMixin):
    __tablename__ = "question_records"
    __table_args__ = (
        UniqueConstraint("learner_id", "question_id", name="uq_question_records_learner_question"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    learner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # CORRECT, REVIEW_NEEDED
    wrong_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_answer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    learner: Mapped[User] = relationship("User")
    question: Mapped[Question] = relationship("Question")
