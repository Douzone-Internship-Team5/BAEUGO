"""시연 화면 캡처용 강좌·학습 데이터를 넣는다.

실행 (기본 시드 app.seed를 먼저 실행한다):
    docker compose exec api python -m app.seed_demo          # 시연 데이터가 없을 때만 추가
    docker compose exec api python -m app.seed_demo --reset  # 강좌·학습 기록을 지우고 다시

--reset은 이 PC의 개발 DB에 있는 모든 강좌, 영상, 퀴즈, 수강, 출석 기록과
media 폴더의 courses/ 아래 파일을 지운다. 계정, 직무, 주제, 학습 목표는 지우지 않는다.

만드는 것:
- 강좌 9개 (app.seed_demo_data): 공개 6, 수정 중 1, 운영자 비공개 1, 작성 중 1
  대본과 퀴즈는 검수가 끝난 상태로 넣으므로 AI 처리(Gemini)를 쓰지 않는다.
- 영상: seed_assets/demo의 슬라이드 이미지를 ffmpeg로 소리 없는 mp4로 만든다.
- 통계용 수강자 6명(learner3~8)과 지난 수강 기록, 강좌별 일별 통계, 좋아요
- learner2의 오늘 학습 기록: 수료 1, 수강 중 2(그중 하나는 복습할 오답 있음), 좋아요.
  오늘 기록은 실제 서비스 함수로 만들어 화면 숫자가 앱 로직과 같게 한다.
- learner2의 지난 출석 기록 (연속 출석일이 보이도록)
"""

import argparse
import logging
import random
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.timezone import SEOUL_TZ
from app.models import (
    AttendanceDay,
    Course,
    CourseDailyStat,
    Enrollment,
    LearningGoal,
    LearningTimeSlot,
    Question,
    QuestionChoice,
    Quiz,
    Role,
    Section,
    User,
    Video,
    VideoLike,
    VideoScript,
)
from app.schemas.learner_quiz import QuizActivitySession, QuizAnswerRequest
from app.schemas.learning import ActivitySessionInput, VideoProgressRequest, VideoViewRequest
from app.seed import SeedAccount, add_account, reset_id_sequence, seed
from app.seed_demo_data import COURSES, DemoCourse, DemoQuestion, DemoVideo
from app.services import learner_quiz, learning
from app.services.course_service import validate_publish_readiness
from app.services.storage import get_media_dir

logger = logging.getLogger(__name__)

ASSET_DIR = Path(__file__).parent / "seed_assets" / "demo"
ADMIN_EMAIL = "admin@baeugo.test"
LEARNER2_EMAIL = "learner2@baeugo.test"

DEMO_LEARNERS = [
    SeedAccount(
        email="learner3@baeugo.test",
        name="박하린",
        roles=[Role.LEARNER],
        learner_profile={"current_status": "JOB_SEEKER", "target_category_id": 4},
        learner_topic_ids=[42],
        daily_target_minutes=30,
    ),
    SeedAccount(
        email="learner4@baeugo.test",
        name="오지훈",
        roles=[Role.LEARNER],
        learner_profile={"current_status": "EMPLOYED", "target_category_id": 3},
        learner_topic_ids=[31, 32],
        daily_target_minutes=20,
    ),
    SeedAccount(
        email="learner5@baeugo.test",
        name="한유진",
        roles=[Role.LEARNER],
        learner_profile={"current_status": "CAREER_CHANGE", "target_category_id": 6},
        learner_topic_ids=[61],
        daily_target_minutes=20,
    ),
    SeedAccount(
        email="learner6@baeugo.test",
        name="서도윤",
        roles=[Role.LEARNER],
        learner_profile={"current_status": "EMPLOYED", "target_category_id": 4},
        learner_topic_ids=[43],
        daily_target_minutes=10,
    ),
    SeedAccount(
        email="learner7@baeugo.test",
        name="임채원",
        roles=[Role.LEARNER],
        learner_profile={"current_status": "JOB_SEEKER", "target_category_id": 1},
        learner_topic_ids=[11],
        daily_target_minutes=30,
    ),
    SeedAccount(
        email="learner8@baeugo.test",
        name="강민서",
        roles=[Role.LEARNER],
        learner_profile={"current_status": "EMPLOYED", "target_category_id": 6},
        learner_topic_ids=[63],
        daily_target_minutes=20,
    ),
]

