from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import auth_header
from tests.factories import enroll, make_course, make_quiz, make_section, make_video

COURSES_URL = "/api/v1/admin/courses"
NOW = datetime(2026, 10, 24, 3, 0, tzinfo=UTC)
LIST_FILTER_MESSAGE = "유효하지 않은 상태 또는 정렬 기준입니다."
REASON_MESSAGE = "비공개 사유를 1~500자 이내로 입력해 주세요."


@pytest.fixture
def operator(get_user):
    return get_user("admin@baeugo.test")


@pytest.fixture
def operator_headers(operator) -> dict[str, str]:
    return auth_header(operator.id)


@pytest.fixture
def courses(db, get_user) -> dict:
    """공개 2개, 운영자 비공개 1개, 작성 중 1개, 수정 중 1개."""
    instructor1 = get_user("instructor1@baeugo.test")
    instructor2 = get_user("instructor2@baeugo.test")
    excel = make_course(
        db, instructor1, title="실무자를 위한 엑셀 자동화", published_at=NOW - timedelta(days=3)
    )
    web = make_course(
        db,
        instructor2,
        title="웹 개발 입문",
        category_id=4,
        topic_id=42,
        price_krw=0,
        published_at=NOW - timedelta(days=1),
    )
    report = make_course(
        db,
        instructor1,
        title="보고서 작성법",
        status="OPERATOR_HIDDEN",
        category_id=1,
        topic_id=11,
        published_at=NOW - timedelta(days=2),
    )
    report.closed_by_user_id = get_user("admin@baeugo.test").id
    report.closed_at = NOW
    report.closed_reason = "저작권 문제가 있는 영상 포함"
    draft = make_course(db, instructor1, title="작성 중 강좌", status="DRAFT")
    draft.published_at = None
    editing = make_course(db, instructor2, title="수정 중 강좌", status="EDITING")
    for learner_email in ("learner1@baeugo.test", "learner2@baeugo.test"):
        enroll(db, get_user(learner_email), excel)
    enroll(db, get_user("learner2@baeugo.test"), report)
    db.flush()
    return {"excel": excel, "web": web, "report": report, "draft": draft, "editing": editing}


def list_courses(client, headers, **params):
    return client.get(COURSES_URL, params=params, headers=headers)


def titles(response) -> list[str]:
    return [item["title"] for item in response.json()["data"]["items"]]


def assert_list_filter_error(response) -> None:
    assert response.status_code == 400
    error = response.json()["error"]
    assert (error["code"], error["message"]) == ("VALIDATION_ERROR", LIST_FILTER_MESSAGE)


# ---------- OP-1 강좌 목록 ----------


def test_list_shows_published_and_hidden_recent_first(client, operator_headers, courses):
    response = list_courses(client, operator_headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert (data["page"], data["size"], data["total"]) == (1, 20, 3)
    assert titles(response) == ["웹 개발 입문", "보고서 작성법", "실무자를 위한 엑셀 자동화"]


def test_list_item_matches_spec(client, operator_headers, courses, get_user):
    excel = courses["excel"]

    response = list_courses(client, operator_headers, q="엑셀")

    item = response.json()["data"]["items"][0]
    assert datetime.fromisoformat(item.pop("publishedAt")) == excel.published_at
    assert item == {
        "courseId": excel.id,
        "title": "실무자를 위한 엑셀 자동화",
        "instructorId": get_user("instructor1@baeugo.test").id,
        "instructorName": "이하늘",
        "categoryId": 6,
        "jobCategory": "OFFICE_PRODUCTIVITY",
        "status": "PUBLISHED",
        "priceKrw": 29000,
        "enrollmentCount": 2,
        "closedByUserId": None,
        "closedAt": None,
        "closedReason": None,
    }


def test_hidden_course_shows_close_info(client, operator_headers, courses, operator):
    item = list_courses(client, operator_headers, status="OPERATOR_HIDDEN").json()["data"]["items"][
        0
    ]

    assert item["closedByUserId"] == operator.id
    assert item["closedReason"] == "저작권 문제가 있는 영상 포함"
    assert datetime.fromisoformat(item["closedAt"]) == NOW


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("PUBLISHED", ["웹 개발 입문", "실무자를 위한 엑셀 자동화"]),
        ("OPERATOR_HIDDEN", ["보고서 작성법"]),
    ],
)
def test_status_filter(client, operator_headers, courses, status, expected):
    assert titles(list_courses(client, operator_headers, status=status)) == expected


@pytest.mark.parametrize(
    "params", [{"status": "DRAFT"}, {"status": "EDITING"}, {"sort": "popular"}]
)
def test_invalid_status_or_sort_is_rejected(client, operator_headers, courses, params):
    assert_list_filter_error(list_courses(client, operator_headers, **params))


