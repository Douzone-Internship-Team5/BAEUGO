import io
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course, Question, QuestionChoice, Quiz, Section, Video


def test_b1_my_courses(client: TestClient, db_session: Session):
    c1 = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="DRAFT 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=10000,
        status="DRAFT",
    )
    c2 = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="PUBLISHED 강좌",
        description="설명",
        difficulty="INTERMEDIATE",
        price_krw=20000,
        status="PUBLISHED",
        published_at=datetime.now(UTC),
    )
    db_session.add_all([c1, c2])
    db_session.commit()

    # [성공 1] 전체 목록 조회 및 counts 검증
    res = client.get("/api/v1/instructor/courses")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["counts"]["DRAFT"] >= 1
    assert data["counts"]["PUBLISHED"] >= 1
    assert data["total"] >= 2
    assert "items" in data

    # [성공 2] status=DRAFT 필터링
    res_draft = client.get("/api/v1/instructor/courses?status=DRAFT")
    assert res_draft.status_code == 200
    draft_data = res_draft.json()["data"]
    assert all(item["status"] == "DRAFT" for item in draft_data["items"])

    # [실패 1] 잘못된 status 값 전달 -> 400 VALIDATION_ERROR
    res_err = client.get("/api/v1/instructor/courses?status=INVALID_STATUS")
    assert res_err.status_code == 400
    assert res_err.json()["error"]["code"] == "VALIDATION_ERROR"


def test_b2_course_detail(client: TestClient, db_session: Session):
    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="상세 조회용 강좌",
        description="설명입니다",
        difficulty="ADVANCED",
        price_krw=50000,
        status="DRAFT",
    )
    db_session.add(course)
    db_session.commit()

    # [성공] 편집용 상세 정상 반환 및 계층 구조 검증
    res = client.get(f"/api/v1/instructor/courses/{course.id}")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["courseId"] == course.id
    assert data["title"] == "상세 조회용 강좌"
    assert "publishCheck" in data
    assert data["publishCheck"]["isReady"] is False
    assert len(data["publishCheck"]["missing"]) > 0

    # [실패] 존재하지 않는 courseId -> 404 NOT_FOUND
    res_404 = client.get("/api/v1/instructor/courses/999999")
    assert res_404.status_code == 404
    assert res_404.json()["error"]["code"] == "NOT_FOUND"


