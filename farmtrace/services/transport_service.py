"""Transport requests, driver assignment and the delivery workflow.

All status changes go through :func:`_transition`, which performs a *conditional*
UPDATE (``WHERE id = ? AND status = ? AND version = ?``). If two people act on the
same request at the same time — for example two drivers accepting it — only one
UPDATE matches; the other receives a ``ConflictError``. Every successful change
writes a hash-linked history event via ``tracking_service.record_event`` in the
same transaction.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from farmtrace import domain
from farmtrace.authentication.permissions import (
    CurrentUser,
    require_admin,
    require_authenticated,
    require_role,
)
from farmtrace.database import repository
from farmtrace.database.models import Driver, Farm, ProduceBatch, TransportRequest
from farmtrace.errors import (
    ConflictError,
    InvalidTransitionError,
    NotFoundError,
    PermissionDenied,
    ValidationError,
)
from farmtrace.services import driver_service
from farmtrace.services.access import driver_for_actor, ensure_can_view_request
from farmtrace.services.audit_service import record_audit
from farmtrace.services.farmer_service import _unique_reference, like_pattern
from farmtrace.services.tracking_service import record_event
from farmtrace.utils.helpers import (
    ensure_utc,
    format_datetime,
    format_naira,
    format_quantity,
    local_today,
    utcnow,
)
from farmtrace.utils.validators import (
    optional_text,
    require_text,
    validate_date_not_past,
    validate_non_negative_decimal,
)


# ------------------------------------------------------------------ internals
def _load(session: Session, request_id: int) -> TransportRequest:
    request = repository.get_request(session, int(request_id))
    if request is None:
        raise NotFoundError("Transport request not found.")
    return request


def _reload(session: Session, request_id: int) -> TransportRequest:
    stmt = (
        select(TransportRequest)
        .options(*repository.request_load_options())
        .where(TransportRequest.id == request_id)
        .execution_options(populate_existing=True)
    )
    return session.execute(stmt).unique().scalar_one()


def _transition(
    session: Session,
    request: TransportRequest,
    *,
    new_status: str,
    values: dict | None = None,
    extra_conditions: tuple = (),
) -> str:
    """Atomically move ``request`` to ``new_status`` (or keep it, for flag/detail changes).

    Returns the previous status. Raises ``InvalidTransitionError`` for workflow
    violations and ``ConflictError`` if the row changed since it was read.
    """
    expected = request.status
    if new_status != expected and not domain.is_transition_allowed(expected, new_status):
        raise InvalidTransitionError(f"A delivery cannot move from {expected} to {new_status}.")
    stmt = (
        update(TransportRequest)
        .where(
            TransportRequest.id == request.id,
            TransportRequest.status == expected,
            TransportRequest.version == request.version,
            *extra_conditions,
        )
        .values(status=new_status, version=TransportRequest.version + 1, updated_at=utcnow(), **(values or {}))
        .execution_options(synchronize_session=False)
    )
    result = session.execute(stmt)
    if result.rowcount != 1:
        raise ConflictError()
    return expected


def _append_note(existing: str | None, note: str, author: str, when: dt.datetime) -> str:
    line = f"[{format_datetime(when)} · {author}] {note}"
    return f"{existing}\n{line}" if existing else line


def _ensure_assigned_driver(session: Session, actor: CurrentUser | None, request: TransportRequest) -> Driver:
    driver = driver_for_actor(session, actor)
    if request.assigned_driver_id != driver.id:
        raise PermissionDenied("Only the driver assigned to this delivery can update it.")
    return driver


# ------------------------------------------------------------------ farmer
def create_request(
    session: Session,
    actor: CurrentUser | None,
    *,
    batch_id: int,
    requested_pickup_date: dt.date,
    pickup_location: str | None = None,
    destination_location: str | None = None,
    notes: str | None = None,
    today: dt.date | None = None,
    is_demo: bool = False,
) -> TransportRequest:
    actor = require_role(actor, domain.ROLE_FARMER)
    batch = repository.get_batch(session, int(batch_id))
    if batch is None or batch.farmer_user_id != actor.id:
        raise PermissionDenied("You can only request transport for your own produce batches.")
    if batch.batch_status == domain.BATCH_CANCELLED:
        raise ValidationError("This batch has been cancelled and cannot be transported.")
    if batch.batch_status == domain.BATCH_DELIVERED:
        raise ValidationError("This batch has already been delivered.")
    if batch.qr_revoked_at is not None:
        raise ValidationError("This batch's QR code has been revoked. Ask an administrator to reissue it first.")
    active = repository.get_active_request_for_batch(session, batch.id)
    if active is not None:
        raise ConflictError(f"This batch already has an active transport request ({active.request_number}).")

    today = today or local_today()
    pickup_date = validate_date_not_past(requested_pickup_date, "Requested pickup date", today)
    if pickup_date < batch.harvest_date:
        raise ValidationError("The pickup date cannot be before the harvest date.")
    pickup = require_text(pickup_location or batch.pickup_location, "Pickup location", max_length=255, min_length=3)
    destination = require_text(
        destination_location or batch.destination_location, "Destination location", max_length=255, min_length=3
    )
    note = optional_text(notes, "Notes for the transporter", max_length=1000)

    request = TransportRequest(
        request_number=_unique_reference(session, TransportRequest, TransportRequest.request_number, "FT-R"),
        produce_batch_id=batch.id,
        farmer_user_id=actor.id,
        pickup_location=pickup,
        destination_location=destination,
        requested_pickup_date=pickup_date,
        status=domain.STATUS_PENDING,
        delivery_notes=_append_note(None, note, actor.full_name, utcnow()) if note else None,
        is_demo=is_demo,
    )
    session.add(request)
    try:
        session.flush()
    except IntegrityError:
        raise ConflictError("This batch already has an active transport request.") from None

    batch.batch_status = domain.BATCH_AWAITING_TRANSPORT
    record_event(
        session,
        request=request,
        actor_user_id=actor.id,
        event_type=domain.EVENT_REQUEST_CREATED,
        previous_status=None,
        new_status=domain.STATUS_PENDING,
        description=(
            f"Transport requested for {format_quantity(batch.quantity, batch.unit_of_measurement)} of "
            f"{batch.produce_name} from {pickup} to {destination}; pickup on {pickup_date:%d %b %Y}."
        ),
    )
    return _reload(session, request.id)


def cancel_request(session: Session, actor: CurrentUser | None, request_id: int, reason: str) -> TransportRequest:
    actor = require_authenticated(actor)
    request = _load(session, request_id)
    reason = require_text(reason, "Cancellation reason", max_length=500, min_length=5)
    if request.status in domain.TERMINAL_STATUSES:
        raise InvalidTransitionError(f"This request is already {request.status}.")
    if not actor.is_admin:
        if not (actor.has_role(domain.ROLE_FARMER) and request.farmer_user_id == actor.id):
            raise PermissionDenied("You can only cancel your own transport requests.")
        if request.status not in domain.FARMER_CANCELLABLE_STATUSES:
            raise PermissionDenied(
                "Once a driver has been assigned, only an administrator can cancel this request."
            )
    previous = _transition(
        session, request, new_status=domain.STATUS_CANCELLED,
        values={"cancellation_reason": reason, "is_delayed": False},
    )
    batch = request.batch
    if batch.batch_status != domain.BATCH_CANCELLED:
        batch.batch_status = domain.BATCH_REGISTERED  # the farmer may request transport again
    record_event(
        session, request=request, actor_user_id=actor.id, event_type=domain.EVENT_CANCELLED,
        previous_status=previous, new_status=domain.STATUS_CANCELLED, description=f"Request cancelled: {reason}",
    )
    if actor.is_admin:
        record_audit(
            session, actor_user_id=actor.id, action="request_cancelled", target_type="transport_request",
            target_id=request.id, details={"request_number": request.request_number, "from_status": previous},
        )
    return _reload(session, request.id)


# ------------------------------------------------------------------- admin
def approve_request(
    session: Session,
    actor: CurrentUser | None,
    request_id: int,
    *,
    transport_cost=None,
    estimated_delivery_time: dt.datetime | None = None,
    note: str | None = None,
    now: dt.datetime | None = None,
) -> TransportRequest:
    actor = require_admin(actor)
    request = _load(session, request_id)
    if request.status != domain.STATUS_PENDING:
        raise InvalidTransitionError(f"Only pending requests can be approved (this one is {request.status}).")
    now = ensure_utc(now) if now else utcnow()
    values: dict = {}
    cost = validate_non_negative_decimal(transport_cost, "Transport cost")
    if cost is not None:
        values["transport_cost"] = cost
    if estimated_delivery_time is not None:
        eta = ensure_utc(estimated_delivery_time)
        if eta <= now:
            raise ValidationError("The estimated delivery time must be in the future.")
        values["estimated_delivery_time"] = eta
    note = optional_text(note, "Note", max_length=500)
    previous = _transition(session, request, new_status=domain.STATUS_APPROVED, values=values)
    details = ["Approved by administrator."]
    if cost is not None:
        details.append(f"Agreed transport cost {format_naira(cost)}.")
    if note:
        details.append(note)
    record_event(
        session, request=request, actor_user_id=actor.id, event_type=domain.EVENT_REQUEST_APPROVED,
        previous_status=previous, new_status=domain.STATUS_APPROVED, description=" ".join(details), timestamp=now,
    )
    record_audit(
        session, actor_user_id=actor.id, action="request_approved", target_type="transport_request",
        target_id=request.id, details={"request_number": request.request_number},
    )
    return _reload(session, request.id)


def assign_driver(
    session: Session, actor: CurrentUser | None, request_id: int, driver_id: int, *, note: str | None = None
) -> TransportRequest:
    """Assign (Approved → Assigned) or reassign (Assigned → Assigned, before dispatch)."""
    actor = require_admin(actor)
    request = _load(session, request_id)
    driver = repository.get_driver(session, int(driver_id))
    if driver is None:
        raise NotFoundError("Driver not found.")
    eligible, reason = driver_service.check_driver_eligible(driver)
    if not eligible:
        raise ValidationError(reason)
    note = optional_text(note, "Note", max_length=500)
    driver_name = driver.user.full_name

    if request.status == domain.STATUS_APPROVED:
        previous = _transition(
            session, request, new_status=domain.STATUS_ASSIGNED, values={"assigned_driver_id": driver.id},
            extra_conditions=(TransportRequest.assigned_driver_id.is_(None),),
        )
        event_type = domain.EVENT_DRIVER_ASSIGNED
        description = f"Driver {driver_name} ({driver.vehicle_type}, {driver.vehicle_registration_number}) assigned."
    elif request.status == domain.STATUS_ASSIGNED:
        if request.assigned_driver_id == driver.id:
            raise ValidationError("This driver is already assigned to the request.")
        old_name = request.driver.user.full_name if request.driver else "previous driver"
        previous = _transition(
            session, request, new_status=domain.STATUS_ASSIGNED, values={"assigned_driver_id": driver.id}
        )
        event_type = domain.EVENT_DRIVER_REASSIGNED
        description = f"Reassigned from {old_name} to {driver_name} ({driver.vehicle_registration_number})."
    else:
        raise InvalidTransitionError(
            "Drivers can be assigned only to approved requests, and reassigned only before dispatch."
        )
    if note:
        description = f"{description} {note}"
    record_event(
        session, request=request, actor_user_id=actor.id, event_type=event_type, previous_status=previous,
        new_status=domain.STATUS_ASSIGNED, description=description,
    )
    record_audit(
        session, actor_user_id=actor.id, action=event_type, target_type="transport_request", target_id=request.id,
        details={"request_number": request.request_number, "driver_id": driver.id},
    )
    return _reload(session, request.id)


def update_logistics(
    session: Session,
    actor: CurrentUser | None,
    request_id: int,
    *,
    transport_cost=None,
    estimated_delivery_time: dt.datetime | None = None,
    now: dt.datetime | None = None,
) -> TransportRequest:
    actor = require_admin(actor)
    request = _load(session, request_id)
    if request.status not in domain.ACTIVE_STATUSES:
        raise InvalidTransitionError("Logistics details can only be changed on active requests.")
    now = ensure_utc(now) if now else utcnow()
    values: dict = {}
    parts: list[str] = []
    cost = validate_non_negative_decimal(transport_cost, "Transport cost")
    if cost is not None:
        values["transport_cost"] = cost
        parts.append(f"transport cost set to {format_naira(cost)}")
    if estimated_delivery_time is not None:
        eta = ensure_utc(estimated_delivery_time)
        if eta <= now:
            raise ValidationError("The estimated delivery time must be in the future.")
        values["estimated_delivery_time"] = eta
        parts.append(f"estimated delivery {format_datetime(eta)}")
    if not values:
        raise ValidationError("Enter a transport cost or an estimated delivery time to update.")
    _transition(session, request, new_status=request.status, values=values)
    record_event(
        session, request=request, actor_user_id=actor.id, event_type=domain.EVENT_DETAILS_UPDATED,
        previous_status=request.status, new_status=request.status,
        description="Administrator updated logistics: " + "; ".join(parts) + ".", timestamp=now,
    )
    return _reload(session, request.id)


# ------------------------------------------------------------------ driver
def accept_request(
    session: Session, actor: CurrentUser | None, request_id: int, *, self_assign_enabled: bool = True
) -> TransportRequest:
    """A driver claims an approved, unassigned request (first come, first served)."""
    actor = require_role(actor, domain.ROLE_DRIVER)
    if not self_assign_enabled:
        raise PermissionDenied("Drivers cannot claim requests directly; an administrator assigns deliveries.")
    driver = driver_for_actor(session, actor)
    driver = repository.get_driver(session, driver.id)
    eligible, reason = driver_service.check_driver_eligible(driver)
    if not eligible:
        raise ValidationError(reason)
    request = _load(session, request_id)
    if request.status != domain.STATUS_APPROVED or request.assigned_driver_id is not None:
        raise ConflictError("This request has already been taken or is no longer available.")
    try:
        previous = _transition(
            session, request, new_status=domain.STATUS_ASSIGNED, values={"assigned_driver_id": driver.id},
            extra_conditions=(TransportRequest.assigned_driver_id.is_(None),),
        )
    except ConflictError:
        raise ConflictError("Another driver accepted this request first.") from None
    record_event(
        session, request=request, actor_user_id=actor.id, event_type=domain.EVENT_REQUEST_ACCEPTED,
        previous_status=previous, new_status=domain.STATUS_ASSIGNED,
        description=f"Accepted by driver {actor.full_name} ({driver.vehicle_registration_number}).",
    )
    return _reload(session, request.id)


def advance_delivery(
    session: Session,
    actor: CurrentUser | None,
    request_id: int,
    new_status: str,
    *,
    location: str | None = None,
    notes: str | None = None,
    now: dt.datetime | None = None,
) -> TransportRequest:
    """Assigned → Dispatched → In-Transit → Delivered, by the assigned driver only."""
    request = _load(session, request_id)
    _ensure_assigned_driver(session, actor, request)
    if request.status in domain.TERMINAL_STATUSES:
        raise InvalidTransitionError(f"This delivery is {request.status} and can no longer be updated.")
    expected_next = domain.DRIVER_PROGRESSIONS.get(request.status)
    if new_status != expected_next:
        raise InvalidTransitionError(
            f"From {request.status} the only allowed next step is {expected_next or 'none'}."
        )
    now = ensure_utc(now) if now else utcnow()
    location = optional_text(location, "Current location", max_length=255)
    note = optional_text(notes, "Delivery notes", max_length=1000)

    values: dict = {}
    if new_status == domain.STATUS_DISPATCHED:
        values["actual_dispatch_time"] = now
    if new_status == domain.STATUS_DELIVERED:
        values["actual_delivery_time"] = now
    if note:
        values["delivery_notes"] = _append_note(request.delivery_notes, note, actor.full_name, now)
    was_delayed = request.is_delayed
    if was_delayed and new_status == domain.STATUS_DELIVERED:
        values["is_delayed"] = False

    previous = _transition(session, request, new_status=new_status, values=values)
    batch = request.batch
    if new_status == domain.STATUS_DISPATCHED:
        batch.batch_status = domain.BATCH_IN_TRANSPORT
    elif new_status == domain.STATUS_DELIVERED:
        batch.batch_status = domain.BATCH_DELIVERED

    descriptions = {
        domain.STATUS_DISPATCHED: "Produce collected and dispatched from the pickup point.",
        domain.STATUS_IN_TRANSIT: "Produce is on the road to its destination.",
        domain.STATUS_DELIVERED: "Produce delivered to the destination.",
    }
    description = descriptions[new_status] + (f" Note: {note}" if note else "")
    record_event(
        session, request=request, actor_user_id=actor.id, event_type=domain.STATUS_EVENT_TYPES[new_status],
        previous_status=previous, new_status=new_status, description=description, recorded_location=location,
        timestamp=now,
    )
    if was_delayed and new_status == domain.STATUS_DELIVERED:
        record_event(
            session, request=request, actor_user_id=actor.id, event_type=domain.EVENT_DELAY_RESOLVED,
            previous_status=new_status, new_status=new_status,
            description="Delay closed because the delivery was completed.", timestamp=now,
        )
    return _reload(session, request.id)


def record_checkpoint(
    session: Session, actor: CurrentUser | None, request_id: int, *, location: str, note: str | None = None,
    now: dt.datetime | None = None,
) -> TransportRequest:
    request = _load(session, request_id)
    _ensure_assigned_driver(session, actor, request)
    if request.status not in domain.ON_ROAD_STATUSES:
        raise InvalidTransitionError("Checkpoints can be recorded only while the produce is on the road.")
    location = require_text(location, "Checkpoint location", max_length=255, min_length=3)
    note = optional_text(note, "Note", max_length=500)
    now = ensure_utc(now) if now else utcnow()
    _transition(session, request, new_status=request.status)
    record_event(
        session, request=request, actor_user_id=actor.id, event_type=domain.EVENT_CHECKPOINT,
        previous_status=request.status, new_status=request.status,
        description="Checkpoint recorded by driver." + (f" {note}" if note else ""), recorded_location=location,
        timestamp=now,
    )
    return _reload(session, request.id)


def _ensure_driver_or_admin(session: Session, actor: CurrentUser | None, request: TransportRequest) -> CurrentUser:
    actor = require_role(actor, domain.ROLE_DRIVER, domain.ROLE_ADMIN)
    if not actor.is_admin:
        _ensure_assigned_driver(session, actor, request)
    return actor


def report_delay(
    session: Session, actor: CurrentUser | None, request_id: int, reason: str, *, location: str | None = None,
    now: dt.datetime | None = None,
) -> TransportRequest:
    request = _load(session, request_id)
    actor = _ensure_driver_or_admin(session, actor, request)
    if request.status not in domain.DELAYABLE_STATUSES:
        raise InvalidTransitionError("Delays can be reported only after a driver is assigned and before delivery.")
    if request.is_delayed:
        raise ConflictError("A delay is already open for this delivery. Resolve it before reporting a new one.")
    reason = require_text(reason, "Delay reason", max_length=500, min_length=5)
    location = optional_text(location, "Current location", max_length=255)
    now = ensure_utc(now) if now else utcnow()
    _transition(
        session, request, new_status=request.status,
        values={"is_delayed": True, "delay_reason": reason, "delay_reported_at": now},
        extra_conditions=(TransportRequest.is_delayed.is_(False),),
    )
    record_event(
        session, request=request, actor_user_id=actor.id, event_type=domain.EVENT_DELAY_REPORTED,
        previous_status=request.status, new_status=request.status, description=f"Delay reported: {reason}",
        recorded_location=location, timestamp=now,
    )
    return _reload(session, request.id)


def resolve_delay(
    session: Session, actor: CurrentUser | None, request_id: int, *, note: str | None = None,
    now: dt.datetime | None = None,
) -> TransportRequest:
    request = _load(session, request_id)
    actor = _ensure_driver_or_admin(session, actor, request)
    if not request.is_delayed:
        raise ValidationError("This delivery has no open delay.")
    note = optional_text(note, "Note", max_length=500)
    now = ensure_utc(now) if now else utcnow()
    _transition(
        session, request, new_status=request.status, values={"is_delayed": False},
        extra_conditions=(TransportRequest.is_delayed.is_(True),),
    )
    record_event(
        session, request=request, actor_user_id=actor.id, event_type=domain.EVENT_DELAY_RESOLVED,
        previous_status=request.status, new_status=request.status,
        description="Delay resolved; delivery continues from its current stage." + (f" {note}" if note else ""),
        timestamp=now,
    )
    return _reload(session, request.id)


# ---------------------------------------------------------------- queries
@dataclass
class RequestFilters:
    status: str | None = None
    search: str | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    delayed_only: bool = False
    state: str | None = None
    include_demo: bool = True


def _scope_conditions(session: Session, actor: CurrentUser) -> list:
    conditions = []
    if actor.has_role(domain.ROLE_FARMER):
        conditions.append(TransportRequest.farmer_user_id == actor.id)
    if actor.has_role(domain.ROLE_DRIVER):
        driver = repository.get_driver_by_user(session, actor.id)
        if driver is not None:
            conditions.append(TransportRequest.assigned_driver_id == driver.id)
    return conditions


def list_requests(
    session: Session, actor: CurrentUser | None, filters: RequestFilters | None = None, *, tz_name: str = "Africa/Lagos"
) -> list[TransportRequest]:
    """Requests visible to the actor: all (admin), own (farmer) or assigned (driver)."""
    actor = require_authenticated(actor)
    filters = filters or RequestFilters()
    stmt = (
        select(TransportRequest)
        .options(*repository.request_load_options())
        .join(ProduceBatch, ProduceBatch.id == TransportRequest.produce_batch_id)
        .join(Farm, Farm.id == ProduceBatch.farm_id)
        .order_by(TransportRequest.created_at.desc(), TransportRequest.id.desc())
    )
    if not actor.is_admin:
        conditions = _scope_conditions(session, actor)
        if not conditions:
            raise PermissionDenied("You do not have access to transport requests.")
        stmt = stmt.where(or_(*conditions))
    if filters.status:
        stmt = stmt.where(TransportRequest.status == filters.status)
    if filters.delayed_only:
        stmt = stmt.where(TransportRequest.is_delayed.is_(True))
    if filters.state:
        stmt = stmt.where(Farm.state == filters.state)
    if not filters.include_demo:
        stmt = stmt.where(TransportRequest.is_demo.is_(False))
    if filters.date_from:
        stmt = stmt.where(TransportRequest.created_at >= _local_midnight_utc(filters.date_from, tz_name))
    if filters.date_to:
        stmt = stmt.where(
            TransportRequest.created_at < _local_midnight_utc(filters.date_to + dt.timedelta(days=1), tz_name)
        )
    if filters.search and filters.search.strip():
        pattern = like_pattern(filters.search)
        stmt = stmt.where(
            or_(
                TransportRequest.request_number.ilike(pattern, escape="\\"),
                ProduceBatch.batch_number.ilike(pattern, escape="\\"),
                ProduceBatch.produce_name.ilike(pattern, escape="\\"),
                TransportRequest.pickup_location.ilike(pattern, escape="\\"),
                TransportRequest.destination_location.ilike(pattern, escape="\\"),
                Farm.farm_name.ilike(pattern, escape="\\"),
            )
        )
    return list(session.execute(stmt).unique().scalars())


def _local_midnight_utc(day: dt.date, tz_name: str) -> dt.datetime:
    from farmtrace.utils.helpers import local_datetime_to_utc

    return local_datetime_to_utc(day, dt.time(0, 0), tz_name)


def get_request(session: Session, actor: CurrentUser | None, request_id: int) -> TransportRequest:
    request = _load(session, request_id)
    ensure_can_view_request(session, actor, request)
    return request


def list_open_requests_for_drivers(session: Session, actor: CurrentUser | None) -> list[TransportRequest]:
    """Approved, unassigned requests a driver could accept (no private farmer contact data is shown)."""
    require_role(actor, domain.ROLE_DRIVER)
    stmt = (
        select(TransportRequest)
        .options(*repository.request_load_options())
        .where(TransportRequest.status == domain.STATUS_APPROVED, TransportRequest.assigned_driver_id.is_(None))
        .order_by(TransportRequest.requested_pickup_date, TransportRequest.id)
    )
    return list(session.execute(stmt).unique().scalars())


def list_assigned_to_me(
    session: Session, actor: CurrentUser | None, *, active_only: bool = True
) -> list[TransportRequest]:
    driver = driver_for_actor(session, actor)
    stmt = (
        select(TransportRequest)
        .options(*repository.request_load_options())
        .where(TransportRequest.assigned_driver_id == driver.id)
        .order_by(TransportRequest.requested_pickup_date, TransportRequest.id)
    )
    if active_only:
        stmt = stmt.where(TransportRequest.status.in_(domain.DELAYABLE_STATUSES))
    else:
        stmt = stmt.where(TransportRequest.status.in_(domain.TERMINAL_STATUSES))
    return list(session.execute(stmt).unique().scalars())


def next_driver_status(request: TransportRequest) -> str | None:
    return domain.DRIVER_PROGRESSIONS.get(request.status)
