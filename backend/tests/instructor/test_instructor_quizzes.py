from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course, Question, QuestionChoice, Quiz, Section
from app.models.learning import AttemptQuestion, QuizAttempt


def test_b8_quizzes_crud_and_validation(client: TestClient, db_session: Session):
    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="퀴즈 종합 테스트 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="DRAFT",
    )
    db_session.add(course)
    db_session.flush()

    sec = Section(course_id=course.id, title="섹션", sort_order=1, is_active=True)
    db_session.add(sec)
    db_session.commit()

    # [실패 1] 아직 생성되지 않은 퀴즈 조회 -> 404 NOT_FOUND
    res_no_quiz = client.get(f"/api/v1/instructor/courses/{course.id}/quiz")
    assert res_no_quiz.status_code == 404
    assert res_no_quiz.json()["error"]["code"] == "NOT_FOUND"

    # [실패 2] PUT 문항 수가 3개 미만(2개) -> 400 VALIDATION_ERROR
    res_q_few = client.put(
        f"/api/v1/instructor/courses/{course.id}/quiz",
        json={
            "questions": [
                {"content": "문제 1", "choices": ["1", "2", "3", "4"], "correctChoiceNo": 1, "explanation": "해설"},
                {"content": "문제 2", "choices": ["1", "2", "3", "4"], "correctChoiceNo": 2, "explanation": "해설"},
            ]
        },
    )
    assert res_q_few.status_code == 400
    assert res_q_few.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패 3] PUT 문항 수가 5개 초과(6개) -> 400 VALIDATION_ERROR
    res_q_many = client.put(
        f"/api/v1/instructor/courses/{course.id}/quiz",
        json={
            "questions": [
                {"content": f"문제 {i}", "choices": ["1", "2", "3", "4"], "correctChoiceNo": 1, "explanation": "해설"}
                for i in range(1, 7)
            ]
        },
    )
    assert res_q_many.status_code == 400

    # [실패 4] PUT 보기가 4개가 아닌 경우 (3개 / 5개) -> 400 VALIDATION_ERROR
    res_c_few = client.put(
        f"/api/v1/instructor/courses/{course.id}/quiz",
        json={
            "questions": [
                {"content": f"문제 {i}", "choices": ["1", "2", "3"], "correctChoiceNo": 1, "explanation": "해설"}
                for i in range(1, 4)
            ]
        },
    )
    assert res_c_few.status_code == 400

    res_c_many = client.put(
        f"/api/v1/instructor/courses/{course.id}/quiz",
        json={
            "questions": [
                {"content": f"문제 {i}", "choices": ["1", "2", "3", "4", "5"], "correctChoiceNo": 1, "explanation": "해설"}
                for i in range(1, 4)
            ]
        },
    )
    assert res_c_many.status_code == 400

    # [실패 5] PUT correctChoiceNo 범위 초과 (0 또는 5) -> 400 VALIDATION_ERROR
    res_c_range = client.put(
        f"/api/v1/instructor/courses/{course.id}/quiz",
        json={
            "questions": [
                {"content": "문제 1", "choices": ["1", "2", "3", "4"], "correctChoiceNo": 5, "explanation": "해설"},
                {"content": "문제 2", "choices": ["1", "2", "3", "4"], "correctChoiceNo": 0, "explanation": "해설"},
                {"content": "문제 3", "choices": ["1", "2", "3", "4"], "correctChoiceNo": 1, "explanation": "해설"},
            ]
        },
    )
    assert res_c_range.status_code == 400

    # [성공 1] PUT 종합 퀴즈 정상 등록 (3문항, 보기 4개)
    valid_payload = {
        "questions": [
            {
                "content": f"문제 {i}",
                "choices": ["A", "B", "C", "D"],
                "correctChoiceNo": 1,
                "explanation": f"해설 {i}",
            }
            for i in range(1, 4)
        ]
    }
    res_final_put = client.put(f"/api/v1/instructor/courses/{course.id}/quiz", json=valid_payload)
    assert res_final_put.status_code == 200
    final_data = res_final_put.json()["data"]
    assert final_data["quizType"] == "FINAL"
    assert final_data["reviewedAt"] is not None

    # [성공 2] GET 종합 퀴즈 조회
    res_final_get = client.get(f"/api/v1/instructor/courses/{course.id}/quiz")
    assert res_final_get.status_code == 200
    assert res_final_get.json()["data"]["quizType"] == "FINAL"

    # [성공 3] PUT 단위 퀴즈 등록 (sectionId 쿼리)
    res_unit_put = client.put(
        f"/api/v1/instructor/courses/{course.id}/quiz?sectionId={sec.id}",
        json=valid_payload,
    )
    assert res_unit_put.status_code == 200
    unit_data = res_unit_put.json()["data"]
    assert unit_data["quizType"] == "UNIT"
    assert unit_data["sectionId"] == sec.id

    # [성공 4] POST 단위 퀴즈 재생성 요청
    res_unit_regen = client.post(
        f"/api/v1/instructor/courses/{course.id}/quiz/regenerate?sectionId={sec.id}"
    )
    assert res_unit_regen.status_code == 200
    assert res_unit_regen.json()["data"]["aiStatus"] == "PENDING"

    # [실패 6] 단위 퀴즈 재생성 시 sectionId 누락 -> 400 VALIDATION_ERROR
    res_regen_no_param = client.post(f"/api/v1/instructor/courses/{course.id}/quiz/regenerate")
    assert res_regen_no_param.status_code == 400

    # [실패 7] PUBLISHED 강좌의 퀴즈 수정 및 재생성 시도 -> 409 CONFLICT
    pub_course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="공개 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="PUBLISHED",
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(pub_course)
    db_session.flush()

    pub_sec = Section(course_id=pub_course.id, title="섹션", sort_order=1, is_active=True)
    db_session.add(pub_sec)
    db_session.commit()

    res_pub_put = client.put(
        f"/api/v1/instructor/courses/{pub_course.id}/quiz",
        json=valid_payload,
    )
    assert res_pub_put.status_code == 409

    res_pub_regen = client.post(
        f"/api/v1/instructor/courses/{pub_course.id}/quiz/regenerate?sectionId={pub_sec.id}"
    )
    assert res_pub_regen.status_code == 409


