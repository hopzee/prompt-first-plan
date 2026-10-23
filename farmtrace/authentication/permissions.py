"""Role-based authorisation checks used inside every service function.

These checks are the real security boundary. The Streamlit pages also hide
controls a user may not use, but hiding a button is only a convenience — every
service re-checks the acting user's roles and record ownership.
"""

from __future__ import annotations

from dataclasses import dataclass

from farmtrace import domain
from farmtrace.errors import PermissionDenied

_ROLE_PRIORITY = (domain.ROLE_ADMIN, domain.ROLE_FARMER, domain.ROLE_DRIVER, domain.ROLE_CONSUMER)


@dataclass(frozen=True)
class CurrentUser:
    """The authenticated actor, re-loaded from the database on every page run."""

    id: int
    email: str
    full_name: str
    roles: frozenset[str]

    def has_role(self, *roles: str) -> bool:
        return any(role in self.roles for role in roles)

    @property
    def is_admin(self) -> bool:
        return domain.ROLE_ADMIN in self.roles

    @property
    def primary_role(self) -> str:
        for role in _ROLE_PRIORITY:
            if role in self.roles:
                return role
        return domain.ROLE_CONSUMER

    @property
    def role_label(self) -> str:
        return " / ".join(domain.ROLE_LABELS[r] for r in _ROLE_PRIORITY if r in self.roles) or "No role"


def require_authenticated(actor: CurrentUser | None) -> CurrentUser:
    if actor is None:
        raise PermissionDenied("Please sign in to continue.")
    return actor


def require_role(actor: CurrentUser | None, *roles: str) -> CurrentUser:
    actor = require_authenticated(actor)
    if not actor.has_role(*roles):
        allowed = ", ".join(domain.ROLE_LABELS.get(r, r) for r in roles)
        raise PermissionDenied(f"This action is only available to: {allowed}.")
    return actor


def require_admin(actor: CurrentUser | None) -> CurrentUser:
    return require_role(actor, domain.ROLE_ADMIN)


def is_owner_or_admin(actor: CurrentUser | None, owner_user_id: int) -> bool:
    return actor is not None and (actor.is_admin or actor.id == owner_user_id)


def ensure_owner_or_admin(actor: CurrentUser | None, owner_user_id: int, what: str = "record") -> CurrentUser:
    actor = require_authenticated(actor)
    if not is_owner_or_admin(actor, owner_user_id):
        raise PermissionDenied(f"You do not have access to this {what}.")
    return actor
