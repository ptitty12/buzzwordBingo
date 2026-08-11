"""Identity.

There are no accounts. An administrator proves it with a PIN; a participant proves nothing
at all beyond picking a nickname inside one meeting (see ``meetings.join_meeting``). This module
covers the admin door and the "who am I" lookup both roles share.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..config import get_settings
from ..db import execute, record_audit, utcnow
from ..models import AdminSession, AdminSignIn
from ..models import Identity as IdentityModel
from ..security import Identity, check_admin_pin, current_identity, issue_admin_token
from ..serializers import participant_public

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/config")
def auth_config() -> dict:
    """Public bootstrap info. Deliberately says nothing about *who* the admin is."""
    settings = get_settings()
    return {
        "app_name": settings.app_name,
        "environment": settings.environment,
        "admin_enabled": bool(settings.admin_pin),
        "suggestions_enabled": True,
        "moderation_enabled": settings.moderation_enabled,
    }


@router.post("/admin", response_model=AdminSession)
def admin_sign_in(payload: AdminSignIn) -> AdminSession:
    """Exchange the admin PIN for an admin token."""
    settings = get_settings()
    if not settings.admin_pin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administration is disabled on this instance.",
        )

    if not check_admin_pin(payload.pin):
        # Deliberately vague: never reveal whether the PIN was close.
        record_audit("admin.signin_failed", actor_name="anonymous", entity="admin")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect PIN.")

    record_audit("admin.signin", actor_name="admin", entity="admin")
    return AdminSession(token=issue_admin_token())


@router.get("/me", response_model=IdentityModel)
def me(identity: Identity = Depends(current_identity)) -> IdentityModel:
    """Resolve the caller — used to restore a session on page load."""
    if identity.is_admin:
        return IdentityModel(is_admin=True, participant=None)

    if identity.participant is not None:
        execute(
            "UPDATE participants SET last_seen_at = ? WHERE id = ?",
            (utcnow(), identity.participant["id"]),
        )
        return IdentityModel(is_admin=False, participant=participant_public(identity.participant))

    return IdentityModel(is_admin=False, participant=None)