def test_b3_course_crud(client: TestClient, db_session: Session):
    # [성공 1] POST 강좌 초안 생성 (파일 첨부 포함)
    thumb_file = io.BytesIO(b"fake image bytes")
    res_create = client.post(
        "/api/v1/instructor/courses",
        data={
            "title": "신규 강좌",
            "description": "강좌 소개 내용",
            "categoryId": 1,
            "topicId": 11,
            "difficulty": "BEGINNER",
            "priceKrw": 30000,
        },
        files={"thumbnail": ("thumb.png", thumb_file, "image/png")},
    )
    assert res_create.status_code == 201
    created_data = res_create.json()["data"]
    course_id = created_data["courseId"]
    assert created_data["status"] == "DRAFT"
    assert created_data["thumbnailUrl"] is not None

    # [실패 1] POST categoryId와 topicId 불일치 -> 400
    res_mismatch = client.post(
        "/api/v1/instructor/courses",
        data={
            "title": "주제 불일치 강좌",
            "description": "설명",
            "categoryId": 1,
            "topicId": 61,
            "difficulty": "BEGINNER",
            "priceKrw": 10000,
        },
    )
    assert res_mismatch.status_code == 400
    assert res_mismatch.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패 2] POST 존재하지 않는 topicId -> 400
    res_no_topic = client.post(
        "/api/v1/instructor/courses",
        data={
            "title": "미존재 주제 강좌",
            "description": "설명",
            "categoryId": 1,
            "topicId": 999999,
            "difficulty": "BEGINNER",
            "priceKrw": 10000,
        },
    )
    assert res_no_topic.status_code == 400

    # [실패 3] POST 음수 가격 -> 400
    res_neg_price = client.post(
        "/api/v1/instructor/courses",
        data={
            "title": "음수 가격 강좌",
            "description": "설명",
            "categoryId": 1,
            "topicId": 11,
            "difficulty": "BEGINNER",
            "priceKrw": -500,
        },
    )
    assert res_neg_price.status_code == 400

    # [실패 4] POST 필수값(title) 누락 -> 400
    res_missing_title = client.post(
        "/api/v1/instructor/courses",
        data={"description": "설명", "categoryId": 1, "topicId": 11, "difficulty": "BEGINNER"},
    )
    assert res_missing_title.status_code == 400

    # [성공 2] PATCH 강좌 메타데이터 수정
    res_patch = client.patch(
        f"/api/v1/instructor/courses/{course_id}",
        data={"title": "수정된 강좌 제목", "priceKrw": 25000},
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["data"]["title"] == "수정된 강좌 제목"
    assert res_patch.json()["data"]["priceKrw"] == 25000

    # [실패 5] PATCH 수정 시 topicId 불일치 -> 400
    res_patch_bad_topic = client.patch(
        f"/api/v1/instructor/courses/{course_id}",
        data={"topicId": 61},
    )
    assert res_patch_bad_topic.status_code == 400

    # [실패 6] PATCH 수정 시 음수 가격 -> 400
    res_patch_neg = client.patch(
        f"/api/v1/instructor/courses/{course_id}",
        data={"priceKrw": -100},
    )
    assert res_patch_neg.status_code == 400

    # [실패 7] PUBLISHED 상태 강좌 직접 PATCH -> 409 CONFLICT
    pub_course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="공개 이력 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="PUBLISHED",
        published_at=datetime.now(UTC),
    )
    db_session.add(pub_course)
    db_session.commit()

    res_patch_pub = client.patch(
        f"/api/v1/instructor/courses/{pub_course.id}",
        data={"title": "수정 시도"},
    )
    assert res_patch_pub.status_code == 409

    # [실패 8] DELETE 이미 공개된 적 있는 강좌 삭제 시도 -> 409 CONFLICT
    res_del_pub = client.delete(f"/api/v1/instructor/courses/{pub_course.id}")
    assert res_del_pub.status_code == 409

    # [실패 9] DELETE EDITING 상태(과거 공개 이력 있음) 강좌 삭제 시도 -> 409 CONFLICT
    editing_course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="수정중 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="EDITING",
        published_at=datetime.now(UTC),
    )
    db_session.add(editing_course)
    db_session.commit()

    res_del_editing = client.delete(f"/api/v1/instructor/courses/{editing_course.id}")
    assert res_del_editing.status_code == 409

    # [성공 3] DELETE 공개된 적 없는 DRAFT 강좌 물리 삭제
    res_delete = client.delete(f"/api/v1/instructor/courses/{course_id}")
    assert res_delete.status_code == 200
    assert res_delete.json()["data"]["deleted"] is True
    assert db_session.get(Course, course_id) is None


