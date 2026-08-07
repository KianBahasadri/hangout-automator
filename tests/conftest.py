import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='hangout-test-')}/test.db"
os.environ["SMS_PROVIDER"] = "mock"
os.environ["FOLLOWUP_HOURS"] = "1,2"

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal, init_db
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    init_db()
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean_tables(db):
    yield
    from app.database import Base, engine

    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_no_raise():
    """Return HTTP 500 responses so API tests can assert no exception escapes."""
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
