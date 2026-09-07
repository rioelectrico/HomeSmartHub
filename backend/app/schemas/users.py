"""HTTP contracts for home-scoped user administration."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=1024)
    role: str = Field(min_length=1, max_length=64)

    @field_validator("email")
    @classmethod
    def email_has_basic_address_shape(cls, value: str) -> str:
        """Reject obviously malformed addresses without adding a mail transport dependency."""

        local, separator, domain = value.partition("@")
        if not separator or not local or not domain or "." not in domain:
            raise ValueError("Invalid email address")
        return value.lower()


class UserUpdateRequest(BaseModel):
    role: str | None = Field(default=None, min_length=1, max_length=64)
    is_active: bool | None = None

    @model_validator(mode="after")
    def contains_a_change(self) -> "UserUpdateRequest":
        """Reject no-op administrative mutations."""

        if self.role is None and self.is_active is None:
            raise ValueError("At least one update is required")
        return self


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=12, max_length=1024)


class HomeUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    email: str
    is_active: bool
    role: str


class HomeUsersResponse(BaseModel):
    items: list[HomeUserResponse]
