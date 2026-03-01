"""Change request approve, apply, and list endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.auth import get_current_user
from app.authorization import AuthorizationService, get_authorization_service
from app.db import get_db
from app.db.models import ChangeRequest
from app.idempotency import (
    get_existing_response,
    require_idempotency_key,
    store_idempotency_response,
)
from app.models import ChangeRequestApplyResponse, ChangeRequestResponse
from app.unleash_client import UnleashClient, get_unleash_client

router = APIRouter(prefix="/v1/change-requests", tags=["change-requests"])


@router.get("", response_model=None)
def list_change_requests(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status"),
    flag_key: Optional[str] = Query(None, description="Filter by flag key"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List change requests with optional filters and pagination."""
    q = db.query(ChangeRequest).order_by(ChangeRequest.created_at.desc())
    if status_filter:
        q = q.filter(ChangeRequest.status == status_filter)
    if flag_key:
        q = q.filter(ChangeRequest.flag_key == flag_key)

    total = q.count()
    items = q.offset(offset).limit(limit).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": cr.id,
                "flag_key": cr.flag_key,
                "project_id": cr.project_id,
                "status": cr.status,
                "created_by": cr.created_by,
                "created_at": cr.created_at.isoformat() if cr.created_at else None,
                "approved_at": cr.approved_at.isoformat() if cr.approved_at else None,
                "applied_at": cr.applied_at.isoformat() if cr.applied_at else None,
            }
            for cr in items
        ],
    }


def _get_change_request(db: Session, cr_id: str) -> ChangeRequest:
    cr = db.query(ChangeRequest).filter(ChangeRequest.id == cr_id).first()
    if not cr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change request not found",
        )
    return cr


def _change_request_to_response(cr: ChangeRequest) -> ChangeRequestResponse:
    return ChangeRequestResponse(
        id=cr.id,
        flag_key=cr.flag_key,
        project_id=cr.project_id,
        tenant=cr.tenant,
        status=cr.status,
        desired_changes=cr.desired_changes,
        environment=cr.environment,
        strategies=cr.strategies,
        created_by=cr.created_by,
        created_at=cr.created_at,
        approved_by=cr.approved_by,
        approved_at=cr.approved_at,
        applied_at=cr.applied_at,
    )


@router.post("/{id}/approve", response_model=ChangeRequestResponse)
def approve_change_request(
    id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    authz: "AuthorizationService" = Depends(get_authorization_service),
):
    """
    Approve a pending change request.
    Requires JWT and authorization.
    """
    user_id = user["sub"]

    cr = _get_change_request(db, id)

    if not authz.can_edit_feature(user_id, cr.tenant, cr.flag_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: insufficient permissions",
        )

    if cr.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Change request is not pending (status: {cr.status})",
        )

    from datetime import datetime, timezone

    cr.status = "approved"
    cr.approved_by = user_id
    cr.approved_at = datetime.now(timezone.utc)

    write_audit(
        db,
        actor=user_id,
        action="change_request_approved",
        resource_type="change_request",
        resource_id=cr.id,
        before_payload={"status": "pending"},
        after_payload={"status": "approved"},
        metadata_={"change_request_id": cr.id},
    )

    return _change_request_to_response(cr)


@router.post(
    "/{id}/apply",
    response_model=ChangeRequestApplyResponse,
)
def apply_change_request(
    id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    authz: AuthorizationService = Depends(get_authorization_service),
    unleash: UnleashClient = Depends(get_unleash_client),
):
    """
    Apply an approved change request to Unleash.
    Requires JWT, authorization, and Idempotency-Key header.
    """
    idempotency_key = require_idempotency_key(request)
    user_id = user["sub"]

    # Check idempotency first - return previous response for duplicate requests
    existing = get_existing_response(db, idempotency_key)
    if existing:
        _status_code, body = existing
        return ChangeRequestApplyResponse(
            change_request_id=body["change_request_id"],
            status=body["status"],
            unleash_result=body.get("unleash_result"),
        )

    cr = _get_change_request(db, id)

    if not authz.can_edit_feature(user_id, cr.tenant, cr.flag_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: insufficient permissions",
        )

    if cr.status != "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Change request must be approved first (status: {cr.status})",
        )

    try:
        result = unleash.apply_change_request(
            project_id=cr.project_id,
            feature_key=cr.flag_key,
            desired_changes=cr.desired_changes,
            environment=cr.environment,
            strategies=cr.strategies,
        )
    except Exception as e:
        write_audit(
            db,
            actor=user_id,
            action="change_request_apply_failed",
            resource_type="change_request",
            resource_id=cr.id,
            metadata_={"error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unleash apply failed: {str(e)}",
        )

    from datetime import datetime, timezone

    cr.status = "applied"
    cr.applied_at = datetime.now(timezone.utc)

    write_audit(
        db,
        actor=user_id,
        action="change_request_applied",
        resource_type="change_request",
        resource_id=cr.id,
        before_payload={"status": "approved"},
        after_payload={"status": "applied", "unleash_result": result},
        metadata_={"change_request_id": cr.id},
    )

    response_body = {
        "change_request_id": cr.id,
        "status": "applied",
        "unleash_result": result,
    }
    store_idempotency_response(
        db, idempotency_key, cr.id, 200, response_body
    )

    return ChangeRequestApplyResponse(
        change_request_id=cr.id,
        status="applied",
        unleash_result=result,
    )
