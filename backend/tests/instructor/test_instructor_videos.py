import io
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course, Section
from app.models.quiz import Quiz
from app.models.video import Video


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
    assert res_del_vid.json()["data"] == {
        "videoId": new_vid_id,
        "courseId": course.id,
        "isActive": False,
    }

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
        published_at=datetime.now(UTC),
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
        published_at=datetime.now(UTC),
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

    # [추가 검증] B-7: seq < 1, 중복 seq, startMs < 0, 빈 content 거부 및 seq 정렬 반환
    res_seq_zero = client.put(
        f"/api/v1/instructor/videos/{video.id}/script",
        json={"segments": [{"seq": 0, "startMs": 0, "endMs": 1000, "content": "오류"}]},
    )
    assert res_seq_zero.status_code == 400

    res_dup_seq = client.put(
        f"/api/v1/instructor/videos/{video.id}/script",
        json={
            "segments": [
                {"seq": 1, "startMs": 0, "endMs": 1000, "content": "첫번째"},
                {"seq": 1, "startMs": 1000, "endMs": 2000, "content": "중복 순번"},
            ]
        },
    )
    assert res_dup_seq.status_code == 400

    res_neg_start = client.put(
        f"/api/v1/instructor/videos/{video.id}/script",
        json={"segments": [{"seq": 1, "startMs": -100, "endMs": 1000, "content": "음수 시작"}]},
    )
    assert res_neg_start.status_code == 400

    res_empty_content = client.put(
        f"/api/v1/instructor/videos/{video.id}/script",
        json={"segments": [{"seq": 1, "startMs": 0, "endMs": 1000, "content": "   "}]},
    )
    assert res_empty_content.status_code == 400

    res_unsorted = client.put(
        f"/api/v1/instructor/videos/{video.id}/script",
        json={
            "segments": [
                {"seq": 2, "startMs": 1000, "endMs": 2000, "content": "두번째"},
                {"seq": 1, "startMs": 0, "endMs": 1000, "content": "첫번째"},
            ]
        },
    )
    assert res_unsorted.status_code == 200
    assert [s["seq"] for s in res_unsorted.json()["data"]["segments"]] == [1, 2]


