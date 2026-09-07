"""Public error envelope; validation input values are never echoed."""

from pydantic import BaseModel, Field


class ValidationDetail(BaseModel):
    location: list[str | int]
    type: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ValidationDetail] = Field(default_factory=list)
    correlation_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody
