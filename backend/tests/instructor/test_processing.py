"""저장 어댑터 테스트. 실제 Whisper/Gemini 호출과 품질 평가는 하지 않는다."""

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.models import Course, ProcessingJob, Question, Quiz, Section, Video, VideoScript
from app.models.learning import Enrollment
from app.models.quiz import AttemptQuestion, QuestionAnswer, QuestionRecord, QuizAttempt
from app.routers.instructor.videos import delete_video, update_video_script
from app.schemas.script import ScriptSegment, ScriptUpdateRequest
from app.services.processing import jobs
from app.services.processing.engine.correction import CorrectionResult, CorrectionService
from app.services.processing.engine.errors import ProcessingError
from app.services.processing.engine.quiz import QuizService
from app.services.processing.engine.quiz_review import QuizReviewService
from app.services.processing.engine.schemas import Choice, QuizResult, Segment, Transcript
from app.services.processing.engine.schemas import Question as GeneratedQuestion
from app.services.processing.inputs import section_input


@pytest.fixture
def processing_case(db, monkeypatch):
    @contextmanager
    def session_scope():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    class Sessions:
        begin = staticmethod(session_scope)

        def __call__(self):
            return session_scope()

    monkeypatch.setattr(jobs, "SessionLocal", Sessions())
    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="처리 테스트",
        description="대본 기반 문제",
        difficulty="BEGINNER",
        price_krw=0,
        status="DRAFT",
    )
    db.add(course)
    db.flush()
    section = Section(course_id=course.id, title="섹션", sort_order=1, is_active=True)
    db.add(section)
    db.flush()
    video = Video(
        course_id=course.id,
        section_id=section.id,
        video_type="LESSON",
        title="영상",
        duration_seconds=60,
        sort_order=1,
        video_storage_key="test/video.mp4",
        is_active=True,
    )
    db.add(video)
    db.flush()

    def transcribe(request):
        return Transcript(request.video_id, (Segment(1, 0, 1000, "원본 대본"),))

    def correct(request):
        corrected = Transcript(request.transcript.video_id, (Segment(1, 0, 1000, "보정 대본"),))
        return CorrectionResult(request.transcript, corrected, {}, (), "mock", "mock")

    def generate(request):
        return QuizResult(
            request.course_id,
            request.section_id,
            request.fingerprint(),
            tuple(
                GeneratedQuestion(
                    i,
                    f"질문 {i}",
                    tuple(Choice(n, f"보기 {n}") for n in range(1, 5)),
                    1,
                    "보정 대본에 근거한 해설",
                )
                for i in range(1, 4)
            ),
        )

    from app.services.processing.engine.quiz_review import QuizReviewResult

    def review(request, quiz):
        return QuizReviewResult(request.fingerprint(), "mock", "mock", (), (1, 2, 3), True)

    monkeypatch.setattr(
        jobs.runtime, "get_stt_service", lambda: SimpleNamespace(transcribe=transcribe)
    )
    monkeypatch.setattr(
        jobs.runtime, "get_correction_service", lambda: SimpleNamespace(correct=correct)
    )
    monkeypatch.setattr(
        jobs.runtime, "get_quiz_service", lambda: SimpleNamespace(generate=generate)
    )
    monkeypatch.setattr(jobs.runtime, "get_review_service", lambda: SimpleNamespace(review=review))
    db.commit()
    return course, section, video


