from types import SimpleNamespace

from tigr.entity_claims import lineage_claim_keys
from tigr.governance import EntityClaim


def test_claim_scope_deduplicates_repeated_lineage_references():
    item = SimpleNamespace(
        source_namespace="tenant:a", source_object="public.customers",
        token_version="hmac-sha256:v1", entity_token="a" * 64,
        key_definition={"kind": "primary", "fields": ["id"]},
    )

    class Result:
        def all(self):
            return [item, item]

    class FakeDb:
        def scalars(self, _query):
            return Result()

    keys = lineage_claim_keys(FakeDb(), "version-id")
    assert len(keys) == 1
    assert keys[0]["entity_token"] == "a" * 64


def test_entity_claim_registry_has_database_partial_unique_constraint():
    indexes = {index.name: index for index in EntityClaim.__table__.indexes}
    active_identity = indexes["uq_entity_claim_active_identity"]
    assert active_identity.unique
    assert str(active_identity.dialect_options["postgresql"]["where"]) == "status = 'active'"
    assert {column.name for column in active_identity.columns} == {
        "source_namespace", "source_object", "token_version", "entity_token",
    }
