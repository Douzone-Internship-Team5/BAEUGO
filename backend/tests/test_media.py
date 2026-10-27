from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import JWT_ALGORITHM
from app.models import (
    QuizAttempt,
    Role,
    UserRole,
    VideoProgress,
)
from tests.conftest import auth_header
from tests.factories import enroll, make_course, make_quiz, make_section, make_video


@pytest.fixture
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "media_data"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.storage.get_media_dir", lambda: root)
    monkeypatch.setattr("app.services.media.get_media_dir", lambda: root)
    return root


def write_media_file(media_root: Path, storage_key: str, content: bytes) -> Path:
    target = media_root / storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def test_m_openapi_contract_matches_spec(client: TestClient):
    schema = client.get("/openapi.json").json()
    assert "/api/v1/videos/{id}/stream" in schema["paths"]
    assert "/api/v1/media/{storageKey}" in schema["paths"]

    m1_get = schema["paths"]["/api/v1/videos/{id}/stream"]["get"]
    m1_params = m1_get["parameters"]
    assert any(p["name"] == "id" and p["in"] == "path" for p in m1_params)
    assert m1_get.get("security") == [{"HTTPBearer": []}]

    m2_get = schema["paths"]["/api/v1/media/{storageKey}"]["get"]
    m2_params = m2_get["parameters"]
    assert any(p["name"] == "storageKey" and p["in"] == "path" for p in m2_params)
    assert "security" not in m2_get


def test_m1_ot_video_streaming_full_and_ranges(
    client: TestClient, db: Session, get_user, media_root: Path
):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")
    course = make_course(db, instructor, status="PUBLISHED")
    ot_video = make_video(
        db,
        course,
        video_type="OT",
        title="무료 OT",
        sort_order=0,
    )
    video_bytes = bytes(range(256)) * 4  # 1024 bytes
    write_media_file(media_root, ot_video.video_storage_key, video_bytes)
    db.commit()

    url = f"/api/v1/videos/{ot_video.id}/stream"

    # 1. 비로그인 상태에서 전체 스트리밍 (Range 헤더 없음 -> 200 OK)
    res_full = client.get(url)
    assert res_full.status_code == 200
    assert res_full.headers["content-type"] == "video/mp4"
    assert res_full.headers["accept-ranges"] == "bytes"
    assert res_full.headers["content-length"] == str(len(video_bytes))
    assert res_full.content == video_bytes

    # 2. 로그인한 수강자 모드에서도 정상 스트리밍
    res_learner = client.get(url, headers=auth_header(learner.id))
    assert res_learner.status_code == 200
    assert res_learner.content == video_bytes

    # 3. Range: bytes=0- 요청 -> 206 Partial Content 전체 구간
    res_range_all = client.get(url, headers={"Range": "bytes=0-"})
    assert res_range_all.status_code == 206
    assert res_range_all.headers["content-type"] == "video/mp4"
    assert res_range_all.headers["accept-ranges"] == "bytes"
    assert (
        res_range_all.headers["content-range"]
        == f"bytes 0-{len(video_bytes) - 1}/{len(video_bytes)}"
    )
    assert res_range_all.headers["content-length"] == str(len(video_bytes))
    assert res_range_all.content == video_bytes

    # 4. 부분 Range 요청 (bytes=10-49)
    res_partial = client.get(url, headers={"Range": "bytes=10-49"})
    assert res_partial.status_code == 206
    assert res_partial.headers["content-range"] == f"bytes 10-49/{len(video_bytes)}"
    assert res_partial.headers["content-length"] == "40"
    assert res_partial.content == video_bytes[10:50]

    # 5. 중간 위치부터 끝까지 Range 요청 (bytes=500-)
    res_from_mid = client.get(url, headers={"Range": "bytes=500-"})
    assert res_from_mid.status_code == 206
    assert (
        res_from_mid.headers["content-range"]
        == f"bytes 500-{len(video_bytes) - 1}/{len(video_bytes)}"
    )
    assert res_from_mid.headers["content-length"] == str(len(video_bytes) - 500)
    assert res_from_mid.content == video_bytes[500:]

    # 6. Suffix Range 요청 (bytes=-100: 마지막 100바이트)
    res_suffix = client.get(url, headers={"Range": "bytes=-100"})
    assert res_suffix.status_code == 206
    assert res_suffix.headers["content-range"] == (
        f"bytes {len(video_bytes) - 100}-{len(video_bytes) - 1}/{len(video_bytes)}"
    )
    assert res_suffix.headers["content-length"] == "100"
    assert res_suffix.content == video_bytes[-100:]

    # 7. 파일 크기를 초과하는 종료 위치 요청 시 파일 끝으로 클램핑 (bytes=900-999999)
    res_clamped = client.get(url, headers={"Range": "bytes=900-999999"})
    assert res_clamped.status_code == 206
    assert (
        res_clamped.headers["content-range"]
        == f"bytes 900-{len(video_bytes) - 1}/{len(video_bytes)}"
    )
    assert res_clamped.content == video_bytes[900:]


