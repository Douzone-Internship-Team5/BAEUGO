from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.timezone import today_in_seoul
from app.models import (
    AttendanceDay,
    CourseDailyStat,
    LearningGoal,
    QuestionRecord,
    QuizAttempt,
    Role,
    UserRole,
    VideoProgress,
)
from tests.conftest import auth_header
from tests.factories import enroll, make_course, make_quiz, make_section, make_video


def _build_full_course(
    db: Session, instructor, *, price_krw: int = 29000, status: str = "PUBLISHED"
):
    course = make_course(
        db,
        instructor,
        title="일잘러의 보고서 작성법",
        status=status,
        category_id=1,
        topic_id=11,
        price_krw=price_krw,
    )
    course.thumbnail_storage_key = f"courses/{course.id}/thumb.jpg"

    ot = make_video(
        db,
        course,
        section=None,
        video_type="OT",
        title="무료 OT - 보고서 작성 핵심",
        sort_order=0,
        duration_seconds=45,
        scripts=("안녕하세요, 오리엔테이션입니다.",),
    )
    ot.thumbnail_storage_key = f"courses/{course.id}/ot_thumb.jpg"

    sec1 = make_section(db, course, title="섹션 1. 기본 개념 및 구조화", sort_order=1)
    v1 = make_video(
        db,
        course,
        section=sec1,
        video_type="LESSON",
        title="1강. 목적과 해결할 문제 정의",
        sort_order=1,
        duration_seconds=360,
        scripts=("1강 첫 번째 자막", "1강 두 번째 자막"),
    )
    v2 = make_video(
        db,
        course,
        section=sec1,
        video_type="LESSON",
        title="2강. 데이터 정리하기",
        sort_order=2,
        duration_seconds=240,
        scripts=("이번 시간에는 데이터를 정리합니다.",),
    )
    uq1 = make_quiz(db, course, section=sec1, question_count=3)

    sec2 = make_section(db, course, title="섹션 2. 실전 작성", sort_order=2)
    v3 = make_video(
        db,
        course,
        section=sec2,
        video_type="LESSON",
        title="3강. 결론 도출",
        sort_order=1,
        duration_seconds=540,
    )
    uq2 = make_quiz(db, course, section=sec2, question_count=3)

    fq = make_quiz(db, course, section=None, question_count=3)
    db.flush()
    return course, ot, sec1, v1, v2, uq1, sec2, v3, uq2, fq


# ==============================================================================
# [C-1] GET /api/v1/courses/{id} — 강좌 상세 · 학습 현황 조회
# ==============================================================================
def test_c1_course_detail_anonymous_and_unenrolled(client: TestClient, db: Session, get_user):
    instructor = get_user("instructor1@baeugo.test")
    course, ot, sec1, v1, v2, uq1, sec2, v3, uq2, fq = _build_full_course(
        db, instructor, price_krw=29000
    )

    # 비활성 섹션/영상/퀴즈 추가 (응답 및 집계에서 제외되어야 함)
    inactive_sec = make_section(db, course, title="비활성 섹션", sort_order=3, is_active=False)
    make_video(db, course, section=inactive_sec, video_type="LESSON", duration_seconds=999)
    make_video(
        db,
        course,
        section=sec1,
        video_type="LESSON",
        title="비활성 영상",
        duration_seconds=888,
        is_active=False,
    )
    make_quiz(db, course, section=sec1, question_count=3, is_active=False)
    db.commit()

    # 1. 비로그인 조회 (유료 강좌 -> actionButton: PURCHASE, 전 항목 isLocked: true)
    res = client.get(f"/api/v1/courses/{course.id}")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["courseId"] == course.id
    assert data["status"] == "PUBLISHED"
    assert data["category"] == {"id": 1, "code": "PLANNING_PM", "name": "기획·PM"}
    assert data["topic"] == {"id": 11, "name": "문서 작성"}
    assert data["priceKrw"] == 29000
    assert data["isFree"] is False
    assert data["thumbnailUrl"] == f"/api/v1/media/courses/{course.id}/thumb.jpg"
    assert data["instructor"] == {
        "id": instructor.id,
        "name": instructor.name,
        "bio": "10년 차 사무 자동화 실무자",
    }
    # 360 + 240 + 540 = 1140초, 활성 LESSON 3개
    assert data["totalLessonDurationSeconds"] == 1140
    assert data["totalLessonCount"] == 3
    assert data["estimatedCompletionDays"] == 1
    assert data["otVideo"]["videoId"] == ot.id
    assert data["otVideo"]["videoUrl"] == f"/api/v1/videos/{ot.id}/stream"
    assert data["enrollment"] == {
        "isEnrolled": False,
        "enrollmentId": None,
        "status": None,
        "progressPercent": 0,
        "actionButton": "PURCHASE",
    }
    assert len(data["sections"]) == 2
    for s in data["sections"]:
        for v in s["videos"]:
            assert v["isCompleted"] is False
            assert v["isLocked"] is True
        assert s["unitQuiz"]["isCompleted"] is False
        assert s["unitQuiz"]["isLocked"] is True
    assert data["finalQuiz"] == {"quizId": fq.id, "isCompleted": False, "isLocked": True}

    # 2. 무료 강좌 미수강 조회 -> actionButton: START_FREE
    free_course = make_course(db, instructor, price_krw=0, status="PUBLISHED")
    db.commit()
    res_free = client.get(f"/api/v1/courses/{free_course.id}")
    assert res_free.status_code == 200
    assert res_free.json()["data"]["isFree"] is True
    assert res_free.json()["data"]["enrollment"]["actionButton"] == "START_FREE"


def test_c1_course_detail_enrolled_curriculum_lock_and_goal_days(
    client: TestClient, db: Session, get_user
):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")
    course, ot, sec1, v1, v2, uq1, sec2, v3, uq2, fq = _build_full_course(db, instructor)

    # learner2의 하루 목표 시간을 10분(600초)으로 변경 -> 1140초 / 600초 = ceil(1.9) = 2일
    goal = db.get(LearningGoal, learner.id)
    goal.daily_target_minutes = 10

    enr = enroll(db, learner, course)
    # v1 완료 처리 (총 6개 항목 중 1개 완료 -> round(1/6*100) = 17%)
    db.add(
        VideoProgress(
            enrollment_id=enr.id,
            video_id=v1.id,
            watched_ranges=[[0, 360]],
            watched_seconds=360,
            last_position_seconds=360,
            is_completed=True,
            completed_at=datetime.now(UTC),
        )
    )
    db.commit()

    res = client.get(f"/api/v1/courses/{course.id}", headers=auth_header(learner.id))
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["estimatedCompletionDays"] == 2
    assert data["enrollment"] == {
        "isEnrolled": True,
        "enrollmentId": enr.id,
        "status": "IN_PROGRESS",
        "progressPercent": 17,
        "actionButton": "CONTINUE",
    }

    sec1_data = data["sections"][0]
    # v1(완료) -> isLocked: False, v2(첫 미완료) -> isLocked: False, uq1(그 다음) -> isLocked: True
    assert sec1_data["videos"][0]["isCompleted"] is True
    assert sec1_data["videos"][0]["isLocked"] is False
    assert sec1_data["videos"][1]["isCompleted"] is False
    assert sec1_data["videos"][1]["isLocked"] is False
    assert sec1_data["unitQuiz"]["isCompleted"] is False
    assert sec1_data["unitQuiz"]["isLocked"] is True
    assert data["sections"][1]["videos"][0]["isLocked"] is True
    assert data["finalQuiz"]["isLocked"] is True


