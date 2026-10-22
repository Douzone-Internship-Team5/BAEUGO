from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import CamelModel


# ==========================================
# [B-1] 강사 강좌 목록
# ==========================================
class ProcessingInfo(CamelModel):
    completed: int
    total: int


class InstructorCourseItem(CamelModel):
    course_id: int
    title: str
    thumbnail_storage_key: str | None = None
    thumbnail_url: str | None = None
    category_id: int
    topic_id: int
    difficulty: str
    price_krw: int
    status: str
    closed_reason: str | None = None
    published_at: datetime | None = None
    section_count: int
    video_count: int
    enrollment_count: int
    processing: ProcessingInfo


class CourseCounts(BaseModel):
    DRAFT: int = 0
    PUBLISHED: int = 0
    EDITING: int = 0
    OPERATOR_HIDDEN: int = 0


class InstructorCourseListResponseData(CamelModel):
    counts: CourseCounts
    items: list[InstructorCourseItem]
    page: int
    size: int
    total: int


# ==========================================
# [B-2] 강좌 편집용 상세
# ==========================================
class InstructorVideoSummary(CamelModel):
    video_id: int
    video_type: str
    title: str
    duration_seconds: int
    sort_order: int
    stt_status: str
    transcript_reviewed_at: datetime | None = None


class InstructorUnitQuizSummary(CamelModel):
    quiz_id: int
    quiz_type: str
    section_id: int | None = None
    ai_status: str | None = None
    reviewed_at: datetime | None = None
    question_count: int


class InstructorSectionDetail(CamelModel):
    section_id: int
    title: str
    sort_order: int
    videos: list[InstructorVideoSummary]
    unit_quiz: InstructorUnitQuizSummary | None = None


class PublishCheck(CamelModel):
    is_ready: bool
    missing: list[str]


class InstructorCourseDetail(CamelModel):
    course_id: int
    status: str
    closed_reason: str | None = None
    published_at: datetime | None = None
    title: str
    description: str
    category_id: int
    topic_id: int
    difficulty: str
    price_krw: int
    thumbnail_storage_key: str | None = None
    thumbnail_url: str | None = None
    total_lesson_duration_seconds: int
    ot_video: InstructorVideoSummary | None = None
    sections: list[InstructorSectionDetail]
    final_quiz: InstructorUnitQuizSummary | None = None
    publish_check: PublishCheck


# ==========================================
# [B-3] 강좌 생성 · 수정 · 삭제
# ==========================================
class CourseCreateRequest(CamelModel):
    title: str
    description: str
    category_id: int
    topic_id: int
    difficulty: str
    price_krw: int = 0


class CourseUpdateRequest(CamelModel):
    title: str | None = None
    description: str | None = None
    category_id: int | None = None
    topic_id: int | None = None
    difficulty: str | None = None
    price_krw: int | None = None


class CourseMutationResponseData(CamelModel):
    course_id: int
    status: str
    title: str
    category_id: int
    topic_id: int
    difficulty: str
    price_krw: int
    thumbnail_storage_key: str | None = None
    thumbnail_url: str | None = None


class CourseDeleteResponseData(CamelModel):
    deleted_course_id: int
    deleted: bool = True


# ==========================================
# [B-4] 강좌 상태 변경 (공개 / 수정 시작)
# ==========================================
class CourseStatusUpdateRequest(CamelModel):
    status: str


class CourseStatusUpdateResponseData(CamelModel):
    course_id: int
    status: str
    published_at: datetime | None = None
    closed_by_user_id: int | None = None
    closed_at: datetime | None = None


# ==========================================
# [B-5] 섹션 관리
# ==========================================
class SectionCreateRequest(CamelModel):
    course_id: int
    title: str


class SectionUpdateRequest(CamelModel):
    title: str | None = None
    sort_order: int | None = None


class SectionResponseData(CamelModel):
    section_id: int
    course_id: int
    title: str | None = None
    sort_order: int | None = None
    is_active: bool


# ==========================================
# [B-6] 영상 관리
# ==========================================
class VideoResponseData(CamelModel):
    video_id: int
    course_id: int
    section_id: int | None = None
    video_type: str | None = None
    title: str | None = None
    duration_seconds: int | None = None
    sort_order: int | None = None
    stt_status: str | None = None
    transcript_reviewed_at: datetime | None = None
    is_active: bool


# ==========================================
# [B-7] 영상 대본
# ==========================================
class ScriptSegment(CamelModel):
    seq: int
    start_ms: int
    end_ms: int
    content: str


class ScriptUpdateRequest(CamelModel):
    segments: list[ScriptSegment]


class ScriptResponseData(CamelModel):
    video_id: int
    stt_status: str
    transcript_reviewed_at: datetime | None = None
    segments: list[ScriptSegment]


class ScriptRegenerateResponseData(CamelModel):
    video_id: int
    stt_status: str
    message: str


# ==========================================
# [B-8] 퀴즈
# ==========================================
class QuestionItem(CamelModel):
    question_id: int | None = None
    order_no: int | None = None
    generated_by: str | None = None
    content: str
    choices: list[str]
    correct_choice_no: int
    explanation: str


class QuizUpdateRequest(CamelModel):
    questions: list[QuestionItem]


class QuizDetailResponseData(CamelModel):
    quiz_id: int
    course_id: int
    section_id: int | None = None
    quiz_type: str
    ai_status: str | None = None
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    questions: list[QuestionItem]


class QuizRegenerateResponseData(CamelModel):
    quiz_id: int
    section_id: int | None = None
    quiz_type: str
    ai_status: str
    message: str


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
