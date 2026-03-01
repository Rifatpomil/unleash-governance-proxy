"""API tests: auth required, forbidden, idempotency, audit."""

import pytest
from fastapi.testclient import TestClient

from app.db.models import AuditLog, ChangeRequest


def test_auth_required(client: TestClient):
    """All endpoints require JWT - 401 without token."""
    # Create change request - no auth
    r = client.post(
        "/v1/flags/my-flag/change-request",
        json={
            "desired_changes": {"description": "test"},
        },
    )
    assert r.status_code == 403  # HTTPBearer returns 403 when no header

    # Approve - no auth
    r = client.post("/v1/change-requests/some-id/approve")
    assert r.status_code == 403

    # Apply - no auth
    r = client.post(
        "/v1/change-requests/some-id/apply",
        headers={"Idempotency-Key": "key-1"},
    )
    assert r.status_code == 403


def test_forbidden_without_permission(
    client: TestClient,
    auth_headers: dict,
    db_session,
):
    """403 when user lacks can_edit_feature permission."""
    # Policy allows alice@acme, not bob. Use bob.
    from app.authorization import AuthorizationService
    from app.main import app
    from app.authorization import get_authorization_service

    class DenyAllPolicy(AuthorizationService):
        def can_edit_feature(self, user_id, tenant, feature_key):
            return False

    app.dependency_overrides[get_authorization_service] = lambda: DenyAllPolicy()

    r = client.post(
        "/v1/flags/my-flag/change-request",
        json={"desired_changes": {"description": "test"}},
        headers=auth_headers,
    )
    assert r.status_code == 403
    assert "Forbidden" in r.json()["detail"]


def test_idempotency_works(
    client: TestClient,
    auth_headers: dict,
    idempotency_key: str,
    db_session,
):
    """Duplicate apply with same Idempotency-Key returns cached response."""
    from app.db.models import ChangeRequest
    from app.authorization import get_authorization_service
    from datetime import datetime, timezone
    from app.main import app

    class AllowAllPolicy:
        def can_edit_feature(self, user_id, tenant, feature_key):
            return True

    app.dependency_overrides[get_authorization_service] = lambda: AllowAllPolicy()

    # Create and approve a change request
    cr = ChangeRequest(
        id="00000000-0000-0000-0000-000000000001",
        flag_key="test-flag",
        project_id="default",
        desired_changes={"description": "test"},
        status="approved",
        created_by="test-user",
        approved_by="test-user",
        approved_at=datetime.now(timezone.utc),
    )
    db_session.add(cr)
    db_session.commit()
    cr_id = cr.id

    headers = {**auth_headers, "Idempotency-Key": idempotency_key}

    # First apply
    r1 = client.post(f"/v1/change-requests/{cr_id}/apply", headers=headers)
    assert r1.status_code == 200
    body1 = r1.json()

    # Second apply with same key - should return same response (idempotent)
    r2 = client.post(f"/v1/change-requests/{cr_id}/apply", headers=headers)
    assert r2.status_code == 200
    body2 = r2.json()

    assert body1["change_request_id"] == body2["change_request_id"]
    assert body1["status"] == body2["status"]


def test_audit_row_created(
    client: TestClient,
    auth_headers: dict,
    db_session,
):
    """Creating change request writes audit log."""
    from app.authorization import get_authorization_service

    class AllowAllPolicy:
        def can_edit_feature(self, user_id, tenant, feature_key):
            return True

    from app.main import app
    app.dependency_overrides[get_authorization_service] = lambda: AllowAllPolicy()

    r = client.post(
        "/v1/flags/audit-test-flag/change-request",
        json={"desired_changes": {"description": "audit test"}},
        headers=auth_headers,
    )
    assert r.status_code == 201

    # Check audit log
    entry = db_session.query(AuditLog).filter(
        AuditLog.action == "change_request_created"
    ).first()
    assert entry is not None
    assert entry.actor == "test-user"
    assert entry.resource_type == "change_request"
    assert "audit-test-flag" in str(entry.after_payload)


def test_list_change_requests(
    client: TestClient,
    auth_headers: dict,
    db_session,
):
    """GET /v1/change-requests returns paginated list."""
    from app.db.models import ChangeRequest
    from app.authorization import get_authorization_service
    from app.main import app

    class AllowAllPolicy:
        def can_edit_feature(self, user_id, tenant, feature_key):
            return True

    app.dependency_overrides[get_authorization_service] = lambda: AllowAllPolicy()

    cr = ChangeRequest(
        flag_key="list-test-flag",
        project_id="default",
        desired_changes={"description": "test"},
        status="pending",
        created_by="test-user",
    )
    db_session.add(cr)
    db_session.commit()

    r = client.get("/v1/change-requests", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 1
    assert any(i["flag_key"] == "list-test-flag" for i in data["items"])


def test_list_audit_logs(
    client: TestClient,
    auth_headers: dict,
    db_session,
):
    """GET /v1/audit returns paginated audit entries."""
    from app.db.models import AuditLog
    from app.authorization import get_authorization_service
    from app.main import app

    class AllowAllPolicy:
        def can_edit_feature(self, user_id, tenant, feature_key):
            return True

    app.dependency_overrides[get_authorization_service] = lambda: AllowAllPolicy()

    # Create audit entry via change request
    r = client.post(
        "/v1/flags/audit-list-flag/change-request",
        json={"desired_changes": {"description": "test"}},
        headers=auth_headers,
    )
    assert r.status_code == 201

    r = client.get("/v1/audit", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert "entries" in data
    assert "total" in data
    assert any(e["action"] == "change_request_created" for e in data["entries"])


def test_apply_requires_idempotency_key(
    client: TestClient,
    auth_headers: dict,
    db_session,
):
    """Apply endpoint returns 400 without Idempotency-Key header."""
    from app.db.models import ChangeRequest
    from datetime import datetime, timezone
    from app.authorization import get_authorization_service

    class AllowAllPolicy:
        def can_edit_feature(self, user_id, tenant, feature_key):
            return True

    from app.main import app
    app.dependency_overrides[get_authorization_service] = lambda: AllowAllPolicy()

    cr = ChangeRequest(
        id="00000000-0000-0000-0000-000000000002",
        flag_key="test-flag",
        project_id="default",
        desired_changes={"description": "test"},
        status="approved",
        created_by="test-user",
        approved_by="test-user",
        approved_at=datetime.now(timezone.utc),
    )
    db_session.add(cr)
    db_session.commit()

    r = client.post(
        f"/v1/change-requests/{cr.id}/apply",
        headers=auth_headers,  # No Idempotency-Key
    )
    assert r.status_code == 400
    assert "Idempotency-Key" in r.json()["detail"]
