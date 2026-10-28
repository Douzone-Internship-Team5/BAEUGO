from datetime import date, time

from app.schemas.common import CamelModel


class TodayGoal(CamelModel):
    daily_target_minutes: int
    target_minutes_snapshot: int
    active_seconds: int
    remaining_seconds: int
    achievement_percent: float
    is_attended: bool


class CurrentTimeSlot(CamelModel):
    slot_id: int
    start_time: time
    end_time: time
    sort_order: int
    is_in_preferred_slot: bool


class RecommendedShortform(CamelModel):
    recommendation_type: str
    video_id: int
    video_type: str
    title: str
    duration_seconds: int
    course_id: int
    course_title: str
    thumbnail_storage_key: str | None
    thumbnail_url: str | None
    estimated_completion_days: int


class TodayData(CamelModel):
    study_date: date
    time_zone: str
    goal: TodayGoal
    current_time_slot: CurrentTimeSlot | None
    recommended_shortforms: list[RecommendedShortform]


class AttendanceStreak(CamelModel):
    current_streak_days: int
    best_streak_days: int
    monthly_attended_days: int


class CalendarDay(CamelModel):
    study_date: date
    target_minutes_snapshot: int
    active_seconds: int
    is_attended: bool


class AttendanceData(CamelModel):
    year: int
    month: int
    streak: AttendanceStreak
    calendar_days: list[CalendarDay]
