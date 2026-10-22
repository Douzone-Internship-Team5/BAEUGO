from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course


def test_course_search_all_cases(client: TestClient, db_session: Session):
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

    # [성공 3] 카테고리 / 토픽 필터링
    res_cat = client.get(f"/api/v1/courses?categoryId={c_free.category_id}")
    assert res_cat.status_code == 200
    assert any(c["courseId"] == c_free.id for c in res_cat.json()["data"]["items"])

    # [성공 4] 난이도 필터 및 정렬 (sort=difficulty, latest, popular)
    res_diff = client.get("/api/v1/courses?sort=difficulty")
    assert res_diff.status_code == 200

    res_latest = client.get("/api/v1/courses?sort=latest")
    assert res_latest.status_code == 200

    res_pop = client.get("/api/v1/courses?sort=popular")
    assert res_pop.status_code == 200

    # [성공 5] 개인화 추천 정렬 (sort=recommended, 로그인 필수)
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

    # [실패 2] 위조/잘못된 토큰으로 sort=recommended 요청 -> 401 UNAUTHORIZED
    res_rec_bad_token = client.get(
        "/api/v1/courses?sort=recommended",
        headers={"Authorization": "Bearer invalid"},
    )
    assert res_rec_bad_token.status_code == 401
    assert res_rec_bad_token.json()["error"]["code"] == "UNAUTHORIZED"

    # [실패 3] 유효하지 않은 difficulty 필터 전달 -> 400 VALIDATION_ERROR
    res_bad_diff = client.get("/api/v1/courses?difficulty=SUPER_HARD")
    assert res_bad_diff.status_code == 400
    assert res_bad_diff.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패 4] 유효하지 않은 price 필터 전달 -> 400 VALIDATION_ERROR
    res_bad_price = client.get("/api/v1/courses?price=CHEAP")
    assert res_bad_price.status_code == 400
    assert res_bad_price.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패 5] 유효하지 않은 sort 값 전달 -> 400 VALIDATION_ERROR
    res_bad_sort = client.get("/api/v1/courses?sort=UNKNOWN_SORT")
    assert res_bad_sort.status_code == 400
    assert res_bad_sort.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패 6] page < 1 경계값 유효성 실패 -> 400 VALIDATION_ERROR
    res_bad_page = client.get("/api/v1/courses?page=0")
    assert res_bad_page.status_code == 400
    assert res_bad_page.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패 7] size > 100 경계값 유효성 실패 -> 400 VALIDATION_ERROR
    res_bad_size = client.get("/api/v1/courses?size=101")
    assert res_bad_size.status_code == 400
    assert res_bad_size.json()["error"]["code"] == "VALIDATION_ERROR"
