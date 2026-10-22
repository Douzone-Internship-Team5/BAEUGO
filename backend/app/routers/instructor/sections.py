from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.core.auth import (
    get_current_instructor,
    verify_course_ownership,
    verify_section_ownership,
)
from app.core.database import DbSession
from app.core.errors import AppError
from app.models.course import Section
from app.models.user import User
from app.schemas.common import DataResponse
from app.schemas.instructor import (
    SectionCreateRequest,
    SectionResponseData,
    SectionUpdateRequest,
)

router = APIRouter(tags=["instructor-sections"])


# ==========================================
# [B-5] 섹션 관리
# ==========================================
@router.post("/sections", response_model=DataResponse[SectionResponseData], status_code=201)
def create_section(
    req: SectionCreateRequest,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
):
    course = verify_course_ownership(req.course_id, current_user.id, db)
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(409, "CONFLICT", "DRAFT 또는 EDITING 상태인 강좌에서만 섹션을 편집할 수 있습니다.")

    # sort_order 자동 배정 (마지막 + 1)
    max_order = db.scalar(
        select(func.coalesce(func.max(Section.sort_order), 0)).where(Section.course_id == course.id)
    )
    new_order = (max_order or 0) + 1

    section = Section(
        course_id=course.id,
        title=req.title,
        sort_order=new_order,
        is_active=True,
    )
    db.add(section)
    db.commit()
    db.refresh(section)

    return DataResponse(
        data=SectionResponseData(
            section_id=section.id,
            course_id=section.course_id,
            title=section.title,
            sort_order=section.sort_order,
            is_active=section.is_active,
        )
    )


@router.patch("/sections/{sectionId}", response_model=DataResponse[SectionResponseData])
def update_section(
    sectionId: int,
    req: SectionUpdateRequest,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
):
    section = verify_section_ownership(sectionId, current_user.id, db)
    course = section.course
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(409, "CONFLICT", "DRAFT 또는 EDITING 상태인 강좌에서만 섹션을 편집할 수 있습니다.")

    if req.title is not None:
        section.title = req.title
    if req.sort_order is not None:
        section.sort_order = req.sort_order

    db.commit()
    db.refresh(section)

    return DataResponse(
        data=SectionResponseData(
            section_id=section.id,
            course_id=section.course_id,
            title=section.title,
            sort_order=section.sort_order,
            is_active=section.is_active,
        )
    )


@router.delete("/sections/{sectionId}", response_model=DataResponse[SectionResponseData])
def delete_section(
    sectionId: int,
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
):
    section = verify_section_ownership(sectionId, current_user.id, db)
    course = section.course
    if course.status not in ("DRAFT", "EDITING"):
        raise AppError(409, "CONFLICT", "DRAFT 또는 EDITING 상태인 강좌에서만 섹션을 편집할 수 있습니다.")

    # 물리 삭제 금지: is_active = False 처리 (하위 영상 및 퀴즈도 비활성화)
    section.is_active = False
    for v in section.videos:
        v.is_active = False
    if section.unit_quiz:
        section.unit_quiz.is_active = False

    db.commit()
    return DataResponse(
        data=SectionResponseData(
            section_id=section.id,
            course_id=section.course_id,
            isActive=False,
        )
    )
