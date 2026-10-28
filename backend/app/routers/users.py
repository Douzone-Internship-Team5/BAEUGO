from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.core.auth import CurrentUser, InstructorUser, LearnerUser
from app.core.database import DbSession
from app.core.errors import AppError
from app.schemas.common import DataResponse
from app.schemas.user import (
    DemoLabelRequest,
    DemoLabelResponse,
    MeResponse,
    OnboardingRequest,
    OnboardingResponse,
    PasswordChangeRequest,
    ProfileImageResponse,
    RoleRequest,
    RoleResponse,
)
from app.services import account as account_service
from app.services import onboarding as onboarding_service
from app.services import profile_image as profile_image_service
from app.services import user as user_service

router = APIRouter(prefix="/users", tags=["users"])

# 요청 본문을 직접 읽어서 /docs에 형식이 나오지 않으므로 두 형식을 직접 적어 둔다.
PROFILE_IMAGE_REQUEST_DOC = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {"presetId": {"type": "integer", "minimum": 1, "maximum": 4}},
                    "required": ["presetId"],
                }
            },
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {"file": {"type": "string", "format": "binary"}},
                    "required": ["file"],
                }
            },
        },
    }
}


@dataclass
class ProfileImageInput:
    preset_id: object = None
    file: object = None


async def read_profile_image_input(request: Request) -> ProfileImageInput:
    """JSON이면 presetId, multipart면 file을 꺼낸다. 값 검사는 서비스에서 한다."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type == "application/json":
        try:
            body = await request.json()
        except ValueError as err:
            raise AppError(
                400,
                "VALIDATION_ERROR",
                "입력값을 확인해 주세요.",
                fields={"request": "JSON 형식이 올바르지 않습니다."},
            ) from err
        return ProfileImageInput(preset_id=body.get("presetId") if isinstance(body, dict) else None)
    if content_type == "multipart/form-data":
        form = await request.form()
        # multipart에서는 file만 받는다. presetId는 file과 같이 왔는지 가리는 데만 쓴다.
        if form.get("file") is None:
            return ProfileImageInput()
        return ProfileImageInput(preset_id=form.get("presetId"), file=form.get("file"))
    return ProfileImageInput()


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


@router.put(
    "/me/profile-image",
    response_model=DataResponse[ProfileImageResponse],
    openapi_extra=PROFILE_IMAGE_REQUEST_DOC,
)
def update_profile_image(
    # 로그인 확인(401)이 본문 검사보다 먼저 되도록 user를 앞에 둔다.
    user: CurrentUser,
    body: Annotated[ProfileImageInput, Depends(read_profile_image_input)],
    db: DbSession,
) -> DataResponse[ProfileImageResponse]:
    return DataResponse(
        data=profile_image_service.update_profile_image(db, user, body.preset_id, body.file)
    )


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
