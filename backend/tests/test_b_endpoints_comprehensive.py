import io
from datetime import date, datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.category import Category, Topic
from app.models.course import (
    Course,
    CourseDailyStat,
    Question,
    QuestionChoice,
    Quiz,
    Section,
    Video,
    VideoScript,
)
from app.models.learning import AttemptQuestion, Enrollment, QuizAttempt
from app.models.user import User


# ==============================================================================
# [B-1] 내 강좌 목록 (GET /api/v1/instructor/courses)
# ==============================================================================
def test_b1_my_courses_all_cases(client: TestClient, db_session: Session):
    # 1. 강좌 준비
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
        published_at=datetime.now(timezone.utc),
    )
    db_session.add_all([c1, c2])
    db_session.commit()

    # [성공 1] 전체 목록 조회
    res = client.get("/api/v1/instructor/courses")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["counts"]["DRAFT"] >= 1
    assert data["counts"]["PUBLISHED"] >= 1
    assert data["total"] >= 2
    assert "items" in data

    # [성공 2] 상태 필터링 (status=DRAFT)
    res_draft = client.get("/api/v1/instructor/courses?status=DRAFT")
    assert res_draft.status_code == 200
    draft_data = res_draft.json()["data"]
    assert all(item["status"] == "DRAFT" for item in draft_data["items"])

    # [실패 1] 잘못된 status 값 전달 -> 400 VALIDATION_ERROR
    res_err = client.get("/api/v1/instructor/courses?status=INVALID_STATUS")
    assert res_err.status_code == 400
    assert res_err.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패 2] 수강자 권한(active_mode=LEARNER)으로 강사 API 접근 시 -> 403 FORBIDDEN
    res_forbidden = client.get(
        "/api/v1/instructor/courses",
        headers={"Authorization": "Bearer learner"},
    )
    assert res_forbidden.status_code == 403
    assert res_forbidden.json()["error"]["code"] == "FORBIDDEN"


# ==============================================================================
# [B-2] 강좌 편집용 상세 (GET /api/v1/instructor/courses/{courseId})
# ==============================================================================
def test_b2_course_detail_all_cases(client: TestClient, db_session: Session):
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

    # [성공 1] 정상 상세 조회 및 계층 구조 확인
    res = client.get(f"/api/v1/instructor/courses/{course.id}")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["courseId"] == course.id
    assert data["title"] == "상세 조회용 강좌"
    assert "publishCheck" in data
    assert data["publishCheck"]["isReady"] is False
    assert len(data["publishCheck"]["missing"]) > 0

    # [실패 1] 존재하지 않는 강좌 ID -> 404 NOT_FOUND
    res_404 = client.get("/api/v1/instructor/courses/999999")
    assert res_404.status_code == 404
    assert res_404.json()["error"]["code"] == "NOT_FOUND"

    # [실패 2] 타인 소유 강좌 접근 -> 보안 규정에 따라 404 NOT_FOUND
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

    res_other = client.get(f"/api/v1/instructor/courses/{other_course.id}")
    assert res_other.status_code == 404
    assert res_other.json()["error"]["code"] == "NOT_FOUND"


