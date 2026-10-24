from typing import Annotated

from fastapi import APIRouter, Query

from app.core.auth import OperatorUser
from app.core.database import DbSession
from app.schemas.admin import (
    AdminCourseDetail,
    AdminCourseItem,
    AdminUserDetail,
    AdminUserItem,
    UnpublishRequest,
    UnpublishResponse,
)
from app.schemas.common import DataResponse, PageData
from app.services import admin_courses as admin_course_service
from app.services import admin_users as admin_user_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/courses", response_model=DataResponse[PageData[AdminCourseItem]])
def list_courses(
    _: OperatorUser,
    db: DbSession,
    status: str | None = None,
    sort: str | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    category_id: Annotated[int | None, Query(alias="categoryId")] = None,
    job_category: Annotated[str | None, Query(alias="jobCategory")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DataResponse[PageData[AdminCourseItem]]:
    return DataResponse(
        data=admin_course_service.list_courses(
            db,
            status=status,
            sort=sort,
            q=q,
            category_id=category_id,
            job_category=job_category,
            page=page,
            size=size,
        )
    )


@router.get("/courses/{course_id}", response_model=DataResponse[AdminCourseDetail])
def get_course(course_id: int, _: OperatorUser, db: DbSession) -> DataResponse[AdminCourseDetail]:
    return DataResponse(data=admin_course_service.get_course_detail(db, course_id))


@router.post("/courses/{course_id}/unpublish", response_model=DataResponse[UnpublishResponse])
def unpublish_course(
    course_id: int, body: UnpublishRequest, operator: OperatorUser, db: DbSession
) -> DataResponse[UnpublishResponse]:
    return DataResponse(
        data=admin_course_service.unpublish_course(db, operator, course_id, body.reason)
    )


@router.get("/users", response_model=DataResponse[PageData[AdminUserItem]])
def list_users(
    _: OperatorUser,
    db: DbSession,
    role: str | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DataResponse[PageData[AdminUserItem]]:
    return DataResponse(data=admin_user_service.list_members(db, role, q, page, size))


@router.get("/users/{user_id}", response_model=DataResponse[AdminUserDetail])
def get_user(user_id: int, _: OperatorUser, db: DbSession) -> DataResponse[AdminUserDetail]:
    return DataResponse(data=admin_user_service.get_member_detail(db, user_id))