def test_m1_large_file_multi_chunk_streaming(
    client: TestClient, db: Session, get_user, media_root: Path
):
    instructor = get_user("instructor1@baeugo.test")
    course = make_course(db, instructor, status="PUBLISHED")
    ot_video = make_video(db, course, video_type="OT", title="대용량 OT", sort_order=0)
    # 64KB 청크 경계를 넘는 150KB 데이터
    large_bytes = (b"BAEUGO_STREAM_BLOCK_" * 8000)[:153600]
    write_media_file(media_root, ot_video.video_storage_key, large_bytes)
    db.commit()

    url = f"/api/v1/videos/{ot_video.id}/stream"
    res_full = client.get(url)
    assert res_full.status_code == 200
    assert res_full.content == large_bytes

    res_cross_chunk = client.get(url, headers={"Range": "bytes=60000-140000"})
    assert res_cross_chunk.status_code == 206
    assert res_cross_chunk.headers["content-length"] == "80001"
    assert res_cross_chunk.content == large_bytes[60000:140001]


def test_m1_ot_video_access_control_by_course_status(
    client: TestClient, db: Session, get_user, media_root: Path
):
    owner = get_user("instructor1@baeugo.test")
    other_instructor = get_user("instructor2@baeugo.test")
    enrolled_learner = get_user("learner2@baeugo.test")
    unenrolled_learner = get_user("learner1@baeugo.test")
    operator = get_user("admin@baeugo.test")

    # learner1에게 LEARNER 역할 부여
    db.add(UserRole(user_id=unenrolled_learner.id, role=Role.LEARNER))
    unenrolled_learner.active_mode = Role.LEARNER

    draft_course = make_course(db, owner, status="DRAFT")
    editing_course = make_course(db, owner, status="EDITING")
    hidden_course = make_course(db, owner, status="OPERATOR_HIDDEN")

    enroll(db, enrolled_learner, editing_course)
    enroll(db, enrolled_learner, hidden_course)

    ot_draft = make_video(db, draft_course, video_type="OT", title="DRAFT OT", sort_order=0)
    ot_editing = make_video(db, editing_course, video_type="OT", title="EDITING OT", sort_order=0)
    ot_hidden = make_video(db, hidden_course, video_type="OT", title="HIDDEN OT", sort_order=0)

    for v in (ot_draft, ot_editing, ot_hidden):
        write_media_file(media_root, v.video_storage_key, b"ot-video-bytes")
    db.commit()

    # 1. DRAFT 강좌의 OT: 비로그인, 일반 수강자, 타 강사는 403 / 소유 강사 및 운영자는 200
    for headers in (None, auth_header(enrolled_learner.id), auth_header(other_instructor.id)):
        res = client.get(f"/api/v1/videos/{ot_draft.id}/stream", headers=headers)
        assert res.status_code == 403
        assert res.json()["error"] == {
            "code": "FORBIDDEN",
            "message": "영상 시청 권한이 없거나 잠금 상태입니다.",
        }

    for headers in (auth_header(owner.id), auth_header(operator.id)):
        res = client.get(f"/api/v1/videos/{ot_draft.id}/stream", headers=headers)
        assert res.status_code == 200

    # 2. EDITING·OPERATOR_HIDDEN 강좌 OT: 미수강자 403 / 기수강자·소유 강사·운영자 200
    for ot in (ot_editing, ot_hidden):
        for headers in (
            None,
            auth_header(unenrolled_learner.id),
            auth_header(other_instructor.id),
        ):
            res = client.get(f"/api/v1/videos/{ot.id}/stream", headers=headers)
            assert res.status_code == 403
            assert res.json()["error"]["code"] == "FORBIDDEN"

        for headers in (
            auth_header(enrolled_learner.id),
            auth_header(owner.id),
            auth_header(operator.id),
        ):
            res = client.get(f"/api/v1/videos/{ot.id}/stream", headers=headers)
            assert res.status_code == 200
            assert res.content == b"ot-video-bytes"


