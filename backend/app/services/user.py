from app.models.user import AccountType, Role, User

ROLE_ORDER = (Role.LEARNER, Role.INSTRUCTOR)


def get_role_names(user: User) -> list[str]:
    owned_roles = {user_role.role for user_role in user.roles}
    return [role.value for role in ROLE_ORDER if role in owned_roles]


def is_onboarded(user: User) -> bool:
    # 운영자는 온보딩 대상이 아니다.
    if user.account_type == AccountType.OPERATOR:
        return True
    if user.active_mode == Role.LEARNER:
        return user.learner_profile is not None
    if user.active_mode == Role.INSTRUCTOR:
        return user.instructor_profile is not None
    return False
