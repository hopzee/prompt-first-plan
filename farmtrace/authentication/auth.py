"""Registration, login, password management and administrator bootstrap.

Passwords are hashed with Argon2id (``argon2-cffi``). Plain-text passwords are
never stored or logged. Login failures return one generic message so that an
attacker cannot tell whether an email address is registered, and repeated
failures temporarily lock the account.
"""

from __future__ import annotations

import datetime as dt
import secrets
import string
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from farmtrace import domain
from farmtrace.authentication.permissions import CurrentUser, require_authenticated
from farmtrace.database import repository
from farmtrace.database.models import User, UserCredential, UserProfile, UserRole
from farmtrace.errors import AuthenticationError, ConflictError, PermissionDenied, ValidationError
from farmtrace.services.audit_service import record_audit
from farmtrace.utils.helpers import ensure_utc, utcnow
from farmtrace.utils.logger import get_logger
from farmtrace.utils.validators import (
    require_text,
    validate_email,
    validate_nigerian_phone,
    validate_password,
)

log = get_logger(__name__)

_hasher = PasswordHasher()  # Argon2id with the library's maintained default parameters
_DUMMY_HASH = _hasher.hash("farmtrace-timing-equaliser-1")

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
GENERIC_LOGIN_ERROR = "Invalid email or password."


# ------------------------------------------------------------------ hashing
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# ------------------------------------------------------------- registration
@dataclass
class DriverDetails:
    driver_license_number: str
    vehicle_registration_number: str
    vehicle_type: str
    vehicle_capacity: object


@dataclass
class RegistrationData:
    full_name: str
    email: str
    password: str
    confirm_password: str
    role: str
    phone_number: str | None = None
    driver: DriverDetails | None = None


def to_current_user(user: User) -> CurrentUser:
    return CurrentUser(id=user.id, email=user.email, full_name=user.full_name, roles=user.role_names)


def _create_account(
    session: Session, *, full_name: str, email: str, password: str, phone: str | None, roles: tuple[str, ...],
    assigned_by: int | None = None,
) -> User:
    user = User(email=email, is_active=True)
    user.credential = UserCredential(password_hash=hash_password(password), password_changed_at=utcnow())
    user.profile = UserProfile(full_name=full_name, phone_number=phone)
    user.roles = [UserRole(role=role, assigned_by_user_id=assigned_by) for role in roles]
    session.add(user)
    session.flush()
    return user


def register_user(session: Session, data: RegistrationData) -> CurrentUser:
    """Public self-registration. Only farmer, driver and consumer roles are allowed."""
    if data.role not in domain.PUBLIC_REGISTRATION_ROLES:
        log.warning("Blocked public registration attempt for role %r", data.role)
        raise PermissionDenied("Administrator accounts cannot be created through public registration.")

    full_name = require_text(data.full_name, "Full name", max_length=150, min_length=2)
    email = validate_email(data.email)
    phone = validate_nigerian_phone(data.phone_number, required=data.role in (domain.ROLE_FARMER, domain.ROLE_DRIVER))
    password = validate_password(data.password, data.confirm_password)

    if data.role == domain.ROLE_DRIVER and data.driver is None:
        raise ValidationError("Drivers must provide licence and vehicle details.")

    if repository.get_user_by_email(session, email) is not None:
        raise ValidationError("An account with this email address already exists. Try signing in instead.")

    # Account, profile, role and (for drivers) the driver record are written in the
    # caller's single transaction: they are committed together or not at all.
    try:
        user = _create_account(
            session, full_name=full_name, email=email, password=password, phone=phone, roles=(data.role,)
        )
        if data.role == domain.ROLE_DRIVER and data.driver is not None:
            from farmtrace.services.driver_service import insert_driver_record

            insert_driver_record(session, user_id=user.id, details=data.driver)
        record_audit(
            session, actor_user_id=user.id, action="user_registered", target_type="user", target_id=user.id,
            details={"role": data.role},
        )
    except IntegrityError:
        raise ConflictError(
            "These details are already registered (email, licence number or vehicle registration)."
        ) from None
    log.info("New %s account registered (user id %s)", data.role, user.id)
    return to_current_user(user)


