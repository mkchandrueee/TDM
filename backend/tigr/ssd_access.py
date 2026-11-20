"""Apply SSD visibility to every request-scoped ORM inventory, join and aggregate."""

from fastapi import Depends
from sqlalchemy import event, or_, select
from sqlalchemy.orm import Session, with_loader_criteria

from tigr.datasets import DataSet, DataSetField, DataSetLineageEvent, DataSetVersion
from tigr.delivery import ProvisioningRun
from tigr.governance import DataSetLock, DataSetReservation, DataUseRequest, MaskingRun, PiiFinding
from tigr.identity import get_db, get_principal
from tigr.jobs import Job
from tigr.schema_records import ArtifactRecord, DataSetRecord, DatasetStatusHistory, ValidationRun


def bind_actor(db=Depends(get_db), principal=Depends(get_principal)):
    db.info["ssd_actor"] = principal.subject


@event.listens_for(Session, "do_orm_execute")
def private_catalog_scope(state):
    actor = state.session.info.get("ssd_actor")
    if not actor or not state.is_select or state.execution_options.get("ssd_unscoped"):
        return
    state.statement = state.statement.options(
        with_loader_criteria(
            DataSet,
            lambda d: or_(d.visibility.is_(None), d.visibility == "public", d.owner_id == actor),
            include_aliases=True,
        ),
        with_loader_criteria(
            DataSetVersion,
            lambda v: v.dataset_id.in_(
                select(DataSet.id).where(
                    or_(
                        DataSet.visibility.is_(None),
                        DataSet.visibility == "public",
                        DataSet.owner_id == actor,
                    )
                )
            ),
            include_aliases=True,
        ),
        with_loader_criteria(
            Job, lambda j: or_(~j.job_type.like("ssd_%"), j.actor_id == actor), include_aliases=True
        ),
    )
    for model in (
        DataSetLock,
        DataSetReservation,
        DataUseRequest,
        MaskingRun,
        ProvisioningRun,
        ArtifactRecord,
        DataSetRecord,
        DatasetStatusHistory,
        PiiFinding,
        DataSetField,
        DataSetLineageEvent,
        ValidationRun,
    ):
        state.statement = state.statement.options(
            with_loader_criteria(
                model,
                lambda row: row.dataset_id.in_(
                    select(DataSet.id).where(
                        or_(
                            DataSet.visibility.is_(None),
                            DataSet.visibility == "public",
                            DataSet.owner_id == actor,
                        )
                    )
                ),
                include_aliases=True,
            )
        )
