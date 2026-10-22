from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LearnerProfile(Base):
    __tablename__ = "learner_profiles"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    current_status: Mapped[str] = mapped_column(String(30))
    target_category_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("categories.id"))
    demo_payment_label: Mapped[str | None] = mapped_column(String(100))


class InstructorProfile(Base):
    __tablename__ = "instructor_profiles"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    primary_category_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("categories.id"))
    bio: Mapped[str] = mapped_column(Text)
    demo_settlement_label: Mapped[str | None] = mapped_column(String(100))


class LearnerTopicInterest(Base):
    __tablename__ = "learner_topic_interests"

    learner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    topic_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("topics.id"), primary_key=True)


class InstructorTopic(Base):
    __tablename__ = "instructor_topics"

    instructor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    topic_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("topics.id"), primary_key=True)
