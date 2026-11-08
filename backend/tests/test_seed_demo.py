from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import (
    Course,
    CourseDailyStat,
    Enrollment,
    QuestionRecord,
    User,
    Video,
    VideoLike,
)
from app.seed_demo import ASSET_DIR, DemoSeedError, encode_slide_video, run
from app.seed_demo_data import COURSES
from app.services.attendance import get_current_streak
from app.services.course_service import validate_publish_readiness
from app.services.storage import extract_video_duration
from tests.conftest import auth_header


def fake_encode(image: Path, target: Path, seconds: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"demo")


@pytest.fixture
def demo(db, monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.storage.get_media_dir", lambda: tmp_path)
    monkeypatch.setattr("app.seed_demo.encode_slide_video", fake_encode)
    run(db, tmp_path, reset=True)
    return tmp_path


def course_by_title(db, title: str) -> Course:
    return db.scalars(select(Course).where(Course.title == title)).one()


def test_demo_seed_makes_courses_in_every_status(db, demo):
    counts = dict(db.execute(select(Course.status, func.count()).group_by(Course.status)).all())
    assert counts == {"PUBLISHED": 6, "EDITING": 1, "OPERATOR_HIDDEN": 1, "DRAFT": 1}

    admin = db.scalars(select(User).where(User.email == "admin@baeugo.test")).one()
    hidden = course_by_title(db, "VBA 매크로 첫걸음")
    assert hidden.closed_by_user_id == admin.id
    assert hidden.closed_reason
    editing = course_by_title(db, "피벗 테이블로 끝내는 매출 분석")
    assert editing.closed_by_user_id == editing.instructor_id
    assert editing.closed_reason is None


def test_demo_courses_pass_publish_check_except_draft(db, demo):
    for course in db.scalars(select(Course)):
        ready, missing = validate_publish_readiness(course, db)
        assert ready is (course.status != "DRAFT"), (course.title, missing)


def test_demo_seed_writes_media_into_given_folder(db, demo):
    for course in db.scalars(select(Course)):
        assert (demo / course.thumbnail_storage_key).is_file()
    for video in db.scalars(select(Video)):
        assert (demo / video.video_storage_key).is_file()
        assert (demo / video.thumbnail_storage_key).is_file()


def test_learner2_has_completed_in_progress_and_review_needed(db, demo, get_user):
    learner2 = get_user("learner2@baeugo.test")
    statuses = dict(
        db.execute(
            select(Course.title, Enrollment.status)
            .join(Enrollment, Enrollment.course_id == Course.id)
            .where(Enrollment.learner_id == learner2.id)
        ).all()
    )
    assert statuses == {
        "생성형 AI로 보고서 초안 빠르게 쓰기": "COMPLETED",
        "실무자를 위한 엑셀 자동화": "IN_PROGRESS",
        "웹 개발 입문: HTML과 CSS": "IN_PROGRESS",
    }
    review_needed = db.scalar(
        select(func.count())
        .select_from(QuestionRecord)
        .join(Enrollment, Enrollment.id == QuestionRecord.enrollment_id)
        .where(Enrollment.learner_id == learner2.id, QuestionRecord.status == "REVIEW_NEEDED")
    )
    assert review_needed == 1
    likes = db.scalar(
        select(func.count()).select_from(VideoLike).where(VideoLike.user_id == learner2.id)
    )
    assert likes == 3
    # 어제부터 4일 연속 출석, 5일 전은 목표 미달 (오늘 활동은 목표 20분보다 적다)
    assert get_current_streak(db, learner2.id) == 4


def test_demo_seed_fills_instructor_stats(db, demo, client, get_user):
    instructor = get_user("instructor1@baeugo.test")
    response = client.get("/api/v1/instructor/stats", headers=auth_header(instructor.id))
    assert response.status_code == 200
    assert db.scalar(select(func.count()).select_from(CourseDailyStat)) > 0


def test_admin_course_list_shows_only_published_history(db, demo, client, get_user):
    admin = get_user("admin@baeugo.test")
    response = client.get("/api/v1/admin/courses?size=100", headers=auth_header(admin.id))
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 8


def test_demo_seed_refuses_to_run_twice_without_reset(db, demo):
    with pytest.raises(DemoSeedError):
        run(db, demo, reset=False)


def test_reset_replaces_courses_and_media(db, demo):
    leftover = demo / "courses" / "999" / "old.mp4"
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(b"old")

    run(db, demo, reset=True)

    assert not leftover.exists()
    assert db.scalar(select(func.count()).select_from(Course)) == len(COURSES)


def test_demo_assets_exist_for_every_video_and_stay_small():
    total = 0
    for course in COURSES:
        names = ["thumbnail", course.ot.slug, *(section.lesson.slug for section in course.sections)]
        for name in names:
            path = ASSET_DIR / course.slug / f"{name}.png"
            assert path.is_file(), path
            total += path.stat().st_size
    assert total < 1_000_000


def test_encode_slide_video_makes_video_of_given_length(tmp_path):
    target = tmp_path / "out.mp4"
    encode_slide_video(ASSET_DIR / "web-basics" / "ot.png", target, 3)
    assert extract_video_duration(target) == 3