# ==============================================================================
# [B-3] 강좌 생성 · 수정 · 삭제 (POST / PATCH / DELETE)
# ==============================================================================
def test_b3_course_create_update_delete_all_cases(client: TestClient, db_session: Session):
    # [성공 1] POST 신규 강좌 초안 생성 (파일 첨부 포함)
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

    # [실패 1] POST categoryId와 topicId 불일치 -> 400 VALIDATION_ERROR
    res_topic_mismatch = client.post(
        "/api/v1/instructor/courses",
        data={
            "title": "주제 불일치 강좌",
            "description": "설명",
            "categoryId": 1,
            "topicId": 61,  # 61은 카테고리 6 소속
            "difficulty": "BEGINNER",
            "priceKrw": 10000,
        },
    )
    assert res_topic_mismatch.status_code == 400
    assert res_topic_mismatch.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패 2] POST priceKrw 음수 -> 400 VALIDATION_ERROR
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
    assert res_neg_price.json()["error"]["code"] == "VALIDATION_ERROR"

    # [성공 2] PATCH 강좌 메타데이터 수정
    res_patch = client.patch(
        f"/api/v1/instructor/courses/{course_id}",
        data={"title": "수정된 강좌 제목", "priceKrw": 25000},
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["data"]["title"] == "수정된 강좌 제목"
    assert res_patch.json()["data"]["priceKrw"] == 25000

    # [실패 3] PATCH 타인 강좌 -> 404 NOT_FOUND
    res_patch_404 = client.patch("/api/v1/instructor/courses/99999", data={"title": "수정"})
    assert res_patch_404.status_code == 404

    # [실패 4] DELETE 이미 공개된 적 있는 강좌 삭제 시도 -> 409 CONFLICT
    pub_course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="공개 이력 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="PUBLISHED",
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(pub_course)
    db_session.commit()

    res_del_conflict = client.delete(f"/api/v1/instructor/courses/{pub_course.id}")
    assert res_del_conflict.status_code == 409
    assert res_del_conflict.json()["error"]["code"] == "CONFLICT"

    # [성공 3] DELETE 공개된 적 없는 DRAFT 강좌 물리 삭제
    res_delete = client.delete(f"/api/v1/instructor/courses/{course_id}")
    assert res_delete.status_code == 200
    assert res_delete.json()["data"]["deleted"] is True
    assert db_session.get(Course, course_id) is None


# ==============================================================================
# [B-4] 강좌 공개 및 수정 시작 (PUT /api/v1/instructor/courses/{courseId}/status)
# ==============================================================================
def test_b4_course_status_transition_all_cases(client: TestClient, db_session: Session):
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
    assert res_invalid_editing.json()["error"]["code"] == "CONFLICT"

    # [실패 3] 잘못된 status 문자열 -> 400 VALIDATION_ERROR
    res_bad_status = client.put(
        f"/api/v1/instructor/courses/{course.id}/status",
        json={"status": "INVALID_STATUS"},
    )
    assert res_bad_status.status_code == 400
    assert res_bad_status.json()["error"]["code"] == "VALIDATION_ERROR"

    # 6대 조건 충족 세팅
    # 1) 활성 무료 OT
    db_session.add(
        Video(
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
            transcript_reviewed_at=datetime.now(timezone.utc),
            is_active=True,
        )
    )
    # 3) 단위 퀴즈 (3문항, 보기 4개, 검수 완료)
    unit_q = Quiz(
        course_id=course.id,
        section_id=sec.id,
        quiz_type="UNIT",
        ai_status="COMPLETED",
        reviewed_at=datetime.now(timezone.utc),
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
        reviewed_at=datetime.now(timezone.utc),
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

    # [성공 2] PUBLISHED 상태에서 EDITING 전환 성공
    res_edit = client.put(
        f"/api/v1/instructor/courses/{course.id}/status",
        json={"status": "EDITING"},
    )
    assert res_edit.status_code == 200
    assert res_edit.json()["data"]["status"] == "EDITING"
    assert res_edit.json()["data"]["closedByUserId"] == 6


# ==============================================================================
# [B-5] 섹션 관리 (POST / PATCH / DELETE)
# ==============================================================================
def test_b5_sections_all_cases(client: TestClient, db_session: Session):
    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="섹션 테스트 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="DRAFT",
    )
    db_session.add(course)
    db_session.commit()

    # [성공 1] POST 섹션 생성 및 sortOrder 자동 배정 (1)
    res_sec1 = client.post(
        "/api/v1/instructor/sections",
        json={"courseId": course.id, "title": "섹션 1"},
    )
    assert res_sec1.status_code == 201
    sec1_data = res_sec1.json()["data"]
    assert sec1_data["sortOrder"] == 1
    sec1_id = sec1_data["sectionId"]

    # [성공 2] POST 두 번째 섹션 생성 -> sortOrder=2 자동 배정
    res_sec2 = client.post(
        "/api/v1/instructor/sections",
        json={"courseId": course.id, "title": "섹션 2"},
    )
    assert res_sec2.status_code == 201
    assert res_sec2.json()["data"]["sortOrder"] == 2

    # [성공 3] PATCH 섹션 수정
    res_patch = client.patch(
        f"/api/v1/instructor/sections/{sec1_id}",
        json={"title": "섹션 1 수정", "sortOrder": 10},
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["data"]["title"] == "섹션 1 수정"
    assert res_patch.json()["data"]["sortOrder"] == 10

    # [성공 4] DELETE 섹션 비활성 삭제 (is_active=False)
    res_del = client.delete(f"/api/v1/instructor/sections/{sec1_id}")
    assert res_del.status_code == 200
    assert res_del.json()["data"]["isActive"] is False
    assert db_session.get(Section, sec1_id).is_active is False

    # [실패 1] 타인 섹션 수정/삭제 시도 -> 404 NOT_FOUND
    res_sec_404 = client.patch("/api/v1/instructor/sections/999999", json={"title": "수정"})
    assert res_sec_404.status_code == 404


# ==============================================================================
# [B-6] 영상 관리 (POST / PATCH / DELETE)
# ==============================================================================
def test_b6_videos_all_cases(client: TestClient, db_session: Session):
    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="영상 테스트 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="DRAFT",
    )
    db_session.add(course)
    db_session.flush()

    sec = Section(course_id=course.id, title="섹션 1", sort_order=1, is_active=True)
    db_session.add(sec)
    db_session.commit()

    # [성공 1] POST 무료 OT 영상 업로드 (videoType=OT)
    fake_vid = io.BytesIO(b"dummy video data")
    res_ot = client.post(
        "/api/v1/instructor/videos",
        data={"courseId": course.id, "videoType": "OT", "title": "무료 OT 1"},
        files={"file": ("ot.mp4", fake_vid, "video/mp4")},
    )
    assert res_ot.status_code == 201
    ot1_id = res_ot.json()["data"]["videoId"]
    assert res_ot.json()["data"]["videoType"] == "OT"

    # [성공 2] 새 OT 업로드 시 기존 OT 자동 비활성화(1개 유지 규칙) 검증
    fake_vid2 = io.BytesIO(b"dummy video data 2")
    res_ot2 = client.post(
        "/api/v1/instructor/videos",
        data={"courseId": course.id, "videoType": "OT", "title": "무료 OT 2"},
        files={"file": ("ot2.mp4", fake_vid2, "video/mp4")},
    )
    assert res_ot2.status_code == 201
    ot2_id = res_ot2.json()["data"]["videoId"]
    db_session.expire_all()
    assert db_session.get(Video, ot1_id).is_active is False
    assert db_session.get(Video, ot2_id).is_active is True

    # [실패 1] POST LESSON 영상인데 sectionId 누락 -> 400 VALIDATION_ERROR
    fake_vid3 = io.BytesIO(b"dummy video data")
    res_lesson_no_sec = client.post(
        "/api/v1/instructor/videos",
        data={"courseId": course.id, "videoType": "LESSON", "title": "학습영상"},
        files={"file": ("v.mp4", fake_vid3, "video/mp4")},
    )
    assert res_lesson_no_sec.status_code == 400
    assert res_lesson_no_sec.json()["error"]["code"] == "VALIDATION_ERROR"

    # [성공 3] POST LESSON 영상 정상 업로드
    fake_vid4 = io.BytesIO(b"dummy video data")
    res_lesson = client.post(
        "/api/v1/instructor/videos",
        data={
            "courseId": course.id,
            "videoType": "LESSON",
            "sectionId": sec.id,
            "title": "1강 개요",
        },
        files={"file": ("lesson.mp4", fake_vid4, "video/mp4")},
    )
    assert res_lesson.status_code == 201
    lesson_id = res_lesson.json()["data"]["videoId"]

    # [성공 4] PATCH 메타데이터만 수정
    res_patch_meta = client.patch(
        f"/api/v1/instructor/videos/{lesson_id}",
        data={"title": "1강 개요 수정본", "sortOrder": 5},
    )
    assert res_patch_meta.status_code == 200
    assert res_patch_meta.json()["data"]["title"] == "1강 개요 수정본"
    assert res_patch_meta.json()["data"]["videoId"] == lesson_id  # 동일 ID 유지

    # [성공 5] PATCH 파일 교체 -> 기존 영상 비활성화 및 신규 videoId 발급 규칙 검증!
    new_vid_file = io.BytesIO(b"replacement video data")
    res_replace = client.patch(
        f"/api/v1/instructor/videos/{lesson_id}",
        files={"file": ("new_lesson.mp4", new_vid_file, "video/mp4")},
    )
    assert res_replace.status_code == 200
    new_vid_id = res_replace.json()["data"]["videoId"]
    assert new_vid_id != lesson_id  # 새 videoId 발급 확인
    db_session.expire_all()
    assert db_session.get(Video, lesson_id).is_active is False
    assert db_session.get(Video, new_vid_id).is_active is True

    # [성공 6] DELETE 영상 비활성화
    res_del_vid = client.delete(f"/api/v1/instructor/videos/{new_vid_id}")
    assert res_del_vid.status_code == 200
    assert res_del_vid.json()["data"]["isActive"] is False


# ==============================================================================
# [B-7] 대본 조회 · 수정 · STT 재시도
# ==============================================================================
def test_b7_scripts_all_cases(client: TestClient, db_session: Session):
    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="대본 테스트 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="DRAFT",
    )
    db_session.add(course)
    db_session.flush()

    video = Video(
        course_id=course.id,
        video_type="OT",
        title="영상",
        duration_seconds=60,
        video_storage_key="test/vid.mp4",
        stt_status="PENDING",
        is_active=True,
    )
    db_session.add(video)
    db_session.commit()

    # [성공 1] GET 대본 조회 (초기 비어있음)
    res_get = client.get(f"/api/v1/instructor/videos/{video.id}/script")
    assert res_get.status_code == 200
    assert res_get.json()["data"]["sttStatus"] == "PENDING"
    assert res_get.json()["data"]["segments"] == []

    # [실패 1] PUT endMs <= startMs 유효성 검증 실패 -> 400 VALIDATION_ERROR
    bad_segments = [
        {"seq": 1, "startMs": 5000, "endMs": 3000, "content": "잘못된 구간"}
    ]
    res_bad = client.put(
        f"/api/v1/instructor/videos/{video.id}/script",
        json={"segments": bad_segments},
    )
    assert res_bad.status_code == 400
    assert res_bad.json()["error"]["code"] == "VALIDATION_ERROR"

    # [성공 2] PUT 정상 자막 교체 및 검수 완료 처리
    valid_segments = [
        {"seq": 1, "startMs": 0, "endMs": 3000, "content": "안녕하세요."},
        {"seq": 2, "startMs": 3000, "endMs": 6000, "content": "배우고 강의입니다."},
    ]
    res_put = client.put(
        f"/api/v1/instructor/videos/{video.id}/script",
        json={"segments": valid_segments},
    )
    assert res_put.status_code == 200
    put_data = res_put.json()["data"]
    assert put_data["sttStatus"] == "COMPLETED"
    assert put_data["transcriptReviewedAt"] is not None
    assert len(put_data["segments"]) == 2

    # [성공 3] POST 대본 재생성 요청 (stt_status=PENDING, reviewed_at=None 초기화)
    res_regen = client.post(f"/api/v1/instructor/videos/{video.id}/script/regenerate")
    assert res_regen.status_code == 200
    assert res_regen.json()["data"]["sttStatus"] == "PENDING"
    db_session.expire_all()
    assert db_session.get(Video, video.id).transcript_reviewed_at is None


