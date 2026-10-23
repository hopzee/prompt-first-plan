"""Centralised, hash-linked transport history.

Every significant event goes through :func:`record_event`; pages never write
history rows themselves. Each event stores ``previous_hash`` (the hash of the
previous event for the same transport request) and ``event_hash``::

    event_hash = SHA-256( canonical_json(event fields + previous_hash) )

:func:`verify_chain` recomputes the chain and reports any event whose content,
order or link no longer matches.

Limitations (stated honestly): the chain is tamper-*evident*, not tamper-*proof*.
Someone with full database privileges could disable the append-only triggers and
rewrite every hash consistently. Exporting the latest hashes regularly (e.g. in
the CSV history report) and comparing them later makes that detectable too.
This is not a blockchain.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from farmtrace import domain
from farmtrace.authentication.permissions import CurrentUser, require_admin, require_authenticated
from farmtrace.database import repository
from farmtrace.database.models import Driver, TransportHistory, TransportRequest, User
from farmtrace.errors import NotFoundError, PermissionDenied
from farmtrace.services.access import ensure_can_view_request
from farmtrace.utils.helpers import ensure_utc, utcnow

HASH_FIELDS = (
    "transport_request_id",
    "produce_batch_id",
    "sequence_number",
    "actor_user_id",
    "event_type",
    "previous_status",
    "new_status",
    "event_description",
    "recorded_location",
    "location_source",
    "event_timestamp",
    "previous_hash",
)


def _canonical_timestamp(value: dt.datetime) -> str:
    return ensure_utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical_payload(event: TransportHistory) -> str:
    data = {}
    for field in HASH_FIELDS:
        value = getattr(event, field)
        if isinstance(value, dt.datetime):
            value = _canonical_timestamp(value)
        data[field] = value
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_event_hash(event: TransportHistory) -> str:
    return hashlib.sha256(canonical_payload(event).encode("utf-8")).hexdigest()


def record_event(
    session: Session,
    *,
    request: TransportRequest,
    actor_user_id: int | None,
    event_type: str,
    previous_status: str | None,
    new_status: str | None,
    description: str,
    recorded_location: str | None = None,
    timestamp: dt.datetime | None = None,
) -> TransportHistory:
    """Append one event to the request's chain inside the caller's transaction."""
    last = session.execute(
        select(TransportHistory)
        .where(TransportHistory.transport_request_id == request.id)
        .order_by(TransportHistory.sequence_number.desc())
        .limit(1)
    ).scalar_one_or_none()

    event = TransportHistory(
        transport_request_id=request.id,
        produce_batch_id=request.produce_batch_id,
        actor_user_id=actor_user_id,
        sequence_number=(last.sequence_number + 1) if last else 1,
        event_type=event_type,
        previous_status=previous_status,
        new_status=new_status,
        event_description=description,
        recorded_location=recorded_location,
        location_source=domain.LOCATION_SOURCE_MANUAL if recorded_location else None,
        event_timestamp=ensure_utc(timestamp) if timestamp else utcnow(),
        previous_hash=last.event_hash if last else None,
    )
    event.event_hash = compute_event_hash(event)
    session.add(event)
    session.flush()
    return event


def get_request_history(session: Session, actor: CurrentUser | None, request_id: int) -> list[TransportHistory]:
    request = repository.get_request(session, request_id)
    if request is None:
        raise NotFoundError("Transport request not found.")
    ensure_can_view_request(session, actor, request)
    return repository.history_for_request(session, request_id)


@dataclass(frozen=True)
class ChainVerification:
    transport_request_id: int
    request_number: str | None
    valid: bool
    events_checked: int
    problems: tuple[str, ...]


def verify_events(events: list[TransportHistory], request_id: int, request_number: str | None = None) -> ChainVerification:
    problems: list[str] = []
    previous_hash: str | None = None
    for expected_sequence, event in enumerate(events, start=1):
        label = f"Event #{event.sequence_number} ({event.event_type})"
        if event.sequence_number != expected_sequence:
            problems.append(f"{label}: sequence gap — expected #{expected_sequence}.")
        if event.previous_hash != previous_hash:
            problems.append(f"{label}: link to the previous event is broken.")
        if compute_event_hash(event) != event.event_hash:
            problems.append(f"{label}: recorded content no longer matches its hash (possible alteration).")
        previous_hash = event.event_hash
    return ChainVerification(request_id, request_number, not problems, len(events), tuple(problems))


def verify_chain(session: Session, request_id: int) -> ChainVerification:
    request = session.get(TransportRequest, request_id)
    if request is None:
        raise NotFoundError("Transport request not found.")
    return verify_events(repository.history_for_request(session, request_id), request_id, request.request_number)


def verify_request_chain(session: Session, actor: CurrentUser | None, request_id: int) -> ChainVerification:
    request = repository.get_request(session, request_id)
    if request is None:
        raise NotFoundError("Transport request not found.")
    ensure_can_view_request(session, actor, request)
    return verify_chain(session, request_id)


def verify_all_chains(session: Session, actor: CurrentUser | None) -> list[ChainVerification]:
    require_admin(actor)
    ids = session.execute(select(TransportRequest.id).order_by(TransportRequest.id)).scalars().all()
    return [verify_chain(session, request_id) for request_id in ids]


def recent_events(session: Session, actor: CurrentUser | None, limit: int = 20) -> list[TransportHistory]:
    """Latest events the actor is allowed to see."""
    actor = require_authenticated(actor)
    stmt = (
        select(TransportHistory)
        .options(joinedload(TransportHistory.actor).joinedload(User.profile))
        .join(TransportRequest, TransportRequest.id == TransportHistory.transport_request_id)
        .order_by(TransportHistory.event_timestamp.desc(), TransportHistory.id.desc())
        .limit(limit)
    )
    if not actor.is_admin:
        conditions = []
        if actor.has_role(domain.ROLE_FARMER):
            conditions.append(TransportRequest.farmer_user_id == actor.id)
        if actor.has_role(domain.ROLE_DRIVER):
            driver_id = session.execute(select(Driver.id).where(Driver.user_id == actor.id)).scalar_one_or_none()
            if driver_id is not None:
                conditions.append(TransportRequest.assigned_driver_id == driver_id)
        if not conditions:
            raise PermissionDenied("You do not have access to transport events.")
        from sqlalchemy import or_

        stmt = stmt.where(or_(*conditions))
    return list(session.execute(stmt).unique().scalars())


def request_numbers(session: Session, request_ids: set[int]) -> dict[int, str]:
    if not request_ids:
        return {}
    rows = session.execute(
        select(TransportRequest.id, TransportRequest.request_number).where(TransportRequest.id.in_(request_ids))
    ).all()
    return {row.id: row.request_number for row in rows}
