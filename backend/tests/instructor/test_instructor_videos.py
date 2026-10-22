import io
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course, Section, Video


def test_b6_videos_crud_and_replacement(client: TestClient, db_session: Session):
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

    # [실패 2] 잘못된 videoType 전달 -> 400 VALIDATION_ERROR
    res_bad_type = client.post(
        "/api/v1/instructor/videos",
        data={"courseId": course.id, "videoType": "INTRO", "title": "영상"},
        files={"file": ("v.mp4", io.BytesIO(b"dummy"), "video/mp4")},
    )
    assert res_bad_type.status_code == 400

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

    # [성공 4] PATCH 메타데이터만 수정 -> 동일 videoId 유지
    res_patch_meta = client.patch(
        f"/api/v1/instructor/videos/{lesson_id}",
        data={"title": "1강 개요 수정본", "sortOrder": 5},
    )
    assert res_patch_meta.status_code == 200
    assert res_patch_meta.json()["data"]["title"] == "1강 개요 수정본"
    assert res_patch_meta.json()["data"]["videoId"] == lesson_id

    # [성공 5] PATCH 파일 교체 -> 기존 영상 비활성화 및 신규 videoId 발급 규칙 검증!
    new_vid_file = io.BytesIO(b"replacement video data")
    res_replace = client.patch(
        f"/api/v1/instructor/videos/{lesson_id}",
        files={"file": ("new_lesson.mp4", new_vid_file, "video/mp4")},
    )
    assert res_replace.status_code == 200
    new_vid_id = res_replace.json()["data"]["videoId"]
    assert new_vid_id != lesson_id
    db_session.expire_all()
    assert db_session.get(Video, lesson_id).is_active is False
    assert db_session.get(Video, new_vid_id).is_active is True

    # [성공 6] DELETE 영상 비활성화
    res_del_vid = client.delete(f"/api/v1/instructor/videos/{new_vid_id}")
    assert res_del_vid.status_code == 200
    assert res_del_vid.json()["data"]["isActive"] is False

    # [실패 3] PUBLISHED 강좌에 영상 업로드/수정/삭제 시도 -> 409 CONFLICT
    pub_course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="공개 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="PUBLISHED",
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(pub_course)
    db_session.flush()

    pub_vid = Video(
        course_id=pub_course.id,
        video_type="OT",
        title="영상",
        duration_seconds=60,
        video_storage_key="test/pub_v.mp4",
        is_active=True,
    )
    db_session.add(pub_vid)
    db_session.commit()

    res_pub_add = client.post(
        "/api/v1/instructor/videos",
        data={"courseId": pub_course.id, "videoType": "OT", "title": "추가 시도"},
        files={"file": ("v.mp4", io.BytesIO(b"dummy"), "video/mp4")},
    )
    assert res_pub_add.status_code == 409

    res_pub_patch = client.patch(
        f"/api/v1/instructor/videos/{pub_vid.id}",
        data={"title": "수정 시도"},
    )
    assert res_pub_patch.status_code == 409

    res_pub_del = client.delete(f"/api/v1/instructor/videos/{pub_vid.id}")
    assert res_pub_del.status_code == 409


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

    # [실패 1] PUT endMs < startMs 유효성 검증 실패 -> 400 VALIDATION_ERROR
    res_bad1 = client.put(
        f"/api/v1/instructor/videos/{video.id}/script",
        json={"segments": [{"seq": 1, "startMs": 5000, "endMs": 3000, "content": "오류"}]},
    )
    assert res_bad1.status_code == 400

    # [실패 2] PUT endMs == startMs (동일 시점) -> 400 VALIDATION_ERROR
    res_bad2 = client.put(
        f"/api/v1/instructor/videos/{video.id}/script",
        json={"segments": [{"seq": 1, "startMs": 3000, "endMs": 3000, "content": "오류"}]},
    )
    assert res_bad2.status_code == 400

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

    # [실패 3] PUBLISHED 강좌의 대본 수정 및 재생성 시도 -> 409 CONFLICT
    pub_course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="공개 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="PUBLISHED",
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(pub_course)
    db_session.flush()

    pub_vid = Video(
        course_id=pub_course.id,
        video_type="OT",
        title="영상",
        duration_seconds=60,
        video_storage_key="test/pub_script.mp4",
        is_active=True,
    )
    db_session.add(pub_vid)
    db_session.commit()

    res_pub_put = client.put(
        f"/api/v1/instructor/videos/{pub_vid.id}/script",
        json={"segments": valid_segments},
    )
    assert res_pub_put.status_code == 409

    res_pub_regen = client.post(f"/api/v1/instructor/videos/{pub_vid.id}/script/regenerate")
    assert res_pub_regen.status_code == 409
