from typing import Annotated

from fastapi import APIRouter, Query

from app.core.auth import LearnerUser
from app.core.database import DbSession
from app.schemas.common import DataResponse
from app.schemas.goals import AttendanceData, TodayData
from app.services import goals as goal_service

router = APIRouter(tags=["goals"])


@router.get("/me/today", response_model=DataResponse[TodayData])
def get_today(current_user: LearnerUser, db: DbSession):
    return DataResponse(data=goal_service.get_today(db, current_user.id))


@router.get("/me/attendance", response_model=DataResponse[AttendanceData])
def get_attendance(
    current_user: LearnerUser,
    db: DbSession,
    year: Annotated[int | None, Query()] = None,
    month: Annotated[int | None, Query()] = None,
):
    return DataResponse(data=goal_service.get_attendance(db, current_user.id, year, month))