def test_sort_by_enrollments(client, operator_headers, courses):
    response = list_courses(client, operator_headers, sort="enrollments")

    assert titles(response) == ["실무자를 위한 엑셀 자동화", "보고서 작성법", "웹 개발 입문"]
    counts = [item["enrollmentCount"] for item in response.json()["data"]["items"]]
    assert counts == [2, 1, 0]


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("엑셀", ["실무자를 위한 엑셀 자동화"]),
        ("정다온", ["웹 개발 입문"]),
        ("이하늘", ["보고서 작성법", "실무자를 위한 엑셀 자동화"]),
        ("수정 중", []),
        ("%", []),
        ("  ", ["웹 개발 입문", "보고서 작성법", "실무자를 위한 엑셀 자동화"]),
    ],
)
def test_search_by_course_title_or_instructor_name(client, operator_headers, courses, q, expected):
    assert titles(list_courses(client, operator_headers, q=q)) == expected


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"categoryId": 6}, ["실무자를 위한 엑셀 자동화"]),
        ({"jobCategory": "DEV_IT"}, ["웹 개발 입문"]),
        ({"categoryId": 1, "jobCategory": "PLANNING_PM"}, ["보고서 작성법"]),
        ({"jobCategory": "UNKNOWN"}, []),
        ({"categoryId": 999}, []),
    ],
)
def test_category_filter(client, operator_headers, courses, params, expected):
    assert titles(list_courses(client, operator_headers, **params)) == expected


def test_mismatched_category_id_and_code_are_rejected(client, operator_headers, courses):
    response = list_courses(client, operator_headers, categoryId=6, jobCategory="DEV_IT")

    assert_list_filter_error(response)
    assert "jobCategory" in response.json()["error"]["fields"]


def test_pagination(client, operator_headers, courses):
    first = list_courses(client, operator_headers, page=1, size=2)
    second = list_courses(client, operator_headers, page=2, size=2)

    assert titles(first) == ["웹 개발 입문", "보고서 작성법"]
    assert titles(second) == ["실무자를 위한 엑셀 자동화"]
    assert second.json()["data"]["total"] == 3


@pytest.mark.parametrize("params", [{"page": 0}, {"size": 0}, {"size": 101}, {"q": "가" * 101}])
def test_invalid_paging_is_rejected(client, operator_headers, params):
    response = list_courses(client, operator_headers, **params)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------- OP-2 강좌 상세 ----------


@pytest.fixture
def full_course(db, get_user):
    instructor = get_user("instructor1@baeugo.test")
    course = make_course(db, instructor)
    ot = make_video(
        db,
        course,
        video_type="OT",
        title="무료 OT",
        sort_order=0,
        duration_seconds=45,
        scripts=("OT 자막",),
    )
    section = make_section(db, course, title="섹션 1. 기본 개념")
    lesson = make_video(db, course, section=section, title="1강", scripts=("첫 자막", "둘째 자막"))
    unit_quiz = make_quiz(db, course, section=section)
    final_quiz = make_quiz(db, course)
    enroll(db, get_user("learner2@baeugo.test"), course)
    return {
        "course": course,
        "instructor": instructor,
        "ot": ot,
        "section": section,
        "lesson": lesson,
        "unit_quiz": unit_quiz,
        "final_quiz": final_quiz,
    }


def expected_video(video, video_type, title, duration, sort_order, contents):
    return {
        "videoId": video.id,
        "videoType": video_type,
        "title": title,
        "durationSeconds": duration,
        "sortOrder": sort_order,
        "videoStorageKey": video.video_storage_key,
        "videoUrl": f"/api/v1/videos/{video.id}/stream",
        "thumbnailStorageKey": None,
        "thumbnailUrl": None,
        "sttStatus": "COMPLETED",
        "transcriptReviewedAt": None,
        "segments": [
            {
                "id": script.id,
                "seq": seq,
                "startMs": (seq - 1) * 1000,
                "endMs": seq * 1000,
                "content": content,
            }
            for seq, (script, content) in enumerate(zip(video.scripts, contents, strict=True), 1)
        ],
    }


def expected_quiz(quiz, quiz_type, section_id, ai_status, generated_by):
    question = quiz.questions[0]
    return {
        "quizId": quiz.id,
        "quizType": quiz_type,
        "sectionId": section_id,
        "aiStatus": ai_status,
        "reviewedBy": None,
        "reviewedAt": None,
        "questions": [
            {
                "questionId": question.id,
                "orderNo": 1,
                "generatedBy": generated_by,
                "content": "문제 1",
                "choices": ["보기 1", "보기 2", "보기 3", "보기 4"],
                "correctChoiceNo": 1,
                "explanation": "해설 1",
            }
        ],
    }


