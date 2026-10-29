import re

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import hash_password, verify_password
from app.models.user import Role, User
from app.schemas.user import DemoLabelResponse, MeResponse, PasswordChangeRequest
from app.services.auth import build_login_user
from app.services.onboarding import get_onboarding
from app.services.storage import get_profile_image_url
from app.services.user import is_onboarded

MIN_PASSWORD_LENGTH = 8
# bcrypt는 앞 72바이트만 해시에 쓰므로, 더 길면 뒷부분이 달라도 같은 비밀번호로 처리된다.
MAX_PASSWORD_BYTES = 72
MAX_LABEL_LENGTH = 100

PASSWORD_RULE_MESSAGE = "새 비밀번호는 8자 이상 영문과 숫자를 포함해야 합니다."
PASSWORD_TOO_LONG_MESSAGE = "새 비밀번호는 72바이트(영문 72자, 한글 24자) 이하여야 합니다."
SAME_PASSWORD_MESSAGE = "새 비밀번호는 현재 비밀번호와 달라야 합니다."
PAYMENT_LABEL_MESSAGE = "결제수단 표시값은 1~100자 이내여야 합니다."
SETTLEMENT_LABEL_MESSAGE = "정산계좌 표시값은 1~100자 이내여야 합니다."
ONBOARDING_REQUIRED_MESSAGE = "온보딩을 먼저 완료해 주세요."


def get_me(db: Session, user: User) -> MeResponse:
    me = MeResponse(
        **build_login_user(user).model_dump(),
        profile_image_key=user.profile_image_key,
        profile_image_url=get_profile_image_url(user.profile_image_key),
        onboarding=get_onboarding(db, user, user.active_mode) if is_onboarded(user) else None,
    )
    # 라벨은 현재 모드에 맞는 것만 값을 넣어서, 나머지는 응답에서 빠지게 한다.
    if user.active_mode == Role.LEARNER:
        profile = user.learner_profile
        me.demo_payment_label = profile.demo_payment_label if profile else None
    elif user.active_mode == Role.INSTRUCTOR:
        profile = user.instructor_profile
        me.demo_settlement_label = profile.demo_settlement_label if profile else None
    return me


def change_password(db: Session, user: User, body: PasswordChangeRequest) -> None:
    current_password = body.current_password or ""
    if not verify_password(current_password, user.password_hash):
        raise AppError(400, "WRONG_PASSWORD", "현재 비밀번호가 일치하지 않습니다.")

    new_password = body.new_password or ""
    if not is_valid_password(new_password):
        raise AppError(400, "VALIDATION_ERROR", PASSWORD_RULE_MESSAGE)
    if len(new_password.encode()) > MAX_PASSWORD_BYTES:
        raise AppError(
            400,
            "VALIDATION_ERROR",
            PASSWORD_TOO_LONG_MESSAGE,
            fields={"newPassword": PASSWORD_TOO_LONG_MESSAGE},
        )
    if new_password == current_password:
        raise AppError(
            400,
            "VALIDATION_ERROR",
            SAME_PASSWORD_MESSAGE,
            fields={"newPassword": SAME_PASSWORD_MESSAGE},
        )

    user.password_hash = hash_password(new_password)
    db.commit()


def is_valid_password(password: str) -> bool:
    return (
        len(password) >= MIN_PASSWORD_LENGTH
        and re.search(r"[A-Za-z]", password) is not None
        and re.search(r"[0-9]", password) is not None
    )


def get_payment_label(user: User) -> DemoLabelResponse:
    profile = user.learner_profile
    return DemoLabelResponse(label=profile.demo_payment_label if profile else None)


def update_payment_label(db: Session, user: User, label: str | None) -> DemoLabelResponse:
    cleaned = clean_label(label, PAYMENT_LABEL_MESSAGE)
    profile = user.learner_profile
    if profile is None:
        raise AppError(409, "ONBOARDING_REQUIRED", ONBOARDING_REQUIRED_MESSAGE)

    profile.demo_payment_label = cleaned
    db.commit()
    return DemoLabelResponse(label=profile.demo_payment_label)


def get_settlement_label(user: User) -> DemoLabelResponse:
    profile = user.instructor_profile
    return DemoLabelResponse(label=profile.demo_settlement_label if profile else None)


def update_settlement_label(db: Session, user: User, label: str | None) -> DemoLabelResponse:
    cleaned = clean_label(label, SETTLEMENT_LABEL_MESSAGE)
    profile = user.instructor_profile
    if profile is None:
        raise AppError(409, "ONBOARDING_REQUIRED", ONBOARDING_REQUIRED_MESSAGE)

    profile.demo_settlement_label = cleaned
    db.commit()
    return DemoLabelResponse(label=profile.demo_settlement_label)


def clean_label(label: str | None, message: str) -> str:
    cleaned = (label or "").strip()
    if not 1 <= len(cleaned) <= MAX_LABEL_LENGTH:
        raise AppError(400, "VALIDATION_ERROR", message)
    return cleaned