def test_processing_saves_unreviewed_results_and_history(db, processing_case):
    course, section, video = processing_case
    job = jobs.queue_video(db, video)
    db.commit()
    jobs.run_job(job.id)
    db.expire_all()
    assert video.stt_status == "COMPLETED"
    assert video.transcript_reviewed_at is None
    assert video.scripts[0].content == "보정 대본"
    assert job.artifacts["raw"]["segments"][0]["content"] == "원본 대본"
    quiz = jobs.active_unit_quiz(db, section.id)
    assert quiz.ai_status == "COMPLETED"
    assert quiz.reviewed_at is None
    assert len(quiz.questions) == 3
    first_quiz_id = quiz.id
    first_question_id = quiz.questions[-1].id
    unreferenced_ids = [question.id for question in quiz.questions[:-1]]
    enrollment = Enrollment(learner_id=2, course_id=course.id)
    db.add(enrollment)
    db.flush()
    attempt = QuizAttempt(
        enrollment_id=enrollment.id, quiz_id=quiz.id, attempt_type="UNIT", status="SUBMITTED"
    )
    db.add(attempt)
    db.flush()
    attempt_question = AttemptQuestion(
        attempt_id=attempt.id, question_id=first_question_id, order_no=1
    )
    db.add(attempt_question)
    db.flush()
    answer = QuestionAnswer(
        attempt_question_id=attempt_question.id, selected_choice_no=2, is_correct=False
    )
    db.add(answer)
    db.flush()
    record = QuestionRecord(
        enrollment_id=enrollment.id,
        question_id=first_question_id,
        last_answer_id=answer.id,
        status="REVIEW_NEEDED",
    )
    db.add(record)
    next_job = jobs.queue_section(db, course.id, section.id)
    db.commit()
    jobs.run_job(next_job.id)
    db.expire_all()
    assert next_job.status == "COMPLETED"
    assert jobs.active_unit_quiz(db, section.id).id == first_quiz_id
    assert db.get(Quiz, first_quiz_id).is_active
    previous_question = db.get(Question, first_question_id)
    assert not previous_question.is_active
    assert previous_question.order_no == -first_question_id
    assert len(previous_question.choices) == 4
    assert attempt.quiz_id == first_quiz_id
    assert attempt_question.question_id == record.question_id == first_question_id
    assert answer.attempt_question_id == attempt_question.id
    assert all(db.get(Question, question_id) is None for question_id in unreferenced_ids)
    active_questions = [question for question in quiz.questions if question.is_active]
    assert [question.order_no for question in active_questions] == [1, 2, 3]
    assert all(question.id != first_question_id for question in active_questions)


def test_invalid_manual_script_preserves_saved_script_and_job(db, processing_case):
    _, _, video = processing_case
    db.add(VideoScript(video_id=video.id, seq=1, start_ms=0, end_ms=1000, content="기존 대본"))
    job = jobs.queue_video(db, video)
    db.commit()
    valid = ScriptSegment(seq=1, start_ms=0, end_ms=1000, content="대본")
    invalid_segments = [
        [],
        [valid, valid.model_copy(update={"seq": 3, "start_ms": 1000, "end_ms": 2000})],
        [valid, valid],
        [valid.model_copy(update={"seq": 0})],
        [valid.model_copy(update={"start_ms": -1})],
        [valid.model_copy(update={"end_ms": 0})],
        [valid.model_copy(update={"content": "   "})],
        [valid.model_copy(update={"start_ms": 70000, "end_ms": 80000})],
        [valid.model_copy(update={"end_ms": 61001})],
        [
            valid.model_copy(update={"start_ms": 2000, "end_ms": 3000}),
            valid.model_copy(update={"seq": 2}),
        ],
    ]
    for segments in invalid_segments:
        with pytest.raises(AppError) as caught:
            update_video_script(
                video.id, ScriptUpdateRequest(segments=segments), db, SimpleNamespace(id=6)
            )
        assert caught.value.status_code == 400
        assert caught.value.code == "VALIDATION_ERROR"
        db.expire_all()
        assert video.scripts[0].content == "기존 대본"
        assert video.transcript_reviewed_at is None
        assert video.stt_status == "PENDING"
        assert job.status == "PENDING"


def test_manual_script_and_video_delete_do_not_regenerate_quiz(db, processing_case):
    course, section, video = processing_case
    quiz = Quiz(
        course_id=course.id,
        section_id=section.id,
        quiz_type="UNIT",
        ai_status="COMPLETED",
        reviewed_at=datetime.now(UTC),
        reviewed_by=6,
        is_active=True,
    )
    db.add(quiz)
    db.commit()
    db.refresh(quiz)
    reviewed_at = quiz.reviewed_at
    response = update_video_script(
        video.id,
        ScriptUpdateRequest(
            segments=[
                ScriptSegment(seq=2, start_ms=1000, end_ms=61000, content="끝"),
                ScriptSegment(seq=1, start_ms=0, end_ms=1000, content="시작"),
            ]
        ),
        db,
        SimpleNamespace(id=6),
    )
    assert [segment.seq for segment in response.data.segments] == [1, 2]
    assert video.transcript_reviewed_at is not None
    assert section_input(db, section.id).videos[0].transcript.segments[-1].end_ms == 61000
    db.refresh(quiz)
    assert quiz.reviewed_at == reviewed_at
    assert quiz.reviewed_by == 6
    assert quiz.ai_status == "COMPLETED"
    assert db.scalars(select(ProcessingJob)).all() == []
    delete_video(video.id, db, SimpleNamespace(id=6))
    db.refresh(quiz)
    assert not video.is_active
    assert quiz.reviewed_at == reviewed_at
    assert quiz.reviewed_by == 6
    assert quiz.ai_status == "COMPLETED"
    assert db.scalars(select(ProcessingJob)).all() == []