# ==============================================================================
# [B-8] 퀴즈 조회 · 수정 · 재생성
# ==============================================================================
def test_b8_quizzes_all_cases(client: TestClient, db_session: Session):
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

    # [실패 1] PUT 문항 수가 3개 미만(2개) -> 400 VALIDATION_ERROR
    res_q_few = client.put(
        f"/api/v1/instructor/courses/{course.id}/quiz",
        json={
            "questions": [
                {
                    "content": "문제 1",
                    "choices": ["1", "2", "3", "4"],
                    "correctChoiceNo": 1,
                    "explanation": "해설",
                },
                {
                    "content": "문제 2",
                    "choices": ["1", "2", "3", "4"],
                    "correctChoiceNo": 2,
                    "explanation": "해설",
                },
            ]
        },
    )
    assert res_q_few.status_code == 400
    assert res_q_few.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패 2] PUT 보기가 3개뿐인 경우 -> 400 VALIDATION_ERROR
    res_c_few = client.put(
        f"/api/v1/instructor/courses/{course.id}/quiz",
        json={
            "questions": [
                {
                    "content": "문제 1",
                    "choices": ["1", "2", "3"],
                    "correctChoiceNo": 1,
                    "explanation": "해설",
                },
                {
                    "content": "문제 2",
                    "choices": ["1", "2", "3", "4"],
                    "correctChoiceNo": 2,
                    "explanation": "해설",
                },
                {
                    "content": "문제 3",
                    "choices": ["1", "2", "3", "4"],
                    "correctChoiceNo": 3,
                    "explanation": "해설",
                },
            ]
        },
    )
    assert res_c_few.status_code == 400
    assert res_c_few.json()["error"]["code"] == "VALIDATION_ERROR"

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


