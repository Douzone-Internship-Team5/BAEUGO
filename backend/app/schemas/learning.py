from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import PlainSerializer

from app.core.timezone import SEOUL_TZ
from app.schemas.common import CamelModel
from app.schemas.search import CategorySimple, TopicSimple


def _serialize_seoul_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=SEOUL_TZ).isoformat()
    return dt.astimezone(SEOUL_TZ).isoformat()


SeoulDatetime = Annotated[datetime, PlainSerializer(_serialize_seoul_dt, return_type=str)]


# ==========================================
# [C-1] 강좌 상세 · 학습 현황 조회
# ==========================================
class CourseDetailInstructor(CamelModel):
    id: int
    name: str
    bio: str | None = None


class CourseDetailOtVideo(CamelModel):
    video_id: int
    title: str
    video_storage_key: str
    video_url: str | None = None
    duration_seconds: int


class CourseDetailEnrollment(CamelModel):
    is_enrolled: bool
    enrollment_id: int | None = None
    status: str | None = None
    progress_percent: int
    action_button: str


class CourseCurriculumVideo(CamelModel):
    video_id: int
    title: str
    duration_seconds: int
    sort_order: int
    is_completed: bool
    is_locked: bool


class CourseCurriculumQuiz(CamelModel):
    quiz_id: int
    is_completed: bool
    is_locked: bool


class CourseCurriculumSection(CamelModel):
    section_id: int
    title: str
    sort_order: int
    videos: list[CourseCurriculumVideo]
    unit_quiz: CourseCurriculumQuiz | None = None


class CourseDetailResponse(CamelModel):
    course_id: int
    status: str
    category: CategorySimple
    topic: TopicSimple
    title: str
    description: str
    difficulty: str
    price_krw: int
    is_free: bool
    thumbnail_storage_key: str | None = None
    thumbnail_url: str | None = None
    instructor: CourseDetailInstructor
    total_lesson_duration_seconds: int
    total_lesson_count: int
    estimated_completion_days: int
    ot_video: CourseDetailOtVideo | None = None
    enrollment: CourseDetailEnrollment
    sections: list[CourseCurriculumSection]
    final_quiz: CourseCurriculumQuiz | None = None


# ==========================================
# [C-2] 수강 등록 (목업 결제 · 무료)
# ==========================================
class EnrollRequest(CamelModel):
    access_source: str | None = "DEMO_GRANT"


class EnrollResponse(CamelModel):
    enrollment_id: int
    learner_id: int
    course_id: int
    access_source: str
    price_snapshot_krw: int
    status: str
    enrolled_at: SeoulDatetime
    first_video_id: int | None = None


# ==========================================
# [C-3] 내 강좌 목록 조회 (수강 중 · 수료)
# ==========================================
class EnrollmentCounts(CamelModel):
    all: int = 0
    in_progress: int = 0
    completed: int = 0


class MyEnrollmentItem(CamelModel):
    enrollment_id: int
    course_id: int
    title: str
    thumbnail_storage_key: str | None = None
    thumbnail_url: str | None = None
    status: str
    progress_percent: int
    unresolved_wrong_count: int
    status_message: str
    participated_quiz_count: int
    enrolled_at: SeoulDatetime
    last_studied_at: SeoulDatetime | None = None


class MyEnrollmentListResponse(CamelModel):
    counts: EnrollmentCounts
    enrollments: list[MyEnrollmentItem]


# ==========================================
# [C-4] 이어가기 (첫 미완료 항목 조회)
# ==========================================
class EnrollmentNextResponse(CamelModel):
    enrollment_id: int
    course_id: int
    next_item_type: str | None = None
    section_id: int | None = None
    video_id: int | None = None
    quiz_id: int | None = None
    last_position_seconds: int | None = None


# ==========================================
# [C-5] 영상 정보 및 잠금 확인
# ==========================================
class VideoProgressSummary(CamelModel):
    watched_seconds: int
    last_position_seconds: int
    is_completed: bool


class VideoSegmentItem(CamelModel):
    id: int
    seq: int
    start_ms: int
    end_ms: int
    content: str


class VideoNavigation(CamelModel):
    prev_video_id: int | None = None
    next_item_type: str | None = None
    next_item_id: int | None = None
    is_next_unlocked: bool


class VideoDetailResponse(CamelModel):
    video_id: int
    course_id: int
    course_title: str
    section_id: int | None = None
    section_title: str | None = None
    video_type: str
    title: str
    video_storage_key: str
    video_url: str | None = None
    thumbnail_storage_key: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: int
    sort_order: int
    view_count: int
    like_count: int
    is_liked: bool
    progress: VideoProgressSummary
    segments: list[VideoSegmentItem]
    navigation: VideoNavigation


# ==========================================
# [C-6] 영상 조회수 집계 (3초 · 하루 1회)
# ==========================================
class VideoViewRequest(CamelModel):
    played_seconds: int


class VideoViewResponse(CamelModel):
    video_id: int
    view_date: str
    is_incremented: bool
    view_count: int


# ==========================================
# [C-7] 학습 진도 · 시청 시간 저장
# ==========================================
class ActivitySessionInput(CamelModel):
    session_key: str
    started_at: datetime
    ended_at: datetime
    active_seconds: int


class VideoProgressRequest(CamelModel):
    last_position_seconds: int
    watched_range: list[int]
    activity_session: ActivitySessionInput


class VideoProgressResponse(CamelModel):
    enrollment_id: int
    video_id: int
    watched_seconds: int
    duration_seconds: int
    watched_ratio_percent: float
    last_position_seconds: int
    is_completed: bool
    completed_at: SeoulDatetime | None = None
    remaining_seconds_for_completion: int
    today_active_seconds: int
    today_is_attended: bool


# ==========================================
# [C-8] 영상 좋아요 등록 · 취소
# ==========================================
class VideoLikeResponse(CamelModel):
    video_id: int
    video_type: str
    is_liked: bool
    like_count: int


# ==========================================
# [C-9] 내가 좋아요한 영상 목록 조회
# ==========================================
class LikedVideoItem(CamelModel):
    video_id: int
    video_type: str
    video_title: str
    thumbnail_storage_key: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: int
    course_id: int
    course_title: str


class LikedVideoListResponse(CamelModel):
    total_count: int
    liked_videos: list[LikedVideoItem]
