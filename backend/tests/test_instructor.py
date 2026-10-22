import io
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.category import Category, Topic
from app.models.course import Course, Question, QuestionChoice, Quiz, Section, Video
from app.models.learning import AttemptQuestion, QuizAttempt
from app.models.user import User


def test_create_and_get_course(client: TestClient, db_session: Session):
    # [B-3] 강좌 생성
    res = client.post(
        "/api/v1/instructor/courses",
        data={
            "title": "테스트 강좌",
            "description": "설명입니다",
            "categoryId": 1,
            "topicId": 11,
            "difficulty": "BEGINNER",
            "priceKrw": 10000,
        },
    )
    assert res.status_code == 201
    data = res.json()["data"]
    course_id = data["courseId"]
    assert data["status"] == "DRAFT"
    assert data["title"] == "테스트 강좌"

    # [B-1] 내 강좌 목록 조회
    res = client.get("/api/v1/instructor/courses")
    assert res.status_code == 200
    list_data = res.json()["data"]
    assert list_data["counts"]["DRAFT"] >= 1
    assert any(item["courseId"] == course_id for item in list_data["items"])

    # [B-2] 편집용 강좌 상세 조회
    res = client.get(f"/api/v1/instructor/courses/{course_id}")
    assert res.status_code == 200
    detail = res.json()["data"]
    assert detail["courseId"] == course_id
    assert detail["publishCheck"]["isReady"] is False
    assert len(detail["publishCheck"]["missing"]) > 0


