from fastapi import APIRouter, Response

from app.core.auth import CurrentUser, InstructorUser, LearnerUser
from app.core.database import DbSession
from app.schemas.common import DataResponse
from app.schemas.user import (
    DemoLabelRequest,
    DemoLabelResponse,
    MeResponse,
    OnboardingRequest,
    OnboardingResponse,
    PasswordChangeRequest,
    RoleRequest,
    RoleResponse,
)
from app.services import account as account_service
from app.services import onboarding as onboarding_service
from app.services import user as user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=DataResponse[MeResponse], response_model_exclude_unset=True)
def get_me(user: CurrentUser, db: DbSession) -> DataResponse[MeResponse]:
    return DataResponse(data=account_service.get_me(db, user))


@router.post("/me/role", response_model=DataResponse[RoleResponse])
def select_role(body: RoleRequest, user: CurrentUser, db: DbSession) -> DataResponse[RoleResponse]:
    return DataResponse(data=user_service.select_role(db, user, body.role))


@router.put("/me/onboarding", response_model=DataResponse[OnboardingResponse])
def save_onboarding(
    body: OnboardingRequest, user: CurrentUser, db: DbSession
) -> DataResponse[OnboardingResponse]:
    return DataResponse(data=onboarding_service.save_onboarding(db, user, body))


@router.put("/me/password", status_code=204, response_class=Response)
def change_password(body: PasswordChangeRequest, user: CurrentUser, db: DbSession) -> Response:
    account_service.change_password(db, user, body)
    return Response(status_code=204)


@router.get("/me/payment-method", response_model=DataResponse[DemoLabelResponse])
def get_payment_method(user: LearnerUser) -> DataResponse[DemoLabelResponse]:
    return DataResponse(data=account_service.get_payment_label(user))


@router.put("/me/payment-method", response_model=DataResponse[DemoLabelResponse])
def update_payment_method(
    body: DemoLabelRequest, user: LearnerUser, db: DbSession
) -> DataResponse[DemoLabelResponse]:
    return DataResponse(data=account_service.update_payment_label(db, user, body.label))


@router.get("/me/settlement-account", response_model=DataResponse[DemoLabelResponse])
def get_settlement_account(user: InstructorUser) -> DataResponse[DemoLabelResponse]:
    return DataResponse(data=account_service.get_settlement_label(user))


@router.put("/me/settlement-account", response_model=DataResponse[DemoLabelResponse])
def update_settlement_account(
    body: DemoLabelRequest, user: InstructorUser, db: DbSession
) -> DataResponse[DemoLabelResponse]:
    return DataResponse(data=account_service.update_settlement_label(db, user, body.label))
