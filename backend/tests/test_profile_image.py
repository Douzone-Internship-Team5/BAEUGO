import base64
import re
from pathlib import Path

import pytest

from app.seed import PROFILE_PRESET_SOURCE_DIR, seed_profile_presets
from tests.conftest import auth_header

PROFILE_IMAGE_URL = "/api/v1/users/me/profile-image"
ME_URL = "/api/v1/users/me"
INVALID_FILE_MESSAGE = "jpg, png 형식의 5MB 이하 이미지만 업로드할 수 있습니다."
CHOICE_MESSAGE = "presetId 또는 file 중 하나만 보내 주세요."
MAX_SIZE = 5 * 1024 * 1024

PNG_BYTES = (PROFILE_PRESET_SOURCE_DIR / "1.png").read_bytes()
# 1×1 JPEG
JPG_BYTES = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIs"
    "IxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAEC"
    "AwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2Jy"
    "ggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWW"
    "l5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEA"
    "AwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMi"
    "MoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0"
    "dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn"
    "6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDkKKKK8U/TD//Z"
)
GIF_BYTES = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
)


@pytest.fixture
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # 실제 개발 media_data 볼륨 대신 임시 폴더에 저장·조회한다.
    root = tmp_path / "media_data"
    root.mkdir()
    monkeypatch.setattr("app.services.storage.get_media_dir", lambda: root)
    monkeypatch.setattr("app.services.media.get_media_dir", lambda: root)
    return root


@pytest.fixture
def learner(get_user):
    return get_user("learner2@baeugo.test")


def put_preset(client, user, preset_id):
    return client.put(PROFILE_IMAGE_URL, json={"presetId": preset_id}, headers=auth_header(user.id))


def put_file(client, user, filename, content, content_type="application/octet-stream"):
    return client.put(
        PROFILE_IMAGE_URL,
        files={"file": (filename, content, content_type)},
        headers=auth_header(user.id),
    )


def assert_error(response, status_code, code, message):
    assert response.status_code == status_code
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message"] == message
    return error


def get_media(client, url):
    return client.get(url)


def uploaded_files(media_root: Path) -> list[Path]:
    return [path for path in (media_root / "profiles").glob("user_*") if path.is_file()]


# ---------- 프리셋 ----------


@pytest.mark.parametrize("preset_id", [1, 2, 3, 4])
def test_preset_saves_key_and_serves_image(client, learner, media_root, preset_id):
    seed_profile_presets(media_root)

    response = put_preset(client, learner, preset_id)

    key = f"profiles/presets/{preset_id}.png"
    url = f"/api/v1/media/{key}"
    assert response.status_code == 200
    assert response.json() == {"data": {"profileImageKey": key, "profileImageUrl": url}}
    assert learner.profile_image_key == key

    me = client.get(ME_URL, headers=auth_header(learner.id)).json()["data"]
    assert (me["profileImageKey"], me["profileImageUrl"]) == (key, url)

    image = get_media(client, url)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content == (PROFILE_PRESET_SOURCE_DIR / f"{preset_id}.png").read_bytes()


@pytest.mark.parametrize("preset_id", [0, 5, -1])
def test_preset_out_of_range(client, learner, media_root, preset_id):
    response = put_preset(client, learner, preset_id)

    assert_error(response, 404, "NOT_FOUND", "존재하지 않는 프리셋 번호입니다.")
    assert learner.profile_image_key is None


@pytest.mark.parametrize("preset_id", ["2", 2.5, True])
def test_preset_must_be_integer(client, learner, media_root, preset_id):
    response = put_preset(client, learner, preset_id)

    error = assert_error(response, 400, "VALIDATION_ERROR", "입력값을 확인해 주세요.")
    assert error["fields"] == {"presetId": "정수여야 합니다."}


def test_operator_can_set_preset(client, get_user, media_root):
    response = put_preset(client, get_user("admin@baeugo.test"), 3)

    assert response.status_code == 200


# ---------- 파일 업로드 ----------


@pytest.mark.parametrize(
    ("filename", "content", "extension", "media_type"),
    [
        ("avatar.png", PNG_BYTES, "png", "image/png"),
        ("photo.jpg", JPG_BYTES, "jpg", "image/jpeg"),
        # jpg 계열은 저장 키 확장자를 .jpg로 통일한다.
        ("photo.jpeg", JPG_BYTES, "jpg", "image/jpeg"),
        ("PHOTO.JPG", JPG_BYTES, "jpg", "image/jpeg"),
        ("PHOTO.JPEG", JPG_BYTES, "jpg", "image/jpeg"),
        ("Photo.Jpeg", JPG_BYTES, "jpg", "image/jpeg"),
        ("AVATAR.PNG", PNG_BYTES, "png", "image/png"),
    ],
    ids=["png", "jpg", "jpeg", "upper-jpg", "upper-jpeg", "mixed-case-jpeg", "upper-png"],
)
def test_upload_saves_unique_key_and_serves_image(
    client, learner, media_root, filename, content, extension, media_type
):
    response = put_file(client, learner, filename, content)

    assert response.status_code == 200
    data = response.json()["data"]
    key = data["profileImageKey"]
    assert re.fullmatch(rf"profiles/user_{learner.id}_[0-9a-f]{{8}}\.{extension}", key)
    assert data["profileImageUrl"] == f"/api/v1/media/{key}"
    assert (media_root / key).read_bytes() == content

    me = client.get(ME_URL, headers=auth_header(learner.id)).json()["data"]
    assert me["profileImageKey"] == key

    image = get_media(client, data["profileImageUrl"])
    assert image.status_code == 200
    assert image.headers["content-type"] == media_type
    assert image.content == content