def test_m1_lesson_sequential_lock_and_progression(
    client: TestClient, db: Session, get_user, media_root: Path
):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")

    course = make_course(db, instructor, status="PUBLISHED")
    sec1 = make_section(db, course, title="섹션 1", sort_order=1)
    sec2 = make_section(db, course, title="섹션 2", sort_order=2)

    v1 = make_video(db, course, section=sec1, video_type="LESSON", title="1강", sort_order=1)
    v2 = make_video(db, course, section=sec1, video_type="LESSON", title="2강", sort_order=2)
    q1 = make_quiz(db, course, section=sec1, question_count=3)
    v3 = make_video(db, course, section=sec2, video_type="LESSON", title="3강", sort_order=1)

    for v in (v1, v2, v3):
        write_media_file(media_root, v.video_storage_key, f"video-{v.id}".encode())
    db.commit()

    # 1. 비로그인 요청 시 401 UNAUTHORIZED
    res_unauth = client.get(f"/api/v1/videos/{v1.id}/stream")
    assert res_unauth.status_code == 401
    assert res_unauth.json()["error"] == {
        "code": "UNAUTHORIZED",
        "message": "로그인이 필요합니다.",
    }

    # 2. 미수강 상태에서 요청 시 403 FORBIDDEN
    res_unenrolled = client.get(f"/api/v1/videos/{v1.id}/stream", headers=auth_header(learner.id))
    assert res_unenrolled.status_code == 403
    assert res_unenrolled.json()["error"] == {
        "code": "FORBIDDEN",
        "message": "영상 시청 권한이 없거나 잠금 상태입니다.",
    }

    # 3. 수강 등록 직후: 첫 학습 영상(v1)만 열리고 v2, v3는 잠금(403)
    enrollment = enroll(db, learner, course)
    db.commit()

    assert (
        client.get(f"/api/v1/videos/{v1.id}/stream", headers=auth_header(learner.id)).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/videos/{v2.id}/stream", headers=auth_header(learner.id)).status_code
        == 403
    )
    assert (
        client.get(f"/api/v1/videos/{v3.id}/stream", headers=auth_header(learner.id)).status_code
        == 403
    )

    # 4. v1 진도가 저장되었으나 아직 90% 미달(is_completed=False)이면 v2는 여전히 잠금
    vp1 = VideoProgress(
        enrollment_id=enrollment.id,
        video_id=v1.id,
        watched_ranges=[[0, 100]],
        watched_seconds=100,
        last_position_seconds=100,
        is_completed=False,
    )
    db.add(vp1)
    db.commit()

    assert (
        client.get(f"/api/v1/videos/{v2.id}/stream", headers=auth_header(learner.id)).status_code
        == 403
    )

    # 5. v1 완료(is_completed=True) -> v2 잠금 해제, v3는 여전히 잠금
    vp1.is_completed = True
    vp1.completed_at = datetime.now(UTC)
    db.commit()

    assert (
        client.get(f"/api/v1/videos/{v2.id}/stream", headers=auth_header(learner.id)).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/videos/{v3.id}/stream", headers=auth_header(learner.id)).status_code
        == 403
    )

    # 6. v2 완료 후에도 섹션 1의 단위 퀴즈(q1)가 미제출(IN_PROGRESS/CANCELLED)이면 v3는 잠금
    vp2 = VideoProgress(
        enrollment_id=enrollment.id,
        video_id=v2.id,
        watched_ranges=[[0, 360]],
        watched_seconds=360,
        last_position_seconds=360,
        is_completed=True,
        completed_at=datetime.now(UTC),
    )
    attempt_in_progress = QuizAttempt(
        enrollment_id=enrollment.id,
        quiz_id=q1.id,
        attempt_type="UNIT",
        attempt_no=1,
        status="IN_PROGRESS",
    )
    db.add_all([vp2, attempt_in_progress])
    db.commit()

    assert (
        client.get(f"/api/v1/videos/{v3.id}/stream", headers=auth_header(learner.id)).status_code
        == 403
    )

    # 7. 단위 퀴즈(q1) 제출 완료(SUBMITTED) -> 섹션 2의 v3 잠금 해제
    attempt_in_progress.status = "SUBMITTED"
    attempt_in_progress.submitted_at = datetime.now(UTC)
    db.commit()

    res_v3 = client.get(f"/api/v1/videos/{v3.id}/stream", headers=auth_header(learner.id))
    assert res_v3.status_code == 200
    assert res_v3.content == f"video-{v3.id}".encode()


