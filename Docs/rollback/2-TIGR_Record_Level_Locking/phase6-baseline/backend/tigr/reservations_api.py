from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import String, cast, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tigr.access import record_audit, workspace_role
from tigr.dataset_api import authorize_dataset_read
from tigr.dataset_schemas import LineageEventCreate
from tigr.dataset_services import append_lineage_event
from tigr.datasets import DataSet, DataSetVersion
from tigr.governance import DataSetLock, DataSetReservation, DataUseRequest, EntityClaim
from tigr.iam import AccessGrant
from tigr.identity import HumanRole, Principal, get_db, get_principal

router = APIRouter(prefix="/api/v1", tags=["reservations and locks"])
Purpose = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class ReservationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    purpose: Purpose
    request_id: UUID | None = None


class DataUseRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    purpose: Purpose


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern="^(approved|denied)$")
    comment: str | None = Field(default=None, max_length=1000)


class LeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_seconds: int = Field(default=900, ge=60, le=3600)


class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fencing_token: int = Field(ge=1)


class RenewRequest(ReleaseRequest):
    lease_seconds: int = Field(default=900, ge=60, le=3600)


class ForceReleaseRequest(ReleaseRequest):
    reason: Purpose


def _dataset_version(db: Session, dataset_id: UUID, version_number: int, *, lock: bool = False) -> tuple[DataSet, DataSetVersion]:
    dataset = db.get(DataSet, dataset_id)
    if not dataset: raise HTTPException(status_code=404, detail="Data set not found")
    query=select(DataSetVersion).where(DataSetVersion.dataset_id==dataset_id,DataSetVersion.version_number==version_number)
    if lock: query=query.with_for_update()
    version=db.scalar(query)
    if not version: raise HTTPException(status_code=404,detail="Data set version not found")
    return dataset,version


def _authorized_use(db: Session, principal: Principal, dataset: DataSet, version: DataSetVersion) -> HumanRole:
    role=authorize_dataset_read(db,principal,dataset,version)
    if version.privacy_state!="compliant" or version.validation_state!="passed" or version.lifecycle_state!="published" or version.publication_state!="published":
        raise HTTPException(status_code=409,detail="Only published, privacy-compliant versions can be reserved or locked")
    if version.expiry_at and version.expiry_at<=datetime.now(UTC):
        raise HTTPException(status_code=409,detail="Expired data set versions cannot be reserved or locked")
    return role


def _reservation_out(item: DataSetReservation, can_manage: bool = False, dataset: DataSet | None = None) -> dict:
    return {"id":item.id,"workspace_id":item.workspace_id,"dataset_id":item.dataset_id,"version_id":item.version_id,
        "dataset_name":dataset.name if dataset else None,"dataset_reference_code":dataset.reference_code if dataset else None,
        "actor_id":item.actor_id,"request_id":item.request_id,"can_manage":can_manage,"starts_at":item.starts_at,"ends_at":item.ends_at,"purpose":item.purpose,"status":item.status,"created_at":item.created_at}


def _request_out(item: DataUseRequest, can_decide: bool = False, dataset: DataSet | None = None) -> dict:
    return {"id":item.id,"workspace_id":item.workspace_id,"dataset_id":item.dataset_id,"version_id":item.version_id,
        "dataset_name":dataset.name if dataset else None,"dataset_reference_code":dataset.reference_code if dataset else None,
        "requester_id":item.requester_id,"purpose":item.purpose,"starts_at":item.starts_at,"ends_at":item.ends_at,
        "status":item.status,"decided_by":item.decided_by,"decided_at":item.decided_at,"decision_comment":item.decision_comment,
        "created_at":item.created_at,"can_decide":can_decide}