def test_question_order_negation_on_update(client: TestClient, db_session: Session):
    # (치명적 제약조건 방어)
    # 수강생 제출 이력이 있는 퀴즈 문항을 수정할 때, 기존 문항의 order_no = -id 로 음수화하여
    # uk_questions_quiz_order 제약조건 충돌 없이 안전하게 보존되는지 검증
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

    quiz = Quiz(course_id=course.id, quiz_type="FINAL", is_active=True)
    db_session.add(quiz)
    db_session.flush()

    q1 = Question(
        quiz_id=quiz.id,
        order_no=1,
        generated_by="INSTRUCTOR",
        content="기존 문항 1",
        explanation="해설",
        correct_choice_no=1,
        is_active=True,
    )
    q2 = Question(
        quiz_id=quiz.id,
        order_no=2,
        generated_by="INSTRUCTOR",
        content="기존 문항 2",
        explanation="해설",
        correct_choice_no=1,
        is_active=True,
    )
    q3 = Question(
        quiz_id=quiz.id,
        order_no=3,
        generated_by="INSTRUCTOR",
        content="기존 문항 3",
        explanation="해설",
        correct_choice_no=1,
        is_active=True,
    )
    db_session.add_all([q1, q2, q3])
    db_session.flush()

    for q in [q1, q2, q3]:
        for c_no in range(1, 5):
            db_session.add(QuestionChoice(question_id=q.id, choice_no=c_no, content=f"보기 {c_no}"))

    # 수강생의 시도 이력(attempt_questions) 생성
    attempt = QuizAttempt(
        quiz_id=quiz.id,
        learner_id=2,
        attempt_type="FINAL",
        status="SUBMITTED",
    )
    db_session.add(attempt)
    db_session.flush()

    db_session.add(AttemptQuestion(attempt_id=attempt.id, question_id=q1.id, order_no=1))
    db_session.commit()

    old_q1_id = q1.id

    # 문항 교체 요청 (기존 문항 1이 보존되고 음수 order_no를 가져야 함)
    new_payload = {
        "questions": [
            {"content": f"새 문항 {i}", "choices": ["A", "B", "C", "D"], "correctChoiceNo": 2, "explanation": f"새 해설 {i}"}
            for i in range(1, 4)
        ]
    }
    res_update = client.put(f"/api/v1/instructor/courses/{course.id}/quiz", json=new_payload)
    assert res_update.status_code == 200

    db_session.expire_all()
    preserved_q1 = db_session.get(Question, old_q1_id)
    assert preserved_q1 is not None
    assert preserved_q1.is_active is False
    assert preserved_q1.order_no == -int(old_q1_id)
