"""Validation for explicitly approved source entity keys and lock paths."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ObjectName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
FieldName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
RelationshipId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=1000)]


class LockAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_name: ObjectName
    key_kind: Literal["primary", "unique"]
    key_name: str = Field(min_length=1, max_length=300)
    key_fields: list[FieldName] = Field(min_length=1, max_length=32)


class RelationshipStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relationship_id: RelationshipId
    direction: Literal["forward", "reverse"]


class ApprovedPath(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=180)
    anchor_object: ObjectName
    steps: list[RelationshipStep] = Field(min_length=1, max_length=16)
    max_depth: int = Field(ge=1, le=16)


class ManualRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relationship_id: RelationshipId
    source_object: ObjectName
    source_fields: list[FieldName] = Field(min_length=1, max_length=32)
    target_object: ObjectName
    target_fields: list[FieldName] = Field(min_length=1, max_length=32)
    join_semantics: Literal["equals"] = "equals"
    reason: Reason


class LockConfigurationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchors: list[LockAnchor] = Field(min_length=1, max_length=100)
    paths: list[ApprovedPath] = Field(max_length=200)
    manual_relationships: list[ManualRelationship] = Field(max_length=100)


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


def validate_configuration(payload: LockConfigurationInput, structure: dict) -> dict:
    objects = _object_index(structure)
    if not objects:
        raise ValueError("The selected structure snapshot has no PostgreSQL table metadata")
    normalized = payload.model_dump(mode="json")
    seen_anchors: set[tuple] = set()
    for anchor in normalized["anchors"]:
        obj = objects.get(anchor["object_name"])
        if obj is None:
            raise ValueError("An anchor references an object outside the selected structure snapshot")
        fields = anchor["key_fields"]
        if len(set(fields)) != len(fields) or any(field not in {item.get("name") for item in obj.get("fields", [])} for field in fields):
            raise ValueError("An anchor contains an unknown or duplicate key field")
        candidate = next((item for item in _key_candidates(obj)
                          if item["key_kind"] == anchor["key_kind"]
                          and item["key_name"] == anchor["key_name"]
                          and item["key_fields"] == fields), None)
        if candidate is None:
            raise ValueError("Anchors must match a primary key or declared unique constraint in the selected snapshot")
        anchor_key = (anchor["object_name"], anchor["key_kind"], anchor["key_name"], tuple(fields))
        if anchor_key in seen_anchors:
            raise ValueError("Duplicate entity anchors are not allowed")
        seen_anchors.add(anchor_key)

    relationships: dict[str, dict] = {}
    for obj_name, obj in objects.items():
        for relation in obj.get("foreign_key_edges", []):
            if isinstance(relation, dict) and relation.get("id"):
                source = objects.get(obj_name)
                target = objects.get(str(relation.get("target_object")))
                source_fields = relation.get("source_fields", [])
                target_fields = relation.get("target_fields", [])
                if source is None or target is None or len(source_fields) != len(target_fields) or not source_fields:
                    raise ValueError("A discovered relationship has incomplete endpoints or ordered fields")
                source_types = {field.get("name"): field.get("type") for field in source.get("fields", [])}
                target_types = {field.get("name"): field.get("type") for field in target.get("fields", [])}
                if any(field not in source_types for field in source_fields) or any(field not in target_types for field in target_fields):
                    raise ValueError("A discovered relationship references fields outside the selected structure snapshot")
                if not any(item["key_fields"] == target_fields for item in _key_candidates(target)):
                    raise ValueError("A discovered relationship target must match a declared primary or unique key")
                relationships[str(relation["id"])] = {
                    "id": str(relation["id"]), "source_object": obj_name,
                    "source_fields": source_fields,
                    "target_object": relation.get("target_object"),
                    "target_fields": target_fields,
                    "evidence": "database_foreign_key",
                }
    for relation in normalized["manual_relationships"]:
        if relation["relationship_id"] in relationships:
            raise ValueError("Relationship IDs must be unique")
        source = objects.get(relation["source_object"])
        target = objects.get(relation["target_object"])
        if source is None or target is None:
            raise ValueError("A manual relationship references an object outside the selected structure snapshot")
        source_fields, target_fields = relation["source_fields"], relation["target_fields"]
        if len(source_fields) != len(target_fields) or len(set(source_fields)) != len(source_fields) or len(set(target_fields)) != len(target_fields):
            raise ValueError("Manual relationships must contain matching, ordered field pairs")
        source_metadata = {item.get("name"): item for item in source.get("fields", [])}
        target_metadata = {item.get("name"): item for item in target.get("fields", [])}
        if any(field not in source_metadata for field in source_fields):
            raise ValueError("A manual relationship contains an unknown source field")
        if any(field not in target_metadata for field in target_fields):
            raise ValueError("A manual relationship contains an unknown target field")
        if any(str(source_metadata[source_field].get("type", "")).casefold()
               != str(target_metadata[target_field].get("type", "")).casefold()
               for source_field, target_field in zip(source_fields, target_fields)):
            raise ValueError("Manual relationship field pairs must have matching declared data types")
        if not any(item["key_fields"] == target_fields for item in _key_candidates(target)):
            raise ValueError("Manual relationship target fields must match a declared primary or unique key")
        relationships[relation["relationship_id"]] = {
            **relation, "evidence": "manual_tde_approval", "validated": False, "enforced": False,
        }

    anchor_objects = {item["object_name"] for item in normalized["anchors"]}
    path_names: set[str] = set()
    for path in normalized["paths"]:
        if path["anchor_object"] not in anchor_objects:
            raise ValueError("Every propagation path must start from a configured anchor object")
        if path["name"] in path_names:
            raise ValueError("Propagation path names must be unique")
        path_names.add(path["name"])
        if path["max_depth"] > len(path["steps"]):
            raise ValueError("Maximum path depth cannot exceed the number of explicitly approved edges")
        current_object = path["anchor_object"]
        for step in path["steps"]:
            relation = relationships.get(step["relationship_id"])
            if relation is None:
                raise ValueError("A propagation path references an unknown relationship")
            start = relation["source_object"] if step["direction"] == "forward" else relation["target_object"]
            end = relation["target_object"] if step["direction"] == "forward" else relation["source_object"]
            if current_object != start:
                raise ValueError("Propagation path edges must form an ordered, connected path from the anchor")
            current_object = end

    return {
        "anchors": normalized["anchors"],
        "paths": normalized["paths"],
        "manual_relationships": normalized["manual_relationships"],
        "relationship_evidence": relationships,
    }
