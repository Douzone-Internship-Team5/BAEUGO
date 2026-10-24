from typing import Annotated

from fastapi import APIRouter, Query

from app.core.auth import OperatorUser
from app.core.database import DbSession
from app.schemas.admin import AdminUserDetail, AdminUserItem
from app.schemas.common import DataResponse, PageData
from app.services import admin_users as admin_user_service

router = APIRouter(prefix="/admin", tags=["admin"])


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
