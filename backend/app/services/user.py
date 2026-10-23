from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.user import AccountType, Role, User, UserRole
from app.schemas.user import RoleResponse

ROLE_ORDER = (Role.LEARNER, Role.INSTRUCTOR)


def get_role_names(user: User) -> list[str]:
    owned_roles = {user_role.role for user_role in user.roles}
    return [role.value for role in ROLE_ORDER if role in owned_roles]


def is_onboarded(user: User) -> bool:
    # 운영자는 온보딩 대상이 아니다.
    if user.account_type == AccountType.OPERATOR:
        return True
    if user.active_mode == Role.LEARNER:
        # 명세상 수강자 온보딩 완료 기준은 learning_goals 생성(A-4)이다.
        return user.learner_profile is not None and user.learning_goal is not None
    if user.active_mode == Role.INSTRUCTOR:
        return user.instructor_profile is not None
    return False


def select_role(db: Session, user: User, role: str) -> RoleResponse:
    if user.account_type != AccountType.MEMBER:
        raise AppError(403, "FORBIDDEN", "운영자 계정은 역할을 선택할 수 없습니다.")

    db.execute(insert(UserRole).values(user_id=user.id, role=role).on_conflict_do_nothing())
    user.active_mode = role
    db.commit()
    db.expire(user, ["roles"])

    return RoleResponse(
        role=user.active_mode,
        active_mode=user.active_mode,
        roles=get_role_names(user),
        onboarded=is_onboarded(user),
    )
