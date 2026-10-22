from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import create_access_token, verify_password
from app.models.user import User
from app.schemas.auth import LoginResponse, LoginUser
from app.services.user import get_role_names, is_onboarded


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
