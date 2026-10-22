"""인증·권한 확인 의존성.

엔드포인트에서 아래 타입으로 받으면 확인을 통과한 사용자가 들어온다.
    user: CurrentUser      로그인만 확인
    user: OperatorUser     운영자 전용
    user: LearnerUser      수강자 모드 전용
    user: InstructorUser   강사 모드 전용
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.database import DbSession
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.models.user import AccountType, Role, User

# 기본값(auto_error=True)이면 헤더가 없을 때 FastAPI가 403을 보내므로 직접 401로 처리한다.
bearer_scheme = HTTPBearer(auto_error=False)

MODE_FORBIDDEN_MESSAGES = {
    Role.LEARNER: "수강자 모드에서만 이용할 수 있습니다.",
    Role.INSTRUCTOR: "강사 모드에서만 이용할 수 있습니다.",
}


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if credentials is None:
        raise AppError(401, "UNAUTHORIZED", "로그인이 필요합니다.")

    invalid_token = AppError(401, "UNAUTHORIZED", "유효하지 않거나 만료된 토큰입니다.")
    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise invalid_token

    user = db.get(User, user_id)
    if user is None or not user.is_login_allowed:
        raise invalid_token
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_operator(user: CurrentUser) -> User:
    if user.account_type != AccountType.OPERATOR:
        raise AppError(403, "FORBIDDEN", "운영자만 이용할 수 있습니다.")
    return user


def require_mode(role: Role) -> Callable[[User], User]:
    """일반 회원이 해당 역할을 갖고 있고, 지금 그 모드로 보고 있을 때만 통과시킨다."""

    def check_mode(user: CurrentUser) -> User:
        is_member = user.account_type == AccountType.MEMBER
        is_in_mode = user.active_mode == role and user.has_role(role)
        if not (is_member and is_in_mode):
            raise AppError(403, "FORBIDDEN", MODE_FORBIDDEN_MESSAGES[role])
        return user

    return check_mode


OperatorUser = Annotated[User, Depends(require_operator)]
LearnerUser = Annotated[User, Depends(require_mode(Role.LEARNER))]
InstructorUser = Annotated[User, Depends(require_mode(Role.INSTRUCTOR))]
