from __future__ import annotations

from pydantic import Field

from app.schemas.common import CamelModel


# ==========================================
# [B-9] 강사 통계
# ==========================================
class StatsPeriod(CamelModel):
    from_date: str = Field(alias="from")
    to_date: str = Field(alias="to")
    group_by: str


class StatsComparison(CamelModel):
    published_course_diff: int = 0
    lesson_views_diff_percent: float = 0.0
    new_students_diff_percent: float = 0.0
    expected_revenue_diff_percent: float = 0.0
    quiz_response_rate_diff_point: float | None = 0.0
    quiz_correct_rate_diff_point: float | None = 0.0


class StatsSummary(CamelModel):
    published_course_count: int
    lesson_views: int
    new_students: int
    expected_revenue: int
    quiz_response_rate: float | None = None
    quiz_correct_rate: float | None = None
    comparison: StatsComparison


class DailyStatItem(CamelModel):
    date: str
    lesson_views: int
    new_students: int
    expected_revenue: int
    available_quiz_cnt: int
    first_submitted_cnt: int
    first_answer_cnt: int
    first_correct_cnt: int


class CourseStatItem(CamelModel):
    course_id: int
    title: str
    lesson_views: int
    new_students: int
    expected_revenue: int
    quiz_response_rate: float | None = None
    quiz_correct_rate: float | None = None


class InstructorStatsResponseData(CamelModel):
    period: StatsPeriod
    summary: StatsSummary
    daily_stats: list[DailyStatItem] | None = None
    course_stats: list[CourseStatItem] | None = None


__all__ = [
    "CourseStatItem",
    "DailyStatItem",
    "InstructorStatsResponseData",
    "StatsComparison",
    "StatsPeriod",
    "StatsSummary",
]
