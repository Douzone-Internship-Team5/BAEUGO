from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

STATUS_TO_CODE = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
}


# pydantic 기본 사유(영어)를 바꿔 쓸 문구. {}에는 pydantic 에러의 ctx 값이 들어간다.
# 여기에 없는 타입이나 직접 만든 검증 에러(PydanticCustomError)는 원래 사유를 그대로 쓴다.
VALIDATION_MESSAGES = {
    "missing": "필수 입력값입니다.",
    "string_type": "문자열이어야 합니다.",
    "string_too_short": "{min_length}자 이상 입력해야 합니다.",
    "string_too_long": "{max_length}자 이하로 입력해야 합니다.",
    "int_type": "정수여야 합니다.",
    "int_parsing": "정수여야 합니다.",
    "int_from_float": "정수여야 합니다.",
    "bool_type": "true 또는 false여야 합니다.",
    "bool_parsing": "true 또는 false여야 합니다.",
    "list_type": "배열이어야 합니다.",
    "too_short": "{min_length}개 이상이어야 합니다.",
    "too_long": "{max_length}개 이하여야 합니다.",
    "greater_than": "{gt}보다 커야 합니다.",
    "greater_than_equal": "{ge} 이상이어야 합니다.",
    "less_than": "{lt}보다 작아야 합니다.",
    "less_than_equal": "{le} 이하여야 합니다.",
    "literal_error": "허용되지 않는 값입니다. (허용값: {expected})",
    "enum": "허용되지 않는 값입니다. (허용값: {expected})",
    "time_parsing": "시각 형식(HH:MM:SS)이 올바르지 않습니다.",
    "date_from_datetime_parsing": "날짜 형식(YYYY-MM-DD)이 올바르지 않습니다.",
    "json_invalid": "JSON 형식이 올바르지 않습니다.",
}


def describe_validation_error(error: dict) -> str:
    template = VALIDATION_MESSAGES.get(error["type"])
    if template is None:
        return error["msg"]
    try:
        return template.format(**error.get("ctx", {}))
    except KeyError:
        return error["msg"]


class AppError(Exception):
    """엔드포인트 명세의 에러 코드로 응답할 때 사용한다.

    예: raise AppError(409, "ROLE_ALREADY_SET", "이미 역할이 있습니다.")
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        fields: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.fields = fields


def error_body(code: str, message: str, fields: dict[str, str] | None = None) -> dict:
    error: dict = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    return {"error": error}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.fields),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = {}
        for err in exc.errors():
            location = [str(part) for part in err["loc"] if part not in ("body", "query", "path")]
            fields[".".join(location) or "request"] = describe_validation_error(err)
        return JSONResponse(
            status_code=400,
            content=error_body("VALIDATION_ERROR", "입력값을 확인해 주세요.", fields),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = STATUS_TO_CODE.get(exc.status_code, "ERROR")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, str(exc.detail)),
        )
