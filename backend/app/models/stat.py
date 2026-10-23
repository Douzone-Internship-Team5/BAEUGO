from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Date, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.course import Course


class CourseDailyStat(Base):
    __tablename__ = "course_daily_stats"

    course_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True
    )
    start_date: Mapped[date] = mapped_column(Date, primary_key=True)
    lesson_views: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    new_students: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    expected_revenue: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    available_quiz_cnt: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    first_submitted_cnt: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    first_answer_cnt: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    first_correct_cnt: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    course: Mapped[Course] = relationship("Course", back_populates="daily_stats")


__all__ = ["CourseDailyStat"]