def test_processing_waits_for_all_active_lesson_scripts(db, processing_case):
    course, section, video = processing_case
    second = Video(
        course_id=course.id,
        section_id=section.id,
        video_type="LESSON",
        title="영상2",
        duration_seconds=60,
        sort_order=2,
        video_storage_key="test/second.mp4",
        is_active=True,
    )
    db.add(second)
    first_job = jobs.queue_video(db, video)
    second_job = jobs.queue_video(db, second)
    db.commit()
    jobs.run_job(first_job.id)
    assert jobs.active_unit_quiz(db, section.id).ai_status == "PENDING"
    jobs.run_job(second_job.id)
    db.expire_all()
    assert jobs.active_unit_quiz(db, section.id).ai_status == "COMPLETED"
    assert video.transcript_reviewed_at is None
    assert second.transcript_reviewed_at is None


def test_rejected_quiz_is_not_saved_as_completed(db, processing_case, monkeypatch):
    _, section, video = processing_case
    from app.services.processing.engine.quiz_review import QuizReviewResult

    monkeypatch.setattr(
        jobs.runtime,
        "get_review_service",
        lambda: SimpleNamespace(
            review=lambda request, quiz: QuizReviewResult(
                request.fingerprint(), "mock", "mock", (), (), False, "QUIZ_REVIEW_REJECTED"
            )
        ),
    )
    job = jobs.queue_video(db, video)
    db.commit()
    jobs.run_job(job.id)
    quiz = jobs.active_unit_quiz(db, section.id)
    assert quiz.ai_status == "FAILED"
    assert quiz.questions == []
    failed = db.scalar(select(ProcessingJob).where(ProcessingJob.kind == "QUIZ"))
    assert failed.artifacts["review"]["section_passed"] is False


def test_correction_failure_preserves_raw_transcript(db, processing_case, monkeypatch):
    _, _, video = processing_case

    def reject(request):
        raise ProcessingError("INVALID_CORRECTION")

    monkeypatch.setattr(
        jobs.runtime, "get_correction_service", lambda: SimpleNamespace(correct=reject)
    )
    job = jobs.queue_video(db, video)
    db.commit()
    jobs.run_job(job.id)
    assert job.status == "FAILED"
    assert video.stt_status == "FAILED"
    assert job.artifacts["raw"]["segments"][0]["content"] == "원본 대본"
    assert video.scripts == []


def test_manual_script_wins_over_running_processing(db, processing_case, monkeypatch):
    _, _, video = processing_case

    def edit_during_correction(request):
        jobs.cancel_jobs(db, video_id=video.id)
        db.add(VideoScript(video_id=video.id, seq=1, start_ms=0, end_ms=1000, content="강사 수정"))
        video.stt_status = "COMPLETED"
        video.transcript_reviewed_at = datetime.now(UTC)
        db.commit()
        return CorrectionResult(request.transcript, request.transcript, {}, (), "mock", "mock")

    monkeypatch.setattr(
        jobs.runtime,
        "get_correction_service",
        lambda: SimpleNamespace(correct=edit_during_correction),
    )
    job = jobs.queue_video(db, video)
    db.commit()
    jobs.run_job(job.id)
    db.expire_all()
    assert job.status == "SUPERSEDED"
    assert video.scripts[0].content == "강사 수정"
    assert video.transcript_reviewed_at is not None


