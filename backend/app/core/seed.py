from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.category import Category, Topic
from app.models.user import InstructorProfile, LearnerProfile, LearnerTopicInterest, User


def seed_data(db: Session) -> None:
    """기초 시드 데이터 생성 (직무, 주제, 목업 강사 및 수강생 계정)"""

    # 1. 카테고리(직무) 및 주제(토픽)
    categories_data = [
        (1, "PLANNING_PM", "기획·PM", 1, [(11, "문서 작성", 1), (12, "서비스 기획", 2)]),
        (2, "MARKETING", "마케팅", 2, [(21, "퍼포먼스 마케팅", 1)]),
        (3, "DATA_ANALYSIS", "데이터 분석", 3, [(31, "SQL/대시보드", 1)]),
        (4, "DEV_IT", "개발·IT", 4, [(41, "웹 개발", 1)]),
        (5, "DESIGN", "디자인", 5, [(51, "UI/UX 디자인", 1)]),
        (6, "OFFICE_PRODUCTIVITY", "사무 생산성", 6, [(61, "업무 자동화", 1), (62, "엑셀 실무", 2)]),
        (7, "CAREER", "커리어", 7, [(71, "이직/면접", 1)]),
    ]

    for cat_id, code, name, sort_order, topics in categories_data:
        category = db.get(Category, cat_id)
        if not category:
            # code로도 확인
            category = db.scalar(select(Category).where(Category.code == code))
        if not category:
            category = Category(id=cat_id, code=code, name=name, sort_order=sort_order, is_active=True)
            db.add(category)
            db.flush()
        else:
            category.name = name
            category.sort_order = sort_order
            category.is_active = True

        for topic_id, t_name, t_sort in topics:
            topic = db.get(Topic, topic_id)
            if not topic:
                topic = Topic(
                    id=topic_id,
                    category_id=category.id,
                    name=t_name,
                    sort_order=t_sort,
                    is_active=True,
                )
                db.add(topic)
                db.flush()
            else:
                topic.name = t_name
                topic.sort_order = t_sort
                topic.is_active = True

    # 2. 목업 강사 계정 (id=6, 이하늘, instructor1@baeugo.test)
    instructor = db.get(User, 6)
    if not instructor:
        instructor = db.scalar(select(User).where(User.email == "instructor1@baeugo.test"))
    if not instructor:
        instructor = User(
            id=6,
            email="instructor1@baeugo.test",
            password_hash="$2b$12$e80MvQzCj7Ucmf7w373cRe7E6bI6JkE7k1yX5sXw8Z4lE9I9rYt2y",  # mock
            name="이하늘",
            account_type="MEMBER",
            active_mode="INSTRUCTOR",
            profile_image_key="profiles/6.png",
            is_active=True,
        )
        db.add(instructor)
        db.flush()

        inst_profile = InstructorProfile(
            user_id=instructor.id,
            bio="10년 차 사무 자동화 실무자",
            demo_settlement_label="신한은행 (5678) 이하늘",
        )
        db.add(inst_profile)
    else:
        instructor.name = "이하늘"
        instructor.active_mode = "INSTRUCTOR"
        instructor.is_active = True
        if not instructor.instructor_profile:
            inst_profile = InstructorProfile(
                user_id=instructor.id,
                bio="10년 차 사무 자동화 실무자",
                demo_settlement_label="신한은행 (5678) 이하늘",
            )
            db.add(inst_profile)

    # 3. 목업 수강자 계정 (id=2, 김배우, learner2@baeugo.test)
    learner = db.get(User, 2)
    if not learner:
        learner = db.scalar(select(User).where(User.email == "learner2@baeugo.test"))
    if not learner:
        learner = User(
            id=2,
            email="learner2@baeugo.test",
            password_hash="$2b$12$e80MvQzCj7Ucmf7w373cRe7E6bI6JkE7k1yX5sXw8Z4lE9I9rYt2y",  # mock
            name="김배우",
            account_type="MEMBER",
            active_mode="LEARNER",
            profile_image_key="profiles/2.png",
            is_active=True,
        )
        db.add(learner)
        db.flush()

        learner_profile = LearnerProfile(
            user_id=learner.id,
            target_category_id=1,
            current_status="EMPLOYED",
            demo_payment_label="신한카드 (1234)",
        )
        db.add(learner_profile)
    else:
        learner.name = "김배우"
        learner.active_mode = "LEARNER"
        learner.is_active = True
        if not learner.learner_profile:
            learner_profile = LearnerProfile(
                user_id=learner.id,
                target_category_id=1,
                current_status="EMPLOYED",
                demo_payment_label="신한카드 (1234)",
            )
            db.add(learner_profile)

    # 목업 수강자 관심 주제(topic_id=11) 시드
    existing_interest = db.scalar(
        select(LearnerTopicInterest).where(
            LearnerTopicInterest.learner_id == learner.id,
            LearnerTopicInterest.topic_id == 11,
        )
    )
    if not existing_interest:
        db.add(LearnerTopicInterest(learner_id=learner.id, topic_id=11))

    db.commit()

    # PostgreSQL 환경에서 명시적 ID 삽입 후 시퀀스 동기화
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        for table_name in ("categories", "topics", "users"):
            db.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table_name}), 1), true)"
                )
            )
        db.commit()


if __name__ == "__main__":
    from app.core.database import SessionLocal

    with SessionLocal() as session:
        seed_data(session)
        print("Seed completed successfully.")