# 통계용 수강자의 지난 수강 기록 (강좌 slug → 수강자 이메일)
BACKFILL_ENROLLMENTS = {
    "excel-automation": ["learner3", "learner4", "learner5", "learner7", "learner8"],
    "ai-report": ["learner3", "learner5", "learner6", "learner7", "learner8"],
    "one-page-report": ["learner4", "learner7"],
    "pivot-sales": ["learner4", "learner5", "learner8"],
    "vba-start": ["learner5", "learner8"],
    "web-basics": ["learner3", "learner4", "learner6", "learner7"],
    "git-collab": ["learner3", "learner6"],
    "sql-basics": ["learner4", "learner6"],
}

# learner2의 지난 출석 (오늘부터 며칠 전, 공부한 초). 목표 20분 = 1200초.
LEARNER2_PAST_DAYS = {
    1: 1500,
    2: 1320,
    3: 1680,
    4: 1260,
    5: 540,
    6: 1440,
    7: 1380,
    9: 1250,
    10: 1600,
    12: 900,
}


class DemoSeedError(Exception):
    pass


@dataclass
class DemoSummary:
    courses: int
    videos: int
    learners: int


def kst(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=SEOUL_TZ)


def encode_slide_video(image: Path, target: Path, seconds: int) -> None:
    """정지 화면 한 장을 소리 없는 mp4로 만든다. 1fps로 만들어 파일이 작고 빠르다."""
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-framerate",
            "1",
            "-i",
            str(image),
            "-t",
            str(seconds),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-tune",
            "stillimage",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "1",
            "-g",
            "10",
            "-movflags",
            "+faststart",
            str(target),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )


def demo_course_exists(db: Session) -> bool:
    titles = [course.title for course in COURSES]
    return db.scalar(select(Course.id).where(Course.title.in_(titles)).limit(1)) is not None


def reset_course_data(db: Session, media_dir: Path) -> None:
    # courses를 참조하는 표(섹션, 영상, 퀴즈, 수강, 응시, 오답, 통계, 처리 작업)가 함께 비워진다.
    db.execute(text("TRUNCATE courses, attendance_days RESTART IDENTITY CASCADE"))
    db.flush()
    shutil.rmtree(media_dir / "courses", ignore_errors=True)


def ensure_demo_learners(db: Session) -> dict[str, User]:
    existing = set(db.scalars(select(User.email)))
    for account in DEMO_LEARNERS:
        if account.email in existing:
            continue
        add_account(db, account)
    reset_id_sequence(db, User.__tablename__)
    for account in DEMO_LEARNERS:
        learner_id = db.scalar(select(User.id).where(User.email == account.email))
        db.execute(
            insert(LearningGoal)
            .values(learner_id=learner_id, daily_target_minutes=account.daily_target_minutes)
            .on_conflict_do_nothing()
        )
    emails = [account.email for account in DEMO_LEARNERS]
    users = db.scalars(select(User).where(User.email.in_(emails)))
    return {user.email.split("@")[0]: user for user in users}


def add_questions(db: Session, quiz: Quiz, questions: tuple[DemoQuestion, ...], by: str) -> None:
    for order_no, spec in enumerate(questions, start=1):
        question = Question(
            quiz_id=quiz.id,
            content=spec.content,
            correct_choice_no=spec.answer,
            explanation=spec.explanation,
            generated_by=by,
            order_no=order_no,
        )
        db.add(question)
        db.flush()
        db.add_all(
            QuestionChoice(question_id=question.id, choice_no=no, content=content)
            for no, content in enumerate(spec.choices, start=1)
        )


