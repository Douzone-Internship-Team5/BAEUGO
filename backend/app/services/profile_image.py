import os
from pathlib import Path

from PIL import Image
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.core.errors import AppError
from app.models.user import User
from app.schemas.user import ProfileImageResponse
from app.services.storage import get_profile_image_url, save_upload_file

PROFILE_PRESET_IDS = (1, 2, 3, 4)
MAX_PROFILE_IMAGE_SIZE = 5 * 1024 * 1024
# 받는 확장자 → 저장할 확장자. jpg 계열은 .jpg로 통일한다.
SAVED_EXTENSIONS = {".jpg": ".jpg", ".jpeg": ".jpg", ".png": ".png"}
# 확장자만 바꾼 파일을 막으려고 파일 앞부분(매직 바이트)도 확인한다.
IMAGE_SIGNATURES = {
    ".jpg": b"\xff\xd8\xff",
    ".png": b"\x89PNG\r\n\x1a\n",
}
# 일부 카메라 JPEG은 Pillow가 MPO(여러 장을 담은 JPEG)로 판단한다.
PILLOW_FORMATS = {".jpg": {"JPEG", "MPO"}, ".png": {"PNG"}}
# 5000×5000. 일반 휴대폰 사진(약 1,200만 화소)은 통과하고,
# 파일은 작은데 디코딩하면 메모리를 크게 쓰는 이미지(압축 폭탄)는 막는다.
MAX_IMAGE_PIXELS = 25_000_000

INVALID_FILE_MESSAGE = "jpg, png 형식의 5MB 이하 이미지만 업로드할 수 있습니다."
PRESET_NOT_FOUND_MESSAGE = "존재하지 않는 프리셋 번호입니다."
CHOICE_MESSAGE = "presetId 또는 file 중 하나만 보내 주세요."


def preset_key(preset_id: int) -> str:
    return f"profiles/presets/{preset_id}.png"


def update_profile_image(
    db: Session, user: User, preset_id: object, file: object
) -> ProfileImageResponse:
    """preset_id는 JSON 본문, file은 multipart 본문에서 꺼낸 값이다(없으면 None)."""
    if (preset_id is None) == (file is None):
        raise AppError(400, "VALIDATION_ERROR", CHOICE_MESSAGE)
    if file is not None:
        if not isinstance(file, UploadFile):
            raise AppError(400, "INVALID_FILE", INVALID_FILE_MESSAGE)
        return upload_profile_image(db, user, file)
    # JSON의 true/false도 파이썬에서는 int로 취급되므로 따로 막는다.
    if isinstance(preset_id, bool) or not isinstance(preset_id, int):
        raise AppError(
            400,
            "VALIDATION_ERROR",
            "입력값을 확인해 주세요.",
            fields={"presetId": "정수여야 합니다."},
        )
    return set_preset_image(db, user, preset_id)


def set_preset_image(db: Session, user: User, preset_id: int) -> ProfileImageResponse:
    if preset_id not in PROFILE_PRESET_IDS:
        raise AppError(404, "NOT_FOUND", PRESET_NOT_FOUND_MESSAGE)
    return save_profile_image_key(db, user, preset_key(preset_id))


def upload_profile_image(db: Session, user: User, file: UploadFile) -> ProfileImageResponse:
    extension = validate_image_file(file)
    # save_upload_file은 파일 이름에서 확장자를 정하므로, 통일한 확장자로 이름을 바꿔서 넘긴다.
    file.filename = f"profile{extension}"
    # 매번 다른 키로 저장해서 브라우저가 예전 사진을 캐시로 보여주지 않게 한다.
    key = save_upload_file(file, "profiles", f"user_{user.id}")
    return save_profile_image_key(db, user, key)


def validate_image_file(file: UploadFile) -> str:
    """검사를 통과하면 저장할 확장자(.jpg 또는 .png)를 돌려준다."""
    extension = SAVED_EXTENSIONS.get(Path(file.filename or "").suffix.lower())
    if extension is None:
        raise AppError(400, "INVALID_FILE", INVALID_FILE_MESSAGE)
    signature = IMAGE_SIGNATURES[extension]

    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(0)
    head = file.file.read(len(signature))
    file.file.seek(0)
    if not 0 < size <= MAX_PROFILE_IMAGE_SIZE or head != signature:
        raise AppError(400, "INVALID_FILE", INVALID_FILE_MESSAGE)
    if not is_decodable_image(file, extension):
        raise AppError(400, "INVALID_FILE", INVALID_FILE_MESSAGE)
    return extension


def is_decodable_image(file: UploadFile, extension: str) -> bool:
    """Pillow가 판단한 형식이 확장자와 같고, 픽셀 수가 기준 이하이며, 끝까지 디코딩되는지 본다."""
    try:
        with Image.open(file.file) as image:
            # open은 머리 부분만 읽는다. 픽셀 수는 디코딩 전에 확인해야 압축 폭탄을 막을 수 있다.
            if image.format not in PILLOW_FORMATS[extension]:
                return False
            if image.width * image.height > MAX_IMAGE_PIXELS:
                return False
            image.load()
    # 깨진 파일은 형식에 따라 OSError·SyntaxError·ValueError 등 여러 예외로 실패한다.
    except Exception:
        return False
    finally:
        file.file.seek(0)
    return True


def save_profile_image_key(db: Session, user: User, key: str) -> ProfileImageResponse:
    user.profile_image_key = key
    db.commit()
    return ProfileImageResponse(profile_image_key=key, profile_image_url=get_profile_image_url(key))
