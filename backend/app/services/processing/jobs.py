"""요청 트랜잭션에서 예약하고, 응답 후 독립 세션으로 처리한다.

모든 강사 콘텐츠 변경과 저장은 course 행을 먼저 잠근다.
외부 AI 호출 중에는 DB 세션/잠금을 유지하지 않는다.
"""

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.course import Course
from app.models.learning import AttemptQuestion, QuestionRecord
from app.models.processing import ProcessingJob
from app.models.quiz import Question, QuestionChoice, Quiz
from app.models.video import Video, VideoScript
from app.services.processing import runtime
from app.services.processing.engine.correction import CorrectionInput
from app.services.processing.engine.errors import ProcessingError
from app.services.processing.engine.schemas import VideoInput, section_from_dict
from app.services.processing.inputs import section_input, video_snapshot

logger = logging.getLogger(__name__)
OPEN_STATUSES = ("PENDING", "PROCESSING")
TRANSCRIPT_FAILURE = "SECTION_TRANSCRIPT_FAILED"


def _json_value(value: dict) -> dict:
    # JSON 저장/재조회 전후 tuple과 list의 차이가 스냅샷 비교에 영향을 주지 않게 한다.
    return json.loads(json.dumps(value, ensure_ascii=False))


def _lock_course(db: Session, course_id: int) -> None:
    db.scalar(
        select(Course)
        .where(Course.id == course_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def active_unit_quiz(db: Session, section_id: int) -> Quiz | None:
    return db.scalar(
        select(Quiz).where(
            Quiz.section_id == section_id,
            Quiz.quiz_type == "UNIT",
            Quiz.is_active.is_(True),
        )
    )


def cancel_jobs(db: Session, *, video_id: int | None = None, section_id: int | None = None) -> None:
    target = (
        ProcessingJob.video_id == video_id
        if video_id is not None
        else ((ProcessingJob.section_id == section_id) & (ProcessingJob.kind == "QUIZ"))
    )
    for job in db.scalars(
        select(ProcessingJob).where(target, ProcessingJob.status.in_(OPEN_STATUSES))
    ):
        job.status = "SUPERSEDED"
        job.finished_at = datetime.now(UTC)
    db.flush()


def queue_section(db: Session, course_id: int, section_id: int) -> ProcessingJob:
    """호출자가 course 잠금을 가진 상태에서 호출하고 직접 commit한다."""
    cancel_jobs(db, section_id=section_id)
    quiz = active_unit_quiz(db, section_id)
    if quiz is None:
        quiz = Quiz(course_id=course_id, section_id=section_id, quiz_type="UNIT", is_active=True)
        db.add(quiz)
    quiz.ai_status = "PENDING"
    quiz.reviewed_at = None
    quiz.reviewed_by = None
    job = ProcessingJob(
        id=str(uuid4()), course_id=course_id, section_id=section_id, kind="QUIZ", status="PENDING"
    )
    db.add(job)
    db.flush()
    return job


def _fail_waiting_quiz(db: Session, section_id: int) -> None:
    quiz = active_unit_quiz(db, section_id)
    if quiz is None or quiz.ai_status != "PENDING" or quiz.reviewed_at is not None:
        return
    failed_video = db.scalar(
        select(Video.id)
        .where(
            Video.section_id == section_id,
            Video.video_type == "LESSON",
            Video.is_active.is_(True),
            Video.stt_status == "FAILED",
        )
        .limit(1)
    )
    if failed_video is None:
        return
    for job in db.scalars(
        select(ProcessingJob).where(
            ProcessingJob.section_id == section_id,
            ProcessingJob.kind == "QUIZ",
            ProcessingJob.status == "PENDING",
        )
    ):
        job.status = "FAILED"
        job.error_code = TRANSCRIPT_FAILURE
        job.finished_at = datetime.now(UTC)
        quiz.ai_status = "FAILED"
    db.flush()


def resume_section_quiz(db: Session, video: Video) -> ProcessingJob | None:
    """대본을 기다리던 퀴즈만 재개한다. 호출자는 course 잠금을 유지한다."""
    if not video.is_active or video.video_type != "LESSON" or video.section_id is None:
        return None
    db.flush()
    quiz = active_unit_quiz(db, video.section_id)
    if quiz is None or quiz.reviewed_at is not None or quiz.ai_status not in ("PENDING", "FAILED"):
        return None
    try:
        if section_input(db, video.section_id) is None:
            return None
    except ProcessingError:
        return None  # 다른 영상도 유효한 대본을 갖춘 뒤 재개한다.
    pending = db.scalar(
        select(ProcessingJob).where(
            ProcessingJob.section_id == video.section_id,
            ProcessingJob.kind == "QUIZ",
            ProcessingJob.status.in_(OPEN_STATUSES),
        )
    )
    if pending is not None:
        return pending if pending.status == "PENDING" else None
    latest = db.scalar(
        select(ProcessingJob)
        .where(
            ProcessingJob.section_id == video.section_id,
            ProcessingJob.kind == "QUIZ",
            ProcessingJob.finished_at.is_not(None),
        )
        .order_by(ProcessingJob.finished_at.desc())
        .limit(1)
    )
    if latest is None or latest.status != "FAILED" or latest.error_code != TRANSCRIPT_FAILURE:
        return None
    return queue_section(db, video.course_id, video.section_id)


def queue_video(db: Session, video: Video) -> ProcessingJob:
    db.flush()
    cancel_jobs(db, video_id=video.id)
    video.stt_status = "PENDING"
    video.transcript_reviewed_at = None
    job = ProcessingJob(
        id=str(uuid4()),
        course_id=video.course_id,
        video_id=video.id,
        section_id=video.section_id,
        kind="VIDEO",
        status="PENDING",
    )
    db.add(job)
    if video.video_type == "LESSON" and video.section_id is not None:
        queue_section(db, video.course_id, video.section_id)
        _fail_waiting_quiz(db, video.section_id)
    db.flush()
    return job


def _set_domain_status(db: Session, job: ProcessingJob, status: str) -> None:
    if job.kind == "VIDEO":
        video = db.get(Video, job.video_id)
        if video is not None and video.is_active:
            video.stt_status = status
            if status == "FAILED" and video.video_type == "LESSON" and video.section_id is not None:
                db.flush()
                _fail_waiting_quiz(db, video.section_id)
    else:
        quiz = active_unit_quiz(db, job.section_id)
        if quiz is not None:
            quiz.ai_status = status


def _locked_job(db: Session, job_id: str) -> ProcessingJob | None:
    job = db.get(ProcessingJob, job_id)
    if job is None:
        return None
    _lock_course(db, job.course_id)
    db.refresh(job)
    return job


def _claim(job_id: str) -> ProcessingJob | None:
    with SessionLocal.begin() as db:
        job = _locked_job(db, job_id)
        if job is None or job.status != "PENDING":
            return None
        if job.kind == "VIDEO":
            snapshot = video_snapshot(db, job.video_id)
        else:
            request = section_input(db, job.section_id)
            if request is None:
                return None  # 마지막 영상 완료 또는 기존 재생성 API가 다시 깨운다.
            snapshot = asdict(request)
        job.input_snapshot = _json_value(snapshot)
        job.status = "PROCESSING"
        _set_domain_status(db, job, "PROCESSING")
        return job


def _record_artifacts(job_id: str, artifacts: dict) -> bool:
    with SessionLocal.begin() as db:
        job = _locked_job(db, job_id)
        if job is None:
            return False
        job.artifacts = _json_value(artifacts)
        return job.status == "PROCESSING"


def _fail(job_id: str, error_code: str) -> None:
    with SessionLocal.begin() as db:
        job = _locked_job(db, job_id)
        if job is None or job.status not in OPEN_STATUSES:
            return
        job.status = "FAILED"
        job.error_code = error_code[:100]
        job.finished_at = datetime.now(UTC)
        _set_domain_status(db, job, "FAILED")


def _is_current(db: Session, job: ProcessingJob) -> bool:
    if job.status != "PROCESSING":
        return False
    try:
        if job.kind == "VIDEO":
            current = video_snapshot(db, job.video_id)
        else:
            request = section_input(db, job.section_id)
            current = asdict(request) if request is not None else None
        matches = current is not None and _json_value(current) == job.input_snapshot
    except ProcessingError:
        matches = False
    if not matches:
        job.status = "SUPERSEDED"
        job.error_code = "INPUT_CHANGED"
        job.finished_at = datetime.now(UTC)
        _set_domain_status(db, job, "FAILED")
    return matches


def _complete_video(job_id: str, correction) -> int | None:
    with SessionLocal.begin() as db:
        job = _locked_job(db, job_id)
        if job is None or not _is_current(db, job):
            return None
        video = db.get(Video, job.video_id)
        db.execute(delete(VideoScript).where(VideoScript.video_id == video.id))
        db.add_all(VideoScript(**row) for row in correction.corrected.rows())
        video.stt_status = "COMPLETED"
        video.transcript_reviewed_at = None
        job.status = "COMPLETED"
        job.finished_at = datetime.now(UTC)
        resume_section_quiz(db, video)
        return video.section_id if video.video_type == "LESSON" else None


def retire_quiz_questions(db: Session, quiz: Quiz) -> None:
    """자동 재생성과 수동 수정에 동일한 문항 이력 보존 규칙을 적용한다."""
    for question in db.scalars(select(Question).where(Question.quiz_id == quiz.id)):
        if not question.is_active:
            if question.order_no > 0:
                question.order_no = -question.id
            continue
        has_attempt = db.scalar(
            select(AttemptQuestion.id).where(AttemptQuestion.question_id == question.id).limit(1)
        )
        has_record = db.scalar(
            select(QuestionRecord.id).where(QuestionRecord.question_id == question.id).limit(1)
        )
        if has_attempt is not None or has_record is not None:
            question.is_active = False
            question.order_no = -question.id
        else:
            db.delete(question)
    # (quiz_id, order_no) 고유 제약 때문에 새 문항 삽입 전에 기존 문항을 정리한다.
    db.flush()


def _complete_quiz(job_id: str, result) -> None:
    with SessionLocal.begin() as db:
        job = _locked_job(db, job_id)
        if job is None or not _is_current(db, job):
            return
        rows = result.rows()
        quiz = active_unit_quiz(db, job.section_id)
        if quiz is None:
            quiz = Quiz(**rows["quiz"])
            db.add(quiz)
        else:
            retire_quiz_questions(db, quiz)
            quiz.ai_status = "COMPLETED"
            quiz.reviewed_at = None
            quiz.reviewed_by = None
        db.flush()
        for item in rows["questions"]:
            question = Question(quiz_id=quiz.id, **item["question"])
            db.add(question)
            db.flush()
            db.add_all(
                QuestionChoice(question_id=question.id, **choice) for choice in item["choices"]
            )
        job.status = "COMPLETED"
        job.finished_at = datetime.now(UTC)


def _process_video(job: ProcessingJob) -> int | None:
    # 키/모델 누락은 무거운 STT 실행 전에 확인한다.
    correction_service = runtime.get_correction_service()
    raw = runtime.get_stt_service().transcribe(VideoInput(**job.input_snapshot["video"]))
    if not _record_artifacts(job.id, {"raw": asdict(raw)}):
        return None
    result = correction_service.correct(CorrectionInput(raw, **job.input_snapshot["metadata"]))
    if not _record_artifacts(job.id, asdict(result)):
        return None
    return _complete_video(job.id, result)


def _process_quiz(job: ProcessingJob) -> None:
    request = section_from_dict(job.input_snapshot)
    quiz = runtime.get_quiz_service().generate(request)
    artifacts = {"quiz": asdict(quiz)}
    if not _record_artifacts(job.id, artifacts):
        return
    review = runtime.get_review_service().review(request, quiz)
    artifacts["review"] = asdict(review)
    if not _record_artifacts(job.id, artifacts):
        return
    if not review.section_passed:
        raise ProcessingError(review.error_code or "QUIZ_REVIEW_REJECTED")
    _complete_quiz(job.id, quiz)


def run_job(job_id: str) -> None:
    """BackgroundTasks 진입점. 실패는 응답 후 발생하므로 DB 상태에 기록한다."""
    next_section_id = None
    try:
        job = _claim(job_id)
        if job is None:
            return
        if job.kind == "VIDEO":
            next_section_id = _process_video(job)
        else:
            _process_quiz(job)
    except ProcessingError as error:
        _fail(job_id, error.code)
    except Exception as error:
        # 사용자 대본/키/공급자 응답은 로그에 남기지 않는다.
        logger.error("Processing job %s failed (%s)", job_id, type(error).__name__)
        _fail(job_id, "INTERNAL_ERROR")
    if next_section_id is not None:
        run_section(next_section_id)


def run_section(section_id: int) -> None:
    with SessionLocal() as db:
        job_id = db.scalar(
            select(ProcessingJob.id)
            .where(
                ProcessingJob.section_id == section_id,
                ProcessingJob.kind == "QUIZ",
                ProcessingJob.status == "PENDING",
            )
            .order_by(ProcessingJob.created_at.desc())
            .limit(1)
        )
    if job_id is not None:
        run_job(job_id)
