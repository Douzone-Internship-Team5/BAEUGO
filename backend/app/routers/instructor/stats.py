from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.auth import (
    get_current_instructor,
    verify_course_ownership,
)
from app.core.database import DbSession
from app.core.errors import AppError
from app.models.course import Course, CourseDailyStat
from app.models.user import User
from app.schemas.common import DataResponse
from app.schemas.instructor import (
    CourseStatItem,
    DailyStatItem,
    InstructorStatsResponseData,
    StatsComparison,
    StatsPeriod,
    StatsSummary,
)

router = APIRouter(tags=["instructor-stats"])


# ==========================================
# [B-9] 강사 통계 조회
# ==========================================
@router.get("/stats", response_model=DataResponse[InstructorStatsResponseData])
def get_instructor_stats(
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_instructor)],
    courseId: int | None = Query(None),
    fromDate: date | None = Query(None, alias="from"),
    toDate: date | None = Query(None, alias="to"),
    groupBy: str = Query("day"),
):
    if groupBy not in ("day", "course"):
        raise AppError(400, "VALIDATION_ERROR", "groupBy는 day 또는 course만 허용됩니다.")

    if courseId is not None:
        verify_course_ownership(courseId, current_user.id, db)

    # 날짜 기본값 (이번 달 1일 ~ 오늘)
    today = date.today()
    if toDate is None:
        toDate = today
    if fromDate is None:
        fromDate = date(toDate.year, toDate.month, 1)

    # 강사 소유 강좌 목록
    my_courses_query = select(Course).where(Course.instructor_id == current_user.id)
    if courseId is not None:
        my_courses_query = my_courses_query.where(Course.id == courseId)
    my_courses = db.scalars(my_courses_query).all()
    my_course_ids = [c.id for c in my_courses]

    published_course_count = sum(1 for c in my_courses if c.status == "PUBLISHED")

    # 당월 기간 stats 쿼리
    stats_query = select(CourseDailyStat).where(
        CourseDailyStat.course_id.in_(my_course_ids),
        CourseDailyStat.start_date >= fromDate,
        CourseDailyStat.start_date <= toDate,
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

    # 전월 동기 대비 비교 (comparison) 계산
    try:
        prev_from = (
            fromDate.replace(year=fromDate.year - 1, month=12)
            if fromDate.month == 1
            else fromDate.replace(month=fromDate.month - 1)
        )
        prev_to = (
            toDate.replace(year=toDate.year - 1, month=12)
            if toDate.month == 1
            else toDate.replace(month=toDate.month - 1)
        )
    except ValueError:
        prev_from = fromDate
        prev_to = toDate

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

    comparison = StatsComparison(
        published_course_diff=0,
        lesson_views_diff_percent=views_diff_pct,
        new_students_diff_percent=students_diff_pct,
        expected_revenue_diff_percent=revenue_diff_pct,
        quiz_response_rate_diff_point=0.0,
        quiz_correct_rate_diff_point=0.0,
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

    daily_stats: list[DailyStatItem] | None = None
    course_stats: list[CourseStatItem] | None = None

    if groupBy == "day":
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

    elif groupBy == "course":
        course_map: dict[int, CourseStatItem] = {}
        c_title_map = {c.id: c.title for c in my_courses}
        c_avail_map: dict[int, int] = {}
        c_sub_map: dict[int, int] = {}
        c_ans_map: dict[int, int] = {}
        c_corr_map: dict[int, int] = {}

        for s in stats_rows:
            cid = s.course_id
            if cid not in course_map:
                course_map[cid] = CourseStatItem(
                    course_id=cid,
                    title=c_title_map.get(cid, ""),
                    lesson_views=0,
                    new_students=0,
                    expected_revenue=0,
                    quiz_response_rate=None,
                    quiz_correct_rate=None,
                )
                c_avail_map[cid] = 0
                c_sub_map[cid] = 0
                c_ans_map[cid] = 0
                c_corr_map[cid] = 0

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
                from_date=fromDate.isoformat(),
                to_date=toDate.isoformat(),
                group_by=groupBy,
            ),
            summary=summary,
            daily_stats=daily_stats,
            course_stats=course_stats,
        )
    )
