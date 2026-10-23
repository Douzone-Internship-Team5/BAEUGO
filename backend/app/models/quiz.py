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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.course import Course, Section
    from app.models.learning import Enrollment
    from app.models.user import User


class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
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
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )

    course: Mapped[Course] = relationship("Course", back_populates="quizzes")
    section: Mapped[Section | None] = relationship("Section", back_populates="quizzes")
    questions: Mapped[list[Question]] = relationship(
        "Question",
        back_populates="quiz",
        cascade="all, delete-orphan",
        order_by="Question.order_no",
    )


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (UniqueConstraint("quiz_id", "order_no", name="uq_questions_quiz_order"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    quiz_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    correct_choice_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 1~4
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    generated_by: Mapped[str] = mapped_column(
        String(20), default="AI", server_default="AI", nullable=False
    )  # AI, INSTRUCTOR
    order_no: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )

    quiz: Mapped[Quiz] = relationship("Quiz", back_populates="questions")
    choices: Mapped[list[QuestionChoice]] = relationship(
        "QuestionChoice",
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="QuestionChoice.choice_no",
    )


class QuestionChoice(Base):
    __tablename__ = "question_choices"

    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True
    )
    choice_no: Mapped[int] = mapped_column(SmallInteger, primary_key=True)  # 1~4
    content: Mapped[str] = mapped_column(String(500), nullable=False)

    question: Mapped[Question] = relationship("Question", back_populates="choices")


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("enrollments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quiz_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("quizzes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    attempt_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # UNIT, FINAL, WRONG_REVIEW
    attempt_no: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="IN_PROGRESS", server_default="IN_PROGRESS", nullable=False
    )  # IN_PROGRESS, SUBMITTED, CANCELLED
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    enrollment: Mapped[Enrollment] = relationship("Enrollment", back_populates="quiz_attempts")
    attempt_questions: Mapped[list[AttemptQuestion]] = relationship(
        "AttemptQuestion", back_populates="attempt", cascade="all, delete-orphan"
    )


class AttemptQuestion(Base):
    __tablename__ = "attempt_questions"
    __table_args__ = (
        UniqueConstraint("attempt_id", "order_no", name="uq_attempt_questions_attempt_order"),
        UniqueConstraint("attempt_id", "question_id", name="uq_attempt_questions_attempt_question"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    attempt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quiz_attempts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_no: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = mapped_column(
        String(20), default="BASE", server_default="BASE", nullable=False
    )

    attempt: Mapped[QuizAttempt] = relationship("QuizAttempt", back_populates="attempt_questions")
    question: Mapped[Question] = relationship("Question")
    answer: Mapped[QuestionAnswer | None] = relationship(
        "QuestionAnswer",
        back_populates="attempt_question",
        uselist=False,
        cascade="all, delete-orphan",
    )


class QuestionAnswer(Base):
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
    answered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    attempt_question: Mapped[AttemptQuestion] = relationship(
        "AttemptQuestion", back_populates="answer"
    )


class QuestionRecord(Base):
    __tablename__ = "question_records"
    __table_args__ = (
        UniqueConstraint(
            "enrollment_id", "question_id", name="uq_question_records_enrollment_question"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("enrollments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # CORRECT, REVIEW_NEEDED
    last_answer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("question_answers.id", ondelete="CASCADE"), nullable=False
    )
    wrong_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    enrollment: Mapped[Enrollment] = relationship("Enrollment", back_populates="question_records")
    question: Mapped[Question] = relationship("Question")
    last_answer: Mapped[QuestionAnswer] = relationship(
        "QuestionAnswer", foreign_keys=[last_answer_id]
    )


__all__ = [
    "AttemptQuestion",
    "Question",
    "QuestionAnswer",
    "QuestionChoice",
    "QuestionRecord",
    "Quiz",
    "QuizAttempt",
]
