"""Passwordless sign-in options for local development only."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from tigr.config import get_settings
from tigr.iam import UserIdentity, Workspace, WorkspaceMembership
from tigr.identity import HumanRole, get_db

router = APIRouter(prefix="/api/v1/dev-auth", tags=["development sign-in"])


class DevelopmentLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=180)
    workspace_id: UUID
    password: str = Field(default="", max_length=256)


def require_development_auth() -> None:
    settings = get_settings()
    if settings.env != "development" or settings.auth_mode != "development":
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/options")
def development_login_options(db: Session = Depends(get_db)) -> list[dict]:
    require_development_auth()
    now = datetime.now(UTC)
    rows = db.execute(
        select(UserIdentity, WorkspaceMembership, Workspace)
        .join(WorkspaceMembership, WorkspaceMembership.subject == UserIdentity.subject)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(
            Workspace.status == "active",
            WorkspaceMembership.revoked_at.is_(None),
            WorkspaceMembership.valid_from <= now,
            or_(WorkspaceMembership.valid_until.is_(None), WorkspaceMembership.valid_until > now),
        )
        .order_by(UserIdentity.display_name, Workspace.name, WorkspaceMembership.role)
    ).all()
    return [
        {
            "subject": user.subject,
            "display_name": user.display_name or user.subject,
            "workspace_id": str(workspace.id),
            "workspace_name": workspace.name,
            "role": HumanRole(membership.role).value,
        }
        for user, membership, workspace in rows
    ]


@router.post("/login")
def development_login(payload: DevelopmentLoginRequest, db: Session = Depends(get_db)) -> dict:
    require_development_auth()
    if payload.password != "":
        raise HTTPException(status_code=401, detail="Use a blank password for local development sign-in")

    now = datetime.now(UTC)
    user = db.get(UserIdentity, payload.subject)
    membership = db.scalar(
        select(WorkspaceMembership)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(
            WorkspaceMembership.subject == payload.subject,
            WorkspaceMembership.workspace_id == payload.workspace_id,
            WorkspaceMembership.revoked_at.is_(None),
            WorkspaceMembership.valid_from <= now,
            or_(WorkspaceMembership.valid_until.is_(None), WorkspaceMembership.valid_until > now),
            Workspace.status == "active",
        )
    )
    if user is None or membership is None:
        raise HTTPException(status_code=401, detail="No active development access was found for this user")
    workspace = db.get(Workspace, payload.workspace_id)
    user.last_seen_at = now
    db.commit()
    return {
        "subject": user.subject,
        "display_name": user.display_name or user.subject,
        "workspace_id": str(payload.workspace_id),
        "workspace_name": workspace.name,
        "role": HumanRole(membership.role).value,
        "last_seen_at": now.isoformat(),
    }
