"""Driver registry, vehicle details and availability."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from farmtrace import domain
from farmtrace.authentication.auth import DriverDetails
from farmtrace.authentication.permissions import CurrentUser, require_admin, require_role
from farmtrace.database import repository
from farmtrace.database.models import Driver, TransportRequest, User, UserProfile
from farmtrace.errors import ConflictError, ValidationError
from farmtrace.services.access import driver_for_actor
from farmtrace.services.farmer_service import like_pattern
from farmtrace.utils.helpers import VEHICLE_TYPES
from farmtrace.utils.validators import (
    validate_choice,
    validate_licence_number,
    validate_positive_decimal,
    validate_vehicle_registration,
)


def clean_driver_details(details: DriverDetails) -> dict:
    return {
        "driver_license_number": validate_licence_number(details.driver_license_number),
        "vehicle_registration_number": validate_vehicle_registration(details.vehicle_registration_number),
        "vehicle_type": validate_choice(details.vehicle_type, VEHICLE_TYPES, "Vehicle type"),
        "vehicle_capacity": validate_positive_decimal(details.vehicle_capacity, "Vehicle capacity (kg)", maximum=100_000),
    }


def _ensure_unique(session: Session, values: dict, exclude_driver_id: int | None = None) -> None:
    stmt = select(Driver.id).where(
        or_(
            Driver.driver_license_number == values["driver_license_number"],
            Driver.vehicle_registration_number == values["vehicle_registration_number"],
        )
    )
    if exclude_driver_id is not None:
        stmt = stmt.where(Driver.id != exclude_driver_id)
    if session.execute(stmt).first():
        raise ValidationError("This licence number or vehicle registration is already registered to another driver.")


def insert_driver_record(session: Session, *, user_id: int, details: DriverDetails, is_demo: bool = False) -> Driver:
    values = clean_driver_details(details)
    _ensure_unique(session, values)
    driver = Driver(user_id=user_id, availability_status=domain.AVAILABILITY_AVAILABLE, is_demo=is_demo, **values)
    session.add(driver)
    try:
        session.flush()
    except IntegrityError:
        raise ConflictError("This licence number or vehicle registration is already registered.") from None
    return driver


def get_own_profile(session: Session, actor: CurrentUser | None) -> Driver | None:
    actor = require_role(actor, domain.ROLE_DRIVER)
    return repository.get_driver_by_user(session, actor.id)


def create_driver_profile(session: Session, actor: CurrentUser | None, details: DriverDetails) -> Driver:
    actor = require_role(actor, domain.ROLE_DRIVER)
    if repository.get_driver_by_user(session, actor.id) is not None:
        raise ValidationError("Your driver profile already exists. Edit it instead.")
    return insert_driver_record(session, user_id=actor.id, details=details)


def update_driver_profile(session: Session, actor: CurrentUser | None, details: DriverDetails) -> Driver:
    driver = driver_for_actor(session, actor)
    values = clean_driver_details(details)
    _ensure_unique(session, values, exclude_driver_id=driver.id)
    for key, value in values.items():
        setattr(driver, key, value)
    session.flush()
    return driver


def set_availability(session: Session, actor: CurrentUser | None, status: str) -> Driver:
    driver = driver_for_actor(session, actor)
    driver.availability_status = validate_choice(status, domain.AVAILABILITY_STATUSES, "Availability")
    session.flush()
    return driver


def check_driver_eligible(driver: Driver) -> tuple[bool, str]:
    """A driver may receive a new assignment only if active, a driver, and not unavailable."""
    user = driver.user
    if user is None or not user.is_active:
        return False, "This driver's account is deactivated."
    if domain.ROLE_DRIVER not in user.role_names:
        return False, "This account no longer has the driver role."
    if driver.availability_status == domain.AVAILABILITY_UNAVAILABLE:
        return False, "This driver has marked themselves as unavailable."
    return True, ""


def list_drivers(
    session: Session, actor: CurrentUser | None, *, eligible_only: bool = False, search: str | None = None
) -> list[Driver]:
    require_admin(actor)
    stmt = (
        select(Driver)
        .join(User, User.id == Driver.user_id)
        .join(UserProfile, UserProfile.user_id == User.id)
        .options(joinedload(Driver.user).joinedload(User.profile), joinedload(Driver.user).selectinload(User.roles))
        .order_by(UserProfile.full_name)
    )
    if search and search.strip():
        pattern = like_pattern(search)
        stmt = stmt.where(
            or_(
                UserProfile.full_name.ilike(pattern, escape="\\"),
                Driver.vehicle_registration_number.ilike(pattern, escape="\\"),
                Driver.vehicle_type.ilike(pattern, escape="\\"),
            )
        )
    drivers = list(session.execute(stmt).unique().scalars())
    if eligible_only:
        drivers = [driver for driver in drivers if check_driver_eligible(driver)[0]]
    return drivers


def active_load(session: Session, driver_ids: list[int]) -> dict[int, int]:
    """Number of not-yet-delivered assignments per driver (helps admins balance work)."""
    if not driver_ids:
        return {}
    rows = session.execute(
        select(TransportRequest.assigned_driver_id, func.count(TransportRequest.id))
        .where(
            TransportRequest.assigned_driver_id.in_(driver_ids),
            TransportRequest.status.in_(domain.DELAYABLE_STATUSES),
        )
        .group_by(TransportRequest.assigned_driver_id)
    ).all()
    return {driver_id: count for driver_id, count in rows}


def driver_summary(session: Session, actor: CurrentUser | None) -> dict[str, int]:
    driver = driver_for_actor(session, actor)

    def scalar(stmt) -> int:
        return int(session.execute(stmt).scalar_one() or 0)

    mine = select(func.count(TransportRequest.id)).where(TransportRequest.assigned_driver_id == driver.id)
    return {
        "active": scalar(mine.where(TransportRequest.status.in_(domain.DELAYABLE_STATUSES))),
        "on_road": scalar(mine.where(TransportRequest.status.in_(domain.ON_ROAD_STATUSES))),
        "delivered": scalar(mine.where(TransportRequest.status == domain.STATUS_DELIVERED)),
        "delayed": scalar(
            mine.where(TransportRequest.is_delayed.is_(True), TransportRequest.status.in_(domain.ACTIVE_STATUSES))
        ),
        "open_requests": scalar(
            select(func.count(TransportRequest.id)).where(
                TransportRequest.status == domain.STATUS_APPROVED, TransportRequest.assigned_driver_id.is_(None)
            )
        ),
    }


__all__ = [
    "active_load",
    "check_driver_eligible",
    "clean_driver_details",
    "create_driver_profile",
    "driver_summary",
    "get_own_profile",
    "insert_driver_record",
    "list_drivers",
    "set_availability",
    "update_driver_profile",
    "selectinload",
]
