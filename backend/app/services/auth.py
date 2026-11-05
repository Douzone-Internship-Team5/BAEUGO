from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import AccountType, User, UserRole
from app.schemas.auth import LoginResponse, LoginUser, SignupRequest
from app.services.user import get_role_names, is_onboarded

PASSWORD_RULE_MESSAGE = "비밀번호는 8자 이상 영문과 숫자를 포함해야 합니다."
PASSWORD_TOO_LONG_MESSAGE = "비밀번호는 72바이트(영문 72자, 한글 24자) 이하여야 합니다."


def signup(db: Session, body: SignupRequest) -> LoginResponse:
    from app.services.account import MAX_PASSWORD_BYTES, is_valid_password

    if not is_valid_password(body.password):
        raise AppError(
            400,
            "VALIDATION_ERROR",
            PASSWORD_RULE_MESSAGE,
            fields={"password": PASSWORD_RULE_MESSAGE},
        )
    if len(body.password.encode()) > MAX_PASSWORD_BYTES:
        raise AppError(
            400,
            "VALIDATION_ERROR",
            PASSWORD_TOO_LONG_MESSAGE,
            fields={"password": PASSWORD_TOO_LONG_MESSAGE},
        )

    existing_user = db.scalar(select(User).where(User.email == body.email))
    if existing_user is not None:
        raise AppError(
            409,
            "CONFLICT",
            "이미 가입된 이메일입니다.",
            fields={"email": "이미 가입된 이메일입니다."},
        )

    name = body.email.split("@")[0][:50]
    active_mode = body.role.value if body.role else None

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        name=name,
        account_type=AccountType.MEMBER,
        active_mode=active_mode,
    )
    db.add(user)

    try:
        db.flush()
        if body.role:
            db.add(UserRole(user_id=user.id, role=body.role.value))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            409,
            "CONFLICT",
            "이미 가입된 이메일입니다.",
            fields={"email": "이미 가입된 이메일입니다."},
        ) from exc

    db.expire(user, ["roles"])
    db.refresh(user)

    return LoginResponse(access_token=create_access_token(user.id), user=build_login_user(user))


def login(db: Session, email: str, password: str) -> LoginResponse:
    user = db.scalar(select(User).where(User.email == email))
    # 어느 조건에서 실패했는지 알려주지 않도록 모두 같은 에러로 응답한다.
    is_valid = (
        user is not None and verify_password(password, user.password_hash) and user.is_login_allowed
    )
    if not is_valid:
        raise AppError(401, "INVALID_CREDENTIALS", "이메일 또는 비밀번호가 일치하지 않습니다.")

    return LoginResponse(access_token=create_access_token(user.id), user=build_login_user(user))


def build_login_user(user: User) -> LoginUser:
    return LoginUser(
        id=user.id,
        email=user.email,
        name=user.name,
        account_type=user.account_type,
        role=user.active_mode,
        active_mode=user.active_mode,
        roles=get_role_names(user),
        onboarded=is_onboarded(user),
    )