def test_each_upload_gets_new_key_and_keeps_old_file(client, learner, media_root):
    first = put_file(client, learner, "a.png", PNG_BYTES).json()["data"]["profileImageKey"]
    second = put_file(client, learner, "a.png", PNG_BYTES).json()["data"]["profileImageKey"]

    assert first != second
    assert learner.profile_image_key == second
    # 예전 업로드 파일 삭제는 이번 범위가 아니다.
    assert (media_root / first).exists()


def test_upload_exactly_5mb_is_allowed(client, learner, media_root):
    content = PNG_BYTES + b"\0" * (MAX_SIZE - len(PNG_BYTES))

    response = put_file(client, learner, "big.png", content)

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("big.png", PNG_BYTES + b"\0" * (MAX_SIZE - len(PNG_BYTES) + 1)),
        ("fake.png", b"this is not an image"),
        ("fake.jpg", PNG_BYTES),
        ("fake.png", JPG_BYTES),
        ("anim.gif", GIF_BYTES),
        ("gif-renamed.png", GIF_BYTES),
        ("fake.jpeg", PNG_BYTES),
        ("FAKE.JPEG", b"this is not an image"),
        ("fake.PNG", JPG_BYTES),
        ("noext", PNG_BYTES),
        ("empty.png", b""),
    ],
    ids=[
        "over-5mb",
        "text-as-png",
        "png-content-jpg-ext",
        "jpg-content-png-ext",
        "gif",
        "gif-content-png-ext",
        "png-content-jpeg-ext",
        "text-as-upper-jpeg",
        "jpg-content-upper-png-ext",
        "no-ext",
        "empty",
    ],
)
def test_invalid_file(client, learner, media_root, filename, content):
    response = put_file(client, learner, filename, content)

    assert_error(response, 400, "INVALID_FILE", INVALID_FILE_MESSAGE)
    assert learner.profile_image_key is None
    assert uploaded_files(media_root) == []


def test_file_field_sent_as_text_is_invalid(client, learner, media_root):
    response = client.put(
        PROFILE_IMAGE_URL, files={"file": (None, "not a file")}, headers=auth_header(learner.id)
    )

    assert_error(response, 400, "INVALID_FILE", INVALID_FILE_MESSAGE)


# ---------- 둘 다 없음 · 둘 다 있음 ----------


def test_both_preset_and_file(client, learner, media_root):
    response = client.put(
        PROFILE_IMAGE_URL,
        files={"file": ("a.png", PNG_BYTES, "image/png"), "presetId": (None, "1")},
        headers=auth_header(learner.id),
    )

    assert_error(response, 400, "VALIDATION_ERROR", CHOICE_MESSAGE)
    assert learner.profile_image_key is None
    assert uploaded_files(media_root) == []


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"json": {}},
        {"json": {"presetId": None}},
        {"json": []},
        {"files": {"presetId": (None, "1")}},
        {"files": {"other": (None, "x")}},
        {"content": b"presetId=1", "headers": {"Content-Type": "text/plain"}},
        {},
    ],
    ids=[
        "empty-json",
        "null-preset",
        "json-array",
        "multipart-preset-only",
        "multipart-without-file",
        "other-content-type",
        "no-body",
    ],
)
def test_neither_preset_nor_file(client, learner, media_root, request_kwargs):
    headers = {**request_kwargs.pop("headers", {}), **auth_header(learner.id)}

    response = client.put(PROFILE_IMAGE_URL, headers=headers, **request_kwargs)

    assert_error(response, 400, "VALIDATION_ERROR", CHOICE_MESSAGE)


def test_broken_json(client, learner, media_root):
    response = client.put(
        PROFILE_IMAGE_URL,
        content=b"{presetId:",
        headers={"Content-Type": "application/json", **auth_header(learner.id)},
    )

    error = assert_error(response, 400, "VALIDATION_ERROR", "입력값을 확인해 주세요.")
    assert error["fields"] == {"request": "JSON 형식이 올바르지 않습니다."}


# ---------- 인증 ----------


def test_requires_login(client, media_root):
    response = client.put(PROFILE_IMAGE_URL, json={"presetId": 1})

    assert_error(response, 401, "UNAUTHORIZED", "로그인이 필요합니다.")


def test_login_is_checked_before_body(client, media_root):
    response = client.put(
        PROFILE_IMAGE_URL,
        content=b"{presetId:",
        headers={"Content-Type": "application/json"},
    )

    assert_error(response, 401, "UNAUTHORIZED", "로그인이 필요합니다.")
