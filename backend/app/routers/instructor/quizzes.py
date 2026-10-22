from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.core.auth import (
    InstructorUser,
    verify_course_ownership,
    verify_section_ownership,
)
from app.core.database import DbSession
from app.core.errors import AppError
from app.models.course import Question, QuestionChoice, Quiz
from app.models.learning import AttemptQuestion, QuestionRecord
from app.schemas.common import DataResponse
from app.schemas.instructor import (
    QuestionItem,
    QuizDetailResponseData,
    QuizRegenerateResponseData,
    QuizUpdateRequest,
)

router = APIRouter(tags=["instructor-quizzes"])


# ==========================================
# [B-8] 단위 · 종합 퀴즈 조회 · 수정 · AI 재생성
# ==========================================
@router.get("/courses/{course_id}/quiz", response_model=DataResponse[QuizDetailResponseData])
def get_quiz_detail(
    course_id: int,
    db: DbSession,
    current_user: InstructorUser,
    section_id: Annotated[
        int | None, Query(alias="sectionId", description="섹션 ID (단위 퀴즈 조회 시 전달)")
    ] = None,
):
    course = verify_course_ownership(course_id, current_user.id, db)

    if section_id is not None:
        verify_section_ownership(section_id, current_user.id, db, course_id=course.id)
        quiz = db.scalar(
            select(Quiz).where(
                Quiz.course_id == course.id,
                Quiz.section_id == section_id,
                Quiz.quiz_type == "UNIT",
                Quiz.is_active.is_(True),
            )
        )
    else:
        quiz = db.scalar(
            select(Quiz).where(
                Quiz.course_id == course.id,
                Quiz.section_id.is_(None),
                Quiz.quiz_type == "FINAL",
                Quiz.is_active.is_(True),
            )
        )

    if not quiz:
        raise AppError(404, "NOT_FOUND", "강좌 또는 섹션을 찾을 수 없습니다.")

    active_questions = [q for q in quiz.questions if q.is_active]
    question_dtos: list[QuestionItem] = []
    for q in active_questions:
        choices = [c.content for c in sorted(q.choices, key=lambda x: x.choice_no)]
        question_dtos.append(
            QuestionItem(
                question_id=q.id,
                order_no=q.order_no,
                generated_by=q.generated_by,
                content=q.content,
                choices=choices,
                correct_choice_no=q.correct_choice_no,
                explanation=q.explanation,
            )
        )

    return DataResponse(
        data=QuizDetailResponseData(
            quiz_id=quiz.id,
            course_id=quiz.course_id,
            section_id=quiz.section_id,
            quiz_type=quiz.quiz_type,
            ai_status=quiz.ai_status,
            reviewed_by=quiz.reviewed_by,
            reviewed_at=quiz.reviewed_at,
            questions=question_dtos,
        )
    )