def test_course_detail_matches_spec(client, operator_headers, full_course):
    course, section = full_course["course"], full_course["section"]
    instructor = full_course["instructor"]

    response = client.get(f"{COURSES_URL}/{course.id}", headers=operator_headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert datetime.fromisoformat(data.pop("publishedAt")) == course.published_at
    assert data == {
        "courseId": course.id,
        "status": "PUBLISHED",
        "title": "실무자를 위한 엑셀 자동화",
        "description": "실무자를 위한 엑셀 자동화 강좌 소개",
        "categoryId": 6,
        "categoryCode": "OFFICE_PRODUCTIVITY",
        "categoryName": "사무 생산성",
        "topicId": 61,
        "topicName": "업무 자동화",
        "difficulty": "BEGINNER",
        "priceKrw": 29000,
        "thumbnailStorageKey": None,
        "thumbnailUrl": None,
        "closedAt": None,
        "closedReason": None,
        "closedByUserId": None,
        "instructor": {
            "id": instructor.id,
            "name": "이하늘",
            "email": "instructor1@baeugo.test",
            "bio": "10년 차 사무 자동화 실무자",
            "profileImageKey": None,
            "profileImageUrl": None,
        },
        "totalLessonDurationSeconds": 360,
        "totalLessonCount": 1,
        "enrollmentCount": 1,
        "otVideo": expected_video(full_course["ot"], "OT", "무료 OT", 45, 0, ["OT 자막"]),
        "sections": [
            {
                "sectionId": section.id,
                "title": "섹션 1. 기본 개념",
                "sortOrder": 1,
                "videos": [
                    expected_video(
                        full_course["lesson"], "LESSON", "1강", 360, 1, ["첫 자막", "둘째 자막"]
                    )
                ],
                "unitQuiz": expected_quiz(
                    full_course["unit_quiz"], "UNIT", section.id, "COMPLETED", "AI"
                ),
            }
        ],
        "finalQuiz": expected_quiz(full_course["final_quiz"], "FINAL", None, None, "INSTRUCTOR"),
    }


def test_course_detail_excludes_inactive_items(client, db, operator_headers, full_course):
    course, section = full_course["course"], full_course["section"]
    make_video(db, course, section=section, title="2강", sort_order=2, duration_seconds=120)
    make_video(db, course, section=section, title="지운 강의", sort_order=3, is_active=False)
    hidden_section = make_section(db, course, title="지운 섹션", sort_order=2, is_active=False)
    make_video(db, course, section=hidden_section, title="지운 섹션 강의")
    make_quiz(db, course, section=hidden_section)
    make_video(db, course, video_type="OT", title="예전 OT", is_active=False)
    unit_quiz = full_course["unit_quiz"]
    make_quiz(db, course, section=section, is_active=False)
    # 수강 이력이 있어 문항 1개를 비활성화하고 새 문항으로 교체한 퀴즈
    second_section = make_section(db, course, title="섹션 2", sort_order=3)
    retired = make_quiz(
        db, course, section=second_section, question_count=2, inactive_question_count=1
    )

    data = client.get(f"{COURSES_URL}/{course.id}", headers=operator_headers).json()["data"]

    assert data["otVideo"]["title"] == "무료 OT"
    assert [s["title"] for s in data["sections"]] == ["섹션 1. 기본 개념", "섹션 2"]
    assert [v["title"] for v in data["sections"][0]["videos"]] == ["1강", "2강"]
    assert data["sections"][0]["unitQuiz"]["quizId"] == unit_quiz.id
    assert data["sections"][1]["videos"] == []
    assert data["sections"][1]["unitQuiz"]["quizId"] == retired.id
    assert [q["orderNo"] for q in data["sections"][1]["unitQuiz"]["questions"]] == [1, 2]
    assert (data["totalLessonCount"], data["totalLessonDurationSeconds"]) == (2, 480)


def test_course_without_ot_or_quizzes(client, db, operator_headers, get_user):
    course = make_course(db, get_user("instructor2@baeugo.test"))
    make_section(db, course)

    data = client.get(f"{COURSES_URL}/{course.id}", headers=operator_headers).json()["data"]

    assert (data["otVideo"], data["finalQuiz"]) == (None, None)
    assert data["sections"][0]["unitQuiz"] is None
    assert data["instructor"]["bio"] == "웹 서비스 개발 7년 차 백엔드 개발자"


def test_hidden_course_detail_is_visible(client, operator_headers, courses):
    response = client.get(f"{COURSES_URL}/{courses['report'].id}", headers=operator_headers)

    assert response.status_code == 200
    assert response.json()["data"]["closedReason"] == "저작권 문제가 있는 영상 포함"


@pytest.mark.parametrize("key", ["draft", "editing"])
def test_draft_and_editing_course_detail_is_not_found(client, operator_headers, courses, key):
    response = client.get(f"{COURSES_URL}/{courses[key].id}", headers=operator_headers)

    assert response.status_code == 404
    assert response.json()["error"] == {"code": "NOT_FOUND", "message": "존재하지 않는 강좌입니다."}


def test_unknown_course_detail_is_not_found(client, operator_headers):
    response = client.get(f"{COURSES_URL}/999999", headers=operator_headers)

    assert response.status_code == 404


# ---------- OP-3 강좌 내리기 ----------


def unpublish(client, headers, course_id, body):
    return client.post(f"{COURSES_URL}/{course_id}/unpublish", json=body, headers=headers)


def test_unpublish_hides_course_and_records_reason(client, operator_headers, operator, courses):
    excel = courses["excel"]

    response = unpublish(
        client, operator_headers, excel.id, {"reason": "  저작권 문제가 있는 영상 포함  "}
    )

    assert response.status_code == 200
    data = response.json()["data"]
    closed_at = datetime.fromisoformat(data.pop("closedAt"))
    assert data == {
        "courseId": excel.id,
        "status": "OPERATOR_HIDDEN",
        "closedByUserId": operator.id,
        "closedReason": "저작권 문제가 있는 영상 포함",
    }
    assert (excel.status, excel.closed_by_user_id) == ("OPERATOR_HIDDEN", operator.id)
    assert excel.closed_reason == "저작권 문제가 있는 영상 포함"
    assert excel.closed_at == closed_at


def test_unpublished_course_moves_to_hidden_list(client, operator_headers, courses):
    unpublish(client, operator_headers, courses["web"].id, {"reason": "유해 콘텐츠"})

    hidden = titles(list_courses(client, operator_headers, status="OPERATOR_HIDDEN"))

    assert hidden == ["웹 개발 입문", "보고서 작성법"]


def test_reason_of_500_characters_is_accepted(client, operator_headers, courses):
    response = unpublish(client, operator_headers, courses["excel"].id, {"reason": "가" * 500})

    assert response.status_code == 200


@pytest.mark.parametrize("key", ["report", "draft", "editing"])
def test_only_published_course_can_be_unpublished(client, operator_headers, courses, key):
    course = courses[key]
    status_before = course.status

    response = unpublish(client, operator_headers, course.id, {"reason": "문제 있음"})

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "NOT_PUBLISHED",
        "message": "현재 공개 중인(PUBLISHED) 강좌만 비공개 처리할 수 있습니다.",
    }
    assert course.status == status_before


