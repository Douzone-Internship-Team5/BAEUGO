from fastapi import APIRouter
from sqlalchemy import text

from app.core.database import DbSession
from app.schemas.common import CamelModel, DataResponse

router = APIRouter(tags=["health"])


class HealthStatus(CamelModel):
    status: str
    database: str | None = None


@router.get("/health", response_model=DataResponse[HealthStatus])
def health() -> DataResponse[HealthStatus]:
    return DataResponse(data=HealthStatus(status="ok"))


@router.get("/health/db", response_model=DataResponse[HealthStatus])
def health_db(db: DbSession) -> DataResponse[HealthStatus]:
    db.execute(text("SELECT 1"))
    return DataResponse(data=HealthStatus(status="ok", database="ok"))