# -------------------------------------------------------------------- login
def authenticate(session: Session, email: str, password: str, *, now: dt.datetime | None = None) -> CurrentUser:
    """Verify credentials. Commits failed-attempt counters even when login fails."""
    now = now or utcnow()
    try:
        normalized = validate_email(email)
    except ValidationError:
        raise AuthenticationError(GENERIC_LOGIN_ERROR) from None

    user = repository.get_user_by_email(session, normalized)
    if user is None or user.credential is None:
        verify_password(_DUMMY_HASH, password or "")  # equalise response time
        raise AuthenticationError(GENERIC_LOGIN_ERROR)

    credential = user.credential
    locked_until = ensure_utc(credential.locked_until)
    if locked_until and locked_until > now:
        minutes = max(1, int((locked_until - now).total_seconds() // 60) + 1)
        raise AuthenticationError(f"Too many failed sign-in attempts. Try again in about {minutes} minute(s).")

    if not verify_password(credential.password_hash, password or ""):
        credential.failed_login_attempts += 1
        if credential.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
            credential.locked_until = now + dt.timedelta(minutes=LOCKOUT_MINUTES)
            credential.failed_login_attempts = 0
            log.warning("User %s locked out after repeated failed logins", user.id)
        session.commit()
        raise AuthenticationError(GENERIC_LOGIN_ERROR)

    if not user.is_active:
        raise AuthenticationError("This account has been deactivated. Please contact an administrator.")
    if not user.roles:
        raise AuthenticationError("This account has no role assigned. Please contact an administrator.")

    credential.failed_login_attempts = 0
    credential.locked_until = None
    if _hasher.check_needs_rehash(credential.password_hash):
        credential.password_hash = hash_password(password)
    user.last_login_at = now
    session.flush()
    return to_current_user(user)


def load_current_user(session: Session, user_id: int | None) -> CurrentUser | None:
    """Re-load the signed-in user so deactivation and role changes apply immediately."""
    if user_id is None:
        return None
    user = repository.get_user(session, int(user_id))
    if user is None or not user.is_active or not user.roles:
        return None
    return to_current_user(user)


# ---------------------------------------------------------- password changes
def change_password(
    session: Session, actor: CurrentUser | None, current_password: str, new_password: str, confirm_password: str
) -> None:
    actor = require_authenticated(actor)
    user = repository.get_user(session, actor.id)
    if user is None or user.credential is None:
        raise AuthenticationError("Your session has expired. Please sign in again.")
    if not verify_password(user.credential.password_hash, current_password or ""):
        raise ValidationError("Your current password is incorrect.")
    new_password = validate_password(new_password, confirm_password)
    if verify_password(user.credential.password_hash, new_password):
        raise ValidationError("Choose a password different from your current one.")
    user.credential.password_hash = hash_password(new_password)
    user.credential.password_changed_at = utcnow()
    record_audit(session, actor_user_id=actor.id, action="password_changed", target_type="user", target_id=actor.id)


def generate_temporary_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isalpha() for c in candidate) and any(c.isdigit() for c in candidate):
            return candidate


# ----------------------------------------------------- administrator bootstrap
def bootstrap_administrator(
    session: Session, *, email: str, full_name: str | None, password: str | None, phone: str | None = None
) -> tuple[User, bool]:
    """Create an administrator, or grant admin to an existing account.

    Only reachable from the command-line bootstrap script (never from the web
    interface). Returns ``(user, created)``.
    """
    email = validate_email(email)
    existing = repository.get_user_by_email(session, email)
    if existing is not None:
        if domain.ROLE_ADMIN not in existing.role_names:
            session.add(UserRole(user_id=existing.id, role=domain.ROLE_ADMIN))
        existing.is_active = True
        session.flush()
        record_audit(
            session, actor_user_id=None, action="admin_bootstrapped", target_type="user", target_id=existing.id,
            details={"mode": "promoted_existing_account"},
        )
        return existing, False

    name = require_text(full_name, "Full name", max_length=150, min_length=2)
    password = validate_password(password)
    user = _create_account(
        session, full_name=name, email=email, password=password, phone=validate_nigerian_phone(phone),
        roles=(domain.ROLE_ADMIN,),
    )
    record_audit(
        session, actor_user_id=None, action="admin_bootstrapped", target_type="user", target_id=user.id,
        details={"mode": "created_new_account"},
    )
    return user, True
