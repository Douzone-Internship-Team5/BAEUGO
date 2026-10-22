from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

T = TypeVar("T")


class CamelModel(BaseModel):
    """파이썬에서는 snake_case, JSON에서는 camelCase로 주고받는 기본 스키마."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class DataResponse(CamelModel, Generic[T]):
    """성공 응답 형식: { "data": ... }"""

    data: T


class PageData(CamelModel, Generic[T]):
    """페이지 기반 목록 응답 형식:
    { "data": { "items": [...], "page": 1, "size": 20, "total": 57 } }
    """

    items: list[T]
    page: int
    size: int
    total: int


class CursorPageData(CamelModel, Generic[T]):
    """커서 기반 목록 응답 형식:
    { "data": { "items": [...], "nextCursor": "...", "hasNext": true } }
    """

    items: list[T]
    next_cursor: str | None = None
    has_next: bool = False
