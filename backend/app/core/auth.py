"""인증·권한 확인 의존성.

엔드포인트에서 아래 타입으로 받으면 확인을 통과한 사용자가 들어온다.
    user: CurrentUser      로그인만 확인
    user: OperatorUser     운영자 전용
    user: LearnerUser      수강자 모드 전용
    user: InstructorUser   강사 모드 전용
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import DbSession
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.models.user import AccountType, Role, User

if TYPE_CHECKING:
    from app.models.course import Course, Section, Video

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
        raise AppError(403, "FORBIDDEN", "운영자 권한이 필요합니다.")
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


get_current_learner = require_mode(Role.LEARNER)
get_current_instructor = require_mode(Role.INSTRUCTOR)

OperatorUser = Annotated[User, Depends(require_operator)]
LearnerUser = Annotated[User, Depends(get_current_learner)]
InstructorUser = Annotated[User, Depends(get_current_instructor)]


def get_optional_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User | None:
    if credentials is None:
        return None
    token = (
        credentials.credentials
        if isinstance(credentials, HTTPAuthorizationCredentials)
        else str(credentials)
    )
    if token.startswith("Bearer "):
        token = token[len("Bearer ") :].strip()
    user_id = decode_access_token(token)
    if user_id is None:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_login_allowed:
        return None
    return user


OptionalUser = Annotated[User | None, Depends(get_optional_current_user)]


# ==========================================
# 강사 소유권 검증 헬퍼 (타인 소유 시 404 NOT_FOUND 반환)
# ==========================================


def verify_course_ownership(
    course_id: int, instructor_id: int, db: Session, *, for_update: bool = False
) -> Course:
    from app.models.course import Course

    course = db.get(Course, course_id, with_for_update=for_update, populate_existing=for_update)
    if not course or course.instructor_id != instructor_id:
        raise AppError(404, "NOT_FOUND", "존재하지 않거나 접근 권한이 없는 강좌입니다.")
    return course


def verify_section_ownership(
    section_id: int,
    instructor_id: int,
    db: Session,
    course_id: int | None = None,
    *,
    for_update: bool = False,
) -> Section:
    from app.models.course import Course, Section

    section = db.get(Section, section_id)
    if section is not None and for_update:
        verify_course_ownership(section.course_id, instructor_id, db, for_update=True)
        db.refresh(section)
    if not section or not section.is_active:
        raise AppError(404, "NOT_FOUND", "섹션 또는 강좌를 찾을 수 없습니다.")

    if course_id is not None and section.course_id != course_id:
        raise AppError(404, "NOT_FOUND", "섹션 또는 강좌를 찾을 수 없습니다.")

    course = db.get(Course, section.course_id)
    if not course or course.instructor_id != instructor_id:
        raise AppError(404, "NOT_FOUND", "섹션 또는 강좌를 찾을 수 없습니다.")

    return section


def verify_video_ownership(
    video_id: int, instructor_id: int, db: Session, *, for_update: bool = False
) -> Video:
    from app.models.course import Course, Video

    video = db.get(Video, video_id)
    if video is not None and for_update:
        verify_course_ownership(video.course_id, instructor_id, db, for_update=True)
        db.refresh(video)
    if not video or not video.is_active:
        raise AppError(404, "NOT_FOUND", "영상 또는 섹션을 찾을 수 없습니다.")

    course = db.get(Course, video.course_id)
    if not course or course.instructor_id != instructor_id:
        raise AppError(404, "NOT_FOUND", "영상 또는 섹션을 찾을 수 없습니다.")

    return video
