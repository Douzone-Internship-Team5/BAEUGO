import re

from pydantic import Field, field_validator
from pydantic_core import PydanticCustomError

from app.schemas.common import CamelModel

# EmailStr은 목업 계정의 .test 도메인을 예약 도메인이라며 거부하므로 형식만 직접 확인한다.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LoginRequest(CamelModel):
    email: str = Field(max_length=100)
    password: str = Field(min_length=1, max_length=255)

    @field_validator("email")
    @classmethod
    def check_email_format(cls, value: str) -> str:
        email = value.strip().lower()
        if not EMAIL_PATTERN.match(email):
            raise PydanticCustomError("email_format", "유효한 이메일 형식이 아닙니다.")
        return email


class LoginUser(CamelModel):
    id: int
    email: str
    name: str
    account_type: str
    role: str | None
    active_mode: str | None
    roles: list[str]
    onboarded: bool


class LoginResponse(CamelModel):
    access_token: str
    user: LoginUser
