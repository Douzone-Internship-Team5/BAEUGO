from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course, Section


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

    # [실패 1] 타인/미존재 섹션 수정/삭제 시도 -> 404 NOT_FOUND
    res_sec_404 = client.patch("/api/v1/instructor/sections/999999", json={"title": "수정"})
    assert res_sec_404.status_code == 404

    res_del_404 = client.delete("/api/v1/instructor/sections/999999")
    assert res_del_404.status_code == 404

    # [실패 2] 이미 삭제(비활성화)된 섹션 수정 시도 -> 404 NOT_FOUND
    res_del_retry = client.patch(f"/api/v1/instructor/sections/{sec1_id}", json={"title": "재수정"})
    assert res_del_retry.status_code == 404

    # [실패 3] 타인 강좌 ID로 섹션 생성 시도 -> 404 NOT_FOUND
    res_other_add = client.post(
        "/api/v1/instructor/sections",
        json={"courseId": 999999, "title": "새 섹션"},
    )
    assert res_other_add.status_code == 404

    # [실패 4] PUBLISHED 강좌에 섹션 추가/수정/삭제 시도 -> 409 CONFLICT
    pub_course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="공개 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="PUBLISHED",
        published_at=datetime.now(UTC),
    )
    db_session.add(pub_course)
    db_session.flush()

    pub_sec = Section(course_id=pub_course.id, title="공개 섹션", sort_order=1, is_active=True)
    db_session.add(pub_sec)
    db_session.commit()

    res_pub_add = client.post(
        "/api/v1/instructor/sections",
        json={"courseId": pub_course.id, "title": "공개 강좌에 섹션 추가"},
    )
    assert res_pub_add.status_code == 409

    res_pub_patch = client.patch(
        f"/api/v1/instructor/sections/{pub_sec.id}",
        json={"title": "공개 강좌 섹션 수정"},
    )
    assert res_pub_patch.status_code == 409

    res_pub_del = client.delete(f"/api/v1/instructor/sections/{pub_sec.id}")
    assert res_pub_del.status_code == 409

    # [실패 5] 공백 제목 / 150자 초과 제목 / sortOrder < 1 검증 -> 400 VALIDATION_ERROR
    res_empty_title = client.post(
        "/api/v1/instructor/sections",
        json={"courseId": course.id, "title": "   "},
    )
    assert res_empty_title.status_code == 400
    assert res_empty_title.json()["error"]["code"] == "VALIDATION_ERROR"

    res_long_title = client.post(
        "/api/v1/instructor/sections",
        json={"courseId": course.id, "title": "가" * 151},
    )
    assert res_long_title.status_code == 400

    sec2_id = res_sec2.json()["data"]["sectionId"]
    res_bad_sort = client.patch(
        f"/api/v1/instructor/sections/{sec2_id}",
        json={"sortOrder": 0},
    )
    assert res_bad_sort.status_code == 400
    assert res_bad_sort.json()["error"]["code"] == "VALIDATION_ERROR"
