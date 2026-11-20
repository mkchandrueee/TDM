import json
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from tigr.dataset_services import serialize_dataset_records
from tigr.record_identity import add_hidden_anchor_fields, capture_entity_lineage, entity_token
from tigr.scenario_schemas import MappingField


def test_identity_survives_output_renaming_and_masking():
    key = "customer-private-91827"
    original = entity_token("identity-test-secret", "connection:abc", "public.customers", ["customer_id"], [key])
    # The source key is retained only in extraction memory; changing the output alias/value has no effect on its identity.
    renamed_masked_output = {"customer_reference": "masked-value-1"}
    after = entity_token("identity-test-secret", "connection:abc", "public.customers", ["customer_id"], [key])
    assert original == after
    artifact = serialize_dataset_records([renamed_masked_output], "json")
    assert key.encode() not in artifact
    assert original.encode() not in artifact


def test_identity_is_namespaced_typed_and_key_ordered():
    secret = "identity-test-secret"
    integer = entity_token(secret, "connection:abc", "public.items", ["id"], [7])
    assert integer != entity_token(secret, "connection:def", "public.items", ["id"], [7])
    assert integer != entity_token(secret, "connection:abc", "public.items", ["id"], ["7"])
    assert entity_token(secret, "n", "o", ["a", "b"], [1, 2]) != entity_token(secret, "n", "o", ["b", "a"], [2, 1])


def test_identity_uses_stable_date_encoding_and_never_contains_source_value():
    raw_value = date(2025, 1, 2)
    token = entity_token("identity-test-secret", "namespace", "schema.table", ["created_on"], [raw_value])
    assert token == entity_token("identity-test-secret", "namespace", "schema.table", ["created_on"], [date(2025, 1, 2)])
    assert raw_value.isoformat() not in token
    assert len(json.loads(json.dumps({"entity_token": token})) ["entity_token"]) == 64


def test_hidden_anchor_key_is_tokenized_before_it_is_removed_from_output(monkeypatch):
    from tigr import record_identity
    from tigr.connection_models import ConnectionStructureSnapshot, LockPropagationConfiguration

    snapshot_id, config_id, connection_id = uuid4(), uuid4(), uuid4()
    snapshot = SimpleNamespace(
        id=snapshot_id, connection_id=connection_id,
        structure={"source_identity_namespace": "tenant:source", "objects": [{"name": "public.customers", "fields": [
            {"name": "customer_id", "type": "UUID"}, {"name": "display_name", "type": "TEXT"},
        ]}]},
    )
    config = SimpleNamespace(
        id=config_id, connection_id=connection_id, structure_snapshot_id=snapshot_id, status="approved",
        configuration={"anchors": [{"object_name": "public.customers", "key_kind": "primary",
                                     "key_name": "customers_pkey", "key_fields": ["customer_id"]}], "paths": []},
    )

    class FakeDb:
        def get(self, model, _identifier):
            return snapshot if model is ConnectionStructureSnapshot else config if model is LockPropagationConfiguration else None

    monkeypatch.setattr(record_identity, "get_settings", lambda: SimpleNamespace(identity_secret="test-secret", identity_key_version="test-v1"))
    version = SimpleNamespace(provenance={"lock_metadata": {
        "structure_snapshot_id": str(snapshot_id), "lock_configuration_id": str(config_id),
    }})
    selected = [MappingField(source_field="display_name", target_field="name", data_type="TEXT", source_object="public.customers")]
    augmented = add_hidden_anchor_fields(FakeDb(), version, connection_id, selected)
    hidden_field = next(item.target_field for item in augmented if item not in selected)
    raw_key = "customer-private-91827"
    rows = [{"name": "masked-name", hidden_field: raw_key}]
    lineage = capture_entity_lineage(FakeDb(), None, version, rows, augmented, connection_id)

    assert len(lineage) == 1
    assert raw_key not in lineage[0]["entity_token"]
    rows[0].pop(hidden_field)
    artifact = serialize_dataset_records(rows, "json")
    assert raw_key.encode() not in artifact
    assert lineage[0]["entity_token"].encode() not in artifact
