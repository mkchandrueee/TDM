from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from tigr.identity import HumanRole

Slug = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=80)]
Subject = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]


class GrantScope(StrEnum):
    WORKSPACE = "workspace"
    DATASET = "dataset"
    RESERVATION = "reservation"
    LOCK = "lock"


class WorkspaceCreate(BaseModel):
    slug: Slug
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=180)]
    purpose: str | None = Field(default=None, max_length=2000)


class WorkspaceUpdate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=180)] | None = None
    purpose: str | None = Field(default=None, max_length=2000)
    status: str | None = Field(default=None, pattern="^(active|inactive)$")


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    purpose: str | None
    status: str
    created_by: str
    created_at: datetime
    updated_at: datetime


class MembershipCreate(BaseModel):
    subject: Subject
    role: HumanRole
    valid_from: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    valid_until: AwareDatetime | None = None
    email: str | None = Field(default=None, max_length=320)
    display_name: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def validate_period(self):
        if self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        return self


class MembershipUpdate(BaseModel):
    role: HumanRole | None = None
    valid_from: AwareDatetime | None = None
    valid_until: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_period(self):
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        return self


class MembershipOut(BaseModel):
    subject: str
    email: str | None
    display_name: str | None
    role: HumanRole
    valid_from: datetime
    valid_until: datetime | None
    revoked_at: datetime | None


class GrantCreate(BaseModel):
    subject: Subject
    scope_type: GrantScope
    scope_id: str | None = Field(default=None, max_length=180)
    permissions: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]] = Field(min_length=1, max_length=30)
    valid_from: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    valid_until: AwareDatetime | None = None
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_scope_and_period(self):
        if self.scope_type == GrantScope.WORKSPACE and self.scope_id:
            raise ValueError("Workspace grants use the workspace_id field and must not include scope_id")
        if self.scope_type != GrantScope.WORKSPACE and not self.scope_id:
            raise ValueError("scope_id is required for object-scoped grants")
        if self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        return self


class GrantOut(BaseModel):
    id: UUID
    subject: str
    scope_type: GrantScope
    scope_id: str | None
    permissions: list[str]
    valid_from: datetime
    valid_until: datetime | None
    reason: str | None
    revoked_at: datetime | None


class ServiceIdentityCreate(BaseModel):
    subject: Subject
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=180)]
    scopes: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]] = Field(min_length=1, max_length=50)


class ServiceIdentityOut(BaseModel):
    subject: str
    display_name: str
    workspace_id: UUID
    scopes: list[str]
    enabled: bool
    created_at: datetime
