"""목업 계정과 직무·주제 기준 데이터를 넣는다.

실행: docker compose exec api python -m app.seed
여러 번 실행해도 된다.
- 직무·주제: 없으면 넣고, 있으면 이름·정렬 순서를 이 파일 기준으로 맞춘다.
  목록에서 빠진 행은 지우지 않는다.
- 목업 계정: 없는 계정만 넣고, 이미 있는 계정은 건드리지 않는다.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import (
    AccountType,
    Category,
    InstructorProfile,
    InstructorTopic,
    LearnerProfile,
    LearnerTopicInterest,
    Role,
    Topic,
    User,
    UserRole,
)

logger = logging.getLogger(__name__)

SEED_PASSWORD = "baeugo1234"

# (id, code, name). id는 sort_order와 같고, 명세 예시(/codes)의 id와 맞춘다.
CATEGORIES = [
    (1, "PLANNING_PM", "기획·PM"),
    (2, "MARKETING", "마케팅"),
    (3, "DATA_ANALYSIS", "데이터 분석"),
    (4, "DEV_IT", "개발·IT"),
    (5, "DESIGN", "디자인"),
    (6, "OFFICE_PRODUCTIVITY", "사무 생산성"),
    (7, "CAREER", "커리어"),
]

# 주제 id = 직무 id × 10 + 순번 (예: 11 문서 작성, 61 업무 자동화)
TOPIC_NAMES = {
    1: ["문서 작성", "서비스 기획", "프로젝트 일정 관리"],
    2: ["퍼포먼스 마케팅", "콘텐츠 마케팅", "SNS 채널 운영"],
    3: ["엑셀 데이터 분석", "SQL 기초", "데이터 시각화"],
    4: ["프로그래밍 기초", "웹 개발", "Git 협업"],
    5: ["UI·UX 디자인", "피그마 활용", "프레젠테이션 디자인"],
    6: ["업무 자동화", "엑셀 실무", "생성형 AI 업무 활용"],
    7: ["이력서·자기소개서", "면접 준비", "비즈니스 커뮤니케이션"],
}


@dataclass
class SeedAccount:
    email: str
    name: str
    account_type: AccountType = AccountType.MEMBER
    roles: list[Role] = field(default_factory=list)
    learner_profile: dict | None = None
    learner_topic_ids: list[int] = field(default_factory=list)
    instructor_profile: dict | None = None
    instructor_topic_ids: list[int] = field(default_factory=list)


SEED_ACCOUNTS = [
    # 역할 선택·온보딩 시연용
    SeedAccount(email="learner1@baeugo.test", name="최새봄"),
    SeedAccount(
        email="learner2@baeugo.test",
        name="김배우",
        roles=[Role.LEARNER],
        learner_profile={
            "current_status": "EMPLOYED",
            "target_category_id": 1,
            "demo_payment_label": "신한카드 (1234)",
        },
        learner_topic_ids=[11],
    ),
    SeedAccount(
        email="instructor1@baeugo.test",
        name="이하늘",
        roles=[Role.INSTRUCTOR],
        instructor_profile={
            "primary_category_id": 6,
            "bio": "10년 차 사무 자동화 실무자",
            "demo_settlement_label": "신한은행 (5678) 이하늘",
        },
        instructor_topic_ids=[61],
    ),
    SeedAccount(
        email="instructor2@baeugo.test",
        name="정다온",
        roles=[Role.INSTRUCTOR],
        instructor_profile={
            "primary_category_id": 4,
            "bio": "웹 서비스 개발 7년 차 백엔드 개발자",
        },
        instructor_topic_ids=[41, 42],
    ),
    SeedAccount(email="admin@baeugo.test", name="운영자", account_type=AccountType.OPERATOR),
]


def seed_categories(db: Session) -> None:
    rows = [
        {"id": category_id, "code": code, "name": name, "sort_order": category_id}
        for category_id, code, name in CATEGORIES
    ]
    upsert_name_and_sort_order(db, Category, rows)
    reset_id_sequence(db, Category.__tablename__)


def seed_topics(db: Session) -> None:
    rows = [
        {
            "id": category_id * 10 + order,
            "category_id": category_id,
            "name": name,
            "sort_order": order,
        }
        for category_id, names in TOPIC_NAMES.items()
        for order, name in enumerate(names, start=1)
    ]
    upsert_name_and_sort_order(db, Topic, rows)
    reset_id_sequence(db, Topic.__tablename__)


def upsert_name_and_sort_order(
    db: Session, model: type[Category | Topic], rows: list[dict]
) -> None:
    statement = insert(model).values(rows)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[model.id],
            set_={"name": statement.excluded.name, "sort_order": statement.excluded.sort_order},
        )
    )


def reset_id_sequence(db: Session, table_name: str) -> None:
    # id를 직접 넣으면 시퀀스가 따라오지 않아, 이후 자동 증가 insert가 PK 충돌을 낸다.
    db.execute(
        text(
            f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
            f"(SELECT COALESCE(MAX(id), 1) FROM {table_name}))"
        )
    )


def seed_accounts(db: Session) -> None:
    existing_emails = set(db.scalars(select(User.email)))
    for account in SEED_ACCOUNTS:
        if account.email in existing_emails:
            continue
        add_account(db, account)


def add_account(db: Session, account: SeedAccount) -> None:
    active_mode = account.roles[0] if account.roles else None
    user = User(
        email=account.email,
        password_hash=hash_password(SEED_PASSWORD),
        name=account.name,
        account_type=account.account_type,
        active_mode=active_mode,
    )
    db.add(user)
    db.flush()

    db.add_all(UserRole(user_id=user.id, role=role) for role in account.roles)
    if account.learner_profile is not None:
        db.add(LearnerProfile(user_id=user.id, **account.learner_profile))
    if account.instructor_profile is not None:
        db.add(InstructorProfile(user_id=user.id, **account.instructor_profile))
    db.add_all(
        LearnerTopicInterest(learner_id=user.id, topic_id=topic_id)
        for topic_id in account.learner_topic_ids
    )
    db.add_all(
        InstructorTopic(instructor_id=user.id, topic_id=topic_id)
        for topic_id in account.instructor_topic_ids
    )
    db.flush()


def seed(db: Session) -> None:
    seed_categories(db)
    seed_topics(db)
    seed_accounts(db)
    db.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with SessionLocal() as db:
        seed(db)
        user_count = db.scalar(select(func.count()).select_from(User))
        topic_count = db.scalar(select(func.count()).select_from(Topic))
    logger.info("시드 완료: 사용자 %d명, 주제 %d개", user_count, topic_count)


if __name__ == "__main__":
    main()