def test_c1_access_control_by_course_status(client: TestClient, db: Session, get_user):
    instructor = get_user("instructor1@baeugo.test")
    other_instructor = get_user("instructor2@baeugo.test")
    learner = get_user("learner2@baeugo.test")
    unenrolled_learner = get_user("learner1@baeugo.test")
    operator = get_user("admin@baeugo.test")

    # 존재하지 않는 강좌 -> 404
    res_404 = client.get("/api/v1/courses/999999")
    assert res_404.status_code == 404
    assert res_404.json()["error"]["code"] == "NOT_FOUND"

    # DRAFT 강좌 -> 403
    draft_course = make_course(db, instructor, status="DRAFT")
    db.commit()
    res_draft = client.get(f"/api/v1/courses/{draft_course.id}", headers=auth_header(learner.id))
    assert res_draft.status_code == 403
    assert res_draft.json()["error"]["message"] == "현재 비공개 상태인 강좌입니다."

    # EDITING 및 OPERATOR_HIDDEN 강좌: 기수강생, 소유 강사, 운영자만 200, 그 외 403
    for hidden_status in ("EDITING", "OPERATOR_HIDDEN"):
        c = make_course(db, instructor, status=hidden_status)
        enroll(db, learner, c)
        db.commit()

        # 미수강자, 비로그인, 타 강사 -> 403
        assert client.get(f"/api/v1/courses/{c.id}").status_code == 403
        assert (
            client.get(
                f"/api/v1/courses/{c.id}", headers=auth_header(unenrolled_learner.id)
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/courses/{c.id}", headers=auth_header(other_instructor.id)
            ).status_code
            == 403
        )

        # 기수강생, 소유 강사, 운영자 -> 200
        assert (
            client.get(f"/api/v1/courses/{c.id}", headers=auth_header(learner.id)).status_code
            == 200
        )
        assert (
            client.get(f"/api/v1/courses/{c.id}", headers=auth_header(instructor.id)).status_code
            == 200
        )
        assert (
            client.get(f"/api/v1/courses/{c.id}", headers=auth_header(operator.id)).status_code
            == 200
        )


# ==============================================================================
# [C-2] POST /api/v1/courses/{id}/enroll — 수강 등록
# ==============================================================================
def test_c2_enroll_success_and_daily_stats_sync(client: TestClient, db: Session, get_user):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")
    course, ot, sec1, v1, v2, uq1, sec2, v3, uq2, fq = _build_full_course(
        db, instructor, price_krw=35000
    )
    db.commit()

    res = client.post(
        f"/api/v1/courses/{course.id}/enroll",
        json={"accessSource": "DEMO_GRANT"},
        headers=auth_header(learner.id),
    )
    assert res.status_code == 201
    data = res.json()["data"]
    assert data["learnerId"] == learner.id
    assert data["courseId"] == course.id
    assert data["accessSource"] == "DEMO_GRANT"
    assert data["priceSnapshotKrw"] == 35000
    assert data["status"] == "IN_PROGRESS"
    assert data["firstVideoId"] == v1.id

    # 당일 course_daily_stats 동기화 확인 (활성 퀴즈: uq1, uq2, fq 총 3개)
    stat = db.get(CourseDailyStat, (course.id, today_in_seoul()))
    assert stat is not None
    assert stat.new_students == 1
    assert stat.expected_revenue == 35000
    assert stat.available_quiz_cnt == 3


def test_c2_enroll_errors_and_permissions(client: TestClient, db: Session, get_user):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")
    no_role_user = get_user("learner1@baeugo.test")
    operator = get_user("admin@baeugo.test")

    course = make_course(db, instructor, status="PUBLISHED", price_krw=0)
    draft_course = make_course(db, instructor, status="DRAFT")
    editing_course = make_course(db, instructor, status="EDITING")
    hidden_course = make_course(db, instructor, status="OPERATOR_HIDDEN")
    db.commit()

    # 1. 비인증 -> 401
    assert client.post(f"/api/v1/courses/{course.id}/enroll").status_code == 401

    # 2. 강사 / 운영자 / 역할 없는 회원 -> 403
    for u in (instructor, operator, no_role_user):
        res_403 = client.post(f"/api/v1/courses/{course.id}/enroll", headers=auth_header(u.id))
        assert res_403.status_code == 403
        assert res_403.json()["error"]["code"] == "FORBIDDEN"
        assert (
            res_403.json()["error"]["message"]
            == "수강자 모드에서만 강좌를 수강 신청할 수 있습니다."
        )

    # 3. 존재하지 않는 강좌 -> 404
    res_404 = client.post("/api/v1/courses/999999/enroll", headers=auth_header(learner.id))
    assert res_404.status_code == 404

    # 4. 잘못된 accessSource -> 400
    res_400 = client.post(
        f"/api/v1/courses/{course.id}/enroll",
        json={"accessSource": "INVALID"},
        headers=auth_header(learner.id),
    )
    assert res_400.status_code == 400

    # 5. body 생략 시 기본값 DEMO_GRANT로 성공 (201)
    res_ok = client.post(f"/api/v1/courses/{course.id}/enroll", headers=auth_header(learner.id))
    assert res_ok.status_code == 201
    assert res_ok.json()["data"]["accessSource"] == "DEMO_GRANT"
    assert res_ok.json()["data"]["firstVideoId"] is None

    # 6. 중복 수강 신청 -> 409 CONFLICT
    res_dup = client.post(f"/api/v1/courses/{course.id}/enroll", headers=auth_header(learner.id))
    assert res_dup.status_code == 409
    assert res_dup.json()["error"]["code"] == "CONFLICT"

    # 7. 비공개/작성중/수정중 강좌 수강 신청 -> 409 CONFLICT
    for non_pub in (draft_course, editing_course, hidden_course):
        res_non_pub = client.post(
            f"/api/v1/courses/{non_pub.id}/enroll", headers=auth_header(learner.id)
        )
        assert res_non_pub.status_code == 409
        assert res_non_pub.json()["error"]["code"] == "CONFLICT"