def test_new_request_supersedes_old_token(db, processing_case):
    _, _, video = processing_case
    old = jobs.queue_video(db, video)
    new = jobs.queue_video(db, video)
    db.commit()
    jobs.run_job(old.id)
    assert old.status == "SUPERSEDED"
    assert video.stt_status == "PENDING"
    jobs.run_job(new.id)
    assert new.status == "COMPLETED"


def test_input_change_discards_late_result(db, processing_case, monkeypatch):
    course, _, video = processing_case

    def change_title(request):
        course.title = "변경된 강좌"
        db.commit()
        return CorrectionResult(request.transcript, request.transcript, {}, (), "mock", "mock")

    monkeypatch.setattr(
        jobs.runtime, "get_correction_service", lambda: SimpleNamespace(correct=change_title)
    )
    job = jobs.queue_video(db, video)
    db.commit()
    jobs.run_job(job.id)
    assert job.status == "SUPERSEDED"
    assert job.error_code == "INPUT_CHANGED"
    assert video.stt_status == "FAILED"
    assert video.scripts == []


def test_registered_course_context_reaches_all_processing_steps(db, processing_case, monkeypatch):
    course, section, video = processing_case
    captured = {}

    class Provider:
        model = "mock"

        def generate(self, payload, schema):
            captured["quiz"] = payload
            return {
                "questions": [
                    {
                        "order_no": order,
                        "content": f"문제 {order}",
                        "choices": [
                            {"choice_no": number, "content": f"보기 {number}"}
                            for number in range(1, 5)
                        ],
                        "correct_choice_no": 1,
                        "explanation": "대본에 근거한 해설",
                    }
                    for order in range(1, payload["question_count"] + 1)
                ]
            }

        def generate_structured(self, payload, schema, instruction):
            if "metadata" in payload:
                captured["correction"] = payload["metadata"]
                return {"segments": [{**segment, "reason": ""} for segment in payload["segments"]]}
            captured["review"] = payload["section"]
            source_video = payload["section"]["videos"][0]
            source_segment = source_video["transcript"]["segments"][0]
            return {
                "reviews": [
                    {
                        "order_no": question["order_no"],
                        "grounded": True,
                        "single_correct": True,
                        "explanation_correct": True,
                        "reason": "테스트용 응답",
                        "evidence": [
                            {
                                "video_id": source_video["video_id"],
                                "seq": source_segment["seq"],
                                "quote": source_segment["content"],
                            }
                        ],
                    }
                    for question in payload["questions"]
                ]
            }

    provider = Provider()
    monkeypatch.setattr(jobs.runtime, "get_correction_service", lambda: CorrectionService(provider))
    monkeypatch.setattr(jobs.runtime, "get_quiz_service", lambda: QuizService(provider))
    monkeypatch.setattr(jobs.runtime, "get_review_service", lambda: QuizReviewService(provider))
    course.category.code = "NEW_CATEGORY_WITHOUT_JSON"
    course.category.name = "새 분야"
    course.topic.name = "강사가 선택한 주제"
    first_fingerprint = None
    for difficulty in ("BEGINNER", "ADVANCED"):
        course.difficulty = difficulty
        course.description = f"강사가 입력한 {difficulty} 강좌 설명"
        job = jobs.queue_video(db, video)
        db.commit()
        jobs.run_job(job.id)
        db.expire_all()
        assert job.status == "COMPLETED"
        assert jobs.active_unit_quiz(db, section.id).ai_status == "COMPLETED"
        for stage in ("correction", "quiz", "review"):
            metadata = captured[stage]
            assert metadata["category_name"] == course.category.name
            assert metadata["category_code"] == course.category.code
            assert metadata["topic"] == course.topic.name
            assert metadata["topic_id"] == course.topic_id
            assert metadata["course_title"] == course.title
            assert metadata["section_title"] == section.title
            assert metadata["course_description"] == course.description
            assert metadata["difficulty"] == difficulty
            assert "profile" not in metadata
        assert captured["correction"]["video_title"] == video.title
        assert captured["quiz"]["videos"][0]["title"] == video.title
        fingerprint = job.artifacts["input_fingerprint"]
        if first_fingerprint is None:
            first_fingerprint = fingerprint
        else:
            assert fingerprint != first_fingerprint
