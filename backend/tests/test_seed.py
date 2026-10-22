from sqlalchemy import func, select

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.core.security import verify_password
from app.models import Base, Category, Topic, User, UserRole
from app.seed import SEED_ACCOUNTS, SEED_PASSWORD, seed


def count_rows(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_migration_matches_models(db):
    context = MigrationContext.configure(db.connection(), opts={"compare_type": True})

    assert compare_metadata(context, Base.metadata) == []


def test_seed_is_idempotent(db):
    counts_before = [count_rows(db, model) for model in (User, UserRole, Category, Topic)]

    seed(db)

    counts_after = [count_rows(db, model) for model in (User, UserRole, Category, Topic)]
    assert counts_after == counts_before
    assert counts_before[0] == len(SEED_ACCOUNTS)


def test_seed_passwords_are_hashed(db):
    for user in db.scalars(select(User)):
        assert user.password_hash != SEED_PASSWORD
        assert verify_password(SEED_PASSWORD, user.password_hash)


def test_seed_categories_and_topics_follow_id_rule(db):
    categories = db.scalars(select(Category).order_by(Category.id)).all()
    topics = db.scalars(select(Topic)).all()

    assert [category.code for category in categories] == [
        "PLANNING_PM",
        "MARKETING",
        "DATA_ANALYSIS",
        "DEV_IT",
        "DESIGN",
        "OFFICE_PRODUCTIVITY",
        "CAREER",
    ]
    for topic in topics:
        assert topic.id == topic.category_id * 10 + topic.sort_order
    for category in categories:
        topic_count = sum(1 for topic in topics if topic.category_id == category.id)
        assert 4 <= topic_count <= 5
    assert db.get(Topic, 11).name == "문서 작성"
    assert db.get(Topic, 61).name == "업무 자동화"


def test_new_topic_gets_id_after_seeded_ones(db):
    topic = Topic(category_id=1, name="새 주제", sort_order=99)
    db.add(topic)
    db.flush()

    assert topic.id > 75
