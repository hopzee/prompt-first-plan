"""Reports, CSV export and dashboard analytics — all computed from database records.

CSV cells that begin with ``= + - @`` are prefixed with an apostrophe to stop
spreadsheet formula injection when reports are opened in Excel.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from farmtrace import domain
from farmtrace.authentication.permissions import CurrentUser, require_admin, require_authenticated
from farmtrace.database import repository
from farmtrace.database.models import Driver, ProduceBatch, TransportHistory, TransportRequest, User, UserRole
from farmtrace.services import farmer_service, transport_service
from farmtrace.services.farmer_service import BatchFilters
from farmtrace.services.transport_service import RequestFilters
from farmtrace.utils.helpers import ensure_utc, to_local

TRANSPORT_COLUMNS = [
    "Request number", "Batch number", "Produce", "Quantity", "Unit", "Farm", "State", "Pickup location",
    "Destination", "Requested pickup date", "Status", "Delayed", "Driver", "Vehicle", "Dispatched at",
    "Delivered at", "Estimated delivery", "Transport cost (NGN)", "Created at", "Demo record",
]
BATCH_COLUMNS = [
    "Batch number", "Produce", "Category", "Quantity", "Unit", "Harvest date", "Farm", "State", "LGA",
    "Destination market", "Batch status", "QR status", "Registered at", "Demo record",
]
HISTORY_COLUMNS = [
    "Request number", "Sequence", "Event", "Previous status", "New status", "Description", "Location",
    "Location source", "Recorded by", "Event time", "Event hash",
]

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _local_naive(value: dt.datetime | None, tz_name: str):
    local = to_local(value, tz_name)
    return local.replace(tzinfo=None) if local else None


def transport_report(
    session: Session, actor: CurrentUser | None, filters: RequestFilters | None = None, *, tz_name: str = "Africa/Lagos"
) -> pd.DataFrame:
    requests = transport_service.list_requests(session, actor, filters, tz_name=tz_name)
    rows = []
    for request in requests:
        batch = request.batch
        driver = request.driver
        rows.append({
            "Request number": request.request_number,
            "Batch number": batch.batch_number,
            "Produce": batch.produce_name,
            "Quantity": float(batch.quantity),
            "Unit": batch.unit_of_measurement,
            "Farm": batch.farm.farm_name,
            "State": batch.farm.state,
            "Pickup location": request.pickup_location,
            "Destination": request.destination_location,
            "Requested pickup date": request.requested_pickup_date,
            "Status": request.status,
            "Delayed": "Yes" if request.is_delayed else "No",
            "Driver": driver.user.full_name if driver else "",
            "Vehicle": f"{driver.vehicle_type} ({driver.vehicle_registration_number})" if driver else "",
            "Dispatched at": _local_naive(request.actual_dispatch_time, tz_name),
            "Delivered at": _local_naive(request.actual_delivery_time, tz_name),
            "Estimated delivery": _local_naive(request.estimated_delivery_time, tz_name),
            "Transport cost (NGN)": float(request.transport_cost) if request.transport_cost is not None else None,
            "Created at": _local_naive(request.created_at, tz_name),
            "Demo record": "Yes" if request.is_demo else "No",
        })
    return pd.DataFrame(rows, columns=TRANSPORT_COLUMNS)


def batch_report(
    session: Session, actor: CurrentUser | None, filters: BatchFilters | None = None, *, tz_name: str = "Africa/Lagos"
) -> pd.DataFrame:
    batches = farmer_service.list_batches(session, actor, filters)
    rows = [{
        "Batch number": b.batch_number,
        "Produce": b.produce_name,
        "Category": b.produce_category,
        "Quantity": float(b.quantity),
        "Unit": b.unit_of_measurement,
        "Harvest date": b.harvest_date,
        "Farm": b.farm.farm_name,
        "State": b.farm.state,
        "LGA": b.farm.local_government_area,
        "Destination market": b.destination_market,
        "Batch status": b.batch_status,
        "QR status": "Revoked" if b.qr_revoked_at else "Active",
        "Registered at": _local_naive(b.created_at, tz_name),
        "Demo record": "Yes" if b.is_demo else "No",
    } for b in batches]
    return pd.DataFrame(rows, columns=BATCH_COLUMNS)


def history_report(
    session: Session, actor: CurrentUser | None, filters: RequestFilters | None = None, *, tz_name: str = "Africa/Lagos"
) -> pd.DataFrame:
    requests = transport_service.list_requests(session, actor, filters, tz_name=tz_name)
    rows = []
    for request in requests:
        for event in repository.history_for_request(session, request.id):
            rows.append({
                "Request number": request.request_number,
                "Sequence": event.sequence_number,
                "Event": domain.EVENT_LABELS.get(event.event_type, event.event_type),
                "Previous status": event.previous_status or "",
                "New status": event.new_status or "",
                "Description": event.event_description,
                "Location": event.recorded_location or "",
                "Location source": domain.LOCATION_SOURCE_LABEL if event.recorded_location else "",
                "Recorded by": event.actor.full_name if event.actor else "System",
                "Event time": _local_naive(event.event_timestamp, tz_name),
                "Event hash": event.event_hash,
            })
    return pd.DataFrame(rows, columns=HISTORY_COLUMNS)


def _neutralise(value):
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def to_csv_bytes(frame: pd.DataFrame) -> bytes:
    safe = frame.copy()
    for column in safe.columns:
        if safe[column].dtype == object or pd.api.types.is_string_dtype(safe[column]):
            safe[column] = safe[column].map(_neutralise)
    return safe.to_csv(index=False).encode("utf-8-sig")


# ------------------------------------------------------------------- analytics
def admin_metrics(session: Session, actor: CurrentUser | None) -> dict[str, int]:
    require_admin(actor)

    def scalar(stmt) -> int:
        return int(session.execute(stmt).scalar_one() or 0)

    def role_count(role: str) -> int:
        return scalar(select(func.count(func.distinct(UserRole.user_id))).where(UserRole.role == role))

    requests = select(func.count(TransportRequest.id))
    return {
        "users": scalar(select(func.count(User.id))),
        "active_users": scalar(select(func.count(User.id)).where(User.is_active.is_(True))),
        "farmers": role_count(domain.ROLE_FARMER),
        "drivers": role_count(domain.ROLE_DRIVER),
        "consumers": role_count(domain.ROLE_CONSUMER),
        "admins": role_count(domain.ROLE_ADMIN),
        "driver_profiles": scalar(select(func.count(Driver.id))),
        "batches": scalar(select(func.count(ProduceBatch.id))),
        "pending": scalar(requests.where(TransportRequest.status == domain.STATUS_PENDING)),
        "approved": scalar(requests.where(TransportRequest.status == domain.STATUS_APPROVED)),
        "active": scalar(requests.where(TransportRequest.status.in_(domain.DELAYABLE_STATUSES))),
        "completed": scalar(requests.where(TransportRequest.status == domain.STATUS_DELIVERED)),
        "cancelled": scalar(requests.where(TransportRequest.status == domain.STATUS_CANCELLED)),
        "delayed": scalar(
            requests.where(TransportRequest.is_delayed.is_(True), TransportRequest.status.in_(domain.ACTIVE_STATUSES))
        ),
        "events": scalar(select(func.count(TransportHistory.id))),
    }


def status_distribution(report: pd.DataFrame) -> pd.DataFrame:
    if report.empty:
        return pd.DataFrame(columns=["Status", "Requests"])
    counts = report.groupby("Status").size().reindex(domain.DELIVERY_STATUSES, fill_value=0)
    return counts.rename("Requests").reset_index().rename(columns={"index": "Status"})


def requests_per_day(report: pd.DataFrame) -> pd.DataFrame:
    if report.empty:
        return pd.DataFrame(columns=["Date", "Requests"])
    dates = pd.to_datetime(report["Created at"]).dt.date
    return dates.value_counts().sort_index().rename_axis("Date").reset_index(name="Requests")


def delivery_performance(report: pd.DataFrame) -> pd.DataFrame:
    """Per delivered request: hours on the road and, where an ETA was set, whether it was on time."""
    columns = ["Request number", "Hours in transit", "On time"]
    if report.empty:
        return pd.DataFrame(columns=columns)
    delivered = report[(report["Status"] == domain.STATUS_DELIVERED) & report["Dispatched at"].notna()].copy()
    if delivered.empty:
        return pd.DataFrame(columns=columns)
    dispatched = pd.to_datetime(delivered["Dispatched at"])
    arrived = pd.to_datetime(delivered["Delivered at"])
    delivered["Hours in transit"] = ((arrived - dispatched).dt.total_seconds() / 3600).round(1)
    eta = pd.to_datetime(delivered["Estimated delivery"])
    delivered["On time"] = [
        ("Yes" if a <= e else "No") if pd.notna(e) else "No ETA set" for a, e in zip(arrived, eta)
    ]
    return delivered[columns].reset_index(drop=True)


def actor_can_report(actor: CurrentUser | None) -> CurrentUser:
    actor = require_authenticated(actor)
    if not actor.has_role(domain.ROLE_ADMIN, domain.ROLE_FARMER, domain.ROLE_DRIVER):
        from farmtrace.errors import PermissionDenied

        raise PermissionDenied("Reports are available to farmers, drivers and administrators.")
    return actor


__all__ = [
    "admin_metrics", "batch_report", "delivery_performance", "ensure_utc", "history_report",
    "requests_per_day", "status_distribution", "to_csv_bytes", "transport_report",
]
