"""운영자가 내린 강좌(OPERATOR_HIDDEN)도 강사가 내용을 고칠 수 있는지 확인한다.

공개 전환은 계속 운영자만 할 수 있다(OP-6). 강사의 B-4는 그대로 막힌다.
"""

import io
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.models import User
from app.services.processing import inputs
from app.services.processing.engine.errors import ProcessingError
from tests.factories import make_course, make_quiz, make_section, make_video

INSTRUCTOR_ID = 6  # InstructorTestClient가 기본으로 쓰는 강사
CLOSED_AT = datetime(2026, 11, 5, 3, 0, tzinfo=UTC)

SECTION_MESSAGE = "DRAFT 또는 EDITING 상태인 강좌에서만 섹션을 편집할 수 있습니다."
VIDEO_MESSAGE = "DRAFT 또는 EDITING 상태인 강좌에서만 영상을 편집할 수 있습니다."
SCRIPT_MESSAGE = "DRAFT 또는 EDITING 상태인 강좌에서만 대본을 수정하거나 재생성할 수 있습니다."
QUIZ_MESSAGE = "DRAFT 또는 EDITING 상태인 강좌에서만 퀴즈를 수정하거나 재생성할 수 있습니다."
COURSE_MESSAGE = (
    "수정은 DRAFT·EDITING 상태에서만 가능하며, 삭제는 공개된 적 없는 DRAFT 강좌만 가능합니다."
)
SEGMENTS = [{"seq": 1, "startMs": 0, "endMs": 3000, "content": "고친 대본"}]
QUESTIONS = [
    {
        "content": f"문제 {number}",
        "choices": ["1", "2", "3", "4"],
        "correctChoiceNo": 1,
        "explanation": "해설",
    }
    for number in (1, 2, 3)
]


@pytest.fixture(autouse=True)
def media_in_tmp(tmp_path, monkeypatch):
    # 영상 업로드 테스트가 개발용 media_data에 파일을 남기지 않게 한다.
    monkeypatch.setattr("app.services.storage.get_media_dir", lambda: tmp_path)


def make_case(db: Session, status: str) -> dict:
    course = make_course(db, db.get(User, INSTRUCTOR_ID), status=status)
    if status == "OPERATOR_HIDDEN":
        course.closed_by_user_id = 1
        course.closed_at = CLOSED_AT
        course.closed_reason = "저작권 문제가 있는 영상 포함"
    ot = make_video(db, course, video_type="OT", title="ot", sort_order=0)
    section = make_section(db, course)
    lesson = make_video(db, course, section=section, title="lesson")
    make_quiz(db, course, section=section, question_count=3)
    make_quiz(db, course, question_count=3)
    db.commit()
    return {"course": course, "ot": ot, "section": section, "lesson": lesson}


def edit_requests(case: dict) -> dict:
    """열어 준 12곳 중 API 11곳. 처리 작업(inputs.py)은 아래에서 따로 확인한다."""
    course_id, section_id = case["course"].id, case["section"].id
    lesson_id, ot_id = case["lesson"].id, case["ot"].id
    upload = {
        "data": {
            "courseId": course_id,
            "videoType": "LESSON",
            "sectionId": section_id,
            "title": "2강",
        },
        "files": {"file": ("new.mp4", io.BytesIO(b"dummy"), "video/mp4")},
    }
    return {
        "course_patch": (
            "patch",
            f"/courses/{course_id}",
            {"data": {"title": "고친 제목"}},
            200,
            COURSE_MESSAGE,
        ),
        "section_create": (
            "post",
            "/sections",
            {"json": {"courseId": course_id, "title": "섹션 2"}},
            201,
            SECTION_MESSAGE,
        ),
        "section_patch": (
            "patch",
            f"/sections/{section_id}",
            {"json": {"title": "고친 섹션"}},
            200,
            SECTION_MESSAGE,
        ),
        "section_delete": ("delete", f"/sections/{section_id}", {}, 200, SECTION_MESSAGE),
        "video_upload": ("post", "/videos", upload, 201, VIDEO_MESSAGE),
        "video_patch": (
            "patch",
            f"/videos/{lesson_id}",
            {"data": {"title": "고친 강의"}},
            200,
            VIDEO_MESSAGE,
        ),
        "video_delete": ("delete", f"/videos/{ot_id}", {}, 200, VIDEO_MESSAGE),
        "script_put": (
            "put",
            f"/videos/{lesson_id}/script",
            {"json": {"segments": SEGMENTS}},
            200,
            SCRIPT_MESSAGE,
        ),
        "script_regenerate": (
            "post",
            f"/videos/{lesson_id}/script/regenerate",
            {},
            200,
            SCRIPT_MESSAGE,
        ),
        "quiz_put": (
            "put",
            f"/courses/{course_id}/quiz",
            {"json": {"questions": QUESTIONS}},
            200,
            QUIZ_MESSAGE,
        ),
        "quiz_regenerate": (
            "post",
            f"/courses/{course_id}/quiz/regenerate?sectionId={section_id}",
            {},
            200,
            QUIZ_MESSAGE,
        ),
    }


EDIT_NAMES = [
    "course_patch",
    "section_create",
    "section_patch",
    "section_delete",
    "video_upload",
    "video_patch",
    "video_delete",
    "script_put",
    "script_regenerate",
    "quiz_put",
    "quiz_regenerate",
]


def call(client, case: dict, name: str):
    method, path, kwargs, _, _ = edit_requests(case)[name]
    return client.request(method, f"/api/v1/instructor{path}", **kwargs)


@pytest.mark.parametrize("name", EDIT_NAMES)
def test_instructor_can_edit_operator_hidden_course(client, db, name):
    case = make_case(db, "OPERATOR_HIDDEN")
    course = case["course"]

    response = call(client, case, name)

    assert response.status_code == edit_requests(case)[name][3], response.text
    db.refresh(course)
    # 고쳐도 운영자가 다시 공개하기 전까지 비공개 상태와 운영자 기록은 그대로다.
    assert course.status == "OPERATOR_HIDDEN"
    assert (course.closed_by_user_id, course.closed_at) == (1, CLOSED_AT)
    assert course.closed_reason == "저작권 문제가 있는 영상 포함"


@pytest.mark.parametrize("name", EDIT_NAMES)
def test_published_course_still_cannot_be_edited(client, db, name):
    case = make_case(db, "PUBLISHED")

    response = call(client, case, name)

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "CONFLICT",
        "message": edit_requests(case)[name][4],
    }


@pytest.mark.parametrize("target", ["PUBLISHED", "EDITING"])
def test_instructor_still_cannot_change_status_of_hidden_course(client, db, target):
    course = make_case(db, "OPERATOR_HIDDEN")["course"]

    response = client.put(f"/api/v1/instructor/courses/{course.id}/status", json={"status": target})

    assert response.status_code == 409
    db.refresh(course)
    assert course.status == "OPERATOR_HIDDEN"


@pytest.mark.parametrize("status", ["DRAFT", "EDITING", "OPERATOR_HIDDEN"])
def test_processing_accepts_editable_course(db, status):
    course = make_case(db, status)["course"]

    assert inputs.editable_course(db, course.id).id == course.id


def test_processing_rejects_published_course(db):
    course = make_case(db, "PUBLISHED")["course"]

    with pytest.raises(ProcessingError) as error:
        inputs.editable_course(db, course.id)

    assert error.value.code == "COURSE_NOT_EDITABLE"
