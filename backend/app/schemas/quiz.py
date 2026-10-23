from __future__ import annotations

from datetime import datetime

from app.schemas.common import CamelModel


# ==========================================
# [B-8] 퀴즈
# ==========================================
class QuestionItem(CamelModel):
    question_id: int | None = None
    order_no: int | None = None
    generated_by: str | None = None
    content: str
    choices: list[str]
    correct_choice_no: int
    explanation: str


class QuizUpdateRequest(CamelModel):
    questions: list[QuestionItem]


class QuizDetailResponseData(CamelModel):
    quiz_id: int
    course_id: int
    section_id: int | None = None
    quiz_type: str
    ai_status: str | None = None
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    questions: list[QuestionItem]


class QuizRegenerateResponseData(CamelModel):
    quiz_id: int
    section_id: int | None = None
    quiz_type: str
    ai_status: str
    message: str


__all__ = [
    "QuestionItem",
    "QuizDetailResponseData",
    "QuizRegenerateResponseData",
    "QuizUpdateRequest",
]
