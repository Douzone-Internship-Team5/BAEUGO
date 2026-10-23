from datetime import date

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


# 출석 집계(G 영역)가 쓰는 테이블. A-4는 오늘 행의 목표 스냅샷만 다시 계산한다.
class AttendanceDay(Base):
    __tablename__ = "attendance_days"

    learner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    study_date: Mapped[date] = mapped_column(Date, primary_key=True)
    target_minutes_snapshot: Mapped[int] = mapped_column(Integer)
    active_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_attended: Mapped[bool] = mapped_column(Boolean)