# ==============================================================================
# [C-3] GET /api/v1/me/enrollments — 내 강좌 목록 조회
# ==============================================================================
def test_c3_list_my_enrollments(client: TestClient, db: Session, get_user):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")

    c1, ot, sec1, v1, v2, uq1, sec2, v3, uq2, fq = _build_full_course(db, instructor)
    c2 = make_course(db, instructor, title="완료한 강좌", status="PUBLISHED")
    s2 = make_section(db, c2)
    c2_v1 = make_video(db, c2, section=s2, video_type="LESSON")

    enr1 = enroll(db, learner, c1, status="IN_PROGRESS")
    enr2 = enroll(db, learner, c2, status="COMPLETED")

    # c1의 영상(3개)·퀴즈(3개) 완료 + 오답 2개 생성 -> progressPercent=100, unresolvedWrongCount=2
    for v in (v1, v2, v3):
        db.add(
            VideoProgress(
                enrollment_id=enr1.id,
                video_id=v.id,
                watched_ranges=[[0, v.duration_seconds]],
                watched_seconds=v.duration_seconds,
                last_position_seconds=v.duration_seconds,
                is_completed=True,
                completed_at=datetime.now(UTC),
            )
        )
    for q in (uq1, uq2, fq):
        attempt = QuizAttempt(
            enrollment_id=enr1.id,
            quiz_id=q.id,
            attempt_type=q.quiz_type,
            attempt_no=1,
            status="SUBMITTED",
            submitted_at=datetime.now(UTC),
        )
        db.add(attempt)
    db.flush()

    from app.models.quiz import AttemptQuestion, QuestionAnswer

    # uq1의 문항 2개를 REVIEW_NEEDED로 기록
    for idx, question in enumerate(uq1.questions[:2], start=1):
        aq = AttemptQuestion(
            attempt_id=attempt.id,
            question_id=question.id,
            order_no=idx,
            origin="BASE",
        )
        db.add(aq)
        db.flush()
        ans = QuestionAnswer(
            attempt_question_id=aq.id,
            selected_choice_no=2,
            is_correct=False,
        )
        db.add(ans)
        db.flush()
        db.add(
            QuestionRecord(
                enrollment_id=enr1.id,
                question_id=question.id,
                status="REVIEW_NEEDED",
                last_answer_id=ans.id,
                wrong_count=1,
            )
        )

    db.add(
        VideoProgress(
            enrollment_id=enr2.id,
            video_id=c2_v1.id,
            watched_ranges=[[0, 360]],
            watched_seconds=360,
            last_position_seconds=360,
            is_completed=True,
            completed_at=datetime.now(UTC),
        )
    )
    db.commit()

    # 1. 전체 조회 (status 생략 -> ALL)
    res_all = client.get("/api/v1/me/enrollments", headers=auth_header(learner.id))
    assert res_all.status_code == 200
    data_all = res_all.json()["data"]
    assert data_all["counts"] == {"all": 2, "inProgress": 1, "completed": 1}
    assert len(data_all["enrollments"]) == 2

    item1 = next(e for e in data_all["enrollments"] if e["enrollmentId"] == enr1.id)
    assert item1["progressPercent"] == 100
    assert item1["unresolvedWrongCount"] == 2
    assert item1["statusMessage"] == "오답 2개 남았어요"
    assert item1["participatedQuizCount"] == 3

    # 2. IN_PROGRESS 필터 조회 (counts는 전체 합계 유지)
    res_ip = client.get(
        "/api/v1/me/enrollments?status=IN_PROGRESS", headers=auth_header(learner.id)
    )
    assert res_ip.status_code == 200
    assert res_ip.json()["data"]["counts"] == {"all": 2, "inProgress": 1, "completed": 1}
    assert len(res_ip.json()["data"]["enrollments"]) == 1
    assert res_ip.json()["data"]["enrollments"][0]["enrollmentId"] == enr1.id

    # 3. COMPLETED 필터 조회
    res_comp = client.get(
        "/api/v1/me/enrollments?status=COMPLETED", headers=auth_header(learner.id)
    )
    assert res_comp.status_code == 200
    assert len(res_comp.json()["data"]["enrollments"]) == 1
    assert res_comp.json()["data"]["enrollments"][0]["enrollmentId"] == enr2.id
    assert res_comp.json()["data"]["enrollments"][0]["statusMessage"] == "수료 완료"

    # 4. 잘못된 status 필터 -> 400
    res_bad = client.get("/api/v1/me/enrollments?status=INVALID", headers=auth_header(learner.id))
    assert res_bad.status_code == 400
    assert res_bad.json()["error"]["code"] == "VALIDATION_ERROR"

    # 5. 권한 검증 (비인증 401, 강사 403)
    assert client.get("/api/v1/me/enrollments").status_code == 401
    assert (
        client.get("/api/v1/me/enrollments", headers=auth_header(instructor.id)).status_code == 403
    )


# ==============================================================================
# [C-4] GET /api/v1/enrollments/{id}/next — 이어가기 조회
# ==============================================================================
def test_c4_enrollment_next_progression_all_stages(client: TestClient, db: Session, get_user):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")
    course, ot, sec1, v1, v2, uq1, sec2, v3, uq2, fq = _build_full_course(db, instructor)
    enr = enroll(db, learner, course)
    db.commit()

    # Stage 1: 아무것도 완료 안 함 -> 첫 영상 v1 (lastPositionSeconds: 0)
    res1 = client.get(f"/api/v1/enrollments/{enr.id}/next", headers=auth_header(learner.id))
    assert res1.status_code == 200
    assert res1.json()["data"] == {
        "enrollmentId": enr.id,
        "courseId": course.id,
        "nextItemType": "VIDEO",
        "sectionId": sec1.id,
        "videoId": v1.id,
        "quizId": None,
        "lastPositionSeconds": 0,
    }

    # Stage 2: v1 완료 + v2는 45초까지 시청 중 -> v2 (lastPositionSeconds: 45)
    db.add_all(
        [
            VideoProgress(
                enrollment_id=enr.id,
                video_id=v1.id,
                watched_seconds=360,
                last_position_seconds=360,
                is_completed=True,
            ),
            VideoProgress(
                enrollment_id=enr.id,
                video_id=v2.id,
                watched_seconds=45,
                last_position_seconds=45,
                is_completed=False,
            ),
        ]
    )
    db.commit()

    res2 = client.get(f"/api/v1/enrollments/{enr.id}/next", headers=auth_header(learner.id))
    assert res2.status_code == 200
    assert res2.json()["data"] == {
        "enrollmentId": enr.id,
        "courseId": course.id,
        "nextItemType": "VIDEO",
        "sectionId": sec1.id,
        "videoId": v2.id,
        "quizId": None,
        "lastPositionSeconds": 45,
    }

    # Stage 3: v2 완료 -> 섹션 1 단위 퀴즈 uq1 (UNIT_QUIZ)
    vp2 = db.get(VideoProgress, (enr.id, v2.id))
    vp2.is_completed = True
    db.commit()

    res3 = client.get(f"/api/v1/enrollments/{enr.id}/next", headers=auth_header(learner.id))
    assert res3.status_code == 200
    assert res3.json()["data"] == {
        "enrollmentId": enr.id,
        "courseId": course.id,
        "nextItemType": "UNIT_QUIZ",
        "sectionId": sec1.id,
        "videoId": None,
        "quizId": uq1.id,
        "lastPositionSeconds": None,
    }

    # Stage 4: uq1 제출 완료 -> 섹션 2의 첫 영상 v3
    db.add(
        QuizAttempt(
            enrollment_id=enr.id,
            quiz_id=uq1.id,
            attempt_type="UNIT",
            status="SUBMITTED",
        )
    )
    db.commit()

    res4 = client.get(f"/api/v1/enrollments/{enr.id}/next", headers=auth_header(learner.id))
    assert res4.status_code == 200
    assert res4.json()["data"]["nextItemType"] == "VIDEO"
    assert res4.json()["data"]["videoId"] == v3.id
    assert res4.json()["data"]["sectionId"] == sec2.id

    # Stage 5: v3 및 uq2 완료 -> 종합 퀴즈 fq (FINAL_QUIZ)
    db.add(
        VideoProgress(
            enrollment_id=enr.id,
            video_id=v3.id,
            watched_seconds=540,
            last_position_seconds=540,
            is_completed=True,
        )
    )
    db.add(
        QuizAttempt(
            enrollment_id=enr.id,
            quiz_id=uq2.id,
            attempt_type="UNIT",
            status="SUBMITTED",
        )
    )
    db.commit()

    res5 = client.get(f"/api/v1/enrollments/{enr.id}/next", headers=auth_header(learner.id))
    assert res5.status_code == 200
    assert res5.json()["data"] == {
        "enrollmentId": enr.id,
        "courseId": course.id,
        "nextItemType": "FINAL_QUIZ",
        "sectionId": None,
        "videoId": None,
        "quizId": fq.id,
        "lastPositionSeconds": None,
    }

    # Stage 6: fq 제출 완료되었으나 미해결 오답(REVIEW_NEEDED) 존재 -> WRONG_REVIEW
    fq_attempt = QuizAttempt(
        enrollment_id=enr.id,
        quiz_id=fq.id,
        attempt_type="FINAL",
        status="SUBMITTED",
    )
    db.add(fq_attempt)
    db.flush()

    from app.models.quiz import AttemptQuestion, QuestionAnswer

    aq = AttemptQuestion(
        attempt_id=fq_attempt.id,
        question_id=fq.questions[0].id,
        order_no=1,
        origin="BASE",
    )
    db.add(aq)
    db.flush()
    ans = QuestionAnswer(attempt_question_id=aq.id, selected_choice_no=2, is_correct=False)
    db.add(ans)
    db.flush()
    q_rec = QuestionRecord(
        enrollment_id=enr.id,
        question_id=fq.questions[0].id,
        status="REVIEW_NEEDED",
        last_answer_id=ans.id,
        wrong_count=1,
    )
    db.add(q_rec)
    db.commit()

    res6 = client.get(f"/api/v1/enrollments/{enr.id}/next", headers=auth_header(learner.id))
    assert res6.status_code == 200
    assert res6.json()["data"]["nextItemType"] == "WRONG_REVIEW"

    # Stage 7: 수료 완료(COMPLETED) 시 복습을 위해 첫 번째 활성 학습 영상(v1) 반환
    q_rec.status = "CORRECT"
    enr.status = "COMPLETED"
    db.commit()

    res7 = client.get(f"/api/v1/enrollments/{enr.id}/next", headers=auth_header(learner.id))
    assert res7.status_code == 200
    assert res7.json()["data"]["nextItemType"] == "VIDEO"
    assert res7.json()["data"]["videoId"] == v1.id


