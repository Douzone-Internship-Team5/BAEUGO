from fastapi import APIRouter

from app.core.auth import CurrentUser
from app.core.database import DbSession
from app.schemas.common import DataResponse
from app.schemas.user import OnboardingRequest, OnboardingResponse, RoleRequest, RoleResponse
from app.services import onboarding as onboarding_service
from app.services import user as user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/me/role", response_model=DataResponse[RoleResponse])
def select_role(body: RoleRequest, user: CurrentUser, db: DbSession) -> DataResponse[RoleResponse]:
    return DataResponse(data=user_service.select_role(db, user, body.role))


@router.put("/me/onboarding", response_model=DataResponse[OnboardingResponse])
def save_onboarding(
    body: OnboardingRequest, user: CurrentUser, db: DbSession
) -> DataResponse[OnboardingResponse]:
    return DataResponse(data=onboarding_service.save_onboarding(db, user, body))
