from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.timezone import today_in_seoul
from app.models import (
    AccountType,
    AttendanceDay,
    Category,
    InstructorProfile,
    InstructorTopic,
    LearnerProfile,
    LearnerTopicInterest,
    LearningGoal,
    LearningTimeSlot,
    Role,
    Topic,
    User,
)
from app.schemas.user import (
    InstructorOnboarding,
    LearnerOnboarding,
    OnboardingRequest,
    OnboardingResponse,
    TimeSlotInput,
    TimeSlotItem,
)
from app.services.user import is_onboarded

CURRENT_STATUSES = ("JOB_SEEKER", "EMPLOYED", "CAREER_CHANGE")
DAILY_GOAL_MINUTES_CHOICES = (10, 20, 30, 40, 50, 60)
MIN_BIO_LENGTH = 5
MAX_NAME_LENGTH = 50

REQUIRED_MESSAGE = "필수 입력값입니다."
TIME_SLOT_MESSAGE = "종료 시각은 시작 시각보다 늦어야 하며 중복 시간대를 등록할 수 없습니다."


def save_onboarding(db: Session, user: User, body: OnboardingRequest) -> OnboardingResponse:
    check_can_onboard(user)

    errors: dict[str, str] = {}
    name = validate_name(body.name, errors)
    category = resolve_category(db, body.category_id, body.job_category, errors)
    if user.active_mode == Role.LEARNER:
        topic_ids = validate_topic_ids(
            db, body.interest_topic_ids, category, "interestTopicIds", errors
        )
        validate_learner_fields(body, errors)
    else:
        topic_ids = validate_topic_ids(
            db, body.lecture_topic_ids, category, "lectureTopicIds", errors
        )
        validate_bio(body.bio, errors)
    if errors:
        raise AppError(400, "VALIDATION_ERROR", "입력값을 확인해 주세요.", fields=errors)

    if name is not None:
        user.name = name
    if user.active_mode == Role.LEARNER:
        save_learner_onboarding(db, user, body, category, topic_ids)
    else:
        save_instructor_onboarding(db, user, body, category, topic_ids)
    db.commit()
    # expire_on_commit=False라서 방금 바꾼 관계(프로필·시간대 등)를 다시 읽도록 비운다.
    db.expire_all()

    return OnboardingResponse(
        active_mode=user.active_mode,
        onboarded=is_onboarded(user),
        onboarding=get_onboarding(db, user, user.active_mode),
    )


def check_can_onboard(user: User) -> None:
    if user.account_type == AccountType.OPERATOR:
        raise AppError(403, "FORBIDDEN", "운영자 계정은 온보딩 대상이 아닙니다.")
    if user.active_mode is None:
        raise AppError(409, "ROLE_NOT_SET", "역할을 먼저 선택한 후 온보딩을 진행할 수 있습니다.")


def validate_name(name: str | None, errors: dict[str, str]) -> str | None:
    if name is None:
        return None
    stripped = name.strip()
    if not stripped or len(stripped) > MAX_NAME_LENGTH:
        errors["name"] = f"이름은 1~{MAX_NAME_LENGTH}자로 입력해 주세요."
        return None
    return stripped


def resolve_category(
    db: Session, category_id: int | None, job_category: str | None, errors: dict[str, str]
) -> Category | None:
    """categoryId가 우선이고, jobCategory 코드만 와도 받는다. 둘 다 오면 서로 같아야 한다."""
    if category_id is None and job_category is None:
        errors["categoryId"] = REQUIRED_MESSAGE
        return None

    if category_id is not None:
        category = db.get(Category, category_id)
        error_field = "categoryId"
    else:
        category = db.scalar(select(Category).where(Category.code == job_category))
        error_field = "jobCategory"

    if category is None or not category.is_active:
        errors[error_field] = "존재하지 않는 직무입니다."
        return None
    if job_category is not None and category.code != job_category:
        errors["jobCategory"] = "categoryId와 jobCategory가 서로 다른 직무입니다."
        return None
    return category


