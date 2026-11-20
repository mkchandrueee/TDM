from collections.abc import Callable
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tigr.db import engine
from tigr.identity import HumanRole, Principal, get_db, get_principal
from tigr.main import app


@pytest.fixture
def app_context() -> Callable:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    current = {"principal": Principal(subject=f"test-admin-{uuid4()}", development_role=HumanRole.ADMIN)}

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_principal] = lambda: current["principal"]
    try:
        with TestClient(app) as client:
            yield client, session, current
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()
