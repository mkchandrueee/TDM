import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


class CreationPathway(StrEnum):
    SCENARIO_TO_DATA = "scenario_to_data"
    SOURCE_TO_DATA = "source_to_data"


class LifecycleState(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    PUBLISHED = "published"
    FAILED = "failed"
    QUARANTINED = "quarantined"
    EXPIRED = "expired"


class PrivacyState(StrEnum):
    NOT_ASSESSED = "not_assessed"
    COMPLIANT = "compliant"
    REQUIRES_MASKING = "requires_masking"
    BLOCKED = "blocked"


class ValidationState(StrEnum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"


FieldName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
ShortReference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
SafeReference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/#-]*$")]


class SchemaField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: FieldName
    data_type: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    entity: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)] | None = None
    json_path: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] | None = None
    required: bool = False


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: Annotated[str, StringConstraints(pattern="^(source_backed|synthetic|not_set)$")] = "not_set"
    source_connection_id: UUID | None = None
    source_object: ShortReference | None = None
    source_schema_version: ShortReference | None = None
    source_query_id: UUID | None = None
    source_mapping_id: UUID | None = None
    source_predicates: list[dict[str, Any]] = Field(default_factory=list)
    mapping_version: ShortReference | None = None
    scenario_id: ShortReference | None = None
    scenario_version: ShortReference | None = None
    job_id: UUID | None = None
    artifact_reference: SafeReference | None = None


class DraftDataSet(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=180)]
    description: str | None = Field(default=None, max_length=2000)
    creation_pathway: CreationPathway
    maximum_records: int = Field(default=100, ge=1, le=10000)
    output_format: Annotated[str, StringConstraints(pattern="^(csv|json)$")] = "json"
    schema_scope: list[SchemaField] = Field(default_factory=list, max_length=500)
    selected_fields: list[FieldName] = Field(default_factory=list, max_length=500)
    provenance: Provenance = Field(default_factory=Provenance)
    expiry_at: AwareDatetime | None = None
    permitted_use: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]] = Field(default_factory=lambda: ["testing"], max_length=30)

    @model_validator(mode="after")
    def selected_fields_are_in_scope(self):
        if len(self.selected_fields) != len(set(self.selected_fields)):
            raise ValueError("selected_fields must be unique")
        field_paths = [field.json_path or (f"{field.entity}.{field.field}" if field.entity else field.field) for field in self.schema_scope]
        if len(field_paths) != len(set(field_paths)):
            raise ValueError("schema_scope field paths must be unique")
        if self.expiry_at is not None and self.expiry_at <= datetime.now(UTC):
            raise ValueError("expiry_at must be in the future")
        if not self.schema_scope or not self.selected_fields:
            return self
        available = set()
        for field in self.schema_scope:
            available.add(field.field)
            if field.entity:
                available.add(f"{field.entity}.{field.field}")
            if field.json_path:
                available.add(field.json_path)
        missing = set(self.selected_fields) - available
        if missing:
            raise ValueError("selected_fields must be present in schema_scope")
        return self


class ValidationUpdate(BaseModel):
    validation_state: ValidationState
    privacy_state: PrivacyState
    evidence: dict[str, str | int | float | bool | None] = Field(default_factory=dict, max_length=30)

    @field_validator("evidence")
    @classmethod
    def evidence_is_safe_metadata(cls, value):
        allowed = {"summary", "record_count", "field_count", "warning_count", "duration_ms", "latency_ms", "usage_count", "reason_code", "schema_version", "mapping_version", "policy_version", "validation_version", "artifact_ref", "source_object", "hash", "provider", "model_version", "prompt_version", "format", "criteria_count", "target_connection_id", "masked_field_count", "masking_run_id", "sensitive_field_count", "reviewed_finding_count"}
        for key, item in value.items():
            if key == "record_count" and isinstance(item, (int, float)) and item < 0:
                raise ValueError("record_count must be non-negative")
            if key not in allowed:
                raise ValueError("evidence keys must use the approved safe metadata fields")
            if isinstance(item, str) and (len(item) > 300 or "\n" in item or re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", item)):
                raise ValueError("evidence strings must be short and must not contain contact details")
        return value


class LineageEventCreate(BaseModel):
    event_type: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    stage: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
    parent_event_ids: list[UUID] | None = Field(default=None, max_length=50)
    source_reference: SafeReference | None = None
    artifact_reference: SafeReference | None = None
    mapping_version: ShortReference | None = None
    scenario_version: ShortReference | None = None
    job_id: UUID | None = None
    policy_version: ShortReference | None = None
    validation_version: ShortReference | None = None
    outcome: Annotated[str, StringConstraints(pattern="^(succeeded|failed|warning)$")] = "succeeded"
    safe_evidence: dict[str, str | int | float | bool | None] = Field(default_factory=dict, max_length=30)

    @field_validator("parent_event_ids")
    @classmethod
    def parents_must_be_unique(cls, value):
        if value is not None and len(value) != len(set(value)):
            raise ValueError("parent_event_ids must be unique")
        return value

    @field_validator("safe_evidence")
    @classmethod
    def evidence_is_safe_metadata(cls, value):
        return ValidationUpdate.evidence_is_safe_metadata(value)


class DataSetVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dataset_id: UUID
    version_number: int
    workspace_id: UUID
    lifecycle_state: LifecycleState
    schema_scope: list[dict[str, Any]]
    fields: list["DataSetFieldOut"]
    selected_fields: list[str]
    generation_settings: dict[str, Any]
    provenance: dict[str, Any]
    privacy_state: PrivacyState
    validation_state: ValidationState
    publication_state: str
    record_count: int | None
    expiry_at: datetime | None
    permitted_use: list[str]
    validation_evidence: dict[str, Any]
    published_at: datetime | None
    expired_at: datetime | None
    created_by: str
    created_at: datetime


class DataSetFieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    field_name: str
    field_path: str
    data_type: str
    required: bool
    target_field_path: str | None
    constraint_spec: dict[str, Any]
    ordinal: int


class DataSetSummary(BaseModel):
    id: UUID
    reference_code: str
    workspace_id: UUID
    name: str
    description: str | None
    owner_id: str
    creation_pathway: CreationPathway
    latest_version: DataSetVersionOut
    created_at: datetime
    updated_at: datetime


class LineageEventOut(BaseModel):
    id: UUID
    dataset_id: UUID
    version_id: UUID
    version_number: int
    sequence_number: int
    event_type: str
    stage: str
    source_reference: str | None
    artifact_reference: str | None
    mapping_version: str | None
    scenario_version: str | None
    job_id: UUID | None
    policy_version: str | None
    validation_version: str | None
    actor_id: str
    actor_type: str
    occurred_at: datetime
    outcome: str
    safe_evidence: dict[str, Any]
    parent_event_ids: list[UUID]
    child_event_ids: list[UUID]


class DataSetDetail(BaseModel):
    data_set: DataSetSummary
    selected_version: DataSetVersionOut
    versions: list[DataSetVersionOut]
    test_data_requirements: str | None = None
    masked_fields: list[str] = Field(default_factory=list)
    lineage: list[LineageEventOut]
    status_history: list[dict[str, Any]]
    validation_runs: list[dict[str, Any]]
    access: dict[str, Any]
    sample_records: list[dict[str, Any]] = Field(default_factory=list)
