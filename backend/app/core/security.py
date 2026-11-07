from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import digest

import bcrypt
import jwt

from app.core.config import get_settings

JWT_ALGORITHM = "HS256"
PLAYBACK_TOKEN_AUDIENCE = "video-playback"
PLAYBACK_URL_TTL_SECONDS = 300


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    issued_at = datetime.now(UTC)
    payload = {
        # PyJWT는 sub가 문자열이 아니면 검증에 실패한다.
        "sub": str(user_id),
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> int | None:
    """서명·만료가 유효하면 user id를, 아니면 None을 돌려준다."""
    try:
        payload = jwt.decode(
            token,
            get_settings().jwt_secret_key,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
        return int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError):
        return None


def _playback_signing_key() -> bytes:
    # 영상 전용 토큰이 일반 로그인 토큰으로 사용되지 않도록 서명 키도 분리한다.
    return digest(get_settings().jwt_secret_key.encode(), b"video-playback", sha256)


def create_playback_token(user_id: int, video_id: int) -> tuple[str, datetime]:
    issued_at = datetime.now(UTC).replace(microsecond=0)
    expires_at = issued_at + timedelta(seconds=PLAYBACK_URL_TTL_SECONDS)
    token = jwt.encode(
        {
            "sub": str(user_id),
            "aud": PLAYBACK_TOKEN_AUDIENCE,
            "video_id": video_id,
            "iat": issued_at,
            "exp": expires_at,
        },
        _playback_signing_key(),
        algorithm=JWT_ALGORITHM,
    )
    return token, expires_at


def decode_playback_token(token: str, video_id: int) -> int | None:
    try:
        payload = jwt.decode(
            token,
            _playback_signing_key(),
            algorithms=[JWT_ALGORITHM],
            audience=PLAYBACK_TOKEN_AUDIENCE,
            options={"require": ["sub", "aud", "video_id", "iat", "exp"]},
        )
        if type(payload["video_id"]) is not int or payload["video_id"] != video_id:
            return None
        return int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError):
        return None
