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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, BigIntId, TimestampMixin

if TYPE_CHECKING:
    from app.models.category import Category, Topic
    from app.models.learning import Enrollment
    from app.models.user import User


class Course(Base, TimestampMixin):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    instructor_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    category_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    topic_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("topics.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # BEGINNER, INTERMEDIATE, ADVANCED
    price_krw: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="DRAFT", nullable=False, index=True
    )  # DRAFT, PUBLISHED, EDITING, OPERATOR_HIDDEN
    thumbnail_storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    instructor: Mapped[User] = relationship("User", foreign_keys=[instructor_id])
    category: Mapped[Category] = relationship("Category", foreign_keys=[category_id])
    topic: Mapped[Topic] = relationship("Topic", foreign_keys=[topic_id])

    sections: Mapped[list[Section]] = relationship(
        "Section",
        back_populates="course",
        cascade="all, delete-orphan",
        order_by="Section.sort_order",
    )
    videos: Mapped[list[Video]] = relationship(
        "Video", back_populates="course", cascade="all, delete-orphan", order_by="Video.sort_order"
    )
    quizzes: Mapped[list[Quiz]] = relationship(
        "Quiz", back_populates="course", cascade="all, delete-orphan"
    )
    daily_stats: Mapped[list[CourseDailyStat]] = relationship(
        "CourseDailyStat", back_populates="course", cascade="all, delete-orphan"
    )
    enrollments: Mapped[list[Enrollment]] = relationship(
        "Enrollment", back_populates="course", cascade="all, delete-orphan"
    )


class Section(Base, TimestampMixin):
    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    course: Mapped[Course] = relationship("Course", back_populates="sections")
    videos: Mapped[list[Video]] = relationship(
        "Video", back_populates="section", cascade="all, delete-orphan", order_by="Video.sort_order"
    )
    quizzes: Mapped[list[Quiz]] = relationship(
        "Quiz", back_populates="section", cascade="all, delete-orphan"
    )

    @property
    def unit_quiz(self) -> Quiz | None:
        return next(
            (q for q in self.quizzes if q.quiz_type == "UNIT" and q.is_active),
            None,
        )


class Video(Base, TimestampMixin):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    video_type: Mapped[str] = mapped_column(String(10), nullable=False)  # OT, LESSON
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    video_storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    thumbnail_storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stt_status: Mapped[str] = mapped_column(
        String(20), default="PENDING", nullable=False
    )  # PENDING, PROCESSING, COMPLETED, FAILED
    transcript_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    like_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    course: Mapped[Course] = relationship("Course", back_populates="videos")
    section: Mapped[Section | None] = relationship("Section", back_populates="videos")
    scripts: Mapped[list[VideoScript]] = relationship(
        "VideoScript",
        back_populates="video",
        cascade="all, delete-orphan",
        order_by="VideoScript.seq",
    )


class VideoScript(Base, TimestampMixin):
    __tablename__ = "video_scripts"
    __table_args__ = (UniqueConstraint("video_id", "seq", name="uq_video_scripts_video_seq"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    video: Mapped[Video] = relationship("Video", back_populates="scripts")


class Quiz(Base, TimestampMixin):
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    quiz_type: Mapped[str] = mapped_column(String(10), nullable=False)  # UNIT, FINAL
    ai_status: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # PENDING, PROCESSING, COMPLETED, FAILED (FINAL is None)
    reviewed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    course: Mapped[Course] = relationship("Course", back_populates="quizzes")
    section: Mapped[Section | None] = relationship("Section", back_populates="quizzes")
    questions: Mapped[list[Question]] = relationship(
        "Question",
        back_populates="quiz",
        cascade="all, delete-orphan",
        order_by="Question.order_no",
    )


class Question(Base, TimestampMixin):
    __tablename__ = "questions"
    __table_args__ = (UniqueConstraint("quiz_id", "order_no", name="uq_questions_quiz_order"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    quiz_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_no: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_by: Mapped[str] = mapped_column(
        String(20), default="AI", nullable=False
    )  # AI, INSTRUCTOR
    content: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    correct_choice_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 1~4
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    quiz: Mapped[Quiz] = relationship("Quiz", back_populates="questions")
    choices: Mapped[list[QuestionChoice]] = relationship(
        "QuestionChoice",
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="QuestionChoice.choice_no",
    )


class QuestionChoice(Base, TimestampMixin):
    __tablename__ = "question_choices"
    __table_args__ = (
        UniqueConstraint("question_id", "choice_no", name="uq_question_choices_question_choice"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    choice_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 1~4
    content: Mapped[str] = mapped_column(Text, nullable=False)

    question: Mapped[Question] = relationship("Question", back_populates="choices")


class CourseDailyStat(Base, TimestampMixin):
    __tablename__ = "course_daily_stats"
    __table_args__ = (
        UniqueConstraint("course_id", "start_date", name="uq_course_daily_stats_course_date"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    lesson_views: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    new_students: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expected_revenue: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_quiz_cnt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_submitted_cnt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_answer_cnt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_correct_cnt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    course: Mapped[Course] = relationship("Course", back_populates="daily_stats")