# ==============================================================================
# [B-9] 강사 통계 (GET /api/v1/instructor/stats)
# ==============================================================================
def test_b9_instructor_stats_all_cases(client: TestClient, db_session: Session):
    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="통계 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=10000,
        status="PUBLISHED",
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(course)
    db_session.flush()

    today = date.today()
    stat1 = CourseDailyStat(
        course_id=course.id,
        start_date=today,
        lesson_views=100,
        new_students=10,
        expected_revenue=100000,
        available_quiz_cnt=20,
        first_submitted_cnt=18,
        first_answer_cnt=50,
        first_correct_cnt=40,
    )
    db_session.add(stat1)
    db_session.commit()

    # [성공 1] groupBy=day 조회
    res_day = client.get(
        f"/api/v1/instructor/stats?courseId={course.id}&groupBy=day&from={today.isoformat()}&to={today.isoformat()}"
    )
    assert res_day.status_code == 200
    day_data = res_day.json()["data"]
    assert day_data["summary"]["lessonViews"] == 100
    assert day_data["summary"]["newStudents"] == 10
    assert day_data["summary"]["quizResponseRate"] == 90.0  # 18/20 * 100
    assert day_data["summary"]["quizCorrectRate"] == 80.0   # 40/50 * 100
    assert len(day_data["dailyStats"]) == 1

    # [성공 2] groupBy=course 조회
    res_course = client.get(
        f"/api/v1/instructor/stats?groupBy=course&from={today.isoformat()}&to={today.isoformat()}"
    )
    assert res_course.status_code == 200
    course_data = res_course.json()["data"]
    assert course_data["courseStats"] is not None
    assert any(cs["courseId"] == course.id for cs in course_data["courseStats"])

    # [실패 1] 잘못된 groupBy 값 전달 -> 400 VALIDATION_ERROR
    res_bad_group = client.get("/api/v1/instructor/stats?groupBy=month")
    assert res_bad_group.status_code == 400
    assert res_bad_group.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패 2] 타인 강좌 ID 통계 요청 -> 404 NOT_FOUND
    res_stats_404 = client.get("/api/v1/instructor/stats?courseId=999999")
    assert res_stats_404.status_code == 404


