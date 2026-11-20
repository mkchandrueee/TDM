from typing import Any

from pydantic import BaseModel, Field


class FieldDetail(BaseModel):
    field: str
    message: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    field_details: list[FieldDetail] = Field(default_factory=list)
    request_id: str
    retryable: bool = False


class Page[T](BaseModel):
    items: list[T]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=200)
    total: int = Field(ge=0)
    has_more: bool


class HealthStatus(BaseModel):
    status: str
    database: str
    graph: str
    environment: str


JsonObject = dict[str, Any]
