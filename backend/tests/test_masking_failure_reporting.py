from types import SimpleNamespace
from uuid import uuid4

from tigr.privacy_api import _masking_failure_lineage_event, _safe_masking_failure_code


def test_missing_identity_secret_has_a_safe_failure_code():
    assert _safe_masking_failure_code(
        ValueError("identity_secret_unavailable"), "source_refresh"
    ) == "identity_secret_unavailable"


def test_masking_failure_lineage_uses_allowlisted_safe_metadata():
    job = SimpleNamespace(id=uuid4(), safe_summary="Masking failed during source refresh")

    event = _masking_failure_lineage_event(job, "identity_secret_unavailable")

    assert event.event_type == "masking_failed"
    assert event.outcome == "failed"
    assert event.safe_evidence == {
        "summary": "Masking failed during source refresh",
        "reason_code": "identity_secret_unavailable",
    }
