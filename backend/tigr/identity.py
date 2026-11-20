from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from hashlib import sha256
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from tigr.config import get_settings
from tigr.db import SessionLocal
from tigr.iam import PlatformAdmin, ServiceIdentity, UserIdentity, Workspace, WorkspaceMembership


class HumanRole(StrEnum):
    TEST_DATA_ENGINEER = "Test Data Engineer"
    QA_ENGINEER = "QA Engineer"
    ADMIN = "Admin"


@dataclass(frozen=True)
class Principal:
    subject: str
    email: str | None = None
    display_name: str | None = None
    development_role: HumanRole | None = None
    token_id: str | None = None
    actor_type: str = "human"
    service_workspace_id: str | None = None
    service_scopes: tuple[str, ...] = ()


bearer = HTTPBearer(auto_error=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@lru_cache
def jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_keys=True, timeout=5)


def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Session = Depends(get_db),
) -> Principal:
    settings = get_settings()
    if settings.auth_mode == "oidc":
        if not settings.oidc_configured:
            raise HTTPException(status_code=503, detail="Identity provider configuration is incomplete")
        if credentials is None:
            raise HTTPException(status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Bearer"})
        try:
            signing_key = jwks_client(settings.oidc_jwks_url).get_signing_key_from_jwt(credentials.credentials)
            claims = jwt.decode(
                credentials.credentials,
                signing_key.key,
                algorithms=[algorithm for algorithm in settings.oidc_algorithms_list if algorithm.lower() != "none"],
                audience=settings.oidc_audience,
                issuer=settings.oidc_issuer,
                options={"require": ["exp", "sub"]},
            )
            subject = claims.get("sub")
            if not isinstance(subject, str) or not subject or len(subject) > 180:
                raise jwt.InvalidTokenError("Invalid subject")
            token_id = claims.get("sid") or claims.get("jti") or sha256(credentials.credentials.encode()).hexdigest()[:32]
            service_identity = db.get(ServiceIdentity, subject)
            if service_identity:
                if not service_identity.enabled:
                    raise jwt.InvalidTokenError("Service identity is disabled")
                return Principal(
                    subject=subject,
                    display_name=service_identity.display_name,
                    token_id=token_id,
                    actor_type="service",
                    service_workspace_id=str(service_identity.workspace_id),
                    service_scopes=tuple(service_identity.scopes),
                )
            return Principal(
                subject=subject,
                email=claims.get("email") if isinstance(claims.get("email"), str) else None,
                display_name=claims.get("name") if isinstance(claims.get("name"), str) else None,
                token_id=token_id,
            )
        except Exception as exc:
            # Keep provider/network details out of API responses and logs.
            raise HTTPException(status_code=401, detail="Invalid authentication token", headers={"WWW-Authenticate": "Bearer"}) from exc
    if settings.env == "development" and settings.auth_mode == "development":
        subject = request.headers.get("X-Dev-Subject", "")
        if not subject or len(subject) > 180 or any(ord(char) < 32 for char in subject):
            raise HTTPException(status_code=401, detail="Development sign-in is required")
        identity = db.get(UserIdentity, subject)
        if identity is None:
            raise HTTPException(status_code=401, detail="Development sign-in is required")
        return Principal(
            subject=subject,
            email=identity.email,
            display_name=identity.display_name or subject,
        )
    raise HTTPException(status_code=503, detail="Identity provider is not configured")


def is_platform_admin(db: Session, principal: Principal) -> bool:
    if principal.development_role == HumanRole.ADMIN:
        return True
    if db.scalar(select(PlatformAdmin.subject).where(PlatformAdmin.subject == principal.subject)) is not None:
        return True
    now = datetime.now(UTC)
    return db.scalar(
        select(WorkspaceMembership.workspace_id)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(
            WorkspaceMembership.subject == principal.subject,
            WorkspaceMembership.role == HumanRole.ADMIN.value,
            WorkspaceMembership.revoked_at.is_(None),
            WorkspaceMembership.valid_from <= now,
            or_(WorkspaceMembership.valid_until.is_(None), WorkspaceMembership.valid_until > now),
            Workspace.status == "active",
        )
        .limit(1)
    ) is not None
