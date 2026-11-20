import json
import os
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.engine import URL

from tigr.config import get_settings

ENCRYPTED_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    key = get_settings().credential_encryption_key
    if not key:
        raise RuntimeError("TIGR_CREDENTIAL_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("TIGR_CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key") from exc


def encrypt_credentials(connector_type: str, credentials: dict[str, object]) -> str:
    """Encrypt a source credential payload before it is stored in the application database."""
    payload = json.dumps({"connector_type": connector_type, **credentials}, separators=(",", ":"))
    token = _fernet().encrypt(payload.encode("utf-8")).decode("ascii")
    return ENCRYPTED_PREFIX + token


def credentials_to_url(connector_type: str, credentials: dict[str, object], database: str | None = None) -> str:
    drivername = {
        "postgresql": "postgresql+psycopg",
        "mysql": "mysql+pymysql",
        "oracle": "oracle+oracledb",
        "mssql": "mssql+pymssql",
    }.get(connector_type)
    if not drivername:
        raise RuntimeError("Unsupported credential connector")
    selected_database = database or credentials.get("initial_database")
    if connector_type == "oracle":
        url = URL.create(
            drivername=drivername,
            username=str(credentials["username"]),
            password=str(credentials.get("password", "")) or None,
            host=str(credentials["host"]),
            port=int(credentials["port"]),
            query={"service_name": str(selected_database)} if selected_database else {},
        )
        return url.render_as_string(hide_password=False)
    url = URL.create(
        drivername=drivername,
        username=str(credentials["username"]),
        password=str(credentials.get("password", "")) or None,
        host=str(credentials["host"]),
        port=int(credentials["port"]),
        database=str(selected_database) if selected_database else None,
    )
    return url.render_as_string(hide_password=False)


def decrypt_credentials(reference: str) -> dict[str, object]:
    if not reference.startswith(ENCRYPTED_PREFIX):
        raise RuntimeError("Credential reference is not encrypted")
    try:
        payload = _fernet().decrypt(reference[len(ENCRYPTED_PREFIX):].encode("ascii"))
        data = json.loads(payload.decode("utf-8"))
    except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("Stored credentials could not be decrypted") from exc
    if not isinstance(data, dict):
        raise TypeError("Stored credentials are invalid")
    return data


class SecretReferenceResolver(Protocol):
    """Resolve opaque references through an approved secret manager adapter."""

    def resolve(self, reference: str) -> str: ...


class UnconfiguredSecretResolver:
    def resolve(self, reference: str) -> str:
        if reference.startswith(ENCRYPTED_PREFIX):
            data = decrypt_credentials(reference)
            connector_type = str(data.pop("connector_type", ""))
            return credentials_to_url(connector_type, data)
        raise RuntimeError("No approved secret manager is configured")


class EnvironmentSecretResolver:
    """Resolve encrypted TIGR credentials and legacy development env references."""

    def resolve(self, reference: str) -> str:
        if reference.startswith(ENCRYPTED_PREFIX):
            data = decrypt_credentials(reference)
            connector_type = str(data.pop("connector_type", ""))
            return credentials_to_url(connector_type, data)
        prefix = "env://"
        if not reference.startswith(prefix):
            raise RuntimeError("Secret reference cannot be resolved")
        name = reference[len(prefix):]
        if not name.startswith("TIGR_SECRET_") or not name.isidentifier():
            raise RuntimeError("Secret reference cannot be resolved")
        value = os.environ.get(name)
        if not value:
            raise RuntimeError("Referenced secret is not configured")
        return value