def test_b4_course_status_transitions(client: TestClient, db_session: Session):
    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="상태 전환 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="DRAFT",
    )
    db_session.add(course)
    db_session.commit()

    # [실패 1] 6대 조건 미충족 상태에서 PUBLISHED 시도 -> 409 PUBLISH_NOT_READY
    res_not_ready = client.put(
        f"/api/v1/instructor/courses/{course.id}/status",
        json={"status": "PUBLISHED"},
    )
    assert res_not_ready.status_code == 409
    err = res_not_ready.json()["error"]
    assert err["code"] == "PUBLISH_NOT_READY"
    assert "missing" in err

    # [실패 2] DRAFT 상태에서 EDITING 전환 시도 -> 409 CONFLICT
    res_invalid_editing = client.put(
        f"/api/v1/instructor/courses/{course.id}/status",
        json={"status": "EDITING"},
    )
    assert res_invalid_editing.status_code == 409

    # [실패 3] 잘못된 status 문자열 -> 400 VALIDATION_ERROR
    res_bad_status = client.put(
        f"/api/v1/instructor/courses/{course.id}/status",
        json={"status": "INVALID_STATUS"},
    )
    assert res_bad_status.status_code == 400

    # 6대 조건 세부 충족 세팅
    # 1) 활성 무료 OT (STT 완료, 검수 완료)
    db_session.add(
        Video(
            course_id=course.id,
            video_type="OT",
            title="무료 OT",
            duration_seconds=60,
            sort_order=0,
            video_storage_key="test/ot.mp4",
            stt_status="COMPLETED",
            transcript_reviewed_at=datetime.now(UTC),
            is_active=True,
        )
    )
    # 2) 활성 섹션 및 학습 영상
    sec = Section(course_id=course.id, title="섹션 1", sort_order=1, is_active=True)
    db_session.add(sec)
    db_session.flush()

    db_session.add(
        Video(
            course_id=course.id,
            section_id=sec.id,
            video_type="LESSON",
            title="1강",
            duration_seconds=120,
            sort_order=1,
            video_storage_key="test/lesson1.mp4",
            stt_status="COMPLETED",
            transcript_reviewed_at=datetime.now(UTC),
            is_active=True,
        )
    )
    # 3) 단위 퀴즈 (3문항, 보기 4개, 검수 완료)
    unit_q = Quiz(
        course_id=course.id,
        section_id=sec.id,
        quiz_type="UNIT",
        ai_status="COMPLETED",
        reviewed_at=datetime.now(UTC),
        is_active=True,
    )
    db_session.add(unit_q)
    db_session.flush()
    for i in range(1, 4):
        q = Question(
            quiz_id=unit_q.id,
            order_no=i,
            generated_by="AI",
            content=f"단위 문항 {i}",
            explanation="해설",
            correct_choice_no=1,
            is_active=True,
        )
        db_session.add(q)
        db_session.flush()
        for c_no in range(1, 5):
            db_session.add(QuestionChoice(question_id=q.id, choice_no=c_no, content=f"보기 {c_no}"))

    # 4) 종합 퀴즈 (3문항, 보기 4개, 검수 완료)
    final_q = Quiz(
        course_id=course.id,
        section_id=None,
        quiz_type="FINAL",
        ai_status=None,
        reviewed_at=datetime.now(UTC),
        is_active=True,
    )
    db_session.add(final_q)
    db_session.flush()
    for i in range(1, 4):
        q = Question(
            quiz_id=final_q.id,
            order_no=i,
            generated_by="INSTRUCTOR",
            content=f"종합 문항 {i}",
            explanation="해설",
            correct_choice_no=1,
            is_active=True,
        )
        db_session.add(q)
        db_session.flush()
        for c_no in range(1, 5):
            db_session.add(QuestionChoice(question_id=q.id, choice_no=c_no, content=f"보기 {c_no}"))

    db_session.commit()
    db_session.expire_all()

    # [성공 1] 6대 조건 충족 후 PUBLISHED 전환 성공
    res_pub = client.put(
        f"/api/v1/instructor/courses/{course.id}/status",
        json={"status": "PUBLISHED"},
    )
    assert res_pub.status_code == 200
    assert res_pub.json()["data"]["status"] == "PUBLISHED"

    # [실패 4] 이미 PUBLISHED 상태인데 다시 PUBLISHED 요청 -> 409 CONFLICT
    res_repub = client.put(
        f"/api/v1/instructor/courses/{course.id}/status",
        json={"status": "PUBLISHED"},
    )
    assert res_repub.status_code == 409

    # [성공 2] PUBLISHED 상태에서 EDITING 전환 성공
    res_edit = client.put(
        f"/api/v1/instructor/courses/{course.id}/status",
        json={"status": "EDITING"},
    )
    assert res_edit.status_code == 200
    assert res_edit.json()["data"]["status"] == "EDITING"
    assert res_edit.json()["data"]["closedByUserId"] == 6


