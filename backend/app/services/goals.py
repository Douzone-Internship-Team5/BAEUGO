import calendar
import math
from datetime import date, datetime, timedelta

from sqlalchemy import case, desc, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.errors import AppError
from app.core.timezone import SEOUL_TZ
from app.models.attendance import AttendanceDay
from app.models.course import Course
from app.models.goal import LearningGoal
from app.models.learning import Enrollment
from app.models.profile import LearnerProfile, LearnerTopicInterest
from app.models.video import Video, VideoView
from app.schemas.goals import (
    AttendanceData,
    AttendanceStreak,
    CalendarDay,
    CurrentTimeSlot,
    RecommendedShortform,
    TodayData,
    TodayGoal,
)
from app.services.attendance import get_current_streak
from app.services.learning_completion import active_curriculum, completed_item_ids
from app.services.storage import get_thumbnail_url

OT_RECOMMENDATION_LIMIT = 10


def _require_goal(db: Session, learner_id: int) -> LearningGoal:
    goal = db.scalar(
        select(LearningGoal)
        .where(LearningGoal.learner_id == learner_id)
        .options(selectinload(LearningGoal.time_slots))
    )
    if goal is None or goal.daily_target_minutes <= 0:
        raise AppError(
            409,
            "ONBOARDING_REQUIRED",
            "온보딩에서 하루 학습 목표를 먼저 설정해 주세요.",
        )
    return goal


def _recommendation(
    video: Video,
    course: Course,
    kind: str,
    target_minutes: int,
    *,
    remaining_lesson_seconds: int | None = None,
):
    if remaining_lesson_seconds is None:
        _, lessons, _ = active_curriculum(course)
        duration = sum(lesson.duration_seconds for lesson in lessons)
    else:
        duration = remaining_lesson_seconds
    thumbnail = video.thumbnail_storage_key or course.thumbnail_storage_key
    return RecommendedShortform(
        recommendation_type=kind,
        video_id=video.id,
        video_type=video.video_type,
        title=video.title,
        duration_seconds=video.duration_seconds,
        course_id=course.id,
        course_title=course.title,
        thumbnail_storage_key=thumbnail,
        thumbnail_url=get_thumbnail_url(thumbnail),
        estimated_completion_days=math.ceil(duration / (target_minutes * 60)),
    )


def _recommend_shortforms(
    db: Session, learner_id: int, remaining_seconds: int, target_minutes: int
) -> list[RecommendedShortform]:
    if remaining_seconds <= 0:
        return []
    enrollments = db.scalars(
        select(Enrollment)
        .join(Course)
        .where(
            Enrollment.learner_id == learner_id,
            Enrollment.status == "IN_PROGRESS",
            Course.status.in_(("PUBLISHED", "EDITING", "OPERATOR_HIDDEN")),
        )
        .options(
            joinedload(Enrollment.course).selectinload(Course.sections),
            joinedload(Enrollment.course).selectinload(Course.videos),
            joinedload(Enrollment.course).selectinload(Course.quizzes),
            selectinload(Enrollment.video_progresses),
        )
        .order_by(Enrollment.last_studied_at.desc().nullslast(), Enrollment.id.desc())
    ).all()
    recommendations = []
    for enrollment in enrollments:
        course = enrollment.course
        sections, videos, quizzes = active_curriculum(course)
        completed, submitted = completed_item_ids(db, enrollment.id)
        next_video = None
        blocked_by_quiz = False
        for section in sections:
            next_video = next(
                (
                    video
                    for video in videos
                    if video.section_id == section.id and video.id not in completed
                ),
                None,
            )
            if next_video is not None:
                break
            if any(
                quiz.id not in submitted
                for quiz in quizzes
                if quiz.quiz_type == "UNIT" and quiz.section_id == section.id
            ):
                blocked_by_quiz = True
                break
        if next_video is None or blocked_by_quiz:
            continue
        progress_by_video = {item.video_id: item for item in enrollment.video_progresses}
        progress = progress_by_video.get(next_video.id)
        position = progress.last_position_seconds if progress else 0
        remaining_video = max(0, next_video.duration_seconds - position)
        if remaining_video == 0:
            # 끝까지 탐색했어도 중간 미시청 구간이 있으면 전체 길이를 기준으로 추천한다.
            remaining_video = next_video.duration_seconds
        if remaining_video <= remaining_seconds:
            # 이어보기 예상 일수는 완료 영상과 실제 시청한 분량을 제외한다.
            remaining_lesson_seconds = sum(
                max(
                    0,
                    video.duration_seconds
                    - max(0, progress_by_video[video.id].watched_seconds or 0),
                )
                if video.id in progress_by_video
                else video.duration_seconds
                for video in videos
                if video.id not in completed
            )
            recommendations.append(
                _recommendation(
                    next_video,
                    course,
                    "CONTINUE_LESSON",
                    target_minutes,
                    remaining_lesson_seconds=remaining_lesson_seconds,
                )
            )

    profile = db.get(LearnerProfile, learner_id)
    interests = set(
        db.scalars(
            select(LearnerTopicInterest.topic_id).where(
                LearnerTopicInterest.learner_id == learner_id
            )
        )
    )
    topic_score = case((Course.topic_id.in_(interests), 2), else_=0)
    category_score = (
        case((Course.category_id == profile.target_category_id, 1), else_=0)
        if (profile is not None and profile.target_category_id is not None)
        else 0
    )
    ot_videos = db.scalars(
        select(Video)
        .join(Course)
        .where(
            Course.status == "PUBLISHED",
            Video.video_type == "OT",
            Video.is_active.is_(True),
            Video.duration_seconds <= remaining_seconds,
            ~select(VideoView.video_id)
            .where(VideoView.user_id == learner_id, VideoView.video_id == Video.id)
            .exists(),
        )
        .options(
            joinedload(Video.course).selectinload(Course.sections),
            joinedload(Video.course).selectinload(Course.videos),
            joinedload(Video.course).selectinload(Course.quizzes),
        )
        .order_by(
            desc(topic_score + category_score),
            Video.view_count.desc(),
            Video.like_count.desc(),
            Course.published_at.desc(),
            Video.id.desc(),
        )
        .limit(OT_RECOMMENDATION_LIMIT)
    ).all()
    recommendations.extend(
        _recommendation(video, video.course, "RECOMMENDED_OT", target_minutes)
        for video in ot_videos
    )
    return recommendations


