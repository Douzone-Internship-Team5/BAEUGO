from datetime import time

from sqlalchemy import BigInteger, ForeignKey, Integer, SmallInteger, String, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

DEFAULT_TIME_ZONE = "Asia/Seoul"


class LearningGoal(Base):
    __tablename__ = "learning_goals"

    learner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    daily_target_minutes: Mapped[int] = mapped_column(Integer)
    time_zone: Mapped[str] = mapped_column(
        String(50), default=DEFAULT_TIME_ZONE, server_default=DEFAULT_TIME_ZONE
    )

    time_slots: Mapped[list["LearningTimeSlot"]] = relationship(
        cascade="all, delete-orphan",
        order_by="(LearningTimeSlot.sort_order, LearningTimeSlot.id)",
    )


class LearningTimeSlot(Base):
    __tablename__ = "learning_time_slots"
    __table_args__ = (UniqueConstraint("learner_id", "start_time", "end_time"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    learner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("learning_goals.learner_id"))
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    sort_order: Mapped[int] = mapped_column(SmallInteger)
