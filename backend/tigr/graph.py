from typing import Protocol


class LineageProjection(Protocol):
    """Optional Neo4j projection; PostgreSQL remains the authoritative store."""

    def project_event(self, event_id: str, event: dict) -> None: ...


class UnconfiguredLineageProjection:
    def project_event(self, event_id: str, event: dict) -> None:
        raise RuntimeError("Neo4j lineage projection is not configured")