def test_b2_b4_multiple_unit_quizzes_and_enrollment_recalculation(
    client: TestClient, db_session: Session
):
    from app.models.learning import Enrollment, QuestionRecord, QuizAttempt, VideoProgress

    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="재공개 이수율 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="EDITING",
        published_at=datetime.now(UTC),
    )
    db_session.add(course)
    db_session.flush()

    db_session.add(
        Video(
            course_id=course.id,
            video_type="OT",
            title="무료 OT",
            duration_seconds=60,
            sort_order=0,
            video_storage_key="test/ot.mp4",
            stt_status="COMPLETED",
            transcript_reviewed_at=datetime.now(UTC),
            is_active=True,
        )
    )

    sec = Section(course_id=course.id, title="섹션 1", sort_order=1, is_active=True)
    db_session.add(sec)
    db_session.flush()

    lesson = Video(
        course_id=course.id,
        section_id=sec.id,
        video_type="LESSON",
        title="1강",
        duration_seconds=120,
        sort_order=1,
        video_storage_key="test/lesson1.mp4",
        stt_status="COMPLETED",
        transcript_reviewed_at=datetime.now(UTC),
        is_active=True,
    )
    db_session.add(lesson)
    db_session.flush()

    # 동일 섹션에 비활성 UNIT 퀴즈와 활성 UNIT 퀴즈가 함께 존재하는 상황
    inactive_unit_q = Quiz(
        course_id=course.id,
        section_id=sec.id,
        quiz_type="UNIT",
        ai_status="COMPLETED",
        reviewed_at=datetime.now(UTC),
        is_active=False,
    )
    active_unit_q = Quiz(
        course_id=course.id,
        section_id=sec.id,
        quiz_type="UNIT",
        ai_status="COMPLETED",
        reviewed_at=datetime.now(UTC),
        is_active=True,
    )
    final_q = Quiz(
        course_id=course.id,
        section_id=None,
        quiz_type="FINAL",
        ai_status=None,
        reviewed_at=datetime.now(UTC),
        is_active=True,
    )
    db_session.add_all([inactive_unit_q, active_unit_q, final_q])
    db_session.flush()

    active_questions: list[Question] = []
    for qz in (active_unit_q, final_q):
        for i in range(1, 4):
            q = Question(
                quiz_id=qz.id,
                order_no=i,
                generated_by="AI",
                content=f"문항 {i}",
                explanation="해설",
                correct_choice_no=1,
                is_active=True,
            )
            db_session.add(q)
            db_session.flush()
            active_questions.append(q)
            for c_no in range(1, 5):
                db_session.add(
                    QuestionChoice(question_id=q.id, choice_no=c_no, content=f"보기 {c_no}")
                )

    # 수강생(id=2)이 영상과 퀴즈를 모두 이수했으나 미해결 오답(REVIEW_NEEDED)이 남아있는 경우
    first_comp = datetime(2026, 1, 1, tzinfo=UTC)
    enr = Enrollment(
        course_id=course.id,
        learner_id=2,
        status="COMPLETED",
        first_completed_at=first_comp,
        current_completed_at=first_comp,
    )
    db_session.add(enr)
    db_session.flush()

    db_session.add(VideoProgress(enrollment_id=enr.id, video_id=lesson.id, is_completed=True))
    db_session.add_all(
        [
            QuizAttempt(
                quiz_id=active_unit_q.id,
                learner_id=2,
                attempt_type="UNIT",
                status="SUBMITTED",
            ),
            QuizAttempt(
                quiz_id=final_q.id,
                learner_id=2,
                attempt_type="FINAL",
                status="SUBMITTED",
            ),
        ]
    )
    q_rec = QuestionRecord(
        learner_id=2,
        question_id=active_questions[0].id,
        status="REVIEW_NEEDED",
        wrong_count=1,
    )
    db_session.add(q_rec)
    db_session.commit()
    db_session.expire_all()

    # [B-2] 다중 UNIT 퀴즈 행이 있어도 상세 조회 및 Section.unit_quiz 프로퍼티 정상 동작
    sec_db = db_session.get(Section, sec.id)
    assert sec_db.unit_quiz is not None
    assert sec_db.unit_quiz.id == active_unit_q.id

    res_detail = client.get(f"/api/v1/instructor/courses/{course.id}")
    assert res_detail.status_code == 200
    sec_detail = res_detail.json()["data"]["sections"][0]
    assert sec_detail["unitQuiz"]["quizId"] == active_unit_q.id
    assert res_detail.json()["data"]["publishCheck"]["isReady"] is True

    # [B-4] 재공개 시 미해결 오답(REVIEW_NEEDED)이 있으므로
    # COMPLETED -> IN_PROGRESS 전환 (first_completed_at 보존)
    res_pub = client.put(
        f"/api/v1/instructor/courses/{course.id}/status",
        json={"status": "PUBLISHED"},
    )
    assert res_pub.status_code == 200
    db_session.expire_all()
    updated_enr = db_session.get(Enrollment, enr.id)
    assert updated_enr.status == "IN_PROGRESS"
    assert updated_enr.current_completed_at is None
    assert updated_enr.first_completed_at is not None

    # 오답 해결(CORRECT) 후 다시 EDITING -> PUBLISHED 전환 시 IN_PROGRESS -> COMPLETED 전환 검증
    client.put(f"/api/v1/instructor/courses/{course.id}/status", json={"status": "EDITING"})
    q_rec_db = db_session.get(QuestionRecord, q_rec.id)
    q_rec_db.status = "CORRECT"
    db_session.commit()

    res_pub2 = client.put(
        f"/api/v1/instructor/courses/{course.id}/status",
        json={"status": "PUBLISHED"},
    )
    assert res_pub2.status_code == 200
    db_session.expire_all()
    updated_enr2 = db_session.get(Enrollment, enr.id)
    assert updated_enr2.status == "COMPLETED"
    assert updated_enr2.current_completed_at is not None


