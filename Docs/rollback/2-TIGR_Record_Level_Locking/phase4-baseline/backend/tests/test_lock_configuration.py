from types import SimpleNamespace

import pytest
from tigr import connection_testing
from tigr.lock_configuration import LockConfigurationInput, validate_configuration


@pytest.fixture
def structure():
    return {
        "format": "postgresql",
        "objects": [
            {
                "name": "customers",
                "fields": [{"name": "tenant_id", "type": "INTEGER"}, {"name": "customer_id", "type": "INTEGER"}],
                "primary_key": {"name": "customers_pkey", "fields": ["tenant_id", "customer_id"]},
                "unique_constraints": [],
                "foreign_key_edges": [],
            },
            {
                "name": "accounts",
                "fields": [{"name": "tenant_id", "type": "INTEGER"}, {"name": "account_id", "type": "INTEGER"}, {"name": "customer_id", "type": "INTEGER"}],
                "primary_key": {"name": "accounts_pkey", "fields": ["tenant_id", "account_id"]},
                "unique_constraints": [],
                "foreign_key_edges": [{
                    "id": "fk_customer_account", "name": "accounts_customer_fk",
                    "source_object": "accounts", "source_fields": ["tenant_id", "customer_id"],
                    "target_object": "customers", "target_fields": ["tenant_id", "customer_id"],
                    "evidence": "database_foreign_key", "validated": True, "enforced": True,
                }, {
                    "id": "fk_account_card", "name": "cards_account_fk",
                    "source_object": "cards", "source_fields": ["tenant_id", "account_id"],
                    "target_object": "accounts", "target_fields": ["tenant_id", "account_id"],
                    "evidence": "database_foreign_key", "validated": True, "enforced": True,
                }],
            },
            {
                "name": "cards",
                "fields": [{"name": "tenant_id", "type": "INTEGER"}, {"name": "card_id", "type": "INTEGER"}, {"name": "account_id", "type": "INTEGER"}],
                "primary_key": {"name": "cards_pkey", "fields": ["tenant_id", "card_id"]},
                "unique_constraints": [],
                "foreign_key_edges": [],
            },
        ],
    }


def test_accepts_composite_keys_and_ordered_propagation_path(structure):
    payload = LockConfigurationInput.model_validate({
        "anchors": [{
            "object_name": "customers", "key_kind": "primary",
            "key_name": "customers_pkey", "key_fields": ["tenant_id", "customer_id"],
        }],
        "paths": [{
            "name": "customer-account-card", "anchor_object": "customers",
            "steps": [
                {"relationship_id": "fk_customer_account", "direction": "reverse"},
                {"relationship_id": "fk_account_card", "direction": "forward"},
            ],
            "max_depth": 2,
        }],
        "manual_relationships": [],
    })

    result = validate_configuration(payload, structure)

    edge = result["relationship_evidence"]["fk_customer_account"]
    assert edge["source_fields"] == ["tenant_id", "customer_id"]
    assert edge["target_fields"] == ["tenant_id", "customer_id"]
    assert result["paths"][0]["steps"][0]["direction"] == "reverse"


def test_rejects_field_name_without_declared_key_metadata(structure):
    payload = LockConfigurationInput.model_validate({
        "anchors": [{
            "object_name": "accounts", "key_kind": "unique",
            "key_name": "customer_id", "key_fields": ["customer_id"],
        }],
        "paths": [],
        "manual_relationships": [],
    })

    with pytest.raises(ValueError, match="primary key or declared unique"):
        validate_configuration(payload, structure)


def test_rejects_disconnected_path(structure):
    payload = LockConfigurationInput.model_validate({
        "anchors": [{
            "object_name": "customers", "key_kind": "primary",
            "key_name": "customers_pkey", "key_fields": ["tenant_id", "customer_id"],
        }],
        "paths": [{
            "name": "broken", "anchor_object": "customers",
            "steps": [{"relationship_id": "fk_account_card", "direction": "forward"}],
            "max_depth": 1,
        }],
        "manual_relationships": [],
    })

    with pytest.raises(ValueError, match="ordered, connected path"):
        validate_configuration(payload, structure)


