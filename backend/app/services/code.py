from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Topic
from app.schemas.code import CodeLabel, CodesResponse, JobCategoryCode, TopicCode
from app.services.onboarding import CURRENT_STATUSES, DAILY_GOAL_MINUTES_CHOICES

CURRENT_STATUS_LABELS = {
    "JOB_SEEKER": "취업 준비 중",
    "EMPLOYED": "재직 중",
    "CAREER_CHANGE": "이직·직무 전환 준비 중",
}
DIFFICULTY_LABELS = {
    "BEGINNER": "초급",
    "INTERMEDIATE": "중급",
    "ADVANCED": "고급",
}


def get_codes(db: Session) -> CodesResponse:
    categories = db.scalars(
        select(Category)
        .where(Category.is_active.is_(True))
        .order_by(Category.sort_order, Category.id)
    ).all()
    # 주제 sort_order는 직무 안에서의 순서라서, 직무 순서로 먼저 묶어야 직무별로 이어서 나온다.
    topic_rows = db.execute(
        select(Topic, Category.code)
        .join(Category, Category.id == Topic.category_id)
        .where(Topic.is_active.is_(True), Category.is_active.is_(True))
        .order_by(Category.sort_order, Category.id, Topic.sort_order, Topic.id)
    ).all()

    return CodesResponse(
        job_categories=[
            JobCategoryCode(
                id=category.id,
                code=category.code,
                name=category.name,
                label=category.name,
                sort_order=category.sort_order,
            )
            for category in categories
        ],
        topics=[
            TopicCode(
                id=topic.id,
                category_id=topic.category_id,
                job_category=category_code,
                name=topic.name,
                label=topic.name,
                sort_order=topic.sort_order,
            )
            for topic, category_code in topic_rows
        ],
        current_statuses=[
            CodeLabel(code=status, label=CURRENT_STATUS_LABELS[status])
            for status in CURRENT_STATUSES
        ],
        difficulties=[
            CodeLabel(code=code, label=label) for code, label in DIFFICULTY_LABELS.items()
        ],
        daily_goal_minutes=list(DAILY_GOAL_MINUTES_CHOICES),
    )