def test_c4_enrollment_next_errors(client: TestClient, db: Session, get_user):
    instructor = get_user("instructor1@baeugo.test")
    learner2 = get_user("learner2@baeugo.test")
    learner1 = get_user("learner1@baeugo.test")
    # learner1에게 LEARNER 역할 부여
    db.add(UserRole(user_id=learner1.id, role=Role.LEARNER))
    learner1.active_mode = Role.LEARNER

    course = make_course(db, instructor)
    enr2 = enroll(db, learner2, course)
    db.commit()

    # 타인의 수강 내역 조회 -> 403 FORBIDDEN ("본인의 수강 내역만 조회할 수 있습니다.")
    res_other = client.get(f"/api/v1/enrollments/{enr2.id}/next", headers=auth_header(learner1.id))
    assert res_other.status_code == 403
    assert res_other.json()["error"]["message"] == "본인의 수강 내역만 조회할 수 있습니다."

    # 존재하지 않는 수강 내역 조회 -> 404 NOT_FOUND
    res_404 = client.get("/api/v1/enrollments/999999/next", headers=auth_header(learner2.id))
    assert res_404.status_code == 404
    assert res_404.json()["error"]["message"] == "존재하지 않는 수강 내역입니다."

    # 강사 모드로 조회 시도 -> 403 FORBIDDEN
    res_inst = client.get(f"/api/v1/enrollments/{enr2.id}/next", headers=auth_header(instructor.id))
    assert res_inst.status_code == 403


# ==============================================================================
# [C-5] GET /api/v1/videos/{id} — 영상 정보 및 잠금 확인
# ==============================================================================
def test_c5_video_detail_and_lock_exemptions(client: TestClient, db: Session, get_user):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")
    operator = get_user("admin@baeugo.test")
    course, ot, sec1, v1, v2, uq1, sec2, v3, uq2, fq = _build_full_course(db, instructor)
    db.commit()

    # 1. 무료 OT 영상은 비로그인으로 조회 가능
    res_ot = client.get(f"/api/v1/videos/{ot.id}")
    assert res_ot.status_code == 200
    ot_data = res_ot.json()["data"]
    assert ot_data["videoId"] == ot.id
    assert ot_data["videoType"] == "OT"
    assert ot_data["sectionId"] is None
    assert len(ot_data["segments"]) == 1
    assert ot_data["navigation"]["prevVideoId"] is None
    assert ot_data["navigation"]["nextItemType"] == "VIDEO"
    assert ot_data["navigation"]["nextItemId"] == v1.id
    assert ot_data["navigation"]["isNextUnlocked"] is False

    # 2. LESSON 영상 비로그인 조회 -> 401 UNAUTHORIZED
    res_unauth = client.get(f"/api/v1/videos/{v1.id}")
    assert res_unauth.status_code == 401

    # 3. LESSON 영상 미수강자 조회 -> 403 FORBIDDEN
    res_unenrolled = client.get(f"/api/v1/videos/{v1.id}", headers=auth_header(learner.id))
    assert res_unenrolled.status_code == 403
    assert (
        res_unenrolled.json()["error"]["message"]
        == "수강 등록이 되어 있지 않거나 이전 학습 단계를 먼저 완료해야 합니다."
    )

    # 4. 수강 등록 후 첫 영상(v1)은 열림, 두 번째 영상(v2)은 잠김(403)
    enr = enroll(db, learner, course)
    db.commit()

    res_v1 = client.get(f"/api/v1/videos/{v1.id}", headers=auth_header(learner.id))
    assert res_v1.status_code == 200
    assert res_v1.json()["data"]["navigation"] == {
        "prevVideoId": None,
        "nextItemType": "VIDEO",
        "nextItemId": v2.id,
        "isNextUnlocked": False,
    }

    res_v2_locked = client.get(f"/api/v1/videos/{v2.id}", headers=auth_header(learner.id))
    assert res_v2_locked.status_code == 403

    # 5. 예외 규칙 ①: v1이 미완료여도 v3 자체가 이미 is_completed=True이면 v3 열람 허용
    db.add(
        VideoProgress(
            enrollment_id=enr.id,
            video_id=v3.id,
            watched_seconds=540,
            last_position_seconds=540,
            is_completed=True,
        )
    )
    db.commit()
    res_v3_completed = client.get(f"/api/v1/videos/{v3.id}", headers=auth_header(learner.id))
    assert res_v3_completed.status_code == 200
    assert res_v3_completed.json()["data"]["progress"]["isCompleted"] is True

    # 6. 예외 규칙 ②: 최초 수료 이력(first_completed_at IS NOT NULL) 보유 시 v2 열람 허용
    enr.first_completed_at = datetime.now(UTC)
    db.commit()
    res_v2_first_comp = client.get(f"/api/v1/videos/{v2.id}", headers=auth_header(learner.id))
    assert res_v2_first_comp.status_code == 200
    assert res_v2_first_comp.json()["data"]["navigation"] == {
        "prevVideoId": v1.id,
        "nextItemType": "UNIT_QUIZ",
        "nextItemId": uq1.id,
        "isNextUnlocked": True,
    }

    # 7. 예외 규칙 ③: 강좌 소유 강사 및 운영자는 수강 등록/선행 학습 없이도 LESSON 열람 가능
    assert (
        client.get(f"/api/v1/videos/{v2.id}", headers=auth_header(instructor.id)).status_code == 200
    )
    assert (
        client.get(f"/api/v1/videos/{v2.id}", headers=auth_header(operator.id)).status_code == 200
    )

    # 8. 비활성 영상 또는 존재하지 않는 영상 -> 404
    inactive_v = make_video(db, course, section=sec1, is_active=False)
    db.commit()
    assert (
        client.get(f"/api/v1/videos/{inactive_v.id}", headers=auth_header(learner.id)).status_code
        == 404
    )
    assert client.get("/api/v1/videos/999999", headers=auth_header(learner.id)).status_code == 404


