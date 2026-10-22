from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.course import Course, Question, QuestionChoice, Quiz, Section, Video
from app.models.learning import Enrollment, QuizAttempt, VideoProgress


def validate_publish_readiness(course: Course, db: Session) -> tuple[bool, list[str]]:
    """6대 공개 검증 조건 검사 (미충족 항목 문자열 목록 반환).

    1. 활성 무료 OT 영상(video_type='OT', is_active=true) 정확히 1개 존재
    2. 활성 섹션(sections.is_active=true) 최소 1개 이상 및 활성 섹션마다 활성 학습 영상(video_type='LESSON', is_active=true) 1개 이상 존재
    3. 모든 활성 영상(OT 포함)의 stt_status='COMPLETED' 및 강사 대본 검수 완료(transcript_reviewed_at IS NOT NULL)
    4. 활성 섹션마다 단위 확인 퀴즈(quiz_type='UNIT', is_active=true)의 ai_status='COMPLETED' 및 강사 검수 완료(reviewed_at IS NOT NULL)
    5. 코스 종합 퀴즈(quiz_type='FINAL', is_active=true) 존재 및 강사 검수 완료(reviewed_at IS NOT NULL)
    6. 모든 활성 퀴즈(단위·종합)당 활성 문항(questions.is_active=true) 3~5개 및 문항당 보기(question_choices) 정확히 4개(1~4번) 존재
    """
    missing: list[str] = []

    # 1. 활성 무료 OT 영상 정확히 1개
    ot_videos = [v for v in course.videos if v.video_type == "OT" and v.is_active]
    if len(ot_videos) != 1:
        missing.append("OT_VIDEO_MISSING")

    # 2. 활성 섹션 최소 1개 및 각 섹션별 활성 학습 영상 1개 이상
    active_sections = [s for s in course.sections if s.is_active]
    if not active_sections:
        missing.append("SECTION_MISSING")
    else:
        for s in active_sections:
            sec_lessons = [v for v in s.videos if v.video_type == "LESSON" and v.is_active]
            if not sec_lessons:
                missing.append(f"LESSON_VIDEO_MISSING:sectionId={s.id}")

    # 3. 모든 활성 영상(OT 포함)의 STT 및 검수 완료
    active_videos = [v for v in course.videos if v.is_active]
    for v in active_videos:
        if v.stt_status != "COMPLETED" or v.transcript_reviewed_at is None:
            missing.append(f"TRANSCRIPT_NOT_REVIEWED:videoId={v.id}")

    # 4. 활성 섹션마다 단위 퀴즈 AI 완료 및 강사 검수 완료
    for s in active_sections:
        unit_quiz = s.unit_quiz
        if not unit_quiz or not unit_quiz.is_active:
            missing.append(f"UNIT_QUIZ_MISSING:sectionId={s.id}")
        else:
            if unit_quiz.ai_status != "COMPLETED" or unit_quiz.reviewed_at is None:
                missing.append(f"UNIT_QUIZ_NOT_REVIEWED:sectionId={s.id}")

    # 5. 코스 종합 퀴즈 존재 및 검수 완료
    final_quizzes = [q for q in course.quizzes if q.quiz_type == "FINAL" and q.is_active]
    if not final_quizzes:
        missing.append("FINAL_QUIZ_MISSING")
    else:
        for fq in final_quizzes:
            if fq.reviewed_at is None:
                missing.append("FINAL_QUIZ_NOT_REVIEWED")

    # 6. 모든 활성 퀴즈당 활성 문항 3~5개 및 보기 정확히 4개
    all_active_quizzes = [q for q in course.quizzes if q.is_active]
    for q in all_active_quizzes:
        active_questions = [quest for quest in q.questions if quest.is_active]
        if len(active_questions) < 3 or len(active_questions) > 5:
            missing.append(f"QUESTION_COUNT_INVALID:quizId={q.id}")
        for quest in active_questions:
            choices = quest.choices
            choice_nos = {c.choice_no for c in choices}
            if len(choices) != 4 or choice_nos != {1, 2, 3, 4}:
                missing.append(f"CHOICE_COUNT_INVALID:questionId={quest.id}")

    is_ready = len(missing) == 0
    return is_ready, missing