def test_b3_course_json_and_strict_validation(client: TestClient, db_session: Session):
    # [성공] application/json 바디로 강좌 생성 및 수정
    res_json_create = client.post(
        "/api/v1/instructor/courses",
        json={
            "title": "JSON 생성 강좌",
            "description": "JSON 바디로 생성",
            "categoryId": 1,
            "topicId": 11,
            "difficulty": "INTERMEDIATE",
            "priceKrw": 15000,
        },
    )
    assert res_json_create.status_code == 201
    cid = res_json_create.json()["data"]["courseId"]
    assert res_json_create.json()["data"]["title"] == "JSON 생성 강좌"

    res_json_patch = client.patch(
        f"/api/v1/instructor/courses/{cid}",
        json={"title": "JSON 수정 강좌", "difficulty": "ADVANCED"},
    )
    assert res_json_patch.status_code == 200
    assert res_json_patch.json()["data"]["title"] == "JSON 수정 강좌"
    assert res_json_patch.json()["data"]["difficulty"] == "ADVANCED"

    # [실패] PATCH categoryId=0 Truthiness 버그 방어 -> 400 VALIDATION_ERROR
    res_cat_zero = client.patch(
        f"/api/v1/instructor/courses/{cid}",
        json={"categoryId": 0},
    )
    assert res_cat_zero.status_code == 400
    assert res_cat_zero.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패] PATCH categoryId만 다른 직무(2)로 변경 시
    # 기존 topicId(11)와 불일치 -> 400 VALIDATION_ERROR
    res_cat_only_mismatch = client.patch(
        f"/api/v1/instructor/courses/{cid}",
        json={"categoryId": 2},
    )
    assert res_cat_only_mismatch.status_code == 400

    # [실패] 실수(float) priceKrw 입력 시 400 VALIDATION_ERROR
    res_float_price = client.patch(
        f"/api/v1/instructor/courses/{cid}",
        json={"priceKrw": 100.5},
    )
    assert res_float_price.status_code == 400
    assert res_float_price.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패] 잘못된 difficulty 값 -> 400 VALIDATION_ERROR
    res_bad_diff = client.post(
        "/api/v1/instructor/courses",
        json={
            "title": "난이도 오류",
            "description": "설명",
            "categoryId": 1,
            "topicId": 11,
            "difficulty": "EXPERT",
        },
    )
    assert res_bad_diff.status_code == 400

    # [실패] 공백 제목 또는 빈 썸네일 파일 / 잘못된 확장자 -> 400
    res_empty_title = client.patch(
        f"/api/v1/instructor/courses/{cid}",
        json={"title": "   "},
    )
    assert res_empty_title.status_code == 400

    res_bad_thumb_ext = client.patch(
        f"/api/v1/instructor/courses/{cid}",
        files={"thumbnail": ("thumb.txt", io.BytesIO(b"not an image"), "text/plain")},
    )
    assert res_bad_thumb_ext.status_code == 400
    assert res_bad_thumb_ext.json()["error"]["code"] == "INVALID_FILE"

    res_empty_thumb = client.patch(
        f"/api/v1/instructor/courses/{cid}",
        files={"thumbnail": ("thumb.png", io.BytesIO(b""), "image/png")},
    )
    assert res_empty_thumb.status_code == 400
    assert res_empty_thumb.json()["error"]["code"] == "INVALID_FILE"
