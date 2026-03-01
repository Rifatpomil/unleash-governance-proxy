"""Pytest fixtures for governance proxy tests."""

import os
from typing import Generator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# SQLite by default for fast isolated tests; Postgres for integration
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "sqlite:///./test_governance.db",
)
TEST_JWT_SECRET = "test-secret"


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch):
    """Override settings for tests."""
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    # Clear cache so settings are reloaded
    from app.config import get_settings
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def engine():
    """Create test database engine (SQLite or Postgres)."""
    connect_args = {}
    if "sqlite" in TEST_DATABASE_URL:
        connect_args["check_same_thread"] = False
    _engine = create_engine(
        TEST_DATABASE_URL,
        connect_args=connect_args,
        pool_pre_ping="sqlite" not in TEST_DATABASE_URL,
    )
    return _engine


@pytest.fixture(scope="session")
def tables(engine):
    """Create tables once per session."""
    from app.db.models import Base
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(engine, tables) -> Generator[Session, None, None]:
    """Provide a clean database session per test."""
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def app(db_session):
    """Create FastAPI app with overridden get_db and mock Unleash."""
    from app.main import create_app
    from app.db import get_db
    from app.unleash_client import get_unleash_client

    app = create_app()

    def override_get_db():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db

    # Mock Unleash client to avoid real API calls
    class MockUnleashClient:
        def apply_change_request(self, **kwargs):
            return {"name": kwargs.get("feature_key", "test-flag"), "enabled": True}

    app.dependency_overrides[get_unleash_client] = lambda: MockUnleashClient()
    return app


@pytest.fixture
def client(app) -> TestClient:
    """Test client."""
    return TestClient(app)


def make_jwt(sub: str = "test-user", tenant: str = "default") -> str:
    """Create a valid JWT for testing."""
    payload = {"sub": sub, "tenant": tenant}
    return jwt.encode(
        payload,
        TEST_JWT_SECRET,
        algorithm="HS256",
    )


@pytest.fixture
def auth_headers():
    """Headers with valid JWT."""
    token = make_jwt()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def idempotency_key():
    """Unique idempotency key per test."""
    return f"test-key-{uuid4()}"
