"""Validate directly mapped source entity keys used for record locking."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ObjectName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
FieldName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class LockAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_name: ObjectName
    key_kind: Literal["primary", "unique", "fingerprint"]
    key_name: str = Field(min_length=1, max_length=300)
    key_fields: list[FieldName] = Field(min_length=1, max_length=32)


class LockConfigurationInput(BaseModel):
    # Ignore legacy relationship properties from older clients. They no longer
    # participate in identity matching or newly approved configurations.
    model_config = ConfigDict(extra="ignore")

    anchors: list[LockAnchor] = Field(min_length=1, max_length=100)


def _object_index(structure: dict) -> dict[str, dict]:
    objects = structure.get("objects")
    if not isinstance(objects, list):
        return {}
    return {str(item.get("name")): item for item in objects if isinstance(item, dict) and item.get("name")}


def _key_candidates(obj: dict) -> list[dict]:
    result = []
    primary = obj.get("primary_key")
    if isinstance(primary, dict) and primary.get("fields"):
        result.append({"key_kind": "primary", "key_name": primary.get("name") or "PRIMARY", "key_fields": primary["fields"]})
    for constraint in obj.get("unique_constraints", []):
        if isinstance(constraint, dict) and constraint.get("fields"):
            result.append({"key_kind": "unique", "key_name": constraint.get("name") or "UNIQUE", "key_fields": constraint["fields"]})
    for index in obj.get("unique_indexes", []):
        if isinstance(index, dict) and index.get("fields"):
            result.append({"key_kind": "unique", "key_name": index.get("name") or "UNIQUE INDEX", "key_fields": index["fields"]})
    return result


def validate_configuration(payload: LockConfigurationInput, structure: dict,
                           connector_type: str = "postgresql") -> dict:
    """Approve declared database keys or explicitly selected file fingerprints."""
    objects = _object_index(structure)
    if not objects:
        raise ValueError("The selected structure snapshot has no source object metadata")
    normalized = payload.model_dump(mode="json")
    seen_anchors: set[tuple] = set()
    for anchor in normalized["anchors"]:
        obj = objects.get(anchor["object_name"])
        if obj is None:
            raise ValueError("An anchor references an object outside the selected structure snapshot")
        fields = anchor["key_fields"]
        if len(set(fields)) != len(fields) or any(field not in {item.get("name") for item in obj.get("fields", [])} for field in fields):
            raise ValueError("An anchor contains an unknown or duplicate key field")
        if connector_type in ("postgresql", "mysql", "oracle", "mssql"):
            candidate = next((item for item in _key_candidates(obj)
                              if item["key_kind"] == anchor["key_kind"]
                              and item["key_name"] == anchor["key_name"]
                              and item["key_fields"] == fields), None)
            if candidate is None:
                raise ValueError("Anchors must match a primary key or declared unique constraint in the selected snapshot")
        elif (anchor["key_kind"] != "fingerprint" or anchor["object_name"] != "source_file"
              or anchor["key_name"] != "TDE fingerprint"):
            raise ValueError("File source identity keys must be TDE-selected fingerprint fields")
        anchor_key = (anchor["object_name"], anchor["key_kind"], anchor["key_name"], tuple(fields))
        if anchor_key in seen_anchors:
            raise ValueError("Duplicate entity anchors are not allowed")
        seen_anchors.add(anchor_key)

    return {"anchors": normalized["anchors"]}
