from uuid import uuid4
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from tigr import privacy_api
from tigr.datasets import DataSetVersion
from tigr.governance import PiiFinding
from tigr.iam import Workspace
from tigr.identity import HumanRole, Principal


def test_invalid_ollama_message_shape_returns_actionable_classification_error(monkeypatch):
    class MalformedAssistant:
        def _model(self):
            return "gemma3:latest"

        def _request(self, _path, _payload):
            return {"message": "Ollama returned a malformed message"}

    monkeypatch.setattr(privacy_api, "OllamaScenarioAssistant", MalformedAssistant)
    with pytest.raises(HTTPException) as error:
        privacy_api._classify_schema_fields_with_ollama(SimpleNamespace(name="Test data", description=None, creation_pathway="source_to_data"), [{"field": "email", "data_type": "text"}])

    assert error.value.status_code == 502
    assert "classify fields manually" in error.value.detail


def test_tde_manual_classification_saves_each_output_field_as_reviewed(app_context):
    client, session, current = app_context
    workspace_id = uuid4()
    session.add(Workspace(id=workspace_id, slug=f"privacy-{workspace_id.hex[:8]}", name="Privacy test", created_by="test-setup"))
    session.flush()
    current["principal"] = Principal(subject="privacy-tde", development_role=HumanRole.TEST_DATA_ENGINEER)

    created = client.post(f"/api/v1/workspaces/{workspace_id}/datasets", json={
        "name": "Manual classification test",
        "creation_pathway": "source_to_data",
        "schema_scope": [
            {"entity": "customer", "field": "customer_id", "data_type": "integer", "required": True},
            {"entity": "customer", "field": "email", "data_type": "text", "required": False},
        ],
        "selected_fields": ["customer.customer_id", "customer.email"],
        "provenance": {"origin": "source_backed", "source_object": "customer"},
    })
    assert created.status_code == 201, created.text
    dataset_id = created.json()["id"]

    current["principal"] = Principal(
        subject="privacy-validation-service", actor_type="service", service_workspace_id=str(workspace_id),
        service_scopes=("dataset:validate",),
    )
    validated = client.post(f"/api/v1/datasets/{dataset_id}/versions/1/validation", json={
        "validation_state": "passed", "privacy_state": "requires_masking", "evidence": {"field_count": 2},
    })
    assert validated.status_code == 200, validated.text
    current["principal"] = Principal(subject="privacy-tde", development_role=HumanRole.TEST_DATA_ENGINEER)

    saved = client.post(f"/api/v1/datasets/{dataset_id}/versions/1/privacy/manual-classifications", json={
        "fields": [
            {"field": "customer.customer_id", "classification": "non_sensitive"},
            {"field": "customer.email", "classification": "sensitive"},
        ],
    })
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["sensitive_field_count"] == 1
    assert body["review_required"] is False
    assert {item["field"]: item["classification"] for item in body["findings"]} == {
        "customer.customer_id": "non_sensitive",
        "customer.email": "sensitive",
    }
    assert all(item["reviewed_at"] and item["reviewed_by"] == "privacy-tde" for item in body["findings"])
    version = session.query(DataSetVersion).filter_by(dataset_id=dataset_id, version_number=1).one()
    assert version.lifecycle_state == "draft"
    assert version.privacy_state == "requires_masking"
    assert session.query(PiiFinding).filter_by(version_id=version.id).count() == 2


def test_tde_manual_classification_requires_every_field_once(app_context):
    client, session, current = app_context
    workspace_id = uuid4()
    session.add(Workspace(id=workspace_id, slug=f"privacy-{workspace_id.hex[:8]}", name="Privacy test", created_by="test-setup"))
    session.flush()
    current["principal"] = Principal(subject="privacy-tde", development_role=HumanRole.TEST_DATA_ENGINEER)
    created = client.post(f"/api/v1/workspaces/{workspace_id}/datasets", json={
        "name": "Manual classification completeness test",
        "creation_pathway": "source_to_data",
        "schema_scope": [{"field": "account_id", "data_type": "integer", "required": True}],
        "selected_fields": ["account_id"],
        "provenance": {"origin": "source_backed", "source_object": "accounts"},
    })
    assert created.status_code == 201, created.text
    dataset_id = created.json()["id"]
    current["principal"] = Principal(
        subject="privacy-validation-service", actor_type="service", service_workspace_id=str(workspace_id),
        service_scopes=("dataset:validate",),
    )
    validated = client.post(f"/api/v1/datasets/{dataset_id}/versions/1/validation", json={
        "validation_state": "passed", "privacy_state": "requires_masking", "evidence": {"field_count": 1},
    })
    assert validated.status_code == 200, validated.text
    current["principal"] = Principal(subject="privacy-tde", development_role=HumanRole.TEST_DATA_ENGINEER)

    incomplete = client.post(f"/api/v1/datasets/{dataset_id}/versions/1/privacy/manual-classifications", json={"fields": []})
    assert incomplete.status_code == 422

    missing = client.post(f"/api/v1/datasets/{dataset_id}/versions/1/privacy/manual-classifications", json={
        "fields": [{"field": "some_other_field", "classification": "sensitive"}],
    })
    assert missing.status_code == 422