def test_b6_cross_section_unit_quiz_auto_create_and_json_patch(
    client: TestClient, db_session: Session
):
    from sqlalchemy import select

    c1 = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="강좌 1",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="DRAFT",
    )
    c2 = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="강좌 2",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="DRAFT",
    )
    db_session.add_all([c1, c2])
    db_session.flush()

    sec1 = Section(course_id=c1.id, title="강좌1 섹션", sort_order=1, is_active=True)
    sec2 = Section(course_id=c2.id, title="강좌2 섹션", sort_order=1, is_active=True)
    db_session.add_all([sec1, sec2])
    db_session.commit()

    # [실패] 강좌 1에 강좌 2의 sectionId로 영상 업로드 시도 -> 404 NOT_FOUND
    res_cross = client.post(
        "/api/v1/instructor/videos",
        data={
            "courseId": c1.id,
            "videoType": "LESSON",
            "sectionId": sec2.id,
            "title": "교차 참조 영상",
        },
        files={"file": ("v.mp4", io.BytesIO(b"video bytes"), "video/mp4")},
    )
    assert res_cross.status_code == 404

    # [성공] LESSON 영상 업로드 시 섹션 단위 퀴즈(UNIT) 자동 생성 확인
    res_lesson = client.post(
        "/api/v1/instructor/videos",
        data={
            "courseId": c1.id,
            "videoType": "LESSON",
            "sectionId": sec1.id,
            "title": "정상 학습 영상",
        },
        files={"file": ("lesson.mp4", io.BytesIO(b"video bytes"), "video/mp4")},
    )
    assert res_lesson.status_code == 201
    vid_id = res_lesson.json()["data"]["videoId"]

    unit_quiz = db_session.scalar(
        select(Quiz).where(
            Quiz.section_id == sec1.id, Quiz.quiz_type == "UNIT", Quiz.is_active.is_(True)
        )
    )
    assert unit_quiz is not None
    assert unit_quiz.ai_status == "PENDING"

    # [성공] PATCH application/json으로 영상 제목 및 정렬 순서 수정
    res_json_patch = client.patch(
        f"/api/v1/instructor/videos/{vid_id}",
        json={"title": "JSON으로 수정한 영상 제목", "sortOrder": 3},
    )
    assert res_json_patch.status_code == 200
    assert res_json_patch.json()["data"]["title"] == "JSON으로 수정한 영상 제목"
    assert res_json_patch.json()["data"]["sortOrder"] == 3

    # [실패] LESSON 영상의 sortOrder=0 또는 float 입력 시 400 VALIDATION_ERROR
    assert (
        client.patch(f"/api/v1/instructor/videos/{vid_id}", json={"sortOrder": 0}).status_code
        == 400
    )
    assert (
        client.patch(f"/api/v1/instructor/videos/{vid_id}", json={"sortOrder": 1.5}).status_code
        == 400
    )

    # [성공] PATCH 파일 교체 시 기존 COMPLETED 단위 퀴즈가 PENDING으로 초기화되는지 확인
    unit_quiz.ai_status = "COMPLETED"
    unit_quiz.reviewed_at = datetime.now(UTC)
    db_session.commit()

    res_replace_vid = client.patch(
        f"/api/v1/instructor/videos/{vid_id}",
        files={"file": ("replaced.mp4", io.BytesIO(b"new video bytes"), "video/mp4")},
    )
    assert res_replace_vid.status_code == 200
    new_vid_id = res_replace_vid.json()["data"]["videoId"]
    assert new_vid_id != vid_id
    db_session.expire_all()
    refreshed_quiz = db_session.get(Quiz, unit_quiz.id)
    assert refreshed_quiz.ai_status == "PENDING"
    assert refreshed_quiz.reviewed_at is None

    # [실패] 빈 영상 파일(0 byte) 또는 잘못된 확장자 업로드 시 -> 400 INVALID_FILE
    res_empty_vid = client.post(
        "/api/v1/instructor/videos",
        data={"courseId": c1.id, "videoType": "OT", "title": "빈 영상"},
        files={"file": ("empty.mp4", io.BytesIO(b""), "video/mp4")},
    )
    assert res_empty_vid.status_code == 400
    assert res_empty_vid.json()["error"]["code"] == "INVALID_FILE"

    res_bad_ext = client.patch(
        f"/api/v1/instructor/videos/{new_vid_id}",
        files={"file": ("bad.txt", io.BytesIO(b"not video"), "text/plain")},
    )
    assert res_bad_ext.status_code == 400
    assert res_bad_ext.json()["error"]["code"] == "INVALID_FILE"


def test_video_duration_extraction_and_fallback(client: TestClient, db_session: Session):
    from unittest.mock import patch

    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="영상 길이 추출 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="DRAFT",
    )
    db_session.add(course)
    db_session.commit()

    # 1. 실제 영상 추출 성공 시 (extract_video_duration이 125초 반환)
    with patch("app.routers.instructor.videos.extract_video_duration", return_value=125):
        res_real = client.post(
            "/api/v1/instructor/videos",
            data={"courseId": course.id, "videoType": "OT", "title": "실제 길이 OT"},
            files={"file": ("real_sample.mp4", io.BytesIO(b"dummy mp4 bytes"), "video/mp4")},
        )
        assert res_real.status_code == 201
        assert res_real.json()["data"]["durationSeconds"] == 125

    # 2. 영상 추출 실패 시 (None 반환) 기본 360초 폴백
    with patch("app.routers.instructor.videos.extract_video_duration", return_value=None):
        res_fallback = client.post(
            "/api/v1/instructor/videos",
            data={"courseId": course.id, "videoType": "OT", "title": "폴백 OT"},
            files={"file": ("fallback_sample.mp4", io.BytesIO(b"dummy mp4 bytes"), "video/mp4")},
        )
        assert res_fallback.status_code == 201
        assert res_fallback.json()["data"]["durationSeconds"] == 360
        active_vid_id = res_fallback.json()["data"]["videoId"]

    # 3. PATCH 영상 파일 교체 시 실제 재생 시간 갱신 (75초)
    with patch("app.routers.instructor.videos.extract_video_duration", return_value=75):
        res_patch_real = client.patch(
            f"/api/v1/instructor/videos/{active_vid_id}",
            files={"file": ("patch_sample.mp4", io.BytesIO(b"dummy mp4 bytes"), "video/mp4")},
        )
        assert res_patch_real.status_code == 200
        assert res_patch_real.json()["data"]["durationSeconds"] == 75
        new_vid_id = res_patch_real.json()["data"]["videoId"]

    # 4. PATCH 영상 파일 교체 시 추출 실패(None)인 경우 이전 재생 시간(75초) 폴백 유지
    with patch("app.routers.instructor.videos.extract_video_duration", return_value=None):
        res_patch_fallback = client.patch(
            f"/api/v1/instructor/videos/{new_vid_id}",
            files={"file": ("patch_fallback.mp4", io.BytesIO(b"dummy mp4 bytes"), "video/mp4")},
        )
        assert res_patch_fallback.status_code == 200
        assert res_patch_fallback.json()["data"]["durationSeconds"] == 75


