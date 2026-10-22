from fastapi import APIRouter, Response

from app.core.auth import CurrentUser
from app.core.database import DbSession
from app.schemas.auth import LoginRequest, LoginResponse
from app.schemas.common import DataResponse
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=DataResponse[LoginResponse])
def login(body: LoginRequest, db: DbSession) -> DataResponse[LoginResponse]:
    return DataResponse(data=auth_service.login(db, body.email, body.password))


# JWT는 서버에 상태가 없으므로 토큰 확인만 하고, 토큰 삭제는 클라이언트가 한다.
@router.post("/logout", status_code=204, response_class=Response)
def logout(_: CurrentUser) -> Response:
    return Response(status_code=204)
