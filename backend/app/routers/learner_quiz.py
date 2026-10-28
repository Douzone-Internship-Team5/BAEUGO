from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.core.auth import LearnerUser
from app.core.database import DbSession
from app.schemas.common import DataResponse
from app.schemas.learner_quiz import (
    CancelAttemptData,
    CompletionData,
    CourseAttemptType,
    QuizAttemptData,
    QuizNotesData,
    ReviewStatus,
)
from app.services import learner_quiz as quiz_service

router = APIRouter(tags=["learner-quiz"])


@router.get("/sections/{id}/quiz", response_model=DataResponse[QuizAttemptData])
def get_unit_attempt(id: int, current_user: LearnerUser, db: DbSession):
    return DataResponse(data=quiz_service.get_unit_attempt(db, current_user.id, id))


@router.get("/courses/{id}/quiz-attempt", response_model=DataResponse[QuizAttemptData])
def get_course_attempt(
    id: int,
    current_user: LearnerUser,
    db: DbSession,
    attempt_type: Annotated[CourseAttemptType, Query(alias="attemptType")],
    question_id: Annotated[int | None, Query(alias="questionId", gt=0)] = None,
):
    return DataResponse(
        data=quiz_service.get_course_attempt(db, current_user.id, id, attempt_type, question_id)
    )


@router.post("/quiz-attempts/{attemptId}/cancel", response_model=DataResponse[CancelAttemptData])
def cancel_attempt(
    attempt_id: Annotated[int, Path(alias="attemptId")], current_user: LearnerUser, db: DbSession
):
    return DataResponse(data=quiz_service.cancel_attempt(db, current_user.id, attempt_id))


@router.get("/courses/{id}/completion", response_model=DataResponse[CompletionData])
def get_completion(id: int, current_user: LearnerUser, db: DbSession):
    return DataResponse(data=quiz_service.get_completion(db, current_user.id, id))


@router.get("/me/quiz-notes", response_model=DataResponse[QuizNotesData])
def get_quiz_notes(current_user: LearnerUser, db: DbSession, status: ReviewStatus = "ALL"):
    return DataResponse(data=quiz_service.get_quiz_notes(db, current_user.id, status))