def validate_topic_ids(
    db: Session,
    topic_ids: list[int] | None,
    category: Category | None,
    field_name: str,
    errors: dict[str, str],
) -> list[int]:
    if not topic_ids:
        errors[field_name] = "주제를 1개 이상 선택해 주세요."
        return []

    unique_ids = list(dict.fromkeys(topic_ids))
    topics = db.scalars(select(Topic).where(Topic.id.in_(unique_ids))).all()
    active_topics = [topic for topic in topics if topic.is_active]
    if len(active_topics) != len(unique_ids):
        errors[field_name] = "존재하지 않는 주제가 포함되어 있습니다."
        return []
    # 직무가 잘못됐으면 소속 검사는 의미가 없으므로 직무 에러만 보여준다.
    if category is None:
        return unique_ids
    if any(topic.category_id != category.id for topic in active_topics):
        errors[field_name] = "선택한 직무에 속하지 않는 주제가 포함되어 있습니다."
        return []
    return unique_ids


def validate_learner_fields(body: OnboardingRequest, errors: dict[str, str]) -> None:
    if body.current_status is None:
        errors["currentStatus"] = REQUIRED_MESSAGE
    elif body.current_status not in CURRENT_STATUSES:
        errors["currentStatus"] = f"허용되지 않는 값입니다. (허용값: {', '.join(CURRENT_STATUSES)})"

    if body.daily_goal_minutes is None:
        errors["dailyGoalMinutes"] = REQUIRED_MESSAGE
    elif body.daily_goal_minutes not in DAILY_GOAL_MINUTES_CHOICES:
        choices = ", ".join(str(minutes) for minutes in DAILY_GOAL_MINUTES_CHOICES)
        errors["dailyGoalMinutes"] = f"{choices} 중 하나여야 합니다."

    if body.time_slots is not None and not are_time_slots_valid(body.time_slots):
        errors["timeSlots"] = TIME_SLOT_MESSAGE


def are_time_slots_valid(time_slots: list[TimeSlotInput]) -> bool:
    if any(slot.end_time <= slot.start_time for slot in time_slots):
        return False
    # 끝과 시작이 맞닿는 것(08:00~09:00, 09:00~10:00)은 겹침으로 보지 않는다.
    ordered = sorted(time_slots, key=lambda slot: slot.start_time)
    return all(
        current.start_time >= previous.end_time
        for previous, current in zip(ordered, ordered[1:], strict=False)
    )


def validate_bio(bio: str | None, errors: dict[str, str]) -> None:
    if bio is None:
        errors["bio"] = REQUIRED_MESSAGE
    elif len(bio.strip()) < MIN_BIO_LENGTH:
        errors["bio"] = f"강사 약력은 {MIN_BIO_LENGTH}자 이상 입력해야 합니다."


def save_learner_onboarding(
    db: Session, user: User, body: OnboardingRequest, category: Category, topic_ids: list[int]
) -> None:
    profile = user.learner_profile
    if profile is None:
        profile = LearnerProfile(user_id=user.id)
        db.add(profile)
    profile.current_status = body.current_status
    profile.target_category_id = category.id

    replace_learner_topics(db, user.id, topic_ids)

    goal = user.learning_goal
    if goal is None:
        goal = LearningGoal(learner_id=user.id)
        db.add(goal)
    goal.daily_target_minutes = body.daily_goal_minutes
    # 시간대 행이 learning_goals를 참조하므로 목표를 먼저 저장한다.
    db.flush()

    if body.time_slots is not None:
        replace_time_slots(db, user.id, body.time_slots)
    refresh_today_attendance(db, user.id, body.daily_goal_minutes)


def replace_learner_topics(db: Session, learner_id: int, topic_ids: list[int]) -> None:
    db.execute(delete(LearnerTopicInterest).where(LearnerTopicInterest.learner_id == learner_id))
    db.add_all(
        LearnerTopicInterest(learner_id=learner_id, topic_id=topic_id) for topic_id in topic_ids
    )


