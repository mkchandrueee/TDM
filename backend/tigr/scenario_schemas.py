from enum import StrEnum
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    BaseModel,
    AwareDatetime,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


class GenerationMode(StrEnum):
    SYNTHETIC = "synthetic"
    SOURCE_BACKED = "source_backed"


class ScenarioCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=180)]
    requirement: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=4000)]
    expected_states: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]] = Field(default_factory=list, max_length=30)
    exclusions: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]] = Field(default_factory=list, max_length=30)
    target_use: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""
    volume: int = Field(default=100, ge=1, le=10000)
    draft_data: dict[str, Any] = Field(default_factory=dict)


class MappingField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_field: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    target_field: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
    data_type: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    source_object: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)] | None = None
    required: bool = False
    primary_key: bool = False
    confidence: float | None = Field(default=None, ge=0, le=1)
    rationale: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    json_path: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] | None = None


class PredicateOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    BETWEEN = "between"
    IN = "in"
    IS_NULL = "is_null"


class TypedPredicate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    operator: PredicateOperator
    value: Any = None

    @field_validator("value")
    @classmethod
    def bound_filter_value(cls, value):
        if isinstance(value, list):
            if not 1 <= len(value) <= 100 or any(not isinstance(item, (str, int, float, bool, type(None))) for item in value):
                raise ValueError("IN predicates must contain 1 to 100 primitive values")
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError("Predicate values must be scalar")
        if isinstance(value, str) and len(value) > 500:
            raise ValueError("Predicate values cannot exceed 500 characters")
        return value

    @model_validator(mode="after")
    def value_matches_operator(self):
        if self.operator == PredicateOperator.IS_NULL and self.value is not None:
            raise ValueError("is_null predicates do not accept a value")
        if self.operator == PredicateOperator.IN and not isinstance(self.value, list):
            raise ValueError("in predicates require a list of values")
        if self.operator == PredicateOperator.BETWEEN and (
            not isinstance(self.value, list) or len(self.value) != 2 or any(item is None for item in self.value)
        ):
            raise ValueError("between predicates require exactly two boundary values")
        if self.operator not in (PredicateOperator.IS_NULL, PredicateOperator.IN, PredicateOperator.BETWEEN) and self.value is None:
            raise ValueError("This predicate requires a value")
        return self


class ScenarioRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_field: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
    child_field: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]

    @model_validator(mode="after")
    def relationship_fields_differ(self):
        if self.parent_field == self.child_field:
            raise ValueError("Parent and child keys must be different fields")
        return self


class SourceJoin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left_object: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
    left_field: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
    right_object: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
    right_field: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]


class SchemaGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_mode: GenerationMode
    source_connection_id: UUID | None = None
    selected_source_objects: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]] | None = Field(default=None, max_length=200)
    feedback: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)] = ""

    @model_validator(mode="after")
    def source_required_for_source_backed(self):
        if self.generation_mode == GenerationMode.SOURCE_BACKED and self.source_connection_id is None:
            raise ValueError("Choose an enabled source connection")
        if self.selected_source_objects is not None and len(set(self.selected_source_objects)) != len(self.selected_source_objects):
            raise ValueError("Selected source objects must be unique")
        return self


class ScenarioPlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=180)] | None = None
    requirement: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=4000)] | None = None
    expected_states: list[str] | None = Field(default=None, max_length=30)
    exclusions: list[str] | None = Field(default=None, max_length=30)
    target_use: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    volume: int | None = Field(default=None, ge=1, le=10000)
    generation_mode: GenerationMode | None = None
    source_connection_id: UUID | None = None
    source_object: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)] | None = None
    mapping: list[MappingField] | None = Field(default=None, max_length=200)
    predicates: list[TypedPredicate] | None = Field(default=None, max_length=30)
    relationships: list[ScenarioRelationship] | None = Field(default=None, max_length=50)
    source_joins: list[SourceJoin] | None = Field(default=None, max_length=30)
    accepted_uncovered_criteria: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]] | None = Field(default=None, max_length=40)
    draft_data: dict[str, Any] | None = None
    # Accepted for compatibility with an already-open older browser tab; ignored by the API.
    requirement_review_confirmed: bool | None = None


class ScenarioQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100000)] | None = None
    background_sample: bool = False
    correction_feedback: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)] | None = None


class ScenarioPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correction_feedback: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)] | None = None


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_source_extraction: bool = False
    dataset_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=180)]
    target_use: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    maximum_records: int = Field(ge=1, le=10000)
    output_format: Annotated[str, StringConstraints(pattern="^(csv|json)$")] = "json"
    expiry_at: AwareDatetime | None = None
    test_data_request_id: UUID | None = None
    draft_dataset_id: UUID | None = None
    edit_dataset_id: UUID | None = None
    base_version_number: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def version_edit_requires_both_identifiers(self):
        if (self.edit_dataset_id is None) != (self.base_version_number is None):
            raise ValueError("edit_dataset_id and base_version_number must be provided together")
        if self.edit_dataset_id is not None and self.draft_dataset_id is not None:
            raise ValueError("Choose either a fresh draft or a version edit")
        return self

    @model_validator(mode="after")
    def expiry_must_be_future(self):
        if self.expiry_at is not None and self.expiry_at <= datetime.now(UTC):
            raise ValueError("expiry_at must be in the future")
        return self
