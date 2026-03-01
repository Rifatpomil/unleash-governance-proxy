"""Flag change request endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.auth import get_current_user
from app.authorization import AuthorizationService, get_authorization_service
from app.db import get_db
from app.db.models import ChangeRequest
from app.models import ChangeRequestCreate, ChangeRequestResponse

router = APIRouter(prefix="/v1/flags", tags=["flags"])


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


@router.post(
    "/{flag_key}/change-request",
    response_model=ChangeRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_change_request(
    flag_key: str,
    body: ChangeRequestCreate,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    authz: AuthorizationService = Depends(get_authorization_service),
):
    """
    Create a change request for a feature flag.
    Requires JWT and authorization (can_edit_feature).
    """
    user_id = user["sub"]
    tenant = body.tenant or user.get("tenant")

    if not authz.can_edit_feature(user_id, tenant, flag_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: insufficient permissions to edit this feature",
        )

    strategies_json = (
        [s.model_dump(exclude_none=True) for s in body.strategies]
        if body.strategies
        else None
    )

    cr = ChangeRequest(
        flag_key=flag_key,
        project_id=body.project_id,
        tenant=tenant,
        desired_changes=body.desired_changes,
        environment=body.environment,
        strategies=strategies_json,
        status="pending",
        created_by=user_id,
    )
    db.add(cr)
    db.flush()

    write_audit(
        db,
        actor=user_id,
        action="change_request_created",
        resource_type="change_request",
        resource_id=cr.id,
        after_payload={
            "flag_key": flag_key,
            "project_id": body.project_id,
            "desired_changes": body.desired_changes,
        },
        metadata_={"change_request_id": cr.id},
    )

    return _change_request_to_response(cr)