def test_m1_three_lock_exemption_rules(client: TestClient, db: Session, get_user, media_root: Path):
    owner = get_user("instructor1@baeugo.test")
    operator = get_user("admin@baeugo.test")
    learner = get_user("learner2@baeugo.test")

    course = make_course(db, owner, status="PUBLISHED")
    sec1 = make_section(db, course, title="섹션 1", sort_order=1)
    v1 = make_video(db, course, section=sec1, video_type="LESSON", title="1강", sort_order=1)
    v2 = make_video(db, course, section=sec1, video_type="LESSON", title="2강", sort_order=2)
    v3 = make_video(db, course, section=sec1, video_type="LESSON", title="3강", sort_order=3)

    for v in (v1, v2, v3):
        write_media_file(media_root, v.video_storage_key, f"data-{v.id}".encode())

    enrollment = enroll(db, learner, course)
    # 예외 ①: v1이 미완료 상태여도 v2 자체가 이미 is_completed=True이면 v2 스트리밍 허용
    db.add(
        VideoProgress(
            enrollment_id=enrollment.id,
            video_id=v2.id,
            watched_ranges=[[0, 360]],
            watched_seconds=360,
            last_position_seconds=360,
            is_completed=True,
            completed_at=datetime.now(UTC),
        )
    )
    db.commit()

    assert (
        client.get(f"/api/v1/videos/{v2.id}/stream", headers=auth_header(learner.id)).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/videos/{v3.id}/stream", headers=auth_header(learner.id)).status_code
        == 403
    )

    # 예외 ②: 최초 수료 이력(first_completed_at IS NOT NULL) 보유 수강생은
    # 현재 status가 IN_PROGRESS이고 앞선 영상이 미완료여도 모든 영상(v3 포함) 스트리밍 허용
    enrollment.first_completed_at = datetime.now(UTC)
    enrollment.status = "IN_PROGRESS"
    db.commit()

    assert (
        client.get(f"/api/v1/videos/{v3.id}/stream", headers=auth_header(learner.id)).status_code
        == 200
    )

    # 예외 ③: 강좌 소유 강사 및 운영자는 수강 여부/선행 완료/강좌 상태(DRAFT 포함)와 무관하게 허용
    course.status = "DRAFT"
    db.commit()

    for user in (owner, operator):
        res = client.get(f"/api/v1/videos/{v3.id}/stream", headers=auth_header(user.id))
        assert res.status_code == 200
        assert res.content == f"data-{v3.id}".encode()