# ==============================================================================
# [C-6] POST /api/v1/videos/{id}/views — 영상 조회수 집계 (3초 · 하루 1회)
# ==============================================================================
def test_c6_video_views_deduplication_and_stats(client: TestClient, db: Session, get_user):
    instructor = get_user("instructor1@baeugo.test")
    learner2 = get_user("learner2@baeugo.test")
    learner1 = get_user("learner1@baeugo.test")
    course, ot, sec1, v1, v2, uq1, sec2, v3, uq2, fq = _build_full_course(db, instructor)
    db.commit()

    # 1. 3초 미만 재생 시 400 VALIDATION_ERROR
    res_short = client.post(
        f"/api/v1/videos/{v1.id}/views",
        json={"playedSeconds": 2},
        headers=auth_header(learner2.id),
    )
    assert res_short.status_code == 400
    assert (
        res_short.json()["error"]["message"]
        == "실제 재생 시간이 3초 이상이어야 조회수가 집계됩니다."
    )

    # 2. LESSON 영상 3초 이상 최초 조회 -> isIncremented: True, viewCount=1, lesson_views=1
    res_first = client.post(
        f"/api/v1/videos/{v1.id}/views",
        json={"playedSeconds": 3},
        headers=auth_header(learner2.id),
    )
    assert res_first.status_code == 200
    assert res_first.json()["data"] == {
        "videoId": v1.id,
        "viewDate": today_in_seoul().isoformat(),
        "isIncremented": True,
        "viewCount": 1,
    }
    stat = db.get(CourseDailyStat, (course.id, today_in_seoul()))
    assert stat is not None
    assert stat.lesson_views == 1

    # 3. 같은 날 동일 유저 재호출 -> isIncremented: False, viewCount=1 유지
    res_dup = client.post(
        f"/api/v1/videos/{v1.id}/views",
        json={"playedSeconds": 30},
        headers=auth_header(learner2.id),
    )
    assert res_dup.status_code == 200
    assert res_dup.json()["data"]["isIncremented"] is False
    assert res_dup.json()["data"]["viewCount"] == 1
    db.refresh(stat)
    assert stat.lesson_views == 1

    # 4. 다른 유저가 조회 -> isIncremented: True, viewCount=2, lesson_views=2
    res_other = client.post(
        f"/api/v1/videos/{v1.id}/views",
        json={"playedSeconds": 5},
        headers=auth_header(learner1.id),
    )
    assert res_other.status_code == 200
    assert res_other.json()["data"]["isIncremented"] is True
    assert res_other.json()["data"]["viewCount"] == 2
    db.refresh(stat)
    assert stat.lesson_views == 2

    # 5. OT 영상 조회 -> viewCount는 증가하나 course_daily_stats.lesson_views는 증가하지 않음
    res_ot = client.post(
        f"/api/v1/videos/{ot.id}/views",
        json={"playedSeconds": 10},
        headers=auth_header(learner2.id),
    )
    assert res_ot.status_code == 200
    assert res_ot.json()["data"]["isIncremented"] is True
    assert res_ot.json()["data"]["viewCount"] == 1
    db.refresh(stat)
    assert stat.lesson_views == 2

    # 6. 존재하지 않는 영상 -> 404
    assert (
        client.post(
            "/api/v1/videos/999999/views",
            json={"playedSeconds": 5},
            headers=auth_header(learner2.id),
        ).status_code
        == 404
    )


# ==============================================================================
# [C-7] PUT · POST /api/v1/videos/{id}/progress — 학습 진도 · 시청 시간 저장
# ==============================================================================
def test_c7_video_progress_merge_completion_and_attendance(
    client: TestClient, db: Session, get_user
):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")
    course, ot, sec1, v1, v2, uq1, sec2, v3, uq2, fq = _build_full_course(db, instructor)
    enr = enroll(db, learner, course)
    db.commit()

    # v2 길이: 240초 -> 90% 완료 기준: 216초
    # 1. PUT [0, 120] 시청 (50.0%, 미완료), activeSeconds=620 (목표 20분=1200초 미달성)
    res1 = client.put(
        f"/api/v1/videos/{v2.id}/progress",
        json={
            "lastPositionSeconds": 120,
            "watchedRange": [0, 120],
            "activitySession": {
                "sessionKey": "sess-v2-1",
                "startedAt": "2026-10-17T08:00:00+09:00",
                "endedAt": "2026-10-17T08:10:20+09:00",
                "activeSeconds": 620,
            },
        },
        headers=auth_header(learner.id),
    )
    assert res1.status_code == 200
    d1 = res1.json()["data"]
    assert d1["enrollmentId"] == enr.id
    assert d1["videoId"] == v2.id
    assert d1["watchedSeconds"] == 120
    assert d1["durationSeconds"] == 240
    assert d1["watchedRatioPercent"] == 50.0
    assert d1["lastPositionSeconds"] == 120
    assert d1["isCompleted"] is False
    assert d1["completedAt"] is None
    assert d1["remainingSecondsForCompletion"] == 96
    assert d1["todayActiveSeconds"] == 620
    assert d1["todayIsAttended"] is False

    # 2. POST (sendBeacon) [100, 220] 시청 -> 중복 병합 [0, 220] = 220초 (91.7% >= 90% 완료!)
    # 추가 activeSeconds=600 -> 총 1220초 >= 1200초 -> todayIsAttended: True!
    res2 = client.post(
        f"/api/v1/videos/{v2.id}/progress",
        json={
            "lastPositionSeconds": 220,
            "watchedRange": [100, 220],
            "activitySession": {
                "sessionKey": "sess-v2-2",
                "startedAt": "2026-10-17T08:10:20+09:00",
                "endedAt": "2026-10-17T08:20:20+09:00",
                "activeSeconds": 600,
            },
        },
        headers=auth_header(learner.id),
    )
    assert res2.status_code == 200
    d2 = res2.json()["data"]
    assert d2["watchedSeconds"] == 220
    assert d2["watchedRatioPercent"] == 91.7
    assert d2["isCompleted"] is True
    assert d2["completedAt"] is not None
    assert d2["remainingSecondsForCompletion"] == 0
    assert d2["todayActiveSeconds"] == 1220
    assert d2["todayIsAttended"] is True

    # 3. 동일한 sessionKey로 재전송 시 멱등성 보장 (todayActiveSeconds 중복 합산 방지)
    res_retry = client.post(
        f"/api/v1/videos/{v2.id}/progress",
        json={
            "lastPositionSeconds": 220,
            "watchedRange": [100, 220],
            "activitySession": {
                "sessionKey": "sess-v2-2",
                "startedAt": "2026-10-17T08:10:20+09:00",
                "endedAt": "2026-10-17T08:20:20+09:00",
                "activeSeconds": 600,
            },
        },
        headers=auth_header(learner.id),
    )
    assert res_retry.status_code == 200
    assert res_retry.json()["data"]["todayActiveSeconds"] == 1220

    att = db.get(AttendanceDay, (learner.id, today_in_seoul()))
    assert att is not None
    assert att.active_seconds == 1220
    assert att.is_attended is True


