from sqlalchemy import update

from app.models import Category, Topic

CODES_URL = "/api/v1/codes"

SEED_CATEGORY_CODES = [
    "PLANNING_PM",
    "MARKETING",
    "DATA_ANALYSIS",
    "DEV_IT",
    "DESIGN",
    "OFFICE_PRODUCTIVITY",
    "CAREER",
]
# 직무 순서 → 직무 안의 주제 순서. 시드 주제 id = 직무 id × 10 + 순번
SEED_TOPIC_IDS = [category_id * 10 + order for category_id in range(1, 8) for order in (1, 2, 3)]


def get_codes(client, headers=None):
    response = client.get(CODES_URL, headers=headers or {})
    assert response.status_code == 200
    return response.json()["data"]


def test_codes_without_login(client):
    data = get_codes(client)

    assert set(data) == {
        "jobCategories",
        "topics",
        "currentStatuses",
        "difficulties",
        "dailyGoalMinutes",
    }


def test_codes_ignores_invalid_token(client):
    # 로그인 없이 쓰는 API라서 잘못된 토큰이 와도 401을 내지 않는다.
    data = get_codes(client, headers={"Authorization": "Bearer invalid"})

    assert len(data["jobCategories"]) == 7


def test_job_categories_in_sort_order(client):
    categories = get_codes(client)["jobCategories"]

    assert [category["code"] for category in categories] == SEED_CATEGORY_CODES
    assert categories[0] == {
        "id": 1,
        "code": "PLANNING_PM",
        "name": "기획·PM",
        "label": "기획·PM",
        "sortOrder": 1,
    }
    assert all(category["label"] == category["name"] for category in categories)


def test_all_seed_topics_grouped_by_category(client):
    topics = get_codes(client)["topics"]

    assert [topic["id"] for topic in topics] == SEED_TOPIC_IDS
    by_id = {topic["id"]: topic for topic in topics}
    assert by_id[11] == {
        "id": 11,
        "categoryId": 1,
        "jobCategory": "PLANNING_PM",
        "name": "문서 작성",
        "label": "문서 작성",
        "sortOrder": 1,
    }
    assert by_id[61] == {
        "id": 61,
        "categoryId": 6,
        "jobCategory": "OFFICE_PRODUCTIVITY",
        "name": "업무 자동화",
        "label": "업무 자동화",
        "sortOrder": 1,
    }
    assert all(
        topic["jobCategory"] == SEED_CATEGORY_CODES[topic["categoryId"] - 1] for topic in topics
    )


def test_fixed_code_lists_match_spec(client):
    data = get_codes(client)

    assert data["currentStatuses"] == [
        {"code": "JOB_SEEKER", "label": "취업 준비 중"},
        {"code": "EMPLOYED", "label": "재직 중"},
        {"code": "CAREER_CHANGE", "label": "이직·직무 전환 준비 중"},
    ]
    assert data["difficulties"] == [
        {"code": "BEGINNER", "label": "초급"},
        {"code": "INTERMEDIATE", "label": "중급"},
        {"code": "ADVANCED", "label": "고급"},
    ]
    assert data["dailyGoalMinutes"] == [10, 20, 30, 40, 50, 60]


def test_inactive_topic_is_excluded(client, db):
    db.execute(update(Topic).where(Topic.id == 12).values(is_active=False))
    db.flush()

    topic_ids = [topic["id"] for topic in get_codes(client)["topics"]]

    assert 12 not in topic_ids
    assert topic_ids == [topic_id for topic_id in SEED_TOPIC_IDS if topic_id != 12]


def test_topics_of_inactive_category_are_excluded(client, db):
    db.execute(update(Category).where(Category.id == 2).values(is_active=False))
    db.flush()

    data = get_codes(client)

    assert "MARKETING" not in [category["code"] for category in data["jobCategories"]]
    # 주제 21·22·23은 활성 상태지만 소속 직무가 비활성이라 빠진다.
    assert [topic["id"] for topic in data["topics"]] == [
        topic_id for topic_id in SEED_TOPIC_IDS if topic_id // 10 != 2
    ]
