from __future__ import annotations

# Re-exports for backward compatibility across all instructor endpoints (B-1 ~ B-9)
from app.schemas.course import (
    CourseCounts,
    CourseCreateRequest,
    CourseDeleteResponseData,
    CourseMutationResponseData,
    CourseStatusUpdateRequest,
    CourseStatusUpdateResponseData,
    CourseUpdateRequest,
    InstructorCourseDetail,
    InstructorCourseItem,
    InstructorCourseListResponseData,
    InstructorSectionDetail,
    InstructorUnitQuizSummary,
    InstructorVideoSummary,
    ProcessingInfo,
    PublishCheck,
)
from app.schemas.quiz import (
    QuestionItem,
    QuizDetailResponseData,
    QuizRegenerateResponseData,
    QuizUpdateRequest,
)
from app.schemas.script import (
    ScriptRegenerateResponseData,
    ScriptResponseData,
    ScriptSegment,
    ScriptUpdateRequest,
)
from app.schemas.section import (
    SectionCreateRequest,
    SectionDeleteResponseData,
    SectionResponseData,
    SectionUpdateRequest,
)
from app.schemas.stats import (
    CourseStatItem,
    DailyStatItem,
    InstructorStatsResponseData,
    StatsComparison,
    StatsPeriod,
    StatsSummary,
)
from app.schemas.video import (
    VideoDeleteResponseData,
    VideoResponseData,
)

__all__ = [
    # B-1 ~ B-4 (course.py)
    "CourseCounts",
    "CourseCreateRequest",
    "CourseDeleteResponseData",
    "CourseMutationResponseData",
    "CourseStatusUpdateRequest",
    "CourseStatusUpdateResponseData",
    "CourseUpdateRequest",
    "InstructorCourseDetail",
    "InstructorCourseItem",
    "InstructorCourseListResponseData",
    "InstructorSectionDetail",
    "InstructorUnitQuizSummary",
    "InstructorVideoSummary",
    "ProcessingInfo",
    "PublishCheck",
    # B-5 (section.py)
    "SectionCreateRequest",
    "SectionDeleteResponseData",
    "SectionResponseData",
    "SectionUpdateRequest",
    # B-6 (video.py)
    "VideoDeleteResponseData",
    "VideoResponseData",
    # B-7 (script.py)
    "ScriptRegenerateResponseData",
    "ScriptResponseData",
    "ScriptSegment",
    "ScriptUpdateRequest",
    # B-8 (quiz.py)
    "QuestionItem",
    "QuizDetailResponseData",
    "QuizRegenerateResponseData",
    "QuizUpdateRequest",
    # B-9 (stats.py)
    "CourseStatItem",
    "DailyStatItem",
    "InstructorStatsResponseData",
    "StatsComparison",
    "StatsPeriod",
    "StatsSummary",
]