def replace_time_slots(db: Session, learner_id: int, time_slots: list[TimeSlotInput]) -> None:
    # 같은 시간대를 다시 보내도 unique 제약에 걸리지 않도록 기존 행을 먼저 지운다.
    db.execute(delete(LearningTimeSlot).where(LearningTimeSlot.learner_id == learner_id))
    db.add_all(
        LearningTimeSlot(
            learner_id=learner_id,
            start_time=slot.start_time,
            end_time=slot.end_time,
            sort_order=slot.sort_order if slot.sort_order is not None else index,
        )
        for index, slot in enumerate(time_slots, start=1)
    )


def refresh_today_attendance(db: Session, learner_id: int, daily_target_minutes: int) -> None:
    """오늘(Asia/Seoul) 출석 행만 새 목표로 다시 계산한다. 지난 날짜의 스냅샷은 보존한다."""
    attendance = db.get(AttendanceDay, (learner_id, today_in_seoul()))
    # 오늘 행은 학습 활동이 기록될 때 G 영역에서 만든다.
    if attendance is None:
        return
    attendance.target_minutes_snapshot = daily_target_minutes
    attendance.is_attended = attendance.active_seconds >= daily_target_minutes * 60


def save_instructor_onboarding(
    db: Session, user: User, body: OnboardingRequest, category: Category, topic_ids: list[int]
) -> None:
    profile = user.instructor_profile
    if profile is None:
        profile = InstructorProfile(user_id=user.id)
        db.add(profile)
    profile.primary_category_id = category.id
    profile.bio = body.bio.strip()

    db.execute(delete(InstructorTopic).where(InstructorTopic.instructor_id == user.id))
    db.add_all(InstructorTopic(instructor_id=user.id, topic_id=topic_id) for topic_id in topic_ids)


def get_onboarding(
    db: Session, user: User, mode: str | None
) -> LearnerOnboarding | InstructorOnboarding | None:
    if mode == Role.LEARNER:
        return get_learner_onboarding(db, user)
    if mode == Role.INSTRUCTOR:
        return get_instructor_onboarding(db, user)
    return None


def get_learner_onboarding(db: Session, user: User) -> LearnerOnboarding | None:
    profile = user.learner_profile
    if profile is None:
        return None

    category = db.get(Category, profile.target_category_id) if profile.target_category_id else None
    topic_ids = db.scalars(
        select(LearnerTopicInterest.topic_id)
        .where(LearnerTopicInterest.learner_id == user.id)
        .order_by(LearnerTopicInterest.topic_id)
    ).all()
    goal = user.learning_goal
    time_slots = goal.time_slots if goal is not None else []

    return LearnerOnboarding(
        current_status=profile.current_status,
        category_id=profile.target_category_id,
        job_category=category.code if category else None,
        interest_topic_ids=list(topic_ids),
        daily_goal_minutes=goal.daily_target_minutes if goal else None,
        time_zone=goal.time_zone if goal else None,
        time_slots=[
            TimeSlotItem(
                id=slot.id,
                start_time=slot.start_time,
                end_time=slot.end_time,
                sort_order=slot.sort_order,
            )
            for slot in time_slots
        ],
    )


def get_instructor_onboarding(db: Session, user: User) -> InstructorOnboarding | None:
    profile = user.instructor_profile
    if profile is None:
        return None

    category = db.get(Category, profile.primary_category_id)
    topic_ids = db.scalars(
        select(InstructorTopic.topic_id)
        .where(InstructorTopic.instructor_id == user.id)
        .order_by(InstructorTopic.topic_id)
    ).all()

    return InstructorOnboarding(
        category_id=profile.primary_category_id,
        job_category=category.code,
        lecture_topic_ids=list(topic_ids),
        bio=profile.bio,
    )
