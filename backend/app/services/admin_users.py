from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import AccountType, Role, User, UserRole
from app.schemas.admin import (
    AdminUserDetail,
    AdminUserItem,
    CourseStatusCount,
    InstructorSummary,
    LearningSummary,
)
from app.schemas.common import PageData
from app.services.attendance import get_current_streak
from app.services.onboarding import get_onboarding

ROLE_FILTERS = (Role.LEARNER, Role.INSTRUCTOR)
LIKE_ESCAPE = "\\"


def list_members(
    db: Session, role: str | None, q: str | None, page: int, size: int
) -> PageData[AdminUserItem]:
    if role is not None and role not in ROLE_FILTERS:
        raise AppError(
            400,
            "VALIDATION_ERROR",
            "유효하지 않은 역할 필터 값입니다.",
            fields={"role": "LEARNER 또는 INSTRUCTOR만 사용할 수 있습니다."},
        )

    query = select(User).where(User.account_type == AccountType.MEMBER)
    if role is not None:
        query = query.where(User.roles.any(UserRole.role == role))
    keyword = q.strip() if q else ""
    if keyword:
        pattern = f"%{escape_like(keyword)}%"
        query = query.where(
            or_(
                User.name.ilike(pattern, escape=LIKE_ESCAPE),
                User.email.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    # 가입 시각 컬럼이 없어서 id 역순을 최근 가입 순으로 쓴다.
    users = db.scalars(query.order_by(User.id.desc()).offset((page - 1) * size).limit(size))
    return PageData(
        items=[build_user_item(user) for user in users], page=page, size=size, total=total
    )


def escape_like(keyword: str) -> str:
    # 검색어의 %, _ 를 와일드카드가 아닌 글자로 찾도록 이스케이프한다.
    return (
        keyword.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", f"{LIKE_ESCAPE}%")
        .replace("_", f"{LIKE_ESCAPE}_")
    )


def build_user_item(user: User) -> AdminUserItem:
    return AdminUserItem(
        user_id=user.id,
        email=user.email,
        name=user.name,
        account_type=user.account_type,
        role=user.active_mode,
        active_mode=user.active_mode,
        is_active=user.is_active,
    )


def get_member_detail(db: Session, user_id: int) -> AdminUserDetail:
    user = db.get(User, user_id)
    if user is None or user.account_type != AccountType.MEMBER:
        raise AppError(404, "NOT_FOUND", "존재하지 않는 회원입니다.")

    return AdminUserDetail(
        user_id=user.id,
        email=user.email,
        name=user.name,
        account_type=user.account_type,
        role=user.active_mode,
        active_mode=user.active_mode,
        is_active=user.is_active,
        onboarding=get_onboarding(db, user, user.active_mode),
        learning_summary=build_learning_summary(db, user),
        instructor_summary=build_instructor_summary(),
    )


def build_learning_summary(db: Session, user: User) -> LearningSummary:
    return LearningSummary(
        # TODO: enrollments(원호·C 영역)가 main에 들어오면
        #  status별 개수와 가장 최근 last_studied_at으로 채운다.
        in_progress_count=0,
        completed_count=0,
        last_studied_at=None,
        attendance_streak=get_current_streak(db, user.id),
    )


def build_instructor_summary() -> InstructorSummary:
    # TODO: courses·enrollments(원호)가 main에 들어오면
    #  본인 강좌의 상태별 개수와 총 수강생 수로 채운다.
    return InstructorSummary(course_count=CourseStatusCount(), total_enrollments=0)