def test_m1_inactive_curriculum_items_do_not_block_lock_and_return_404(
    client: TestClient, db: Session, get_user, media_root: Path
):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")

    course = make_course(db, instructor, status="PUBLISHED")
    inactive_sec = make_section(db, course, title="비활성 섹션", sort_order=1, is_active=False)
    active_sec = make_section(db, course, title="활성 섹션", sort_order=2, is_active=True)

    v_in_inactive_sec = make_video(
        db, course, section=inactive_sec, video_type="LESSON", title="비활성섹션영상", sort_order=1
    )
    v_inactive = make_video(
        db,
        course,
        section=active_sec,
        video_type="LESSON",
        title="비활성영상",
        sort_order=1,
        is_active=False,
    )
    make_quiz(db, course, section=inactive_sec, is_active=True)
    v_first_active = make_video(
        db,
        course,
        section=active_sec,
        video_type="LESSON",
        title="실제 첫 활성 영상",
        sort_order=2,
        is_active=True,
    )

    for v in (v_in_inactive_sec, v_inactive, v_first_active):
        write_media_file(media_root, v.video_storage_key, b"stream-data")

    enroll(db, learner, course)
    db.commit()

    # 비활성 영상 및 비활성 섹션 소속 영상은 소유 강사가 조회하더라도 404 NOT_FOUND
    for vid in (v_inactive.id, v_in_inactive_sec.id):
        res = client.get(f"/api/v1/videos/{vid}/stream", headers=auth_header(instructor.id))
        assert res.status_code == 404
        assert res.json()["error"] == {
            "code": "NOT_FOUND",
            "message": "영상 파일을 찾을 수 없습니다.",
        }

    # 비활성 섹션/비활성 영상 뒤의 첫 활성 영상(v_first_active)은 즉시 열림(200 OK)
    res_active = client.get(
        f"/api/v1/videos/{v_first_active.id}/stream", headers=auth_header(learner.id)
    )
    assert res_active.status_code == 200
    assert res_active.content == b"stream-data"


def test_m1_auth_errors_not_found_and_range_416(
    client: TestClient, db: Session, get_user, media_root: Path
):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")

    course = make_course(db, instructor, status="PUBLISHED")
    ot_video = make_video(db, course, video_type="OT", title="OT", sort_order=0)
    missing_file_video = make_video(db, course, video_type="OT", title="파일없는OT", sort_order=1)
    write_media_file(media_root, ot_video.video_storage_key, b"0123456789")
    db.commit()

    # 1. 잘못된 토큰, 만료된 토큰, 비-Bearer 스킴 -> 401 UNAUTHORIZED
    expired_token = jwt.encode(
        {"sub": str(learner.id), "exp": datetime.now(UTC) - timedelta(minutes=5)},
        get_settings().jwt_secret_key,
        JWT_ALGORITHM,
    )
    for bad_auth in (
        "Bearer invalid.jwt.token",
        f"Bearer {expired_token}",
        "Basic dXNlcjpwYXNz",
        "Bearer   ",
    ):
        res = client.get(
            f"/api/v1/videos/{ot_video.id}/stream",
            headers={"Authorization": bad_auth},
        )
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "UNAUTHORIZED"

    # 2. 존재하지 않는 영상 ID -> 404 NOT_FOUND
    res_no_id = client.get("/api/v1/videos/999999/stream")
    assert res_no_id.status_code == 404
    assert res_no_id.json()["error"] == {
        "code": "NOT_FOUND",
        "message": "영상 파일을 찾을 수 없습니다.",
    }

    # 3. DB에는 있으나 디스크에 실제 파일이 없는 경우 -> 404 NOT_FOUND
    res_no_file = client.get(f"/api/v1/videos/{missing_file_video.id}/stream")
    assert res_no_file.status_code == 404
    assert res_no_file.json()["error"] == {
        "code": "NOT_FOUND",
        "message": "영상 파일을 찾을 수 없습니다.",
    }

    # 4. 잘못된 Range 헤더 -> 416 Range Not Satisfiable
    for bad_range in (
        "bytes=10-99",
        "bytes=8-3",
        "bytes=-0",
        "bytes=-",
        "bytes=abc-def",
        "bytes=+0-5",
        "bytes=0-+5",
        "bytes=0_0-5",
        "items=0-5",
        "bytes=0-2, 4-6",
    ):
        res_416 = client.get(
            f"/api/v1/videos/{ot_video.id}/stream",
            headers={"Range": bad_range},
        )
        assert res_416.status_code == 416
        assert res_416.headers["content-range"] == "bytes */10"

    # 4-1. 1바이트 단일 구간(bytes=0-0, bytes=9-9)은 정상 206 반환
    res_single = client.get(
        f"/api/v1/videos/{ot_video.id}/stream",
        headers={"Range": "bytes=0-0"},
    )
    assert res_single.status_code == 206
    assert res_single.headers["content-range"] == "bytes 0-0/10"
    assert res_single.headers["content-length"] == "1"
    assert res_single.content == b"0"

    # 5. 정수가 아닌 ID -> 400 VALIDATION_ERROR
    res_bad_id = client.get("/api/v1/videos/not-an-int/stream")
    assert res_bad_id.status_code == 400
    assert res_bad_id.json()["error"]["code"] == "VALIDATION_ERROR"