# ==============================================================================
# [B-10] 무료 OT 피드 (GET /api/v1/feed/ot)
# ==============================================================================
def test_b10_feed_ot_all_cases(client: TestClient, db_session: Session):
    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="피드용 강좌",
        description="설명",
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
        title="피드 무료 OT",
        duration_seconds=45,
        video_storage_key="test/ot_feed.mp4",
        stt_status="COMPLETED",
        view_count=120,
        like_count=15,
        is_active=True,
    )
    db_session.add(ot_vid)
    db_session.commit()

    # [성공 1] 정상 로그인 헤더 전달 시 OT 목록 및 커서 페이징 반환
    res = client.get(
        "/api/v1/feed/ot?size=5",
        headers={"Authorization": "Bearer valid_token"},
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert "items" in data
    assert any(item["videoId"] == ot_vid.id for item in data["items"])
    assert "hasNext" in data

    # [실패 1] Authorization 헤더 누락 시 401 UNAUTHORIZED
    res_no_auth = client.get("/api/v1/feed/ot")
    assert res_no_auth.status_code == 401
    assert res_no_auth.json()["error"]["code"] == "UNAUTHORIZED"

    # [실패 2] 유효하지 않은 토큰(invalid) -> 401 UNAUTHORIZED
    res_inv_auth = client.get(
        "/api/v1/feed/ot",
        headers={"Authorization": "Bearer invalid"},
    )
    assert res_inv_auth.status_code == 401
    assert res_inv_auth.json()["error"]["code"] == "UNAUTHORIZED"


# ==============================================================================
# [B-11] 강좌 검색 · 필터 · 정렬 (GET /api/v1/courses)
# ==============================================================================
def test_b11_course_search_all_cases(client: TestClient, db_session: Session):
    c_free = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="무료 기획 강좌",
        description="기획 문서 작성 가이드",
        difficulty="BEGINNER",
        price_krw=0,
        status="PUBLISHED",
        published_at=datetime.now(timezone.utc),
    )
    c_paid = Course(
        instructor_id=6,
        category_id=6,
        topic_id=61,
        title="유료 엑셀 자동화",
        description="사무 생산성 엑셀 실무",
        difficulty="ADVANCED",
        price_krw=45000,
        status="PUBLISHED",
        published_at=datetime.now(timezone.utc),
    )
    db_session.add_all([c_free, c_paid])
    db_session.commit()

    # [성공 1] 비로그인 상태에서 검색어 필터링 (q="기획")
    res_q = client.get("/api/v1/courses?q=기획")
    assert res_q.status_code == 200
    assert any(c["courseId"] == c_free.id for c in res_q.json()["data"]["items"])
    assert not any(c["courseId"] == c_paid.id for c in res_q.json()["data"]["items"])

    # [성공 2] 가격 필터 (price=free / price=paid)
    res_free = client.get("/api/v1/courses?price=free")
    assert res_free.status_code == 200
    assert all(c["isFree"] is True for c in res_free.json()["data"]["items"])

    res_paid = client.get("/api/v1/courses?price=paid")
    assert res_paid.status_code == 200
    assert all(c["isFree"] is False for c in res_paid.json()["data"]["items"])

    # [성공 3] 난이도 필터 및 정렬 (sort=difficulty)
    res_diff = client.get("/api/v1/courses?sort=difficulty")
    assert res_diff.status_code == 200

    # [성공 4] 개인화 추천 정렬 (sort=recommended, 로그인 필수)
    res_rec = client.get(
        "/api/v1/courses?sort=recommended",
        headers={"Authorization": "Bearer user_2"},
    )
    assert res_rec.status_code == 200
    assert "items" in res_rec.json()["data"]

    # [실패 1] 비로그인 상태에서 sort=recommended 요청 -> 401 UNAUTHORIZED
    res_rec_unauth = client.get("/api/v1/courses?sort=recommended")
    assert res_rec_unauth.status_code == 401
    assert res_rec_unauth.json()["error"]["code"] == "UNAUTHORIZED"

    # [실패 2] 유효하지 않은 difficulty 필터 전달 -> 400 VALIDATION_ERROR
    res_bad_diff = client.get("/api/v1/courses?difficulty=SUPER_HARD")
    assert res_bad_diff.status_code == 400
    assert res_bad_diff.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패 3] 유효하지 않은 price 필터 전달 -> 400 VALIDATION_ERROR
    res_bad_price = client.get("/api/v1/courses?price=CHEAP")
    assert res_bad_price.status_code == 400
    assert res_bad_price.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패 4] 유효하지 않은 sort 값 전달 -> 400 VALIDATION_ERROR
    res_bad_sort = client.get("/api/v1/courses?sort=UNKNOWN_SORT")
    assert res_bad_sort.status_code == 400
    assert res_bad_sort.json()["error"]["code"] == "VALIDATION_ERROR"