@router.put("/courses/{course_id}/quiz", response_model=DataResponse[QuizDetailResponseData])
def update_quiz(
    course_id: int,
    req: QuizUpdateRequest,
    db: DbSession,
    current_user: InstructorUser,
    section_id: Annotated[int | None, Query(alias="sectionId")] = None,
):
    course = verify_course_ownership(course_id, current_user.id, db)
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(
            409,
            "CONFLICT",
            "DRAFT 또는 EDITING 상태인 강좌에서만 퀴즈를 수정하거나 재생성할 수 있습니다.",
        )

    quiz_validation_msg = (
        "퀴즈는 3~5문항이어야 하며, 각 문항의 보기(choices)는 정확히 4개, "
        "정답 번호(correctChoiceNo)는 1~4여야 합니다."
    )
    # 3~5문항, 문항당 보기 4개, 정답 1~4 및 공백 검증
    if len(req.questions) < 3 or len(req.questions) > 5:
        raise AppError(
            400,
            "VALIDATION_ERROR",
            quiz_validation_msg,
        )
    for q_item in req.questions:
        if (
            not q_item.content
            or not q_item.content.strip()
            or not q_item.explanation
            or not q_item.explanation.strip()
            or len(q_item.choices) != 4
            or any(not c or not c.strip() for c in q_item.choices)
            or q_item.correct_choice_no not in (1, 2, 3, 4)
        ):
            raise AppError(
                400,
                "VALIDATION_ERROR",
                quiz_validation_msg,
            )

    # 퀴즈 조회 또는 생성
    if section_id is not None:
        verify_section_ownership(section_id, current_user.id, db, course_id=course.id)
        quiz = db.scalar(
            select(Quiz).where(
                Quiz.course_id == course.id,
                Quiz.section_id == section_id,
                Quiz.quiz_type == "UNIT",
                Quiz.is_active.is_(True),
            )
        )
        if not quiz:
            quiz = Quiz(
                course_id=course.id,
                section_id=section_id,
                quiz_type="UNIT",
                ai_status="COMPLETED",
                is_active=True,
            )
            db.add(quiz)
            db.flush()
    else:
        quiz = db.scalar(
            select(Quiz).where(
                Quiz.course_id == course.id,
                Quiz.section_id.is_(None),
                Quiz.quiz_type == "FINAL",
                Quiz.is_active.is_(True),
            )
        )
        if not quiz:
            quiz = Quiz(
                course_id=course.id,
                section_id=None,
                quiz_type="FINAL",
                ai_status=None,
                is_active=True,
            )
            db.add(quiz)
            db.flush()

    # (치명적 제약조건 방어)
    # 기존 문항 중 수강생 제출 이력(attempt_questions) 또는
    # 오답노트 이력(question_records)이 있는 문항은
    # is_active=False 및 order_no = -id 로 음수화하여 보존!
    existing_questions = list(quiz.questions)
    for old_q in existing_questions:
        if not old_q.is_active:
            if old_q.order_no > 0:
                old_q.order_no = -int(old_q.id)
            continue
        has_attempts = db.scalar(
            select(func.count(AttemptQuestion.id)).where(AttemptQuestion.question_id == old_q.id)
        )
        has_records = db.scalar(
            select(func.count(QuestionRecord.id)).where(QuestionRecord.question_id == old_q.id)
        )
        if (has_attempts and has_attempts > 0) or (has_records and has_records > 0):
            old_q.is_active = False
            old_q.order_no = -int(old_q.id)
        else:
            # 시도 이력이 없는 기존 문항은 삭제
            db.delete(old_q)
    db.flush()

    # 새 문항 삽입
    new_question_dtos: list[QuestionItem] = []
    for idx, q_item in enumerate(req.questions, start=1):
        new_q = Question(
            quiz_id=quiz.id,
            order_no=idx,
            generated_by=q_item.generated_by
            or ("AI" if quiz.quiz_type == "UNIT" else "INSTRUCTOR"),
            content=q_item.content,
            explanation=q_item.explanation,
            correct_choice_no=q_item.correct_choice_no,
            is_active=True,
        )
        db.add(new_q)
        db.flush()

        for c_idx, choice_text in enumerate(q_item.choices, start=1):
            db.add(
                QuestionChoice(
                    question_id=new_q.id,
                    choice_no=c_idx,
                    content=choice_text,
                )
            )

        new_question_dtos.append(
            QuestionItem(
                question_id=new_q.id,
                order_no=idx,
                generated_by=new_q.generated_by,
                content=new_q.content,
                choices=q_item.choices,
                correct_choice_no=new_q.correct_choice_no,
                explanation=new_q.explanation,
            )
        )

    # 검수 시각 및 검수자 기록
    now = datetime.now(UTC)
    quiz.reviewed_by = current_user.id
    quiz.reviewed_at = now
    if quiz.quiz_type == "UNIT":
        quiz.ai_status = "COMPLETED"

    db.commit()
    db.refresh(quiz)

    return DataResponse(
        data=QuizDetailResponseData(
            quiz_id=quiz.id,
            course_id=quiz.course_id,
            section_id=quiz.section_id,
            quiz_type=quiz.quiz_type,
            ai_status=quiz.ai_status,
            reviewed_by=quiz.reviewed_by,
            reviewed_at=quiz.reviewed_at,
            questions=new_question_dtos,
        )
    )


@router.post(
    "/courses/{course_id}/quiz/regenerate", response_model=DataResponse[QuizRegenerateResponseData]
)
def regenerate_unit_quiz(
    course_id: int,
    db: DbSession,
    current_user: InstructorUser,
    section_id: Annotated[
        int, Query(alias="sectionId", description="재생성할 단위 퀴즈의 섹션 ID")
    ],
):
    course = verify_course_ownership(course_id, current_user.id, db)
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(
            409,
            "CONFLICT",
            "DRAFT 또는 EDITING 상태인 강좌에서만 퀴즈를 수정하거나 재생성할 수 있습니다.",
        )

    verify_section_ownership(section_id, current_user.id, db, course_id=course.id)
    quiz = db.scalar(
        select(Quiz).where(
            Quiz.course_id == course.id,
            Quiz.section_id == section_id,
            Quiz.quiz_type == "UNIT",
            Quiz.is_active.is_(True),
        )
    )
    if not quiz:
        quiz = Quiz(
            course_id=course.id,
            section_id=section_id,
            quiz_type="UNIT",
            ai_status="PENDING",
            reviewed_at=None,
            is_active=True,
        )
        db.add(quiz)
    else:
        quiz.ai_status = "PENDING"
        quiz.reviewed_at = None

    db.commit()
    db.refresh(quiz)

    return DataResponse(
        data=QuizRegenerateResponseData(
            quiz_id=quiz.id,
            section_id=quiz.section_id,
            quiz_type=quiz.quiz_type,
            ai_status="PENDING",
            message="단위 퀴즈 AI 생성 작업이 등록되었습니다.",
        )
    )