def add_video(
    db: Session,
    course: Course,
    spec: DemoCourse,
    video_spec: DemoVideo,
    *,
    section_id: int | None,
    reviewed_at: datetime | None,
    media_dir: Path,
) -> Video:
    slide = ASSET_DIR / spec.slug / f"{video_spec.slug}.png"
    video_key = f"courses/{course.id}/videos/{video_spec.slug}.mp4"
    thumbnail_key = f"courses/{course.id}/videos/{video_spec.slug}.png"
    encode_slide_video(slide, media_dir / video_key, video_spec.duration_seconds)
    shutil.copyfile(slide, media_dir / thumbnail_key)

    video = Video(
        course_id=course.id,
        section_id=section_id,
        video_type="LESSON" if section_id is not None else "OT",
        title=video_spec.title,
        video_storage_key=video_key,
        thumbnail_storage_key=thumbnail_key,
        duration_seconds=video_spec.duration_seconds,
        sort_order=1 if section_id is not None else 0,
        stt_status="COMPLETED",
        transcript_reviewed_at=reviewed_at,
    )
    db.add(video)
    db.flush()
    step_ms = video_spec.duration_seconds * 1000 // len(video_spec.script)
    db.add_all(
        VideoScript(
            video_id=video.id,
            seq=seq,
            start_ms=(seq - 1) * step_ms,
            end_ms=seq * step_ms,
            content=content,
        )
        for seq, content in enumerate(video_spec.script, start=1)
    )
    return video


def create_course(
    db: Session, spec: DemoCourse, users: dict[str, User], media_dir: Path, today: date
) -> Course:
    instructor = users[spec.instructor_email]
    published_at = kst(min(spec.published_on, today), 10) if spec.published_on else None
    reviewed_at = (published_at or kst(today, 9)) - timedelta(days=1)
    course = Course(
        instructor_id=instructor.id,
        status=spec.status,
        category_id=spec.category_id,
        topic_id=spec.topic_id,
        title=spec.title,
        description=spec.description,
        difficulty=spec.difficulty,
        price_krw=spec.price_krw,
        published_at=published_at,
    )
    if spec.status == "EDITING":
        course.closed_by_user_id = instructor.id
        course.closed_at = kst(spec.closed_on, 14)
    if spec.status == "OPERATOR_HIDDEN":
        course.closed_by_user_id = users[ADMIN_EMAIL].id
        course.closed_at = kst(spec.closed_on, 16)
        course.closed_reason = spec.closed_reason
    db.add(course)
    db.flush()

    thumbnail_key = f"courses/{course.id}/thumbnail.png"
    (media_dir / thumbnail_key).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ASSET_DIR / spec.slug / "thumbnail.png", media_dir / thumbnail_key)
    course.thumbnail_storage_key = thumbnail_key

    add_video(
        db, course, spec, spec.ot, section_id=None, reviewed_at=reviewed_at, media_dir=media_dir
    )
    lesson_reviewed_at = reviewed_at if spec.reviewed else None
    for sort_order, section_spec in enumerate(spec.sections, start=1):
        section = Section(course_id=course.id, title=section_spec.title, sort_order=sort_order)
        db.add(section)
        db.flush()
        add_video(
            db,
            course,
            spec,
            section_spec.lesson,
            section_id=section.id,
            reviewed_at=lesson_reviewed_at,
            media_dir=media_dir,
        )
        unit_quiz = Quiz(
            course_id=course.id,
            section_id=section.id,
            quiz_type="UNIT",
            ai_status="COMPLETED",
            reviewed_by=instructor.id if spec.reviewed else None,
            reviewed_at=lesson_reviewed_at,
        )
        db.add(unit_quiz)
        db.flush()
        add_questions(db, unit_quiz, section_spec.quiz, "AI")

    if spec.final_quiz:
        final_quiz = Quiz(
            course_id=course.id,
            quiz_type="FINAL",
            ai_status=None,
            reviewed_by=instructor.id,
            reviewed_at=reviewed_at,
        )
        db.add(final_quiz)
        db.flush()
        add_questions(db, final_quiz, spec.final_quiz, "INSTRUCTOR")

    db.flush()
    db.expire(course)
    if spec.status != "DRAFT":
        ready, missing = validate_publish_readiness(course, db)
        if not ready:
            raise DemoSeedError(f"{spec.title} 공개 조건 미충족: {missing}")
    return course