def test_m1_deactivated_or_deleted_user_and_corrupted_storage_key(
    client: TestClient, db: Session, get_user, media_root: Path
):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")

    course = make_course(db, instructor, status="PUBLISHED")
    ot_video = make_video(db, course, video_type="OT", title="OT", sort_order=0)
    bad_key_video = make_video(db, course, video_type="OT", title="잘못된키OT", sort_order=1)
    bad_key_video.video_storage_key = "courses/../secret.mp4"
    write_media_file(media_root, ot_video.video_storage_key, b"0123456789")
    db.commit()

    # 1. 존재하지 않는 사용자 ID 토큰 -> 401
    res_ghost = client.get(
        f"/api/v1/videos/{ot_video.id}/stream",
        headers=auth_header(999999),
    )
    assert res_ghost.status_code == 401

    # 2. 비활성화된 계정(is_active=False) 토큰 -> 401
    learner.is_active = False
    db.commit()
    res_deactivated = client.get(
        f"/api/v1/videos/{ot_video.id}/stream",
        headers=auth_header(learner.id),
    )
    assert res_deactivated.status_code == 401

    # 3. 탈퇴 처리된 계정(deleted_at != None) 토큰 -> 401
    learner.is_active = True
    learner.deleted_at = datetime.now(UTC)
    db.commit()
    res_deleted = client.get(
        f"/api/v1/videos/{ot_video.id}/stream",
        headers=auth_header(learner.id),
    )
    assert res_deleted.status_code == 401

    # 4. DB의 video_storage_key에 경로 탐색(..)이 포함된 경우 -> 404
    res_bad_key = client.get(f"/api/v1/videos/{bad_key_video.id}/stream")
    assert res_bad_key.status_code == 404
    assert res_bad_key.json()["error"]["code"] == "NOT_FOUND"


def test_m1_dual_role_instructor_mode_cannot_stream_other_course_lesson_as_learner(
    client: TestClient, db: Session, get_user, media_root: Path
):
    owner = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")

    course = make_course(db, owner, status="PUBLISHED")
    sec = make_section(db, course, title="섹션 1", sort_order=1)
    lesson = make_video(db, course, section=sec, video_type="LESSON", title="1강", sort_order=1)
    write_media_file(media_root, lesson.video_storage_key, b"lesson-bytes")

    enroll(db, learner, course)
    # learner2에게 INSTRUCTOR 역할을 추가하고 활성 모드를 INSTRUCTOR로 전환
    db.add(UserRole(user_id=learner.id, role=Role.INSTRUCTOR))
    learner.active_mode = Role.INSTRUCTOR
    db.commit()

    # 타인 강좌의 LESSON 영상은 강사 모드 상태에서 수강생 자격으로 열람 불가(403)
    res_inst_mode = client.get(
        f"/api/v1/videos/{lesson.id}/stream", headers=auth_header(learner.id)
    )
    assert res_inst_mode.status_code == 403

    # 다시 수강자 모드(LEARNER)로 전환하면 정상 스트리밍(200)
    learner.active_mode = Role.LEARNER
    db.commit()

    res_learner_mode = client.get(
        f"/api/v1/videos/{lesson.id}/stream", headers=auth_header(learner.id)
    )
    assert res_learner_mode.status_code == 200
    assert res_learner_mode.content == b"lesson-bytes"