def test_publish_check_and_status_transition(client: TestClient, db_session: Session):
    # 1. 강좌 생성
    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="공개 조건 테스트 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="DRAFT",
    )
    db_session.add(course)
    db_session.flush()

    # 미충족 상태에서 PUBLISHED 시도 -> 409 Conflict (PUBLISH_NOT_READY)
    res = client.put(
        f"/api/v1/instructor/courses/{course.id}/status",
        json={"status": "PUBLISHED"},
    )
    assert res.status_code == 409
    err = res.json()["error"]
    assert err["code"] == "PUBLISH_NOT_READY"
    assert "missing" in err
    assert "OT_VIDEO_MISSING" in err["missing"]

    # 2. 6대 공개 조건 충족 작업
    # 1) 활성 무료 OT 영상 1개 + STT 완료 + 검수 완료
    ot_video = Video(
        course_id=course.id,
        video_type="OT",
        title="무료 OT",
        duration_seconds=60,
        sort_order=0,
        video_storage_key="test/ot.mp4",
        stt_status="COMPLETED",
        transcript_reviewed_at=datetime.now(timezone.utc),
        is_active=True,
    )
    db_session.add(ot_video)

    # 2) 활성 섹션 1개 + 활성 학습 영상 1개 + STT 완료 + 검수 완료
    section = Section(course_id=course.id, title="섹션 1", sort_order=1, is_active=True)
    db_session.add(section)
    db_session.flush()

    lesson_video = Video(
        course_id=course.id,
        section_id=section.id,
        video_type="LESSON",
        title="1강",
        duration_seconds=120,
        sort_order=1,
        video_storage_key="test/lesson1.mp4",
        stt_status="COMPLETED",
        transcript_reviewed_at=datetime.now(timezone.utc),
        is_active=True,
    )
    db_session.add(lesson_video)

    # 3) 활성 단위 퀴즈 1개 + AI 완료 + 검수 완료 + 3문항 (보기 4개)
    unit_quiz = Quiz(
        course_id=course.id,
        section_id=section.id,
        quiz_type="UNIT",
        ai_status="COMPLETED",
        reviewed_at=datetime.now(timezone.utc),
        is_active=True,
    )
    db_session.add(unit_quiz)
    db_session.flush()

    for i in range(1, 4):
        q = Question(
            quiz_id=unit_quiz.id,
            order_no=i,
            generated_by="AI",
            content=f"단위 문제 {i}",
            explanation=f"해설 {i}",
            correct_choice_no=1,
            is_active=True,
        )
        db_session.add(q)
        db_session.flush()
        for c_no in range(1, 5):
            db_session.add(
                QuestionChoice(
                    question_id=q.id,
                    choice_no=c_no,
                    content=f"보기 {c_no}",
                )
            )

    # 4) 활성 코스 종합 퀴즈 1개 + 검수 완료 + 3문항 (보기 4개)
    final_quiz = Quiz(
        course_id=course.id,
        section_id=None,
        quiz_type="FINAL",
        ai_status=None,
        reviewed_at=datetime.now(timezone.utc),
        is_active=True,
    )
    db_session.add(final_quiz)
    db_session.flush()

    for i in range(1, 4):
        q = Question(
            quiz_id=final_quiz.id,
            order_no=i,
            generated_by="INSTRUCTOR",
            content=f"종합 문제 {i}",
            explanation=f"해설 {i}",
            correct_choice_no=1,
            is_active=True,
        )
        db_session.add(q)
        db_session.flush()
        for c_no in range(1, 5):
            db_session.add(
                QuestionChoice(
                    question_id=q.id,
                    choice_no=c_no,
                    content=f"보기 {c_no}",
                )
            )

    db_session.commit()
    db_session.expire_all()

    # 3. 공개 조건 통과 후 PUBLISHED 전환
    res = client.put(
        f"/api/v1/instructor/courses/{course.id}/status",
        json={"status": "PUBLISHED"},
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["status"] == "PUBLISHED"
    assert data["publishedAt"] is not None

    # 4. PUBLISHED 상태에서 EDITING 전환
    res = client.put(
        f"/api/v1/instructor/courses/{course.id}/status",
        json={"status": "EDITING"},
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["status"] == "EDITING"
    assert data["closedByUserId"] == 6
    assert data["closedAt"] is not None


def test_question_order_negation_on_update(client: TestClient, db_session: Session):
    """[B-8] 문항 수정 시 Unique 제약 충돌 방지 및 기출제 문항 order_no = -id 음수화 검증"""
    # 강좌 및 종합 퀴즈 생성
    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="퀴즈 제약조건 테스트 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="DRAFT",
    )
    db_session.add(course)
    db_session.flush()

    quiz = Quiz(
        course_id=course.id,
        section_id=None,
        quiz_type="FINAL",
        is_active=True,
    )
    db_session.add(quiz)
    db_session.flush()

    # 기존 3개 문항 추가
    old_questions: list[Question] = []
    for i in range(1, 4):
        q = Question(
            quiz_id=quiz.id,
            order_no=i,
            generated_by="INSTRUCTOR",
            content=f"기존 문제 {i}",
            explanation="해설",
            correct_choice_no=1,
            is_active=True,
        )
        db_session.add(q)
        db_session.flush()
        old_questions.append(q)
        for c_no in range(1, 5):
            db_session.add(
                QuestionChoice(
                    question_id=q.id,
                    choice_no=c_no,
                    content=f"보기 {c_no}",
                )
            )

    # 1번 문항에 대해 학생 응시 제출 이력(AttemptQuestion) 생성
    attempt = QuizAttempt(
        quiz_id=quiz.id,
        learner_id=2,
        attempt_type="FINAL",
        status="SUBMITTED",
    )
    db_session.add(attempt)
    db_session.flush()

    aq = AttemptQuestion(
        attempt_id=attempt.id,
        question_id=old_questions[0].id,
        order_no=1,
    )
    db_session.add(aq)
    db_session.commit()

    target_old_q_id = old_questions[0].id

    # [B-8] 퀴즈 PUT 호출로 신규 3개 문항 전체 교체
    new_payload = {
        "questions": [
            {
                "content": "신규 문제 1",
                "choices": ["A", "B", "C", "D"],
                "correctChoiceNo": 2,
                "explanation": "신규 해설 1",
            },
            {
                "content": "신규 문제 2",
                "choices": ["1", "2", "3", "4"],
                "correctChoiceNo": 3,
                "explanation": "신규 해설 2",
            },
            {
                "content": "신규 문제 3",
                "choices": ["가", "나", "다", "라"],
                "correctChoiceNo": 1,
                "explanation": "신규 해설 3",
            },
        ]
    }

    res = client.put(f"/api/v1/instructor/courses/{course.id}/quiz", json=new_payload)
    assert res.status_code == 200

    # 검증: 기존 1번 문항이 비활성화되고 order_no가 -id로 음수화되었는지 확인!
    db_session.expire_all()
    preserved_old_q = db_session.get(Question, target_old_q_id)
    assert preserved_old_q is not None
    assert preserved_old_q.is_active is False
    assert preserved_old_q.order_no == -target_old_q_id

    # 신규 문항들이 1, 2, 3 순서로 활성화되어 저장되었는지 확인
    active_qs = (
        db_session.query(Question)
        .filter(Question.quiz_id == quiz.id, Question.is_active.is_(True))
        .order_by(Question.order_no)
        .all()
    )
    assert len(active_qs) == 3
    assert [q.order_no for q in active_qs] == [1, 2, 3]


def test_course_ownership_forbidden_for_other(client: TestClient, db_session: Session):
    """타인 소유 강좌 접근 시 404 NOT_FOUND 반환 검증"""
    # 강사 id=99 소유의 강좌 생성
    other_user = User(
        id=99,
        email="other@baeugo.test",
        password_hash="hash",
        name="타인강사",
        account_type="MEMBER",
        active_mode="INSTRUCTOR",
        is_active=True,
    )
    db_session.add(other_user)
    db_session.flush()

    other_course = Course(
        instructor_id=99,
        category_id=1,
        topic_id=11,
        title="타인 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="DRAFT",
    )
    db_session.add(other_course)
    db_session.commit()

    # 현재 로그인 강사(id=6)가 조회 시도 -> 404 NOT_FOUND
    res = client.get(f"/api/v1/instructor/courses/{other_course.id}")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_feed_and_search(client: TestClient, db_session: Session):
    """[B-10] OT 피드 및 [B-11] 강좌 검색 엔드포인트 검증"""
    # 1. 공개 강좌 및 OT 영상 추가
    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="공개용 검색 대상 강좌",
        description="상세한 설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="PUBLISHED",
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(course)
    db_session.flush()

    ot_vid = Video(
        course_id=course.id,
        video_type="OT",
        title="공개 무료 OT",
        duration_seconds=30,
        video_storage_key="test/ot_search.mp4",
        stt_status="COMPLETED",
        view_count=50,
        like_count=5,
        is_active=True,
    )
    db_session.add(ot_vid)
    db_session.commit()

    # [B-10] 무료 OT 피드 조회 (JWT 헤더 포함)
    res = client.get(
        "/api/v1/feed/ot",
        headers={"Authorization": "Bearer mock_token"},
    )
    assert res.status_code == 200
    feed_data = res.json()["data"]
    assert "items" in feed_data
    assert any(item["videoId"] == ot_vid.id for item in feed_data["items"])

    # [B-11] 강좌 검색 (q="검색 대상")
    res = client.get("/api/v1/courses?q=검색%20대상")
    assert res.status_code == 200
    search_data = res.json()["data"]
    assert search_data["total"] >= 1
    assert any(c["courseId"] == course.id for c in search_data["items"])
