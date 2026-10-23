"""Shared record-level access rules (prevents insecure direct object references)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from farmtrace import domain
from farmtrace.authentication.permissions import CurrentUser, require_authenticated, require_role
from farmtrace.database import repository
from farmtrace.database.models import Driver, TransportRequest
from farmtrace.errors import PermissionDenied, ValidationError


def driver_for_actor(session: Session, actor: CurrentUser | None) -> Driver:
    """The driver registry entry belonging to the signed-in driver."""
    actor = require_role(actor, domain.ROLE_DRIVER)
    driver = repository.get_driver_by_user(session, actor.id)
    if driver is None:
        raise ValidationError("Complete your driver and vehicle profile on the Driver Dashboard first.")
    return driver


def can_view_request(session: Session, actor: CurrentUser | None, request: TransportRequest) -> bool:
    if actor is None:
        return False
    if actor.is_admin:
        return True
    if actor.has_role(domain.ROLE_FARMER) and request.farmer_user_id == actor.id:
        return True
    if actor.has_role(domain.ROLE_DRIVER) and request.assigned_driver_id is not None:
        driver = repository.get_driver_by_user(session, actor.id)
        if driver is not None and driver.id == request.assigned_driver_id:
            return True
    return False


def ensure_can_view_request(session: Session, actor: CurrentUser | None, request: TransportRequest) -> CurrentUser:
    actor = require_authenticated(actor)
    if not can_view_request(session, actor, request):
        raise PermissionDenied("You do not have access to this transport request.")
    return actor