def visible_until(spec: DemoCourse, today: date) -> date:
    """새 수강생과 조회가 생기는 마지막 날."""
    if spec.status in ("EDITING", "OPERATOR_HIDDEN"):
        return min(spec.closed_on, today)
    return today


STAT_COLUMNS = (
    "lesson_views",
    "new_students",
    "expected_revenue",
    "available_quiz_cnt",
    "first_submitted_cnt",
    "first_answer_cnt",
    "first_correct_cnt",
)


def stat_row(days: dict[date, dict[str, int]], day: date) -> dict[str, int]:
    return days.setdefault(day, dict.fromkeys(STAT_COLUMNS, 0))


def backfill_history(
    db: Session, courses: dict[str, Course], learners: dict[str, User], today: date
) -> None:
    """통계용 수강자의 지난 수강, 강좌별 일별 통계, 영상 조회수·좋아요를 넣는다.

    오늘 날짜 통계는 learner2의 실제 활동이 서비스 함수로 더한다.
    """
    yesterday = today - timedelta(days=1)
    specs = {spec.slug: spec for spec in COURSES}
    for slug, learner_keys in BACKFILL_ENROLLMENTS.items():
        spec = specs[slug]
        course = courses[slug]
        rng = random.Random(slug)
        start = spec.published_on
        last_day = min(visible_until(spec, today), yesterday)
        span = max((last_day - start).days, 1)
        quiz_count = len(spec.sections) + (1 if spec.final_quiz else 0)

        days: dict[date, dict[str, int]] = {}
        for index, key in enumerate(learner_keys):
            enrolled_on = start + timedelta(days=span * (index + 1) // (len(learner_keys) + 1))
            enrolled_at = kst(enrolled_on, 9 + index * 2, 15)
            db.add(
                Enrollment(
                    learner_id=learners[key].id,
                    course_id=course.id,
                    access_source="DEMO_GRANT",
                    price_snapshot_krw=spec.price_krw,
                    status="IN_PROGRESS",
                    enrolled_at=enrolled_at,
                    last_studied_at=enrolled_at + timedelta(hours=1),
                )
            )
            row = stat_row(days, enrolled_on)
            row["new_students"] += 1
            row["expected_revenue"] += spec.price_krw
            row["available_quiz_cnt"] += quiz_count

            submit_day = min(enrolled_on + timedelta(days=1), last_day)
            submitted = rng.randint(1, quiz_count)
            answers = submitted * 3
            submit_row = stat_row(days, submit_day)
            submit_row["first_submitted_cnt"] += submitted
            submit_row["first_answer_cnt"] += answers
            submit_row["first_correct_cnt"] += answers - rng.randint(0, submitted)

        total_views = 0
        day = start
        while day <= last_day:
            views = rng.randint(2, 9) + (4 if day.weekday() < 5 else 0)
            stat_row(days, day)["lesson_views"] += views
            total_views += views
            day += timedelta(days=1)

        db.execute(
            insert(CourseDailyStat),
            [{"course_id": course.id, "start_date": d, **values} for d, values in days.items()],
        )

        videos = db.scalars(select(Video).where(Video.course_id == course.id)).all()
        lessons = [video for video in videos if video.video_type == "LESSON"]
        for video in lessons:
            video.view_count = total_views // len(lessons)
        ot = next(video for video in videos if video.video_type == "OT")
        ot.view_count = rng.randint(120, 480)
        likers = [learners[key] for key in learner_keys if rng.random() < 0.7]
        db.add_all(VideoLike(user_id=learner.id, video_id=ot.id) for learner in likers)
        ot.like_count = len(likers)
    db.flush()


class ActivityClock:
    """learner2의 오늘 활동 시간을 겹치지 않게 차례로 배정한다.

    학습 활동은 오늘 날짜이고 지금보다 앞이어야 한다. 자정 직후에 실행해도 되도록
    남은 시간이 부족하면 모든 활동 길이를 같은 비율로 줄인다.
    """

    def __init__(self, now: datetime, planned_seconds: int):
        day_start = datetime.combine(now.date(), time(0, 0, 1), tzinfo=SEOUL_TZ)
        self.cursor = max(now - timedelta(seconds=planned_seconds + 600), day_start)
        available = (now - self.cursor).total_seconds() - 60
        self.scale = min(1.0, max(available, 1) / planned_seconds)

    def next(self, seconds: int) -> tuple[datetime, datetime, int]:
        length = max(1, int(seconds * self.scale))
        started_at = self.cursor
        ended_at = started_at + timedelta(seconds=length)
        self.cursor = ended_at + timedelta(seconds=1)
        return started_at, ended_at, length


def watch(db: Session, clock: ActivityClock, user: User, video: Video, until: int) -> None:
    started_at, ended_at, length = clock.next(until)
    learning.save_video_progress(
        db,
        video.id,
        user,
        VideoProgressRequest(
            last_position_seconds=until,
            watched_range=[0, until],
            activity_session=ActivitySessionInput(
                session_key=uuid4().hex,
                started_at=started_at,
                ended_at=ended_at,
                active_seconds=length,
            ),
        ),
    )


def answer_attempt(
    db: Session, clock: ActivityClock, user: User, attempt, wrong_orders: set[int]
) -> None:
    quiz_id = attempt.quiz_id if attempt.quiz_id is not None else 0
    for item in sorted(attempt.questions, key=lambda question: question.order_no):
        correct = db.get(Question, item.question_id).correct_choice_no
        selected = correct % 4 + 1 if item.order_no in wrong_orders else correct
        started_at, ended_at, length = clock.next(25)
        learner_quiz.submit_answer(
            db,
            user.id,
            quiz_id,
            QuizAnswerRequest(
                attempt_id=attempt.attempt_id,
                attempt_question_id=item.attempt_question_id,
                selected_choice_no=selected,
                activity_session=QuizActivitySession(
                    session_key=uuid4().hex,
                    started_at=started_at,
                    ended_at=ended_at,
                    active_seconds=length,
                ),
            ),
        )


def lessons_of(course: Course) -> list[Video]:
    sections = sorted(course.sections, key=lambda section: section.sort_order)
    return [
        video for section in sections for video in section.videos if video.video_type == "LESSON"
    ]


def play_learner2(db: Session, courses: dict[str, Course], now: datetime) -> None:
    user = db.scalars(select(User).where(User.email == LEARNER2_EMAIL)).one()
    if not db.scalar(select(LearningTimeSlot.id).where(LearningTimeSlot.learner_id == user.id)):
        db.add_all(
            [
                LearningTimeSlot(
                    learner_id=user.id, start_time=time(7, 30), end_time=time(8, 30), sort_order=1
                ),
                LearningTimeSlot(
                    learner_id=user.id, start_time=time(21, 0), end_time=time(22, 30), sort_order=2
                ),
            ]
        )
        db.flush()

    completed = courses["ai-report"]
    in_progress = courses["excel-automation"]
    just_started = courses["web-basics"]
    planned = (
        sum(video.duration_seconds for video in lessons_of(completed))
        + lessons_of(in_progress)[0].duration_seconds
        + lessons_of(in_progress)[1].duration_seconds * 2 // 5
        + 25 * 13
    )
    clock = ActivityClock(now, planned)

    for slug in ("web-basics", "git-collab", "one-page-report"):
        ot = next(video for video in courses[slug].videos if video.video_type == "OT")
        learning.record_video_view(db, ot.id, user, VideoViewRequest(played_seconds=30))
        learning.like_video(db, ot.id, user)

    for course in (completed, in_progress, just_started):
        learning.enroll_course(db, course.id, user, None)

    # 수료: 1단원 퀴즈 2번을 틀렸다가 종합 퀴즈 뒤 오답 복습에서 맞힌다.
    first, second = lessons_of(completed)
    watch(db, clock, user, first, first.duration_seconds)
    answer_attempt(
        db, clock, user, learner_quiz.get_unit_attempt(db, user.id, first.section_id), {2}
    )
    watch(db, clock, user, second, second.duration_seconds)
    answer_attempt(
        db, clock, user, learner_quiz.get_unit_attempt(db, user.id, second.section_id), set()
    )
    final_attempt = learner_quiz.get_course_attempt(db, user.id, completed.id, "FINAL", None)
    answer_attempt(db, clock, user, final_attempt, set())
    review = learner_quiz.get_course_attempt(db, user.id, completed.id, "WRONG_REVIEW", None)
    answer_attempt(db, clock, user, review, set())

    # 수강 중: 1강을 보고 1단원 퀴즈 1번을 틀린 채(복습 필요) 2강을 40%까지 본다.
    first, second = lessons_of(in_progress)
    watch(db, clock, user, first, first.duration_seconds)
    answer_attempt(
        db, clock, user, learner_quiz.get_unit_attempt(db, user.id, first.section_id), {1}
    )
    watch(db, clock, user, second, second.duration_seconds * 2 // 5)


def add_past_attendance(db: Session, today: date) -> None:
    learner_id = db.scalar(select(User.id).where(User.email == LEARNER2_EMAIL))
    target_minutes = db.get(LearningGoal, learner_id).daily_target_minutes
    rows = [
        {
            "learner_id": learner_id,
            "study_date": today - timedelta(days=days_ago),
            "target_minutes_snapshot": target_minutes,
            "active_seconds": seconds,
            "is_attended": seconds >= target_minutes * 60,
        }
        for days_ago, seconds in LEARNER2_PAST_DAYS.items()
    ]
    db.execute(insert(AttendanceDay).values(rows).on_conflict_do_nothing())


def run(db: Session, media_dir: Path, *, reset: bool, now: datetime | None = None) -> DemoSummary:
    now = now or datetime.now(SEOUL_TZ)
    today = now.date()
    seed(db)
    if reset:
        reset_course_data(db, media_dir)
    elif demo_course_exists(db):
        raise DemoSeedError(
            "이미 시연 강좌가 있습니다. 다시 만들려면 --reset을 붙여 실행하세요. "
            "(--reset은 이 PC의 모든 강좌·학습 기록을 지웁니다)"
        )

    learners = ensure_demo_learners(db)
    emails = {spec.instructor_email for spec in COURSES} | {ADMIN_EMAIL}
    users = {user.email: user for user in db.scalars(select(User).where(User.email.in_(emails)))}

    courses = {spec.slug: create_course(db, spec, users, media_dir, today) for spec in COURSES}
    backfill_history(db, courses, learners, today)
    add_past_attendance(db, today)
    db.commit()

    play_learner2(db, courses, now)
    db.commit()
    return DemoSummary(
        courses=len(courses),
        videos=sum(1 + len(spec.sections) for spec in COURSES),
        learners=len(learners),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="시연 화면 캡처용 데이터를 넣습니다.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="이 PC의 모든 강좌·수강·출석 기록과 media/courses 파일을 지우고 다시 만듭니다.",
    )
    args = parser.parse_args()
    media_dir = get_media_dir()
    with SessionLocal() as db:
        if args.reset:
            course_count = db.scalar(text("SELECT count(*) FROM courses"))
            enrollment_count = db.scalar(text("SELECT count(*) FROM enrollments"))
            logger.info(
                "--reset: 강좌 %d개, 수강 %d건, 출석 기록과 %s를 지웁니다.",
                course_count,
                enrollment_count,
                media_dir / "courses",
            )
        try:
            summary = run(db, media_dir, reset=args.reset)
        except DemoSeedError as error:
            raise SystemExit(str(error)) from None
    logger.info(
        "시연 시드 완료: 강좌 %d개, 영상 %d개, 통계용 수강자 %d명",
        summary.courses,
        summary.videos,
        summary.learners,
    )


if __name__ == "__main__":
    main()