@router.get("/workspaces/{workspace_id}/data-requests")
def list_data_use_requests(workspace_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> list[dict]:
    role = workspace_role(db, principal, workspace_id)
    query = select(DataUseRequest, DataSet).outerjoin(DataSet, DataSet.id == DataUseRequest.dataset_id).where(DataUseRequest.workspace_id == workspace_id)
    if role == HumanRole.QA_ENGINEER:
        query = query.where(DataUseRequest.requester_id == principal.subject)
    rows = db.execute(query.order_by(DataUseRequest.created_at.desc()).limit(300)).all()
    return [_request_out(row, role in (HumanRole.ADMIN, HumanRole.TEST_DATA_ENGINEER) and row.status == "pending", dataset)
            for row, dataset in rows]


@router.post("/datasets/{dataset_id}/versions/{version_number}/data-requests", status_code=201)
def create_data_use_request(dataset_id: UUID, version_number: int, payload: DataUseRequestCreate, request: Request,
                            db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> dict:
    dataset, version = _dataset_version(db, dataset_id, version_number)
    role = _authorized_use(db, principal, dataset, version)
    now = datetime.now(UTC)
    if role != HumanRole.QA_ENGINEER:
        raise HTTPException(status_code=403, detail="Only QA Engineers can request data use")
    if payload.starts_at < now - timedelta(minutes=1) or payload.ends_at <= payload.starts_at or payload.ends_at - payload.starts_at > timedelta(days=30):
        raise HTTPException(status_code=422, detail="Choose a future data-use window of no more than 30 days")
    if version.expiry_at and payload.ends_at > version.expiry_at:
        raise HTTPException(status_code=409, detail="The request must end before the data set version expires")
    item = DataUseRequest(id=uuid4(), workspace_id=dataset.workspace_id, dataset_id=dataset.id, version_id=version.id,
        requester_id=principal.subject, purpose=payload.purpose, starts_at=payload.starts_at, ends_at=payload.ends_at, status="pending")
    db.add(item)
    record_audit(db, principal, request.state.request_id, "data_use.requested", "data_use_request", str(item.id), dataset.workspace_id, role,
                 {"dataset_id":str(dataset.id),"version_number":version_number,"starts_at":payload.starts_at.isoformat(),"ends_at":payload.ends_at.isoformat()})
    _append_lineage(db, dataset.id, version.id, principal, "data_use_requested", "QA requested a bounded data-use window")
    db.commit(); db.refresh(item)
    return _request_out(item, dataset=dataset)


@router.post("/data-requests/{request_id}/decision")
def decide_data_use_request(request_id: UUID, payload: DecisionRequest, request: Request, db: Session = Depends(get_db),
                            principal: Principal = Depends(get_principal)) -> dict:
    item = db.scalar(select(DataUseRequest).where(DataUseRequest.id == request_id).with_for_update())
    if not item:
        raise HTTPException(status_code=404, detail="Data-use request not found")
    role = workspace_role(db, principal, item.workspace_id)
    if principal.actor_type != "human" or role not in (HumanRole.ADMIN, HumanRole.TEST_DATA_ENGINEER):
        raise HTTPException(status_code=403, detail="An Admin or Test Data Engineer must decide this request")
    if item.status != "pending":
        raise HTTPException(status_code=409, detail="Only pending data-use requests can be decided")
    reservation = None
    if payload.decision == "approved":
        dataset, version = _dataset_version(db, item.dataset_id, db.scalar(select(DataSetVersion.version_number).where(DataSetVersion.id == item.version_id)), lock=True)
        _authorized_use(db, principal, dataset, version)
        now = datetime.now(UTC)
        if item.ends_at <= now:
            raise HTTPException(status_code=409, detail="This reservation request has expired")
        overlaps = db.scalar(select(DataSetReservation.id).where(
            DataSetReservation.version_id == version.id,
            DataSetReservation.status.in_(("scheduled", "active")),
            DataSetReservation.starts_at < item.ends_at,
            DataSetReservation.ends_at > item.starts_at,
        ).limit(1))
        if overlaps:
            raise HTTPException(status_code=409, detail="This data set version already has an overlapping reservation")
        reservation = DataSetReservation(
            id=uuid4(), workspace_id=item.workspace_id, dataset_id=item.dataset_id, version_id=item.version_id,
            request_id=item.id, actor_id=item.requester_id, starts_at=item.starts_at, ends_at=item.ends_at,
            purpose=item.purpose, status="active" if item.starts_at <= now else "scheduled",
        )
        db.add(reservation)
    item.status = payload.decision
    item.decided_by = principal.subject
    item.decided_at = datetime.now(UTC)
    item.decision_comment = payload.comment
    record_audit(db, principal, request.state.request_id, f"data_use.{payload.decision}", "data_use_request", str(item.id), item.workspace_id, role,
                 {"dataset_id":str(item.dataset_id),"version_id":str(item.version_id),"requester_id":item.requester_id,"comment":payload.comment})
    _append_lineage(db, item.dataset_id, item.version_id, principal, f"data_use_{payload.decision}", f"Data-use request {payload.decision}")
    if reservation:
        _append_lineage(db, reservation.dataset_id, reservation.version_id, principal, "reservation_created", "Approved reservation request was booked")
        record_audit(db, principal, request.state.request_id, "reservation.created", "reservation", str(reservation.id), item.workspace_id, role,
            {"dataset_id":str(item.dataset_id),"version_id":str(item.version_id),"request_id":str(item.id),"starts_at":item.starts_at.isoformat(),"ends_at":item.ends_at.isoformat()})
    db.commit(); db.refresh(item)
    if reservation:
        db.refresh(reservation)
    return {**_request_out(item, dataset=db.get(DataSet, item.dataset_id)), "reservation": _reservation_out(reservation, True) if reservation else None}


def _lock_out(item: DataSetLock) -> dict:
    return {"id":item.id,"workspace_id":item.workspace_id,"dataset_id":item.dataset_id,"version_id":item.version_id,
        "actor_id":item.actor_id,"fencing_token":item.fencing_token,"acquired_at":item.acquired_at,"expires_at":item.expires_at,
        "released_at":item.released_at,"status":item.status,"release_reason":item.release_reason}


def _append_lineage(db: Session, dataset_id: UUID, version_id: UUID, principal: Principal, event_type: str, summary: str) -> None:
    dataset=db.get(DataSet,dataset_id);version=db.get(DataSetVersion,version_id)
    if dataset and version:
        append_lineage_event(db,dataset,version,LineageEventCreate(event_type=event_type,stage="allocation",safe_evidence={"summary":summary}),principal)


def _refresh_temporal_states(db: Session, workspace_id: UUID, principal: Principal, request_id: str) -> None:
    now=datetime.now(UTC)
    reservations=db.scalars(select(DataSetReservation).where(DataSetReservation.workspace_id==workspace_id,
        DataSetReservation.status.in_(("scheduled","active")),DataSetReservation.ends_at<=now).with_for_update()).all()
    role=workspace_role(db,principal,workspace_id)
    for row in reservations:
        row.status="expired"
        record_audit(db,principal,request_id,"reservation.expired","reservation",str(row.id),workspace_id,role,{"dataset_id":str(row.dataset_id)})
        _append_lineage(db,row.dataset_id,row.version_id,Principal(subject="tigr-allocation-service",actor_type="service"),"reservation_expired","Reservation window expired")
    scheduled=db.scalars(select(DataSetReservation).where(DataSetReservation.workspace_id==workspace_id,DataSetReservation.status=="scheduled",
        DataSetReservation.starts_at<=now,DataSetReservation.ends_at>now).with_for_update()).all()
    for row in scheduled:
        row.status="active"
        _append_lineage(db,row.dataset_id,row.version_id,Principal(subject="tigr-allocation-service",actor_type="service"),"reservation_activated","Reservation window started")
    locks=db.scalars(select(DataSetLock).where(DataSetLock.workspace_id==workspace_id,DataSetLock.status=="active",DataSetLock.expires_at<=now).with_for_update()).all()
    for row in locks:
        row.status="expired";row.released_at=now
        from tigr.entity_claims import close_entity_claims
        close_entity_claims(db, row.id, "expired", now, "lease_expired")
        record_audit(db,principal,request_id,"lock.expired","lock",str(row.id),workspace_id,role,{"dataset_id":str(row.dataset_id),"fencing_token":row.fencing_token})
        _append_lineage(db,row.dataset_id,row.version_id,Principal(subject="tigr-allocation-service",actor_type="service"),"lock_expired","Lock lease expired")
    if reservations or scheduled or locks: db.commit()


@router.get("/workspaces/{workspace_id}/reservations")
def list_reservations(workspace_id:UUID,request:Request,include_closed:bool=False,dataset_id:UUID|None=None,db:Session=Depends(get_db),principal:Principal=Depends(get_principal))->list[dict]:
    role=workspace_role(db,principal,workspace_id)
    _refresh_temporal_states(db,workspace_id,principal,request.state.request_id if request else "reservation-list")
    query=select(DataSetReservation,DataSet).outerjoin(DataSet,DataSet.id==DataSetReservation.dataset_id).where(DataSetReservation.workspace_id==workspace_id)
    if not include_closed: query=query.where(DataSetReservation.status.in_(("scheduled","active")))
    if dataset_id is not None:
        dataset=db.get(DataSet,dataset_id)
        if dataset is None or dataset.workspace_id!=workspace_id:
            raise HTTPException(status_code=404,detail="Data set not found")
        if role==HumanRole.QA_ENGINEER:
            version=db.scalar(select(DataSetVersion).where(DataSetVersion.dataset_id==dataset.id,
                DataSetVersion.lifecycle_state=="published",DataSetVersion.publication_state=="published",
                DataSetVersion.validation_state=="passed",DataSetVersion.privacy_state=="compliant",
                or_(DataSetVersion.expiry_at.is_(None),DataSetVersion.expiry_at>datetime.now(UTC)))
                .order_by(DataSetVersion.version_number.desc()).limit(1))
            if version is None:
                raise HTTPException(status_code=404,detail="Data set not found")
            authorize_dataset_read(db,principal,dataset,version)
            query=query.where(DataSetReservation.dataset_id==dataset.id,DataSetReservation.version_id==version.id)
        else:
            # Workspace managers can inspect the full reservation history
            # across every version of the selected data set.
            query=query.where(DataSetReservation.dataset_id==dataset.id)
    elif role==HumanRole.QA_ENGINEER:
        now=datetime.now(UTC)
        grant=select(AccessGrant.id).where(AccessGrant.subject==principal.subject,AccessGrant.workspace_id==workspace_id,
            AccessGrant.revoked_at.is_(None),AccessGrant.valid_from<=now,
            or_(AccessGrant.valid_until.is_(None),AccessGrant.valid_until>now),
            or_((AccessGrant.scope_type=="reservation")&(AccessGrant.scope_id==cast(DataSetReservation.id,String)),
                (AccessGrant.scope_type=="workspace")&(AccessGrant.scope_id.is_(None))),
            AccessGrant.permissions.contains(["reservation:read"])).exists()
        query=query.where(or_(DataSetReservation.actor_id==principal.subject,grant))
    rows=db.execute(query.order_by(DataSetReservation.starts_at.desc()).limit(200)).all()
    return [_reservation_out(row,row.actor_id==principal.subject or role in (HumanRole.ADMIN,HumanRole.TEST_DATA_ENGINEER),dataset)
            for row,dataset in rows]


@router.post("/datasets/{dataset_id}/versions/{version_number}/reservations",status_code=201)
def create_reservation(dataset_id:UUID,version_number:int,payload:ReservationCreate,request:Request,db:Session=Depends(get_db),principal:Principal=Depends(get_principal))->dict:
    dataset,version=_dataset_version(db,dataset_id,version_number,lock=True)
    role=_authorized_use(db,principal,dataset,version)
    now=datetime.now(UTC)
    if payload.starts_at<now-timedelta(minutes=1) or payload.ends_at<=payload.starts_at or payload.ends_at-payload.starts_at>timedelta(days=30):
        raise HTTPException(status_code=422,detail="Choose a future reservation window of no more than 30 days")
    if version.expiry_at and payload.ends_at>version.expiry_at:
        raise HTTPException(status_code=409,detail="Reservation must end before the data set version expires")
    data_request = db.scalar(select(DataUseRequest).where(DataUseRequest.id == payload.request_id,
        DataUseRequest.dataset_id == dataset.id, DataUseRequest.version_id == version.id,
        DataUseRequest.requester_id == principal.subject, DataUseRequest.status == "approved"))
    if role == HumanRole.QA_ENGINEER and (not data_request or payload.starts_at < data_request.starts_at or payload.ends_at > data_request.ends_at):
        raise HTTPException(status_code=409, detail="An approved request covering this version and reservation window is required")
    overlaps=db.scalar(select(DataSetReservation.id).where(DataSetReservation.version_id==version.id,
        DataSetReservation.status.in_(("scheduled","active")),DataSetReservation.starts_at<payload.ends_at,DataSetReservation.ends_at>payload.starts_at).limit(1))
    if overlaps: raise HTTPException(status_code=409,detail="This data set version already has an overlapping reservation")
    status="active" if payload.starts_at<=now else "scheduled"
    item=DataSetReservation(id=uuid4(),workspace_id=dataset.workspace_id,dataset_id=dataset.id,version_id=version.id,
        request_id=data_request.id if data_request else None,actor_id=principal.subject,starts_at=payload.starts_at,ends_at=payload.ends_at,purpose=payload.purpose,status=status)
    db.add(item)
    _append_lineage(db,dataset.id,version.id,principal,"reservation_created","A planned use window was reserved")
    record_audit(db,principal,request.state.request_id,"reservation.created","reservation",str(item.id),dataset.workspace_id,role,
        {"dataset_id":str(dataset.id),"version_number":version_number,"starts_at":payload.starts_at.isoformat(),"ends_at":payload.ends_at.isoformat()})
    db.commit();db.refresh(item);return _reservation_out(item,True)


@router.post("/reservations/{reservation_id}/cancel")
def cancel_reservation(reservation_id:UUID,request:Request,db:Session=Depends(get_db),principal:Principal=Depends(get_principal))->dict:
    item=db.scalar(select(DataSetReservation).where(DataSetReservation.id==reservation_id).with_for_update())
    if not item: raise HTTPException(status_code=404,detail="Reservation not found")
    role=workspace_role(db,principal,item.workspace_id)
    if role not in (HumanRole.ADMIN,HumanRole.TEST_DATA_ENGINEER) and item.actor_id!=principal.subject: raise HTTPException(status_code=404,detail="Reservation not found")
    if item.status not in ("scheduled","active"): raise HTTPException(status_code=409,detail="Reservation is already closed")
    item.status="cancelled"
    _append_lineage(db,item.dataset_id,item.version_id,principal,"reservation_cancelled","Reservation was cancelled")
    record_audit(db,principal,request.state.request_id,"reservation.cancelled","reservation",str(item.id),item.workspace_id,role,{"dataset_id":str(item.dataset_id)})
    db.commit();db.refresh(item);return _reservation_out(item,item.actor_id==principal.subject or role in (HumanRole.ADMIN,HumanRole.TEST_DATA_ENGINEER))


@router.get("/datasets/{dataset_id}/versions/{version_number}/locks/active")
def inspect_active_lock(dataset_id:UUID,version_number:int,request:Request,db:Session=Depends(get_db),principal:Principal=Depends(get_principal))->dict|None:
    dataset,version=_dataset_version(db,dataset_id,version_number)
    role=workspace_role(db,principal,dataset.workspace_id)
    if role==HumanRole.QA_ENGINEER: _authorized_use(db,principal,dataset,version)
    _refresh_temporal_states(db,dataset.workspace_id,principal,request.state.request_id if request else "lock-inspect")
    item=db.scalar(select(DataSetLock).where(DataSetLock.version_id==version.id,DataSetLock.status=="active"))
    if item and role==HumanRole.QA_ENGINEER and item.actor_id!=principal.subject:
        return {**_lock_out(item),"actor_id":None,"can_manage":False,"status":"in_use"}
    return ({**_lock_out(item),"can_manage":item.actor_id==principal.subject} if item else None)


@router.post("/datasets/{dataset_id}/versions/{version_number}/locks",status_code=201)
def acquire_lock(dataset_id:UUID,version_number:int,payload:LeaseRequest,request:Request,db:Session=Depends(get_db),principal:Principal=Depends(get_principal))->dict:
    dataset,version=_dataset_version(db,dataset_id,version_number,lock=True)
    role=_authorized_use(db,principal,dataset,version)
    now=datetime.now(UTC)
    _refresh_temporal_states(db, dataset.workspace_id, principal, request.state.request_id)
    if role == HumanRole.QA_ENGINEER:
        reservation = db.scalar(select(DataSetReservation).where(DataSetReservation.version_id == version.id,
            DataSetReservation.actor_id == principal.subject, DataSetReservation.status.in_(("scheduled", "active")),
            DataSetReservation.starts_at <= now, DataSetReservation.ends_at > now))
        use_request = db.get(DataUseRequest, reservation.request_id) if reservation and reservation.request_id else None
        if not reservation or not use_request or use_request.status != "approved":
            raise HTTPException(status_code=409, detail="An active reservation backed by an approved data-use request is required")
    expired=db.scalars(select(DataSetLock).where(DataSetLock.version_id==version.id,DataSetLock.status=="active",DataSetLock.expires_at<=now).with_for_update()).all()
    for row in expired:
        row.status="expired";row.released_at=now
        from tigr.entity_claims import close_entity_claims
        close_entity_claims(db, row.id, "expired", now, "lease_expired")
        record_audit(db,principal,request.state.request_id,"lock.expired","lock",str(row.id),dataset.workspace_id,role,{"fencing_token":row.fencing_token})
        _append_lineage(db,row.dataset_id,row.version_id,Principal(subject="tigr-allocation-service",actor_type="service"),"lock_expired","Lock lease expired")
    current=db.scalar(select(DataSetLock).where(DataSetLock.version_id==version.id,DataSetLock.status=="active").with_for_update())
    if current: raise HTTPException(status_code=409,detail="This data set version is currently locked")
    next_token=(db.scalar(select(func.max(DataSetLock.fencing_token)).where(DataSetLock.version_id==version.id)) or 0)+1
    item=DataSetLock(id=uuid4(),workspace_id=dataset.workspace_id,dataset_id=dataset.id,version_id=version.id,actor_id=principal.subject,actor_role=role.value,
        fencing_token=next_token,acquired_at=now,expires_at=now+timedelta(seconds=payload.lease_seconds),status="active")
    if version.expiry_at and item.expires_at>version.expiry_at:
        raise HTTPException(status_code=409,detail="Lock lease cannot extend past the data set version expiry")
    db.add(item)
    from tigr.entity_claims import create_entity_claims
    try:
        with db.begin_nested():
            db.flush()
            claim_count = create_entity_claims(db, dataset, version, item, principal.subject, now)
            db.flush()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="These records overlap another active lock. Retry after the lease expires or is released, or contact your workspace Admin or Test Data Engineer.") from exc
    _append_lineage(db,dataset.id,version.id,principal,"lock_acquired","Exclusive TIGR lock lease acquired")
    record_audit(db,principal,request.state.request_id,"lock.acquired","lock",str(item.id),dataset.workspace_id,role,
        {"dataset_id":str(dataset.id),"version_number":version_number,"fencing_token":next_token,"lease_seconds":payload.lease_seconds,
         "entity_claim_count":claim_count})
    db.commit();db.refresh(item);return {**_lock_out(item),"can_manage":True}


@router.post("/locks/{lock_id}/renew")
def renew_lock(lock_id:UUID,payload:RenewRequest,request:Request,db:Session=Depends(get_db),principal:Principal=Depends(get_principal))->dict:
    item=db.scalar(select(DataSetLock).where(DataSetLock.id==lock_id).with_for_update())
    if not item: raise HTTPException(status_code=404,detail="Lock not found")
    role=workspace_role(db,principal,item.workspace_id);now=datetime.now(UTC)
    if item.actor_id!=principal.subject or role==HumanRole.ADMIN: raise HTTPException(status_code=404,detail="Lock not found")
    if item.expires_at<=now and item.status=="active":
        item.status="expired";item.released_at=now
        from tigr.entity_claims import close_entity_claims
        close_entity_claims(db, item.id, "expired", now, "lease_expired")
        record_audit(db,principal,request.state.request_id,"lock.expired","lock",str(item.id),item.workspace_id,role,{"fencing_token":item.fencing_token})
        _append_lineage(db,item.dataset_id,item.version_id,Principal(subject="tigr-allocation-service",actor_type="service"),"lock_expired","Lock lease expired")
        db.commit()
        raise HTTPException(status_code=409,detail="Lock lease or fencing token is stale")
    if item.status!="active" or item.fencing_token!=payload.fencing_token: raise HTTPException(status_code=409,detail="Lock lease or fencing token is stale")
    dataset=db.get(DataSet,item.dataset_id);version=db.get(DataSetVersion,item.version_id)
    if not dataset or not version: raise HTTPException(status_code=404,detail="Lock not found")
    _authorized_use(db,principal,dataset,version)
    item.expires_at=now+timedelta(seconds=payload.lease_seconds)
    if version.expiry_at and item.expires_at>version.expiry_at:
        raise HTTPException(status_code=409,detail="Lock lease cannot extend past the data set version expiry")
    db.execute(update(EntityClaim).where(EntityClaim.lock_id == item.id, EntityClaim.status == "active").values(expires_at=item.expires_at))
    _append_lineage(db,item.dataset_id,item.version_id,principal,"lock_renewed","Exclusive lock lease renewed")
    record_audit(db,principal,request.state.request_id,"lock.renewed","lock",str(item.id),item.workspace_id,role,{"fencing_token":item.fencing_token,"expires_at":item.expires_at.isoformat()})
    db.commit();db.refresh(item);return _lock_out(item)


@router.post("/locks/{lock_id}/release")
def release_lock(lock_id:UUID,payload:ReleaseRequest,request:Request,db:Session=Depends(get_db),principal:Principal=Depends(get_principal))->dict:
    item=db.scalar(select(DataSetLock).where(DataSetLock.id==lock_id).with_for_update())
    if not item: raise HTTPException(status_code=404,detail="Lock not found")
    role=workspace_role(db,principal,item.workspace_id)
    if item.status=="active" and item.expires_at<=datetime.now(UTC):
        item.status="expired";item.released_at=datetime.now(UTC)
        from tigr.entity_claims import close_entity_claims
        close_entity_claims(db, item.id, "expired", item.released_at, "lease_expired")
        record_audit(db,principal,request.state.request_id,"lock.expired","lock",str(item.id),item.workspace_id,role,{"fencing_token":item.fencing_token})
        _append_lineage(db,item.dataset_id,item.version_id,Principal(subject="tigr-allocation-service",actor_type="service"),"lock_expired","Lock lease expired")
        db.commit()
        raise HTTPException(status_code=409,detail="Lock lease or fencing token is stale")
    if item.actor_id!=principal.subject or role==HumanRole.ADMIN: raise HTTPException(status_code=404,detail="Lock not found")
    if item.status!="active" or item.fencing_token!=payload.fencing_token: raise HTTPException(status_code=409,detail="Lock lease or fencing token is stale")
    item.status="released";item.released_at=datetime.now(UTC)
    from tigr.entity_claims import close_entity_claims
    close_entity_claims(db, item.id, "released", item.released_at, "owner_released")
    _append_lineage(db,item.dataset_id,item.version_id,principal,"lock_released","Exclusive lock lease released")
    record_audit(db,principal,request.state.request_id,"lock.released","lock",str(item.id),item.workspace_id,role,{"fencing_token":item.fencing_token})
    db.commit();db.refresh(item);return _lock_out(item)


@router.post("/locks/{lock_id}/force-release")
def force_release_lock(lock_id:UUID,payload:ForceReleaseRequest,request:Request,db:Session=Depends(get_db),principal:Principal=Depends(get_principal))->dict:
    item=db.scalar(select(DataSetLock).where(DataSetLock.id==lock_id).with_for_update())
    if not item: raise HTTPException(status_code=404,detail="Lock not found")
    role=workspace_role(db,principal,item.workspace_id)
    if role!=HumanRole.ADMIN: raise HTTPException(status_code=403,detail="Administrator access is required for force release")
    if item.status!="active" or item.fencing_token!=payload.fencing_token: raise HTTPException(status_code=409,detail="Lock lease or fencing token is stale")
    item.status="force_released";item.released_at=datetime.now(UTC);item.release_reason=payload.reason
    from tigr.entity_claims import close_entity_claims
    close_entity_claims(db, item.id, "force_released", item.released_at, "administrator_force_release")
    _append_lineage(db,item.dataset_id,item.version_id,principal,"lock_force_released","Administrator force-released the lock")
    record_audit(db,principal,request.state.request_id,"lock.force_released","lock",str(item.id),item.workspace_id,role,
        {"fencing_token":item.fencing_token,"reason":payload.reason,"actor_id":item.actor_id})
    db.commit();db.refresh(item);return _lock_out(item)


@router.post("/locks/{lock_id}/validate")
def validate_fencing_token(lock_id:UUID,payload:ReleaseRequest,request:Request,db:Session=Depends(get_db),principal:Principal=Depends(get_principal))->dict:
    item=db.get(DataSetLock,lock_id)
    if not item: raise HTTPException(status_code=404,detail="Lock not found")
    dataset=db.get(DataSet,item.dataset_id);version=db.get(DataSetVersion,item.version_id)
    if not dataset or not version: raise HTTPException(status_code=404,detail="Lock not found")
    role: HumanRole | None = None
    if principal.actor_type!="service":
        role=workspace_role(db,principal,item.workspace_id)
        if role==HumanRole.QA_ENGINEER and item.actor_id!=principal.subject: raise HTTPException(status_code=404,detail="Lock not found")
        _authorized_use(db,principal,dataset,version)
    else:
        if principal.service_workspace_id!=str(item.workspace_id) or "lock:use" not in principal.service_scopes: raise HTTPException(status_code=403,detail="Service identity scope does not allow lock use")
    now = datetime.now(UTC)
    valid=item.status=="active" and item.expires_at>now and item.fencing_token==payload.fencing_token and version.lifecycle_state=="published" and version.privacy_state=="compliant"
    if valid:
        from tigr.entity_claims import ensure_claims_current
        try:
            ensure_claims_current(db, item, payload.fencing_token, now)
        except HTTPException:
            valid = False
    record_audit(db,principal,request.state.request_id,"lock.fencing_checked","lock",str(item.id),item.workspace_id,role,
        {"fencing_token":payload.fencing_token,"valid":valid})
    db.commit()
    return {"valid":valid,"fencing_token":item.fencing_token,"expires_at":item.expires_at,"dataset_id":item.dataset_id,"version_id":item.version_id}
