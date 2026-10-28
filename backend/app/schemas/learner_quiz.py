from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import CamelModel


class AttemptChoice(CamelModel):
    choice_no: int
    content: str


class AttemptQuestionData(CamelModel):
    attempt_question_id: int
    question_id: int
    order_no: int
    origin: str
    content: str
    choices: list[AttemptChoice]


class QuizAttemptData(CamelModel):
    attempt_id: int
    quiz_id: int | None
    quiz_type: str | None
    attempt_type: str
    attempt_no: int
    status: str
    total_questions: int
    next_order_no: int | None
    questions: list[AttemptQuestionData]


class CancelAttemptData(CamelModel):
    attempt_id: int
    status: str


class QuizActivitySession(CamelModel):
    session_key: str = Field(min_length=1, max_length=64)
    started_at: datetime
    ended_at: datetime
    active_seconds: int = Field(ge=0)


class QuizAnswerRequest(CamelModel):
    attempt_id: int = Field(gt=0)
    attempt_question_id: int = Field(gt=0)
    selected_choice_no: int = Field(ge=1, le=4)
    activity_session: QuizActivitySession


class QuizAnswerData(CamelModel):
    answer_id: int
    attempt_question_id: int
    question_id: int
    selected_choice_no: int
    is_correct: bool
    correct_choice_no: int
    explanation: str
    question_record_status: str
    attempt_status: str
    next_order_no: int | None
    has_unresolved_wrong_after_final: bool
    is_course_completed: bool


class CompletionData(CamelModel):
    course_id: int
    enrollment_id: int
    is_completed: bool
    status: str
    progress_percent: int
    unresolved_wrong_count: int
    first_completed_at: datetime | None
    current_completed_at: datetime | None
    message: str
    redirect_target: str | None


class QuizNoteCounts(CamelModel):
    all: int
    correct: int
    review_needed: int


class QuizNote(CamelModel):
    record_id: int
    enrollment_id: int
    course_id: int
    course_title: str
    thumbnail_storage_key: str | None
    thumbnail_url: str | None
    quiz_id: int
    quiz_type: str
    question_id: int
    question_content: str
    status: str
    wrong_count: int
    last_answer_id: int
    last_answered_at: datetime
    resolved_at: datetime | None


class QuizNotesData(CamelModel):
    counts: QuizNoteCounts
    quiz_notes: list[QuizNote]


ReviewStatus = Literal["ALL", "CORRECT", "REVIEW_NEEDED"]
CourseAttemptType = Literal["FINAL", "WRONG_REVIEW"]
