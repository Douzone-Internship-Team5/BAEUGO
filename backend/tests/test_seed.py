from sqlalchemy import func, select

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.core.security import verify_password
from app.models import Base, Category, LearningGoal, Topic, User, UserRole
from app.seed import (
    PROFILE_PRESET_SOURCE_DIR,
    SEED_ACCOUNTS,
    SEED_PASSWORD,
    TOPIC_NAMES,
    seed,
    seed_profile_presets,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def count_rows(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_migration_matches_models(db):
    context = MigrationContext.configure(db.connection(), opts={"compare_type": True})

    assert compare_metadata(context, Base.metadata) == []


def test_seed_is_idempotent(db):
    counts_before = [
        count_rows(db, model) for model in (User, UserRole, Category, Topic, LearningGoal)
    ]

    seed(db)

    counts_after = [
        count_rows(db, model) for model in (User, UserRole, Category, Topic, LearningGoal)
    ]
    assert counts_after == counts_before
    assert counts_before[0] == len(SEED_ACCOUNTS)


def test_seed_overwrites_category_and_topic_name_and_sort_order(db):
    category = db.get(Category, 1)
    topic = db.get(Topic, 11)
    category.name, category.sort_order = "예전 직무명", 99
    topic.name, topic.sort_order = "예전 주제명", 99
    db.flush()

    seed(db)
    db.refresh(category)
    db.refresh(topic)

    assert (category.name, category.sort_order) == ("기획·PM", 1)
    assert (topic.name, topic.sort_order) == ("문서 작성", 1)


def test_seed_keeps_topics_missing_from_list(db):
    removed_topic = Topic(category_id=1, name="목록에서 빠진 주제", sort_order=9)
    db.add(removed_topic)
    db.flush()

    seed(db)

    assert db.get(Topic, removed_topic.id) is not None


def test_seed_does_not_overwrite_existing_account(db):
    learner = db.scalars(select(User).where(User.email == "learner2@baeugo.test")).one()
    learner.name = "바뀐 이름"
    db.flush()

    seed(db)
    db.refresh(learner)

    assert learner.name == "바뀐 이름"


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
        assert topic_count == 3
    assert len(topics) == sum(len(names) for names in TOPIC_NAMES.values())
    assert db.get(Topic, 11).name == "문서 작성"
    assert db.get(Topic, 61).name == "업무 자동화"


def test_new_topic_gets_id_after_seeded_ones(db):
    topic = Topic(category_id=1, name="새 주제", sort_order=99)
    db.add(topic)
    db.flush()

    assert topic.id > 73


def test_seed_adds_learning_goal_for_learner2(db):
    learner = db.scalars(select(User).where(User.email == "learner2@baeugo.test")).one()

    goal = db.get(LearningGoal, learner.id)

    assert (goal.daily_target_minutes, goal.time_zone) == (20, "Asia/Seoul")


def test_seed_adds_missing_learning_goal_to_existing_account(db):
    learner = db.scalars(select(User).where(User.email == "learner2@baeugo.test")).one()
    db.delete(db.get(LearningGoal, learner.id))
    db.flush()

    seed(db)

    assert db.get(LearningGoal, learner.id).daily_target_minutes == 20


def test_preset_source_images_are_small_png():
    for preset_id in (1, 2, 3, 4):
        content = (PROFILE_PRESET_SOURCE_DIR / f"{preset_id}.png").read_bytes()
        assert content.startswith(PNG_SIGNATURE)
        assert len(content) < 50 * 1024


def test_seed_copies_profile_presets(tmp_path):
    copied = seed_profile_presets(tmp_path)

    assert copied == 4
    for preset_id in (1, 2, 3, 4):
        target = tmp_path / "profiles" / "presets" / f"{preset_id}.png"
        source = PROFILE_PRESET_SOURCE_DIR / f"{preset_id}.png"
        assert target.read_bytes() == source.read_bytes()


def test_seed_profile_presets_twice_keeps_existing_files(tmp_path):
    seed_profile_presets(tmp_path)
    replaced = tmp_path / "profiles" / "presets" / "2.png"
    replaced.write_bytes(b"design image")

    copied = seed_profile_presets(tmp_path)

    assert copied == 0
    assert replaced.read_bytes() == b"design image"


def test_seed_function_does_not_touch_media(db, monkeypatch):
    # conftest가 seed()를 부르므로, 여기서 파일을 쓰면 개발 media_data에 테스트 파일이 남는다.
    def fail():
        raise AssertionError("seed()가 미디어 폴더에 접근했습니다.")

    monkeypatch.setattr("app.seed.get_media_dir", fail)

    seed(db)


def test_seed_profile_presets_fills_only_missing_files(tmp_path):
    seed_profile_presets(tmp_path)
    (tmp_path / "profiles" / "presets" / "3.png").unlink()

    assert seed_profile_presets(tmp_path) == 1
    assert (tmp_path / "profiles" / "presets" / "3.png").exists()
