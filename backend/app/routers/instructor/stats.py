from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.core.auth import (
    InstructorUser,
    verify_course_ownership,
)
from app.core.database import DbSession
from app.core.errors import AppError
from app.models.course import Course
from app.models.stat import CourseDailyStat
from app.schemas.common import DataResponse
from app.schemas.stats import (
    CourseStatItem,
    DailyStatItem,
    InstructorStatsResponseData,
    StatsComparison,
    StatsPeriod,
    StatsSummary,
)

router = APIRouter(tags=["instructor-stats"])

try:
    KST = ZoneInfo("Asia/Seoul")
except Exception:
    KST = timezone(timedelta(hours=9))  # type: ignore[assignment]


def _shift_prev_month(d: date) -> date:
    if d.month == 1:
        prev_year, prev_month = d.year - 1, 12
    else:
        prev_year, prev_month = d.year, d.month - 1
    max_day = calendar.monthrange(prev_year, prev_month)[1]
    return date(prev_year, prev_month, min(d.day, max_day))


def _to_kst_date(dt: datetime) -> date:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC).astimezone(KST).date()
    return dt.astimezone(KST).date()


# ==========================================
# [B-9] 강사 통계 조회
# ==========================================
@router.get("/stats", response_model=DataResponse[InstructorStatsResponseData])
def get_instructor_stats(
    db: DbSession,
    current_user: InstructorUser,
    course_id: Annotated[int | None, Query(alias="courseId")] = None,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    group_by: Annotated[str, Query(alias="groupBy")] = "day",
):
    if group_by not in ("day", "course"):
        raise AppError(400, "VALIDATION_ERROR", "groupBy는 day 또는 course만 허용됩니다.")

    if course_id is not None:
        verify_course_ownership(course_id, current_user.id, db)

    # 날짜 기본값 (Asia/Seoul 기준 이번 달 1일 ~ 오늘)
    today = datetime.now(KST).date()
    if to_date is None:
        to_date = today
    if from_date is None:
        from_date = date(to_date.year, to_date.month, 1)

    if from_date > to_date:
        raise AppError(
            400, "VALIDATION_ERROR", "조회 시작일(from)은 종료일(to)보다 늦을 수 없습니다."
        )

    # 강사 소유 강좌 목록
    my_courses_query = select(Course).where(Course.instructor_id == current_user.id)
    if course_id is not None:
        my_courses_query = my_courses_query.where(Course.id == course_id)
    my_courses = db.scalars(my_courses_query).all()
    my_course_ids = [c.id for c in my_courses]

    published_course_count = sum(1 for c in my_courses if c.status == "PUBLISHED")

    # 당월 기간 stats 쿼리
    stats_query = select(CourseDailyStat).where(
        CourseDailyStat.course_id.in_(my_course_ids),
        CourseDailyStat.start_date >= from_date,
        CourseDailyStat.start_date <= to_date,
    )
    stats_rows = db.scalars(stats_query).all() if my_course_ids else []

    lesson_views = sum(s.lesson_views for s in stats_rows)
    new_students = sum(s.new_students for s in stats_rows)
    expected_revenue = sum(s.expected_revenue for s in stats_rows)

    tot_avail_quiz = sum(s.available_quiz_cnt for s in stats_rows)
    tot_first_sub = sum(s.first_submitted_cnt for s in stats_rows)
    quiz_resp_rate = round(tot_first_sub / tot_avail_quiz * 100, 1) if tot_avail_quiz > 0 else None

    tot_first_ans = sum(s.first_answer_cnt for s in stats_rows)
    tot_first_corr = sum(s.first_correct_cnt for s in stats_rows)
    quiz_corr_rate = round(tot_first_corr / tot_first_ans * 100, 1) if tot_first_ans > 0 else None

    # 전월 동기 대비 비교 (comparison) 계산 (31일 경계 안전 처리)
    prev_from = _shift_prev_month(from_date)
    prev_to = _shift_prev_month(to_date)

    prev_stats_query = select(CourseDailyStat).where(
        CourseDailyStat.course_id.in_(my_course_ids),
        CourseDailyStat.start_date >= prev_from,
        CourseDailyStat.start_date <= prev_to,
    )
    prev_rows = db.scalars(prev_stats_query).all() if my_course_ids else []
    prev_lesson_views = sum(s.lesson_views for s in prev_rows)
    prev_new_students = sum(s.new_students for s in prev_rows)
    prev_expected_revenue = sum(s.expected_revenue for s in prev_rows)

    views_diff_pct = (
        round((lesson_views - prev_lesson_views) / prev_lesson_views * 100, 1)
        if prev_lesson_views > 0
        else 0.0
    )
    students_diff_pct = (
        round((new_students - prev_new_students) / prev_new_students * 100, 1)
        if prev_new_students > 0
        else 0.0
    )
    revenue_diff_pct = (
        round((expected_revenue - prev_expected_revenue) / prev_expected_revenue * 100, 1)
        if prev_expected_revenue > 0
        else 0.0
    )

    prev_tot_avail_quiz = sum(s.available_quiz_cnt for s in prev_rows)
    prev_tot_first_sub = sum(s.first_submitted_cnt for s in prev_rows)
    prev_quiz_resp_rate = (
        round(prev_tot_first_sub / prev_tot_avail_quiz * 100, 1)
        if prev_tot_avail_quiz > 0
        else None
    )

    prev_tot_first_ans = sum(s.first_answer_cnt for s in prev_rows)
    prev_tot_first_corr = sum(s.first_correct_cnt for s in prev_rows)
    prev_quiz_corr_rate = (
        round(prev_tot_first_corr / prev_tot_first_ans * 100, 1) if prev_tot_first_ans > 0 else None
    )

    quiz_resp_diff_pt = (
        round(quiz_resp_rate - prev_quiz_resp_rate, 1)
        if (quiz_resp_rate is not None and prev_quiz_resp_rate is not None)
        else 0.0
    )
    quiz_corr_diff_pt = (
        round(quiz_corr_rate - prev_quiz_corr_rate, 1)
        if (quiz_corr_rate is not None and prev_quiz_corr_rate is not None)
        else 0.0
    )

    prev_published_course_count = sum(
        1
        for c in my_courses
        if c.status == "PUBLISHED"
        and c.published_at is not None
        and _to_kst_date(c.published_at) <= prev_to
    )
    published_course_diff = published_course_count - prev_published_course_count

    comparison = StatsComparison(
        published_course_diff=published_course_diff,
        lesson_views_diff_percent=views_diff_pct,
        new_students_diff_percent=students_diff_pct,
        expected_revenue_diff_percent=revenue_diff_pct,
        quiz_response_rate_diff_point=quiz_resp_diff_pt,
        quiz_correct_rate_diff_point=quiz_corr_diff_pt,
    )

    summary = StatsSummary(
        published_course_count=published_course_count,
        lesson_views=lesson_views,
        new_students=new_students,
        expected_revenue=expected_revenue,
        quiz_response_rate=quiz_resp_rate,
        quiz_correct_rate=quiz_corr_rate,
        comparison=comparison,
    )

    # 1. 일별 추이 (dailyStats)
    date_map: dict[str, DailyStatItem] = {}
    for s in stats_rows:
        d_str = s.start_date.isoformat()
        if d_str not in date_map:
            date_map[d_str] = DailyStatItem(
                date=d_str,
                lesson_views=0,
                new_students=0,
                expected_revenue=0,
                available_quiz_cnt=0,
                first_submitted_cnt=0,
                first_answer_cnt=0,
                first_correct_cnt=0,
            )
        item = date_map[d_str]
        item.lesson_views += s.lesson_views
        item.new_students += s.new_students
        item.expected_revenue += s.expected_revenue
        item.available_quiz_cnt += s.available_quiz_cnt
        item.first_submitted_cnt += s.first_submitted_cnt
        item.first_answer_cnt += s.first_answer_cnt
        item.first_correct_cnt += s.first_correct_cnt
    daily_stats = sorted(date_map.values(), key=lambda x: x.date)

    # 2. 강좌별 성과 (courseStats - 무실적 강좌 포함)
    course_map: dict[int, CourseStatItem] = {}
    c_avail_map: dict[int, int] = {}
    c_sub_map: dict[int, int] = {}
    c_ans_map: dict[int, int] = {}
    c_corr_map: dict[int, int] = {}

    for c in my_courses:
        course_map[c.id] = CourseStatItem(
            course_id=c.id,
            title=c.title,
            lesson_views=0,
            new_students=0,
            expected_revenue=0,
            quiz_response_rate=None,
            quiz_correct_rate=None,
        )
        c_avail_map[c.id] = 0
        c_sub_map[c.id] = 0
        c_ans_map[c.id] = 0
        c_corr_map[c.id] = 0

    for s in stats_rows:
        cid = s.course_id
        if cid not in course_map:
            continue
        c_item = course_map[cid]
        c_item.lesson_views += s.lesson_views
        c_item.new_students += s.new_students
        c_item.expected_revenue += s.expected_revenue
        c_avail_map[cid] += s.available_quiz_cnt
        c_sub_map[cid] += s.first_submitted_cnt
        c_ans_map[cid] += s.first_answer_cnt
        c_corr_map[cid] += s.first_correct_cnt

    for cid, c_item in course_map.items():
        avail = c_avail_map[cid]
        sub = c_sub_map[cid]
        ans = c_ans_map[cid]
        corr = c_corr_map[cid]
        c_item.quiz_response_rate = round(sub / avail * 100, 1) if avail > 0 else None
        c_item.quiz_correct_rate = round(corr / ans * 100, 1) if ans > 0 else None

    course_stats = list(course_map.values())

    return DataResponse(
        data=InstructorStatsResponseData(
            period=StatsPeriod(
                from_date=from_date.isoformat(),
                to_date=to_date.isoformat(),
                group_by=group_by,
            ),
            summary=summary,
            daily_stats=daily_stats,
            course_stats=course_stats,
        )
    )
