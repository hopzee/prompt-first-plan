"""Farms and produce batches. Farmers only ever see and change their own records."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from farmtrace import domain
from farmtrace.authentication.permissions import CurrentUser, ensure_owner_or_admin, require_role
from farmtrace.database import repository
from farmtrace.database.models import Farm, ProduceBatch, TransportRequest
from farmtrace.errors import ConflictError, NotFoundError, PermissionDenied, ValidationError
from farmtrace.services.audit_service import record_audit
from farmtrace.services.qr_service import generate_qr_token
from farmtrace.utils.helpers import PRODUCE_CATEGORIES, UNITS_OF_MEASUREMENT, generate_reference, local_today
from farmtrace.utils.validators import (
    optional_text,
    require_text,
    validate_choice,
    validate_date_not_future,
    validate_positive_decimal,
    validate_state,
)


def like_pattern(term: str) -> str:
    escaped = term.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# ========================================================================= farms
@dataclass
class FarmInput:
    farm_name: str
    farm_location: str
    state: str
    local_government_area: str
    contact_information: str | None = None


def _clean_farm(data: FarmInput) -> dict:
    return {
        "farm_name": require_text(data.farm_name, "Farm name", max_length=150, min_length=2),
        "farm_location": require_text(data.farm_location, "Farm location / address", max_length=255, min_length=3),
        "state": validate_state(data.state),
        "local_government_area": require_text(data.local_government_area, "Local government area", max_length=100),
        "contact_information": optional_text(data.contact_information, "Contact information", max_length=255),
    }


def create_farm(session: Session, actor: CurrentUser | None, data: FarmInput, *, is_demo: bool = False) -> Farm:
    actor = require_role(actor, domain.ROLE_FARMER)
    values = _clean_farm(data)
    duplicate = session.execute(
        select(Farm.id).where(Farm.farmer_user_id == actor.id, func.lower(Farm.farm_name) == values["farm_name"].lower())
    ).first()
    if duplicate:
        raise ValidationError("You already have a farm with this name.")
    farm = Farm(farmer_user_id=actor.id, is_demo=is_demo, **values)
    session.add(farm)
    try:
        session.flush()
    except IntegrityError:
        raise ConflictError("You already have a farm with this name.") from None
    return farm


def get_farm(session: Session, actor: CurrentUser | None, farm_id: int) -> Farm:
    farm = repository.get_farm(session, farm_id)
    if farm is None:
        raise NotFoundError("Farm not found.")
    ensure_owner_or_admin(actor, farm.farmer_user_id, "farm")
    return farm


def update_farm(session: Session, actor: CurrentUser | None, farm_id: int, data: FarmInput) -> Farm:
    farm = get_farm(session, actor, farm_id)
    values = _clean_farm(data)
    clash = session.execute(
        select(Farm.id).where(
            Farm.farmer_user_id == farm.farmer_user_id,
            func.lower(Farm.farm_name) == values["farm_name"].lower(),
            Farm.id != farm.id,
        )
    ).first()
    if clash:
        raise ValidationError("Another of your farms already uses this name.")
    for key, value in values.items():
        setattr(farm, key, value)
    session.flush()
    if actor and actor.is_admin and actor.id != farm.farmer_user_id:
        record_audit(session, actor_user_id=actor.id, action="farm_updated_by_admin", target_type="farm", target_id=farm.id)
    return farm


def set_farm_active(session: Session, actor: CurrentUser | None, farm_id: int, active: bool) -> Farm:
    farm = get_farm(session, actor, farm_id)
    farm.is_active = active
    session.flush()
    return farm


def list_farms(
    session: Session, actor: CurrentUser | None, *, search: str | None = None, active_only: bool = False
) -> list[Farm]:
    actor = require_role(actor, domain.ROLE_FARMER, domain.ROLE_ADMIN)
    stmt = select(Farm).options(joinedload(Farm.farmer)).order_by(Farm.farm_name)
    if not actor.is_admin:
        stmt = stmt.where(Farm.farmer_user_id == actor.id)
    if active_only:
        stmt = stmt.where(Farm.is_active.is_(True))
    if search and search.strip():
        pattern = like_pattern(search)
        stmt = stmt.where(
            or_(
                Farm.farm_name.ilike(pattern, escape="\\"),
                Farm.state.ilike(pattern, escape="\\"),
                Farm.local_government_area.ilike(pattern, escape="\\"),
            )
        )
    return list(session.execute(stmt).unique().scalars())


# ============================================================== produce batches
@dataclass
class BatchInput:
    farm_id: int
    produce_name: str
    produce_category: str
    quantity: object
    unit_of_measurement: str
    harvest_date: dt.date
    pickup_location: str
    destination_market: str
    destination_location: str
    additional_notes: str | None = None


@dataclass
class BatchFilters:
    search: str | None = None
    status: str | None = None
    category: str | None = None
    farm_id: int | None = None
    state: str | None = None


def _unique_reference(session: Session, model, column, prefix: str) -> str:
    for _ in range(10):
        candidate = generate_reference(prefix)
        if session.execute(select(model.id).where(column == candidate)).first() is None:
            return candidate
    raise ConflictError("Could not generate a unique reference. Please try again.")


def create_batch(
    session: Session, actor: CurrentUser | None, data: BatchInput, *, today: dt.date | None = None,
    is_demo: bool = False,
) -> ProduceBatch:
    actor = require_role(actor, domain.ROLE_FARMER)
    today = today or local_today()
    farm = repository.get_farm(session, int(data.farm_id)) if data.farm_id else None
    if farm is None or farm.farmer_user_id != actor.id:
        raise PermissionDenied("Select one of your own registered farms.")
    if not farm.is_active:
        raise ValidationError("This farm is marked inactive. Reactivate it before registering produce.")

    batch = ProduceBatch(
        batch_number=_unique_reference(session, ProduceBatch, ProduceBatch.batch_number, "FT-B"),
        farmer_user_id=actor.id,
        farm_id=farm.id,
        produce_name=require_text(data.produce_name, "Produce name", max_length=100, min_length=2),
        produce_category=validate_choice(data.produce_category, PRODUCE_CATEGORIES, "Produce category"),
        quantity=validate_positive_decimal(data.quantity, "Quantity"),
        unit_of_measurement=validate_choice(data.unit_of_measurement, UNITS_OF_MEASUREMENT, "Unit of measurement"),
        harvest_date=validate_date_not_future(data.harvest_date, "Harvest date", today),
        pickup_location=require_text(data.pickup_location, "Pickup location", max_length=255, min_length=3),
        destination_market=require_text(data.destination_market, "Destination market", max_length=150, min_length=2),
        destination_location=require_text(data.destination_location, "Destination location", max_length=255, min_length=3),
        additional_notes=optional_text(data.additional_notes, "Additional notes", max_length=2000),
        qr_token=generate_qr_token(),
        batch_status=domain.BATCH_REGISTERED,
        is_demo=is_demo,
    )
    session.add(batch)
    try:
        session.flush()
    except IntegrityError:
        raise ConflictError("A batch with the same reference already exists. Please try again.") from None
    return batch


def get_batch(session: Session, actor: CurrentUser | None, batch_id: int) -> ProduceBatch:
    batch = repository.get_batch(session, batch_id)
    if batch is None:
        raise NotFoundError("Produce batch not found.")
    ensure_owner_or_admin(actor, batch.farmer_user_id, "produce batch")
    return batch


def update_batch_details(
    session: Session, actor: CurrentUser | None, batch_id: int, *, pickup_location: str, destination_market: str,
    destination_location: str, additional_notes: str | None,
) -> ProduceBatch:
    batch = get_batch(session, actor, batch_id)
    if batch.batch_status != domain.BATCH_REGISTERED:
        raise ValidationError("Batch details can only be edited before transport has been requested.")
    batch.pickup_location = require_text(pickup_location, "Pickup location", max_length=255, min_length=3)
    batch.destination_market = require_text(destination_market, "Destination market", max_length=150, min_length=2)
    batch.destination_location = require_text(destination_location, "Destination location", max_length=255, min_length=3)
    batch.additional_notes = optional_text(additional_notes, "Additional notes", max_length=2000)
    session.flush()
    return batch


def cancel_batch(session: Session, actor: CurrentUser | None, batch_id: int, reason: str) -> ProduceBatch:
    batch = get_batch(session, actor, batch_id)
    reason = require_text(reason, "Reason", max_length=255, min_length=5)
    if batch.batch_status == domain.BATCH_CANCELLED:
        raise ValidationError("This batch is already cancelled.")
    if batch.batch_status == domain.BATCH_DELIVERED:
        raise ValidationError("Delivered batches cannot be cancelled; their record is kept for traceability.")
    if repository.get_active_request_for_batch(session, batch.id) is not None:
        raise ValidationError("Cancel the active transport request for this batch first.")
    batch.batch_status = domain.BATCH_CANCELLED
    session.flush()
    record_audit(
        session, actor_user_id=actor.id if actor else None, action="batch_cancelled", target_type="produce_batch",
        target_id=batch.id, details={"batch_number": batch.batch_number, "reason": reason},
    )
    return batch


def list_batches(session: Session, actor: CurrentUser | None, filters: BatchFilters | None = None) -> list[ProduceBatch]:
    actor = require_role(actor, domain.ROLE_FARMER, domain.ROLE_ADMIN)
    filters = filters or BatchFilters()
    stmt = (
        select(ProduceBatch)
        .options(joinedload(ProduceBatch.farm), joinedload(ProduceBatch.farmer))
        .join(Farm, Farm.id == ProduceBatch.farm_id)
        .order_by(ProduceBatch.created_at.desc(), ProduceBatch.id.desc())
    )
    if not actor.is_admin:
        stmt = stmt.where(ProduceBatch.farmer_user_id == actor.id)
    if filters.status:
        stmt = stmt.where(ProduceBatch.batch_status == filters.status)
    if filters.category:
        stmt = stmt.where(ProduceBatch.produce_category == filters.category)
    if filters.farm_id:
        stmt = stmt.where(ProduceBatch.farm_id == filters.farm_id)
    if filters.state:
        stmt = stmt.where(Farm.state == filters.state)
    if filters.search and filters.search.strip():
        pattern = like_pattern(filters.search)
        stmt = stmt.where(
            or_(
                ProduceBatch.batch_number.ilike(pattern, escape="\\"),
                ProduceBatch.produce_name.ilike(pattern, escape="\\"),
                ProduceBatch.destination_market.ilike(pattern, escape="\\"),
                Farm.farm_name.ilike(pattern, escape="\\"),
            )
        )
    return list(session.execute(stmt).unique().scalars())


def batches_eligible_for_transport(session: Session, actor: CurrentUser | None) -> list[ProduceBatch]:
    actor = require_role(actor, domain.ROLE_FARMER)
    active = select(TransportRequest.produce_batch_id).where(TransportRequest.status.in_(domain.ACTIVE_STATUSES))
    stmt = (
        select(ProduceBatch)
        .options(joinedload(ProduceBatch.farm))
        .where(
            ProduceBatch.farmer_user_id == actor.id,
            ProduceBatch.batch_status == domain.BATCH_REGISTERED,
            ProduceBatch.qr_revoked_at.is_(None),
            ProduceBatch.id.not_in(active),
        )
        .order_by(ProduceBatch.created_at.desc())
    )
    return list(session.execute(stmt).scalars())


def farmer_summary(session: Session, actor: CurrentUser | None) -> dict[str, int]:
    actor = require_role(actor, domain.ROLE_FARMER)

    def scalar(stmt) -> int:
        return int(session.execute(stmt).scalar_one() or 0)

    own_requests = select(func.count(TransportRequest.id)).where(TransportRequest.farmer_user_id == actor.id)
    return {
        "farms": scalar(select(func.count(Farm.id)).where(Farm.farmer_user_id == actor.id)),
        "batches": scalar(select(func.count(ProduceBatch.id)).where(ProduceBatch.farmer_user_id == actor.id)),
        "pending": scalar(own_requests.where(TransportRequest.status.in_((domain.STATUS_PENDING, domain.STATUS_APPROVED)))),
        "active": scalar(
            own_requests.where(TransportRequest.status.in_((domain.STATUS_ASSIGNED,) + domain.ON_ROAD_STATUSES))
        ),
        "delivered": scalar(own_requests.where(TransportRequest.status == domain.STATUS_DELIVERED)),
        "delayed": scalar(
            own_requests.where(TransportRequest.is_delayed.is_(True), TransportRequest.status.in_(domain.ACTIVE_STATUSES))
        ),
    }
