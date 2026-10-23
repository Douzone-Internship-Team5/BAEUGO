from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course
from tests.conftest import auth_header


def test_course_search_all_cases(client: TestClient, db_session: Session):
    c_free = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="무료 기획 강좌",
        description="기획 문서 작성 가이드",
        difficulty="BEGINNER",
        price_krw=0,
        thumbnail_storage_key="courses/free_thumb.png",
        status="PUBLISHED",
        published_at=datetime.now(UTC),
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
        published_at=datetime.now(UTC),
    )
    db_session.add_all([c_free, c_paid])
    db_session.commit()

    # [성공 1] 비로그인 상태에서 검색어 필터링 (q="기획")
    res_q = client.get("/api/v1/courses?q=기획")
    assert res_q.status_code == 200
    matched_item = next(c for c in res_q.json()["data"]["items"] if c["courseId"] == c_free.id)
    assert matched_item["thumbnailUrl"] == "/api/v1/media/courses/free_thumb.png"
    assert not any(c["courseId"] == c_paid.id for c in res_q.json()["data"]["items"])

    # [성공 2] 가격 필터 (price=free / price=paid)
    res_free = client.get("/api/v1/courses?price=free")
    assert res_free.status_code == 200
    assert all(c["isFree"] is True for c in res_free.json()["data"]["items"])

    res_paid = client.get("/api/v1/courses?price=paid")
    assert res_paid.status_code == 200
    assert all(c["isFree"] is False for c in res_paid.json()["data"]["items"])

    # [성공 3] 카테고리 / 토픽 필터링 (categoryId=0, topicId=0 포함)
    res_cat = client.get(f"/api/v1/courses?categoryId={c_free.category_id}")
    assert res_cat.status_code == 200
    assert any(c["courseId"] == c_free.id for c in res_cat.json()["data"]["items"])

    res_cat_zero = client.get("/api/v1/courses?categoryId=0")
    assert res_cat_zero.status_code == 200
    assert res_cat_zero.json()["data"]["total"] == 0

    res_topic_zero = client.get("/api/v1/courses?topicId=0")
    assert res_topic_zero.status_code == 200
    assert res_topic_zero.json()["data"]["total"] == 0

    # [성공 4] 난이도 필터링 (difficulty=BEGINNER / difficulty=ADVANCED)
    res_diff_beg = client.get("/api/v1/courses?difficulty=BEGINNER")
    assert res_diff_beg.status_code == 200
    assert any(c["courseId"] == c_free.id for c in res_diff_beg.json()["data"]["items"])
    assert not any(c["courseId"] == c_paid.id for c in res_diff_beg.json()["data"]["items"])

    res_diff_adv = client.get("/api/v1/courses?difficulty=ADVANCED")
    assert res_diff_adv.status_code == 200
    assert any(c["courseId"] == c_paid.id for c in res_diff_adv.json()["data"]["items"])
    assert not any(c["courseId"] == c_free.id for c in res_diff_adv.json()["data"]["items"])

    # [성공 5] 복합 필터 (검색어 + 카테고리 + 가격 + 난이도)
    res_comb = client.get("/api/v1/courses?q=기획&categoryId=1&price=free&difficulty=BEGINNER")
    assert res_comb.status_code == 200
    assert any(c["courseId"] == c_free.id for c in res_comb.json()["data"]["items"])

    # [성공 6] 정렬 (sort=difficulty, latest, popular)
    res_diff = client.get("/api/v1/courses?sort=difficulty")
    assert res_diff.status_code == 200

    res_latest = client.get("/api/v1/courses?sort=latest")
    assert res_latest.status_code == 200

    res_pop = client.get("/api/v1/courses?sort=popular")
    assert res_pop.status_code == 200

    # [성공 7] 개인화 추천 정렬 (sort=recommended, 로그인 필수)
    res_rec = client.get(
        "/api/v1/courses?sort=recommended",
        headers=auth_header(2),
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

    # [실패 7] size < 1 경계값 유효성 실패 -> 400 VALIDATION_ERROR
    res_bad_size_zero = client.get("/api/v1/courses?size=0")
    assert res_bad_size_zero.status_code == 400
    assert res_bad_size_zero.json()["error"]["code"] == "VALIDATION_ERROR"

    # [실패 8] size > 100 경계값 유효성 실패 -> 400 VALIDATION_ERROR
    res_bad_size = client.get("/api/v1/courses?size=101")
    assert res_bad_size.status_code == 400
    assert res_bad_size.json()["error"]["code"] == "VALIDATION_ERROR"
