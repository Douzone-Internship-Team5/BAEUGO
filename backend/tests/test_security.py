from datetime import UTC, datetime, timedelta

import jwt

from app.core import security
from app.core.config import get_settings
from app.core.security import (
    JWT_ALGORITHM,
    create_access_token,
    create_playback_token,
    decode_access_token,
    decode_playback_token,
    hash_password,
    verify_password,
)


def make_token(payload: dict, secret_key: str | None = None) -> str:
    return jwt.encode(payload, secret_key or get_settings().jwt_secret_key, JWT_ALGORITHM)


def test_hash_password_is_salted_and_verifiable():
    first_hash = hash_password("baeugo1234")
    second_hash = hash_password("baeugo1234")

    assert first_hash != "baeugo1234"
    assert first_hash != second_hash
    assert verify_password("baeugo1234", first_hash)
    assert not verify_password("wrong-password", first_hash)


def test_access_token_round_trip():
    token = create_access_token(42)

    assert decode_access_token(token) == 42


def test_expired_token_is_rejected():
    expired_at = datetime.now(UTC) - timedelta(minutes=1)
    token = make_token({"sub": "42", "exp": expired_at})

    assert decode_access_token(token) is None


def test_token_signed_with_other_key_is_rejected():
    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    token = make_token({"sub": "42", "exp": expires_at}, secret_key="other-secret-key")

    assert decode_access_token(token) is None


def test_token_without_exp_is_rejected():
    assert decode_access_token(make_token({"sub": "42"})) is None


def test_token_with_non_numeric_sub_is_rejected():
    expires_at = datetime.now(UTC) + timedelta(minutes=10)

    assert decode_access_token(make_token({"sub": "abc", "exp": expires_at})) is None


def test_malformed_token_is_rejected():
    assert decode_access_token("not-a-jwt") is None


def test_playback_token_is_scoped_to_video_and_separate_from_login():
    token, expires_at = create_playback_token(42, 7)

    assert decode_playback_token(token, 7) == 42
    assert decode_playback_token(token, 8) is None
    assert decode_access_token(token) is None
    assert decode_playback_token(create_access_token(42), 7) is None
    assert 295 <= (expires_at - datetime.now(UTC)).total_seconds() <= 300

    header, payload, signature = token.split(".")
    changed = ("A" if signature[0] != "A" else "B") + signature[1:]
    assert decode_playback_token(f"{header}.{payload}.{changed}", 7) is None
    assert decode_playback_token("not-a-jwt", 7) is None
    assert decode_playback_token("", 7) is None


def test_expired_playback_token_is_rejected(monkeypatch):
    class EarlierDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) - timedelta(minutes=6)

    monkeypatch.setattr(security, "datetime", EarlierDatetime)
    token, _ = create_playback_token(42, 7)
    assert decode_playback_token(token, 7) is None