def test_m2_static_media_serving_and_security(client: TestClient, media_root: Path):
    jpg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF-FAKE-JPEG"
    png_bytes = b"\x89PNG\r\n\x1a\nFAKE-PNG-CONTENT"

    write_media_file(media_root, "courses/101/thumb.jpg", jpg_bytes)
    write_media_file(media_root, "courses/101/sections/201/thumb_501.jpeg", jpg_bytes)
    write_media_file(media_root, "profiles/2.png", png_bytes)
    write_media_file(media_root, "courses/101/ot.mp4", b"secret-video-data")

    # 1. JPG 및 JPEG 서빙 (인증 불필요, Content-Type: image/jpeg)
    res_jpg = client.get("/api/v1/media/courses/101/thumb.jpg")
    assert res_jpg.status_code == 200
    assert res_jpg.headers["content-type"] == "image/jpeg"
    assert res_jpg.content == jpg_bytes

    res_jpeg = client.get("/api/v1/media/courses/101/sections/201/thumb_501.jpeg")
    assert res_jpeg.status_code == 200
    assert res_jpeg.headers["content-type"] == "image/jpeg"
    assert res_jpeg.content == jpg_bytes

    # 2. PNG 서빙 (Content-Type: image/png)
    res_png = client.get("/api/v1/media/profiles/2.png")
    assert res_png.status_code == 200
    assert res_png.headers["content-type"] == "image/png"
    assert res_png.content == png_bytes

    # 3. 존재하지 않는 이미지 -> 404 NOT_FOUND ("이미지를 찾을 수 없습니다.")
    res_missing = client.get("/api/v1/media/courses/999/not_exists.png")
    assert res_missing.status_code == 404
    assert res_missing.json()["error"] == {
        "code": "NOT_FOUND",
        "message": "이미지를 찾을 수 없습니다.",
    }

    # 4. 경로 탐색(..), 현재 디렉토리(.), 빈 세그먼트(//), 역슬래시 공격 차단 -> 404 NOT_FOUND
    outside_file = media_root.parent / "secret.png"
    outside_file.write_bytes(png_bytes)

    for traversal_path in (
        "/api/v1/media/..%2Fsecret.png",
        "/api/v1/media/courses/..%2F..%2Fsecret.png",
        "/api/v1/media/courses%5C..%5C..%5Csecret.png",
        "/api/v1/media/..%252Fsecret.png",
        "/api/v1/media/courses/101/..%2Fthumb.jpg",
        "/api/v1/media/courses/%2e/101/thumb.jpg",
        "/api/v1/media/courses//101/thumb.jpg",
        "/api/v1/media/%20courses/101/thumb.jpg",
        "/api/v1/media/C:secret.png",
        "/api/v1/media/courses/101/thumb.jpg::$DATA",
    ):
        res_trav = client.get(traversal_path)
        assert res_trav.status_code == 404
        assert res_trav.json()["error"] == {
            "code": "NOT_FOUND",
            "message": "이미지를 찾을 수 없습니다.",
        }

    # 5. 이미지가 아닌 파일(.mp4 영상 등)을 M-2 경로로 우회 접근 시 404 차단
    res_mp4 = client.get("/api/v1/media/courses/101/ot.mp4")
    assert res_mp4.status_code == 404
    assert res_mp4.json()["error"] == {
        "code": "NOT_FOUND",
        "message": "이미지를 찾을 수 없습니다.",
    }

    # 6. 디렉토리 경로 또는 255자 초과 키 요청 시 404 차단
    res_dir = client.get("/api/v1/media/courses/101")
    assert res_dir.status_code == 404

    long_key = "courses/" + ("a" * 260) + ".png"
    res_long = client.get(f"/api/v1/media/{long_key}")
    assert res_long.status_code == 404


