"""User and role administration plus the audit trail (administrators only)."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from farmtrace import domain
from farmtrace.authentication.auth import generate_temporary_password, hash_password
from farmtrace.authentication.permissions import CurrentUser, require_admin
from farmtrace.database import repository
from farmtrace.database.models import AuditLog, User, UserProfile, UserRole
from farmtrace.errors import NotFoundError, ValidationError
from farmtrace.services.audit_service import record_audit
from farmtrace.services.farmer_service import like_pattern
from farmtrace.utils.helpers import utcnow
from farmtrace.utils.validators import require_text, validate_choice


@dataclass(frozen=True)
class UserRow:
    id: int
    email: str
    full_name: str
    phone_number: str | None
    roles: tuple[str, ...]
    is_active: bool
    created_at: dt.datetime
    last_login_at: dt.datetime | None


def list_users(
    session: Session, actor: CurrentUser | None, *, search: str | None = None, role: str | None = None,
    active: bool | None = None,
) -> list[UserRow]:
    require_admin(actor)
    stmt = (
        select(User)
        .join(UserProfile, UserProfile.user_id == User.id)
        .options(joinedload(User.profile), selectinload(User.roles))
        .order_by(User.created_at.desc())
    )
    if search and search.strip():
        pattern = like_pattern(search)
        stmt = stmt.where(or_(User.email.ilike(pattern, escape="\\"), UserProfile.full_name.ilike(pattern, escape="\\")))
    if role:
        stmt = stmt.where(User.id.in_(select(UserRole.user_id).where(UserRole.role == role)))
    if active is not None:
        stmt = stmt.where(User.is_active.is_(active))
    users = session.execute(stmt).unique().scalars().all()
    return [
        UserRow(
            id=u.id, email=u.email, full_name=u.full_name, phone_number=u.profile.phone_number if u.profile else None,
            roles=tuple(sorted(u.role_names)), is_active=u.is_active, created_at=u.created_at,
            last_login_at=u.last_login_at,
        )
        for u in users
    ]


def _get_user(session: Session, user_id: int) -> User:
    user = repository.get_user(session, int(user_id))
    if user is None:
        raise NotFoundError("User not found.")
    return user


def set_user_active(session: Session, actor: CurrentUser | None, user_id: int, active: bool, reason: str) -> User:
    actor = require_admin(actor)
    user = _get_user(session, user_id)
    reason = require_text(reason, "Reason", max_length=255, min_length=5)
    if user.id == actor.id and not active:
        raise ValidationError("You cannot deactivate your own account.")
    if not active and domain.ROLE_ADMIN in user.role_names and repository.count_admins(session) <= 1:
        raise ValidationError("You cannot deactivate the last active administrator.")
    if user.is_active == active:
        raise ValidationError(f"This account is already {'active' if active else 'deactivated'}.")
    user.is_active = active
    session.flush()
    record_audit(
        session, actor_user_id=actor.id, action="user_activated" if active else "user_deactivated",
        target_type="user", target_id=user.id, details={"email": user.email, "reason": reason},
    )
    return user


def grant_role(session: Session, actor: CurrentUser | None, user_id: int, role: str) -> User:
    actor = require_admin(actor)
    role = validate_choice(role, domain.ALL_ROLES, "Role")
    user = _get_user(session, user_id)
    if role in user.role_names:
        raise ValidationError("The user already has this role.")
    session.add(UserRole(user_id=user.id, role=role, assigned_by_user_id=actor.id))
    session.flush()
    session.expire(user, ["roles"])
    record_audit(
        session, actor_user_id=actor.id, action="role_granted", target_type="user", target_id=user.id,
        details={"role": role, "email": user.email},
    )
    return user


def revoke_role(session: Session, actor: CurrentUser | None, user_id: int, role: str) -> User:
    actor = require_admin(actor)
    user = _get_user(session, user_id)
    assignment = next((r for r in user.roles if r.role == role), None)
    if assignment is None:
        raise ValidationError("The user does not have this role.")
    if len(user.roles) == 1:
        raise ValidationError("A user must keep at least one role. Deactivate the account instead.")
    if role == domain.ROLE_ADMIN:
        if user.id == actor.id:
            raise ValidationError("You cannot remove your own administrator role.")
        if repository.count_admins(session) <= 1:
            raise ValidationError("You cannot remove the last administrator.")
    user.roles.remove(assignment)
    session.flush()
    record_audit(
        session, actor_user_id=actor.id, action="role_revoked", target_type="user", target_id=user.id,
        details={"role": role, "email": user.email},
    )
    return user


def reset_user_password(session: Session, actor: CurrentUser | None, user_id: int) -> str:
    """Set a random temporary password, shown once to the administrator."""
    actor = require_admin(actor)
    user = _get_user(session, user_id)
    if user.id == actor.id:
        raise ValidationError("Use 'Change password' on the My Account page for your own account.")
    temporary = generate_temporary_password()
    user.credential.password_hash = hash_password(temporary)
    user.credential.password_changed_at = utcnow()
    user.credential.failed_login_attempts = 0
    user.credential.locked_until = None
    session.flush()
    record_audit(
        session, actor_user_id=actor.id, action="password_reset_by_admin", target_type="user", target_id=user.id,
        details={"email": user.email},
    )
    return temporary


@dataclass(frozen=True)
class AuditRow:
    created_at: dt.datetime
    actor: str
    action: str
    target: str
    details: str


def list_audit_logs(
    session: Session, actor: CurrentUser | None, *, action: str | None = None, limit: int = 200
) -> list[AuditRow]:
    require_admin(actor)
    stmt = (
        select(AuditLog)
        .options(joinedload(AuditLog.actor).joinedload(User.profile))
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
    )
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = []
    for entry in session.execute(stmt).unique().scalars():
        details = ""
        if entry.details:
            try:
                details = ", ".join(f"{k}: {v}" for k, v in json.loads(entry.details).items())
            except (ValueError, AttributeError):
                details = entry.details
        rows.append(
            AuditRow(
                created_at=entry.created_at,
                actor=entry.actor.full_name if entry.actor else "System / setup script",
                action=entry.action.replace("_", " "),
                target=f"{entry.target_type} #{entry.target_id}" if entry.target_id else entry.target_type,
                details=details,
            )
        )
    return rows


def audit_actions(session: Session, actor: CurrentUser | None) -> list[str]:
    require_admin(actor)
    return list(session.execute(select(AuditLog.action).distinct().order_by(AuditLog.action)).scalars())