def recalculate_enrollments_progress(course: Course, db: Session) -> None:
    """재공개 시 기존 수강생의 활성 항목 기준 이수율을 재계산한다.

    활성 이수율이 100% 미만이 된 경우 enrollments.status = 'IN_PROGRESS',
    current_completed_at = NULL 로 갱신하되 first_completed_at 은 보존.
    """
    active_lessons = [v for v in course.videos if v.video_type == "LESSON" and v.is_active]
    active_quizzes = [q for q in course.quizzes if q.is_active]

    total_items_count = len(active_lessons) + len(active_quizzes)
    if total_items_count == 0:
        return

    active_lesson_ids = {v.id for v in active_lessons}
    active_quiz_ids = {q.id for q in active_quizzes}

    enrollments = db.scalars(
        select(Enrollment).where(Enrollment.course_id == course.id)
    ).all()
    if not enrollments:
        return

    enrollment_ids = [e.id for e in enrollments]
    learner_ids = [e.learner_id for e in enrollments]

    # 1. 수강생별 완료된 활성 영상 개수 일괄 집계 (1회 쿼리)
    video_counts: dict[int, int] = {}
    if active_lesson_ids:
        raw_video_counts = db.execute(
            select(VideoProgress.enrollment_id, func.count(VideoProgress.id))
            .where(
                VideoProgress.enrollment_id.in_(enrollment_ids),
                VideoProgress.video_id.in_(active_lesson_ids),
                VideoProgress.is_completed.is_(True),
            )
            .group_by(VideoProgress.enrollment_id)
        ).all()
        video_counts = {eid: cnt for eid, cnt in raw_video_counts}

    # 2. 수강생별 제출 완료된 활성 퀴즈 개수 일괄 집계 (1회 쿼리)
    quiz_counts: dict[int, int] = {}
    if active_quiz_ids:
        raw_quiz_counts = db.execute(
            select(QuizAttempt.learner_id, func.count(func.distinct(QuizAttempt.quiz_id)))
            .where(
                QuizAttempt.learner_id.in_(learner_ids),
                QuizAttempt.quiz_id.in_(active_quiz_ids),
                QuizAttempt.status == "SUBMITTED",
            )
            .group_by(QuizAttempt.learner_id)
        ).all()
        quiz_counts = {lid: cnt for lid, cnt in raw_quiz_counts}

    # 3. 인메모리 매핑으로 상태 일괄 갱신 (반복 쿼리 제거)
    for enr in enrollments:
        completed_videos_cnt = video_counts.get(enr.id, 0)
        submitted_quizzes_cnt = quiz_counts.get(enr.learner_id, 0)
        completed_items = completed_videos_cnt + submitted_quizzes_cnt

        if completed_items < total_items_count:
            if enr.status == "COMPLETED":
                enr.status = "IN_PROGRESS"
                enr.current_completed_at = None


def calculate_course_processing(course: Course) -> tuple[int, int]:
    """활성 영상 STT 및 활성 단위 퀴즈 AI 처리 상태 집계 (completed, total)"""
    active_videos = [v for v in course.videos if v.is_active]
    active_unit_quizzes = [q for q in course.quizzes if q.quiz_type == "UNIT" and q.is_active]

    total = len(active_videos) + len(active_unit_quizzes)
    completed_videos = sum(1 for v in active_videos if v.stt_status == "COMPLETED")
    completed_quizzes = sum(1 for q in active_unit_quizzes if q.ai_status == "COMPLETED")
    completed = completed_videos + completed_quizzes

    return completed, total
