from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.profile import InstructorProfile, LearnerProfile


class AccountType(StrEnum):
    MEMBER = "MEMBER"
    OPERATOR = "OPERATOR"


class Role(StrEnum):
    LEARNER = "LEARNER"
    INSTRUCTOR = "INSTRUCTOR"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(50))
    account_type: Mapped[str] = mapped_column(String(20))
    active_mode: Mapped[str | None] = mapped_column(String(20))
    profile_image_key: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    roles: Mapped[list["UserRole"]] = relationship(cascade="all, delete-orphan")
    learner_profile: Mapped["LearnerProfile | None"] = relationship()
    instructor_profile: Mapped["InstructorProfile | None"] = relationship()

    @property
    def is_login_allowed(self) -> bool:
        return self.is_active and self.deleted_at is None

    def has_role(self, role: str) -> bool:
        return any(user_role.role == role for user_role in self.roles)


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), primary_key=True)