def test_extract_video_duration_unit(tmp_path):
    import subprocess

    from app.services.storage import extract_video_duration

    # 1. 존재하지 않는 파일 -> None
    assert extract_video_duration(tmp_path / "nonexistent.mp4") is None

    # 2. 손상된 영상 / 더미 파일 -> None
    corrupt_file = tmp_path / "corrupt.mp4"
    corrupt_file.write_bytes(b"not a valid video content")
    assert extract_video_duration(corrupt_file) is None

    # 3. 빈 파일 -> None
    empty_file = tmp_path / "empty.mp4"
    empty_file.write_bytes(b"")
    assert extract_video_duration(empty_file) is None

    # 4. 실제 ffmpeg로 생성한 2초짜리 유효한 mp4 영상 검증 (컨테이너 내 ffmpeg 활용)
    real_video = tmp_path / "real_2s.mp4"
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=2:size=160x120:rate=1",
                "-c:v",
                "libx264",
                str(real_video),
            ],
            check=True,
            capture_output=True,
        )
        dur = extract_video_duration(real_video)
        assert dur == 2
    except (subprocess.SubprocessError, FileNotFoundError):
        # ffmpeg가 없는 런타임 환경인 경우 스킵
        pass


def test_extract_video_thumbnail_unit(tmp_path):
    import subprocess

    from app.services.storage import extract_video_thumbnail

    # 1. 존재하지 않는 파일 -> False
    assert extract_video_thumbnail(tmp_path / "nonexistent.mp4", tmp_path / "thumb.jpg") is False

    # 2. 손상된 영상 -> False
    corrupt_file = tmp_path / "corrupt.mp4"
    corrupt_file.write_bytes(b"not a video")
    assert extract_video_thumbnail(corrupt_file, tmp_path / "thumb.jpg") is False

    # 3. 실제 ffmpeg로 생성한 영상 -> 썸네일 추출 성공
    real_video = tmp_path / "real_thumb_test.mp4"
    out_thumb = tmp_path / "thumb.jpg"
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=2:size=160x120:rate=1",
                "-c:v",
                "libx264",
                str(real_video),
            ],
            check=True,
            capture_output=True,
        )
        success = extract_video_thumbnail(real_video, out_thumb)
        assert success is True
        assert out_thumb.exists()
        assert out_thumb.stat().st_size > 0
    except (subprocess.SubprocessError, FileNotFoundError):
        pass


def test_upload_video_auto_thumbnail(client: TestClient, db_session: Session):
    from unittest.mock import patch

    course = Course(
        instructor_id=6,
        category_id=1,
        topic_id=11,
        title="자동 썸네일 테스트 강좌",
        description="설명",
        difficulty="BEGINNER",
        price_krw=0,
        status="DRAFT",
    )
    db_session.add(course)
    db_session.commit()

    with patch("app.routers.instructor.videos.extract_video_thumbnail", return_value=True):
        res = client.post(
            "/api/v1/instructor/videos",
            data={"courseId": course.id, "videoType": "OT", "title": "자동 썸네일 OT"},
            files={"file": ("auto_ot.mp4", io.BytesIO(b"dummy mp4 bytes"), "video/mp4")},
        )
        assert res.status_code == 201
        vid_id = res.json()["data"]["videoId"]
        vid = db_session.get(Video, vid_id)
        assert vid.thumbnail_storage_key is not None
        assert vid.thumbnail_storage_key.endswith(".jpg")
