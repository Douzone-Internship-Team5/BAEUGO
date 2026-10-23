from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

# Re-exports for backward compatibility
from app.models.quiz import (
    Question,
    QuestionChoice,
    Quiz,
)
from app.models.stat import CourseDailyStat
from app.models.video import (
    Video,
    VideoScript,
)

if TYPE_CHECKING:
    from app.models.category import Category, Topic
    from app.models.learning import Enrollment
    from app.models.user import User


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    instructor_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="DRAFT", server_default="DRAFT", nullable=False, index=True
    )  # DRAFT, PUBLISHED, EDITING, OPERATOR_HIDDEN
    closed_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    price_krw: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    thumbnail_storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    course_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, default=1, server_default="1", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )

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


__all__ = [
    "Course",
    "Section",
    # re-exports for backward compatibility
    "CourseDailyStat",
    "Question",
    "QuestionChoice",
    "Quiz",
    "Video",
    "VideoScript",
]