@pytest.mark.parametrize(
    "bad_range,bad_pos",
    [
        ([100, 50], 50),
        ([50, 50], 50),
        ([-10, 50], 50),
        ([0, 999], 50),
        ([10], 10),
        ([0, 50], -1),
        ([0, 50], 999),
    ],
)
def test_c7_video_progress_validation_and_forbidden(
    client: TestClient, db: Session, get_user, bad_range, bad_pos
):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")
    course, ot, sec1, v1, v2, uq1, sec2, v3, uq2, fq = _build_full_course(db, instructor)
    enroll(db, learner, course)
    db.commit()

    res = client.put(
        f"/api/v1/videos/{v2.id}/progress",
        json={
            "lastPositionSeconds": bad_pos,
            "watchedRange": bad_range,
            "activitySession": {
                "sessionKey": "sess-bad",
                "startedAt": "2026-10-17T08:00:00+09:00",
                "endedAt": "2026-10-17T08:01:00+09:00",
                "activeSeconds": 10,
            },
        },
        headers=auth_header(learner.id),
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_c7_ot_rejected_and_unenrolled_forbidden(client: TestClient, db: Session, get_user):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")
    course, ot, sec1, v1, v2, uq1, sec2, v3, uq2, fq = _build_full_course(db, instructor)
    db.commit()

    valid_payload = {
        "lastPositionSeconds": 10,
        "watchedRange": [0, 10],
        "activitySession": {
            "sessionKey": "sess-1",
            "startedAt": "2026-10-17T08:00:00+09:00",
            "endedAt": "2026-10-17T08:01:00+09:00",
            "activeSeconds": 10,
        },
    }

    # 1. 무료 OT 영상 진도 저장 시도 -> 400 VALIDATION_ERROR
    res_ot = client.put(
        f"/api/v1/videos/{ot.id}/progress",
        json=valid_payload,
        headers=auth_header(learner.id),
    )
    assert res_ot.status_code == 400
    assert (
        res_ot.json()["error"]["message"]
        == "무료 OT 영상은 진도 저장 대상이 아니거나 시청 구간 값이 올바르지 않습니다."
    )

    # 2. 미수강 강좌의 LESSON 영상 진도 저장 시도 -> 403 FORBIDDEN
    res_unenrolled = client.put(
        f"/api/v1/videos/{v1.id}/progress",
        json=valid_payload,
        headers=auth_header(learner.id),
    )
    assert res_unenrolled.status_code == 403
    assert (
        res_unenrolled.json()["error"]["message"]
        == "수강 중인 강좌의 영상만 진도를 저장할 수 있습니다."
    )


# ==============================================================================
# [C-8] POST · DELETE /api/v1/videos/{id}/like & [C-9] GET /api/v1/me/likes
# ==============================================================================
def test_c8_and_c9_video_likes_and_list(client: TestClient, db: Session, get_user):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")
    course, ot, sec1, v1, v2, uq1, sec2, v3, uq2, fq = _build_full_course(db, instructor)
    db.commit()

    # 1. 미수강 상태에서 무료 OT 좋아요 등록 가능 (200 OK)
    res_like_ot = client.post(f"/api/v1/videos/{ot.id}/like", headers=auth_header(learner.id))
    assert res_like_ot.status_code == 200
    assert res_like_ot.json()["data"] == {
        "videoId": ot.id,
        "videoType": "OT",
        "isLiked": True,
        "likeCount": 1,
    }

    # 중복 POST 호출 시 likeCount 중복 증가 없음 (멱등)
    res_like_ot_dup = client.post(f"/api/v1/videos/{ot.id}/like", headers=auth_header(learner.id))
    assert res_like_ot_dup.status_code == 200
    assert res_like_ot_dup.json()["data"]["likeCount"] == 1

    # 2. 미수강 상태에서 유료/학습(LESSON) 영상 좋아요 등록 및 취소 -> 403 FORBIDDEN
    res_like_lesson_403 = client.post(
        f"/api/v1/videos/{v1.id}/like", headers=auth_header(learner.id)
    )
    assert res_like_lesson_403.status_code == 403
    assert (
        res_like_lesson_403.json()["error"]["message"]
        == "미수강 상태에서는 유료 학습 영상에 좋아요를 누를 수 없습니다."
    )

    res_unlike_lesson_403 = client.delete(
        f"/api/v1/videos/{v1.id}/like", headers=auth_header(learner.id)
    )
    assert res_unlike_lesson_403.status_code == 403

    # 3. 수강 등록 후 LESSON 영상 좋아요 등록 -> 200 OK
    enroll(db, learner, course)
    db.commit()

    res_like_v1 = client.post(f"/api/v1/videos/{v1.id}/like", headers=auth_header(learner.id))
    assert res_like_v1.status_code == 200
    assert res_like_v1.json()["data"] == {
        "videoId": v1.id,
        "videoType": "LESSON",
        "isLiked": True,
        "likeCount": 1,
    }

    # 4. [C-9] 내가 좋아요한 영상 목록 조회 (ALL / OT / LESSON)
    res_likes_all = client.get("/api/v1/me/likes", headers=auth_header(learner.id))
    assert res_likes_all.status_code == 200
    assert res_likes_all.json()["data"]["totalCount"] == 2
    assert {item["videoId"] for item in res_likes_all.json()["data"]["likedVideos"]} == {
        ot.id,
        v1.id,
    }

    res_likes_ot = client.get("/api/v1/me/likes?videoType=OT", headers=auth_header(learner.id))
    assert res_likes_ot.status_code == 200
    assert res_likes_ot.json()["data"]["totalCount"] == 1
    assert res_likes_ot.json()["data"]["likedVideos"][0]["videoId"] == ot.id

    res_likes_lesson = client.get(
        "/api/v1/me/likes?videoType=LESSON", headers=auth_header(learner.id)
    )
    assert res_likes_lesson.status_code == 200
    assert res_likes_lesson.json()["data"]["totalCount"] == 1
    assert res_likes_lesson.json()["data"]["likedVideos"][0]["videoId"] == v1.id

    # 잘못된 videoType 필터 -> 400 VALIDATION_ERROR
    res_bad_type = client.get("/api/v1/me/likes?videoType=SHORTS", headers=auth_header(learner.id))
    assert res_bad_type.status_code == 400

    # 5. 좋아요 취소(DELETE) 후 목록에서 제외 및 likeCount 감소 확인
    res_unlike_ot = client.delete(f"/api/v1/videos/{ot.id}/like", headers=auth_header(learner.id))
    assert res_unlike_ot.status_code == 200
    assert res_unlike_ot.json()["data"] == {
        "videoId": ot.id,
        "videoType": "OT",
        "isLiked": False,
        "likeCount": 0,
    }

    # 중복 DELETE 호출 시 음수로 내려가지 않음
    res_unlike_ot_dup = client.delete(
        f"/api/v1/videos/{ot.id}/like", headers=auth_header(learner.id)
    )
    assert res_unlike_ot_dup.status_code == 200
    assert res_unlike_ot_dup.json()["data"]["likeCount"] == 0

    res_after_unlike = client.get("/api/v1/me/likes", headers=auth_header(learner.id))
    assert res_after_unlike.json()["data"]["totalCount"] == 1

    # 6. 존재하지 않는 영상 좋아요 -> 404
    assert (
        client.post("/api/v1/videos/999999/like", headers=auth_header(learner.id)).status_code
        == 404
    )
    assert (
        client.delete("/api/v1/videos/999999/like", headers=auth_header(learner.id)).status_code
        == 404
    )


def test_c7_auto_course_completion_when_all_items_done(client: TestClient, db: Session, get_user):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")
    course, ot, sec1, v1, v2, uq1, sec2, v3, uq2, fq = _build_full_course(db, instructor)
    enrollment = enroll(db, learner, course)

    now = datetime.now(UTC)
    for vid in (v1, v2):
        db.add(
            VideoProgress(
                enrollment_id=enrollment.id,
                video_id=vid.id,
                watched_ranges=[[0, vid.duration_seconds]],
                watched_seconds=vid.duration_seconds,
                last_position_seconds=vid.duration_seconds,
                is_completed=True,
                completed_at=now,
            )
        )
    for idx, q in enumerate((uq1, uq2, fq), start=1):
        db.add(
            QuizAttempt(
                enrollment_id=enrollment.id,
                quiz_id=q.id,
                attempt_type=q.quiz_type,
                attempt_no=idx,
                status="SUBMITTED",
                started_at=now,
                submitted_at=now,
            )
        )
    db.commit()

    assert enrollment.status == "IN_PROGRESS"

    # 마지막 미완료 영상 v3(duration=540초)을 486초(90%) 시청하여 완료 달성
    res = client.post(
        f"/api/v1/videos/{v3.id}/progress",
        json={
            "lastPositionSeconds": 486,
            "watchedRange": [0, 486],
            "activitySession": {
                "sessionKey": "sess-auto-complete",
                "startedAt": "2026-04-09T12:00:00Z",
                "endedAt": "2026-04-09T12:08:06Z",
                "activeSeconds": 486,
            },
        },
        headers=auth_header(learner.id),
    )
    assert res.status_code == 200
    assert res.json()["data"]["isCompleted"] is True

    db.refresh(enrollment)
    assert enrollment.status == "COMPLETED"
    assert enrollment.first_completed_at is not None
    assert enrollment.current_completed_at is not None


def test_inactive_items_and_sections_filtering_across_endpoints(
    client: TestClient, db: Session, get_user
):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")
    course, ot, sec1, v1, v2, uq1, sec2, v3, uq2, fq = _build_full_course(db, instructor)
    enroll(db, learner, course)
    db.commit()

    # 먼저 v2와 v3에 좋아요를 눌러둔다
    assert (
        client.post(f"/api/v1/videos/{v2.id}/like", headers=auth_header(learner.id)).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/videos/{v3.id}/like", headers=auth_header(learner.id)).status_code
        == 200
    )

    # v2는 직접 비활성화(is_active=False), sec2는 섹션 비활성화(v3 접근 불가)
    v2.is_active = False
    sec2.is_active = False
    db.commit()

    # C-5: 비활성 영상 및 비활성 섹션 소속 영상 모두 404
    assert client.get(f"/api/v1/videos/{v2.id}", headers=auth_header(learner.id)).status_code == 404
    assert client.get(f"/api/v1/videos/{v3.id}", headers=auth_header(learner.id)).status_code == 404

    # C-6: 비활성 영상 및 비활성 섹션 소속 영상 조회수 집계 -> 404
    assert (
        client.post(
            f"/api/v1/videos/{v2.id}/views",
            json={"playedSeconds": 5},
            headers=auth_header(learner.id),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/videos/{v3.id}/views",
            json={"playedSeconds": 5},
            headers=auth_header(learner.id),
        ).status_code
        == 404
    )

    # C-7: 비활성 영상 및 비활성 섹션 소속 영상 진도 저장 -> 404
    payload = {
        "lastPositionSeconds": 10,
        "watchedRange": [0, 10],
        "activitySession": {
            "sessionKey": "sess-inactive-check",
            "startedAt": "2026-04-09T12:00:00Z",
            "endedAt": "2026-04-09T12:00:10Z",
            "activeSeconds": 10,
        },
    }
    assert (
        client.put(
            f"/api/v1/videos/{v2.id}/progress", json=payload, headers=auth_header(learner.id)
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/v1/videos/{v3.id}/progress", json=payload, headers=auth_header(learner.id)
        ).status_code
        == 404
    )

    # C-8: 비활성 영상 및 비활성 섹션 소속 영상 좋아요 등록/취소 -> 404
    assert (
        client.post(f"/api/v1/videos/{v2.id}/like", headers=auth_header(learner.id)).status_code
        == 404
    )
    assert (
        client.post(f"/api/v1/videos/{v3.id}/like", headers=auth_header(learner.id)).status_code
        == 404
    )
    assert (
        client.delete(f"/api/v1/videos/{v2.id}/like", headers=auth_header(learner.id)).status_code
        == 404
    )
    assert (
        client.delete(f"/api/v1/videos/{v3.id}/like", headers=auth_header(learner.id)).status_code
        == 404
    )

    # C-9: 좋아요 목록에서도 비활성 영상(v2)과 비활성 섹션 소속 영상(v3)은 제외됨
    res_likes = client.get("/api/v1/me/likes", headers=auth_header(learner.id))
    assert res_likes.status_code == 200
    assert res_likes.json()["data"]["totalCount"] == 0


def test_auth_and_mode_permissions_matrix(client: TestClient, db: Session, get_user):
    instructor = get_user("instructor1@baeugo.test")
    operator = get_user("admin@baeugo.test")
    learner = get_user("learner2@baeugo.test")
    course, ot, sec1, v1, v2, uq1, sec2, v3, uq2, fq = _build_full_course(db, instructor)
    enrollment = enroll(db, learner, course)
    db.commit()

    bad_headers = {"Authorization": "Bearer invalid.jwt.token"}

    # C-3: 비로그인 401, 강사모드 403, 운영자 403
    assert client.get("/api/v1/me/enrollments").status_code == 401
    assert client.get("/api/v1/me/enrollments", headers=bad_headers).status_code == 401
    assert (
        client.get("/api/v1/me/enrollments", headers=auth_header(instructor.id)).status_code == 403
    )
    assert client.get("/api/v1/me/enrollments", headers=auth_header(operator.id)).status_code == 403

    # C-5: OT에 잘못된 토큰 전송 시 401, 존재하지 않는 영상 404
    assert client.get(f"/api/v1/videos/{ot.id}", headers=bad_headers).status_code == 401
    assert client.get(f"/api/v1/videos/{v1.id}", headers=bad_headers).status_code == 401
    assert client.get("/api/v1/videos/999999", headers=auth_header(learner.id)).status_code == 404

    # C-6: 비로그인 401, 존재하지 않는 영상 404
    assert (
        client.post(f"/api/v1/videos/{v1.id}/views", json={"playedSeconds": 5}).status_code == 401
    )
    assert (
        client.post(
            "/api/v1/videos/999999/views",
            json={"playedSeconds": 5},
            headers=auth_header(learner.id),
        ).status_code
        == 404
    )

    # C-7: 비로그인 401, 강사모드 403, 운영자 403, 타 수강내역의 sessionKey 재사용 400
    progress_body = {
        "lastPositionSeconds": 10,
        "watchedRange": [0, 10],
        "activitySession": {
            "sessionKey": "sess-shared-key",
            "startedAt": "2026-04-09T12:00:00Z",
            "endedAt": "2026-04-09T12:00:10Z",
            "activeSeconds": 10,
        },
    }
    assert client.put(f"/api/v1/videos/{v1.id}/progress", json=progress_body).status_code == 401
    assert (
        client.put(
            f"/api/v1/videos/{v1.id}/progress",
            json=progress_body,
            headers=auth_header(instructor.id),
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/v1/videos/{v1.id}/progress",
            json=progress_body,
            headers=auth_header(operator.id),
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/api/v1/videos/999999/progress",
            json=progress_body,
            headers=auth_header(learner.id),
        ).status_code
        == 404
    )

    # learner가 sess-shared-key 저장 후, 타 수강내역(course2)에서 동일 sessionKey 사용 시 400
    assert (
        client.put(
            f"/api/v1/videos/{v1.id}/progress",
            json=progress_body,
            headers=auth_header(learner.id),
        ).status_code
        == 200
    )
    course2, _, sec2_1, v2_1, *_ = _build_full_course(db, instructor)
    enroll(db, learner, course2)
    db.commit()
    assert (
        client.put(
            f"/api/v1/videos/{v2_1.id}/progress",
            json=progress_body,
            headers=auth_header(learner.id),
        ).status_code
        == 400
    )

    # C-8 & C-9: 비로그인 401, C-9 강사/운영자 403
    assert client.post(f"/api/v1/videos/{ot.id}/like").status_code == 401
    assert client.delete(f"/api/v1/videos/{ot.id}/like").status_code == 401
    assert client.get("/api/v1/me/likes").status_code == 401
    assert client.get("/api/v1/me/likes", headers=auth_header(instructor.id)).status_code == 403
    assert client.get("/api/v1/me/likes", headers=auth_header(operator.id)).status_code == 403
    _ = enrollment


def test_c7_naive_and_mixed_timezone_datetimes_and_simultaneous_deduplication(
    client: TestClient, db: Session, get_user
):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")
    course, ot, sec1, v1, v2, uq1, sec2, v3, uq2, fq = _build_full_course(db, instructor)
    enr = enroll(db, learner, course)
    db.commit()

    # 1. Naive startedAt + Aware endedAt 및 동일 sessionKey 재호출 시 200 반환
    res_mixed = client.put(
        f"/api/v1/videos/{v1.id}/progress",
        json={
            "lastPositionSeconds": 60,
            "watchedRange": [0, 60],
            "activitySession": {
                "sessionKey": "sess-naive-mixed",
                "startedAt": "2026-10-17T08:00:00",
                "endedAt": "2026-10-17T08:10:00+09:00",
                "activeSeconds": 600,
            },
        },
        headers=auth_header(learner.id),
    )
    assert res_mixed.status_code == 200
    assert res_mixed.json()["data"]["todayActiveSeconds"] == 600

    # 양쪽 모두 naive datetime으로 동일 sessionKey 재호출 (DB의 aware ended_at과 비교 검증)
    res_naive_retry = client.put(
        f"/api/v1/videos/{v1.id}/progress",
        json={
            "lastPositionSeconds": 70,
            "watchedRange": [0, 70],
            "activitySession": {
                "sessionKey": "sess-naive-mixed",
                "startedAt": "2026-10-17T08:00:00",
                "endedAt": "2026-10-17T08:10:00",
                "activeSeconds": 600,
            },
        },
        headers=auth_header(learner.id),
    )
    assert res_naive_retry.status_code == 200
    assert res_naive_retry.json()["data"]["todayActiveSeconds"] == 600

    # 2. 다른 탭에서 동일한 시간대 [08:00:00, 08:10:00] 동시 시청 시 중복 합산 제외 (600초 유지)
    res_simul = client.put(
        f"/api/v1/videos/{v2.id}/progress",
        json={
            "lastPositionSeconds": 60,
            "watchedRange": [0, 60],
            "activitySession": {
                "sessionKey": "sess-simultaneous-tab2",
                "startedAt": "2026-10-17T08:00:00+09:00",
                "endedAt": "2026-10-17T08:10:00+09:00",
                "activeSeconds": 600,
            },
        },
        headers=auth_header(learner.id),
    )
    assert res_simul.status_code == 200
    assert res_simul.json()["data"]["todayActiveSeconds"] == 600
    assert res_simul.json()["data"]["todayIsAttended"] is False

    # 3. 이미 is_completed=True인 영상에 짧은 구간 시청 시 remainingSecondsForCompletion == 0 유지
    db.add(
        VideoProgress(
            enrollment_id=enr.id,
            video_id=v3.id,
            watched_ranges=[],
            watched_seconds=0,
            last_position_seconds=0,
            is_completed=True,
            completed_at=datetime.now(UTC),
        )
    )
    db.commit()

    res_pre_comp = client.put(
        f"/api/v1/videos/{v3.id}/progress",
        json={
            "lastPositionSeconds": 10,
            "watchedRange": [0, 10],
            "activitySession": {
                "sessionKey": "sess-v3-recheck",
                "startedAt": "2026-10-17T09:00:00+09:00",
                "endedAt": "2026-10-17T09:00:10+09:00",
                "activeSeconds": 10,
            },
        },
        headers=auth_header(learner.id),
    )
    assert res_pre_comp.status_code == 200
    d_pre = res_pre_comp.json()["data"]
    assert d_pre["isCompleted"] is True
    assert d_pre["remainingSecondsForCompletion"] == 0
    assert d_pre["completedAt"].endswith("+09:00")


def test_c8_like_count_drift_sync_and_c5_draft_owner_access(
    client: TestClient, db: Session, get_user
):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")
    operator = get_user("admin@baeugo.test")
    course, ot, sec1, v1, *_ = _build_full_course(db, instructor)

    # 1. videos.like_count가 실제 video_likes 집계와 어긋나 있을 때 C-8 호출 시 원본 집계로 동기화
    ot.like_count = 99
    db.commit()

    res_like = client.post(f"/api/v1/videos/{ot.id}/like", headers=auth_header(learner.id))
    assert res_like.status_code == 200
    assert res_like.json()["data"]["likeCount"] == 1

    res_unlike = client.delete(f"/api/v1/videos/{ot.id}/like", headers=auth_header(learner.id))
    assert res_unlike.status_code == 200
    assert res_unlike.json()["data"]["likeCount"] == 0

    # 2. DRAFT 상태 강좌의 영상이라도 강좌 소유 강사 및 운영자는 C-5 열람 가능 (수강자는 403)
    draft_course, draft_ot, draft_sec, draft_v1, *_ = _build_full_course(
        db, instructor, status="DRAFT"
    )
    db.commit()

    assert (
        client.get(f"/api/v1/videos/{draft_v1.id}", headers=auth_header(learner.id)).status_code
        == 403
    )
    assert (
        client.get(f"/api/v1/videos/{draft_v1.id}", headers=auth_header(instructor.id)).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/videos/{draft_v1.id}", headers=auth_header(operator.id)).status_code
        == 200
    )
