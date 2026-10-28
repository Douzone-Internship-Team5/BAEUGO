from fastapi import APIRouter

from app.core.database import DbSession
from app.schemas.code import CodesResponse
from app.schemas.common import DataResponse
from app.services import code as code_service

router = APIRouter(tags=["codes"])


@router.get("/codes", response_model=DataResponse[CodesResponse])
def get_codes(db: DbSession) -> DataResponse[CodesResponse]:
    return DataResponse(data=code_service.get_codes(db))
