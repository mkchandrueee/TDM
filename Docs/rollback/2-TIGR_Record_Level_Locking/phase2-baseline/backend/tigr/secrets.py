import os
from typing import Protocol


class SecretReferenceResolver(Protocol):
    """Resolve opaque references through an approved secret manager adapter."""

    def resolve(self, reference: str) -> str: ...


class UnconfiguredSecretResolver:
    def resolve(self, reference: str) -> str:
        raise RuntimeError("No approved secret manager is configured")


class EnvironmentSecretResolver:
    """Development adapter for explicitly named secret variables only."""

    def resolve(self, reference: str) -> str:
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