def test_m1_zero_byte_file_and_republished_curriculum_edge_cases(
    client: TestClient, db: Session, get_user, media_root: Path
):
    instructor = get_user("instructor1@baeugo.test")
    learner = get_user("learner2@baeugo.test")

    course = make_course(db, instructor, status="PUBLISHED")
    empty_ot = make_video(db, course, video_type="OT", title="빈OT", sort_order=0)
    write_media_file(media_root, empty_ot.video_storage_key, b"")

    sec1 = make_section(db, course, title="섹션 1", sort_order=1, is_active=True)
    sec2 = make_section(db, course, title="섹션 2", sort_order=2, is_active=True)

    v1 = make_video(db, course, section=sec1, video_type="LESSON", title="1강", sort_order=1)
    q1_old_inactive = make_quiz(db, course, section=sec1, is_active=False)
    q1_new_active = make_quiz(db, course, section=sec1, is_active=True)
    make_quiz(db, course, section=None, is_active=True)  # FINAL 퀴즈
    v2 = make_video(db, course, section=sec2, video_type="LESSON", title="2강", sort_order=1)

    for v in (v1, v2):
        write_media_file(media_root, v.video_storage_key, b"lesson-content")

    enrollment = enroll(db, learner, course)
    db.add(
        VideoProgress(
            enrollment_id=enrollment.id,
            video_id=v1.id,
            watched_ranges=[[0, 360]],
            watched_seconds=360,
            last_position_seconds=360,
            is_completed=True,
            completed_at=datetime.now(UTC),
        )
    )
    # 비활성 구 단위 퀴즈(q1_old_inactive)만 SUBMITTED 상태이고 신규 활성 단위 퀴즈는 미제출
    db.add(
        QuizAttempt(
            enrollment_id=enrollment.id,
            quiz_id=q1_old_inactive.id,
            attempt_type="UNIT",
            attempt_no=1,
            status="SUBMITTED",
            submitted_at=datetime.now(UTC),
        )
    )
    db.commit()

    # 1. 0바이트 영상 파일: Range 미지정 시 200 OK (0바이트), Range 지정 시 416 (bytes */0)
    res_empty_200 = client.get(f"/api/v1/videos/{empty_ot.id}/stream")
    assert res_empty_200.status_code == 200
    assert res_empty_200.headers["content-length"] == "0"
    assert res_empty_200.content == b""

    res_empty_416 = client.get(
        f"/api/v1/videos/{empty_ot.id}/stream", headers={"Range": "bytes=0-"}
    )
    assert res_empty_416.status_code == 416
    assert res_empty_416.headers["content-range"] == "bytes */0"

    # 2. 구 비활성 단위 퀴즈만 제출되고 신규 활성 단위 퀴즈가 미제출이면 v2는 잠금(403)
    res_v2_locked = client.get(f"/api/v1/videos/{v2.id}/stream", headers=auth_header(learner.id))
    assert res_v2_locked.status_code == 403

    # 3. 신규 활성 단위 퀴즈(q1_new_active) 제출 시 FINAL 퀴즈 미제출과 무관하게 v2 잠금 해제(200)
    db.add(
        QuizAttempt(
            enrollment_id=enrollment.id,
            quiz_id=q1_new_active.id,
            attempt_type="UNIT",
            attempt_no=1,
            status="SUBMITTED",
            submitted_at=datetime.now(UTC),
        )
    )
    db.commit()

    res_v2_unlocked = client.get(f"/api/v1/videos/{v2.id}/stream", headers=auth_header(learner.id))
    assert res_v2_unlocked.status_code == 200
    assert res_v2_unlocked.content == b"lesson-content"