def test_manual_relationship_requires_exact_ordered_fields(structure):
    payload = LockConfigurationInput.model_validate({
        "anchors": [{
            "object_name": "customers", "key_kind": "primary",
            "key_name": "customers_pkey", "key_fields": ["tenant_id", "customer_id"],
        }],
        "paths": [{
            "name": "manual", "anchor_object": "customers",
            "steps": [{"relationship_id": "manual:customer-account", "direction": "reverse"}],
            "max_depth": 1,
        }],
        "manual_relationships": [{
            "relationship_id": "manual:customer-account",
            "source_object": "accounts", "source_fields": ["tenant_id", "customer_id"],
            "target_object": "customers", "target_fields": ["tenant_id", "customer_id"],
            "join_semantics": "equals", "reason": "The source has no declared foreign key.",
        }],
    })

    result = validate_configuration(payload, structure)

    assert result["relationship_evidence"]["manual:customer-account"]["evidence"] == "manual_tde_approval"
    assert result["relationship_evidence"]["manual:customer-account"]["enforced"] is False


def test_postgresql_discovery_keeps_composite_foreign_key_grouped(monkeypatch):
    class Result:
        def one(self):
            return True, False

        def all(self):
            return []

    class SourceConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args, **_kwargs):
            return Result()

    class Engine:
        def connect(self):
            return SourceConnection()

        def dispose(self):
            pass

    class Inspector:
        def get_table_names(self, schema):
            return ["accounts"]

        def get_columns(self, table, schema):
            return [
                {"name": "tenant_id", "type": "INTEGER", "nullable": False, "default": None},
                {"name": "account_id", "type": "INTEGER", "nullable": False, "default": None},
                {"name": "customer_id", "type": "INTEGER", "nullable": False, "default": None},
            ]

        def get_pk_constraint(self, table, schema):
            return {"name": "accounts_pkey", "constrained_columns": ["tenant_id", "account_id"]}

        def get_unique_constraints(self, table, schema):
            return []

        def get_indexes(self, table, schema):
            return []

        def get_check_constraints(self, table, schema):
            return []

        def get_foreign_keys(self, table, schema):
            return [{
                "name": "accounts_customer_fk", "constrained_columns": ["tenant_id", "customer_id"],
                "referred_schema": "public", "referred_table": "customers",
                "referred_columns": ["tenant_id", "customer_id"],
            }]

    monkeypatch.setattr(connection_testing, "get_settings", lambda: SimpleNamespace(env="development"))
    monkeypatch.setattr(connection_testing, "EnvironmentSecretResolver", lambda: SimpleNamespace(
        resolve=lambda _ref: "postgresql+psycopg://user:pass@localhost/db",
    ))
    monkeypatch.setattr(connection_testing, "create_engine", lambda *_args, **_kwargs: Engine())
    monkeypatch.setattr(connection_testing, "inspect", lambda _db: Inspector())
    connection = SimpleNamespace(
        secret_reference="env://SOURCE", connector_config={"schema": "public"},
        operation_scope="read_only",
    )

    result = connection_testing.test_postgresql(connection)

    assert result.succeeded
    account = result.structure["objects"][0]
    assert len(account["foreign_key_edges"]) == 1
    assert account["foreign_key_edges"][0]["source_fields"] == ["tenant_id", "customer_id"]
    assert account["foreign_key_edges"][0]["target_fields"] == ["tenant_id", "customer_id"]
    assert account["foreign_keys"] == [
        {"field": "tenant_id", "references_object": "customers", "references_field": "tenant_id"},
        {"field": "customer_id", "references_object": "customers", "references_field": "customer_id"},
    ]