def get_today(db: Session, learner_id: int) -> TodayData:
    goal = _require_goal(db, learner_id)
    now = datetime.now(SEOUL_TZ)
    attendance = db.get(AttendanceDay, (learner_id, now.date()))
    snapshot = attendance.target_minutes_snapshot if attendance else goal.daily_target_minutes
    active_seconds = attendance.active_seconds if attendance else 0
    remaining = max(0, snapshot * 60 - active_seconds)
    slot = next(
        (slot for slot in goal.time_slots if slot.start_time <= now.time() < slot.end_time), None
    )
    return TodayData(
        study_date=now.date(),
        time_zone="Asia/Seoul",
        goal=TodayGoal(
            daily_target_minutes=goal.daily_target_minutes,
            target_minutes_snapshot=snapshot,
            active_seconds=active_seconds,
            remaining_seconds=remaining,
            achievement_percent=(active_seconds * 1000 // (snapshot * 60)) / 10,
            is_attended=attendance.is_attended if attendance else False,
        ),
        current_time_slot=CurrentTimeSlot(
            slot_id=slot.id,
            start_time=slot.start_time,
            end_time=slot.end_time,
            sort_order=slot.sort_order,
            is_in_preferred_slot=True,
        )
        if slot
        else None,
        recommended_shortforms=_recommend_shortforms(
            db, learner_id, remaining, goal.daily_target_minutes
        ),
    )


def get_attendance(db: Session, learner_id: int, year: int | None, month: int | None):
    _require_goal(db, learner_id)
    today = datetime.now(SEOUL_TZ).date()
    year = year if year is not None else today.year
    month = month if month is not None else today.month
    if not 1 <= month <= 12:
        raise AppError(400, "VALIDATION_ERROR", "조회 월(month)은 1~12 사이의 값이어야 합니다.")
    if not 1 <= year <= 9999:
        raise AppError(400, "VALIDATION_ERROR", "조회 연도(year)는 1~9999 사이의 값이어야 합니다.")
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    days = db.scalars(
        select(AttendanceDay)
        .where(
            AttendanceDay.learner_id == learner_id,
            AttendanceDay.study_date >= start,
            AttendanceDay.study_date <= end,
            AttendanceDay.study_date <= today,
        )
        .order_by(AttendanceDay.study_date)
    ).all()
    attended_dates = db.scalars(
        select(AttendanceDay.study_date)
        .where(
            AttendanceDay.learner_id == learner_id,
            AttendanceDay.is_attended.is_(True),
            AttendanceDay.study_date <= today,
        )
        .order_by(AttendanceDay.study_date)
    ).all()
    best, streak = 0, 0
    previous = None
    for day in attended_dates:
        streak = streak + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        best = max(best, streak)
        previous = day
    return AttendanceData(
        year=year,
        month=month,
        streak=AttendanceStreak(
            current_streak_days=get_current_streak(db, learner_id, today),
            best_streak_days=best,
            monthly_attended_days=sum(day.is_attended for day in days),
        ),
        calendar_days=[
            CalendarDay(
                study_date=day.study_date,
                target_minutes_snapshot=day.target_minutes_snapshot,
                active_seconds=day.active_seconds,
                is_attended=day.is_attended,
            )
            for day in days
        ],
    )