@pytest.mark.parametrize(
    "body", [{}, {"reason": None}, {"reason": ""}, {"reason": "   "}, {"reason": "가" * 501}]
)
def test_invalid_reason_is_rejected(client, operator_headers, courses, body):
    excel = courses["excel"]

    response = unpublish(client, operator_headers, excel.id, body)

    assert response.status_code == 400
    error = response.json()["error"]
    assert (error["code"], error["message"]) == ("VALIDATION_ERROR", REASON_MESSAGE)
    assert excel.status == "PUBLISHED"


def test_unpublish_unknown_course_is_not_found(client, operator_headers):
    response = unpublish(client, operator_headers, 999999, {"reason": "문제 있음"})

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "존재하지 않는 강좌입니다."


# ---------- 권한 ----------

PROTECTED_REQUESTS = [
    ("get", COURSES_URL),
    ("get", f"{COURSES_URL}/1"),
    ("post", f"{COURSES_URL}/1/unpublish"),
]


@pytest.mark.parametrize(("method", "url"), PROTECTED_REQUESTS)
@pytest.mark.parametrize("email", ["learner2@baeugo.test", "instructor1@baeugo.test"])
def test_members_are_forbidden(client, get_user, method, url, email):
    response = client.request(
        method, url, json={"reason": "문제 있음"}, headers=auth_header(get_user(email).id)
    )

    assert response.status_code == 403
    assert response.json()["error"] == {"code": "FORBIDDEN", "message": "운영자 권한이 필요합니다."}


@pytest.mark.parametrize(("method", "url"), PROTECTED_REQUESTS)
def test_login_is_required(client, method, url):
    response = client.request(method, url, json={"reason": "문제 있음"})

    assert response.status_code == 401
