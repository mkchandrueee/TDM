"""Keep generated data sets linked to the TDE test data request that initiated them."""
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from tigr.datasets import DataSet, DataSetVersion
from tigr.identity import HumanRole, Principal
from tigr.ticket_models import Ticket, TicketEvent


def link_generation_to_ticket(
    db: Session,
    ticket_id: UUID | None,
    workspace_id: UUID,
    dataset: DataSet,
    version: DataSetVersion,
    principal: Principal,
    role: HumanRole,
) -> None:
    """Validate and link an optional TDE request within the generation transaction."""
    if ticket_id is None:
        return
    if principal.actor_type != "human" or role != HumanRole.TEST_DATA_ENGINEER:
        raise HTTPException(status_code=403, detail="Mapping a test data request requires Test Data Engineer access")
    ticket = db.scalar(select(Ticket).where(
        Ticket.id == ticket_id,
        Ticket.workspace_id == workspace_id,
    ).with_for_update())
    if ticket is None or ticket.status == "Draft":
        raise HTTPException(status_code=404, detail="Test data request not found in this workspace")
    if ticket.assignee_user_id != principal.subject:
        raise HTTPException(status_code=403, detail="Only the assigned Test Data Engineer can link a data set")
    if ticket.status != "In Progress":
        raise HTTPException(status_code=409, detail="Start work on the test data request before linking a data set")
    if dataset.workspace_id != ticket.workspace_id or version.dataset_id != dataset.id or version.workspace_id != workspace_id:
        raise HTTPException(status_code=422, detail="The test data request and generated data set must belong to the same workspace")

    previous_dataset_id = ticket.linked_dataset_id
    previous_version_id = ticket.linked_dataset_version_id
    ticket.linked_dataset_id = dataset.id
    ticket.linked_dataset_version_id = version.id
    ticket.updated_at = datetime.now(UTC)
    ticket.row_version += 1
    db.add(TicketEvent(
        id=uuid4(), ticket_id=ticket.id, event_type="dataset_linked",
        actor_user_id=principal.subject, actor_role=role.value,
        event_data={
            "dataset_id": str(dataset.id), "version_id": str(version.id),
            "mapped_during_generation": True,
            "previous_dataset_id": str(previous_dataset_id) if previous_dataset_id else None,
            "previous_version_id": str(previous_version_id) if previous_version_id else None,
        },
        workspace_id=workspace_id, project_id=ticket.project_id,
        correlation_id=ticket.correlation_id,
    ))
