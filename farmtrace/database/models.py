"""SQLAlchemy ORM models.

Design notes
------------
* Authentication credentials (``user_credentials``), profile information
  (``user_profiles``) and role assignments (``user_roles``) are stored in
  separate tables, all keyed to ``users``.
* Ownership is enforced by the database as well as by services: a produce batch
  references ``farms(id, farmer_user_id)`` with a composite foreign key, so a
  batch can never point at another farmer's farm; transport requests do the
  same against ``produce_batches(id, farmer_user_id)``.
* Only one *active* transport request may exist per batch (partial unique index).
* ``transport_history`` and ``audit_logs`` are append-only. The ORM refuses to
  update/delete them, and the Alembic migration adds PostgreSQL triggers that
  reject UPDATE/DELETE for ordinary connections.
* All timestamps are stored as timezone-aware UTC values.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from farmtrace import domain
from farmtrace.errors import AppendOnlyViolation
from farmtrace.utils.helpers import utcnow

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _sql_in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


ACTIVE_REQUEST_CLAUSE = _sql_in("status", domain.ACTIVE_STATUSES)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _created_at() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now())


def _updated_at() -> Mapped[dt.datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )


# ============================================================== users & accounts
class User(Base):
    """Account identity. Credentials, profile and roles live in separate tables."""

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = _updated_at()

    credential: Mapped["UserCredential"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    profile: Mapped["UserProfile"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    roles: Mapped[list["UserRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", foreign_keys="UserRole.user_id"
    )

    @property
    def role_names(self) -> frozenset[str]:
        return frozenset(role.role for role in self.roles)

    @property
    def full_name(self) -> str:
        return self.profile.full_name if self.profile else self.email


class UserCredential(Base):
    """Password hash and login-throttling state. Never exposed outside the auth module."""

    __tablename__ = "user_credentials"
    __table_args__ = (CheckConstraint("failed_login_attempts >= 0", name="failed_attempts_non_negative"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_changed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = _updated_at()

    user: Mapped[User] = relationship(back_populates="credential")


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = _updated_at()

    user: Mapped[User] = relationship(back_populates="profile")


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role"),
        CheckConstraint(_sql_in("role", domain.ALL_ROLES), name="valid_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    assigned_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = _created_at()

    user: Mapped[User] = relationship(back_populates="roles", foreign_keys=[user_id])


# ========================================================================= farms
class Farm(Base):
    __tablename__ = "farms"
    __table_args__ = (
        UniqueConstraint("farmer_user_id", "farm_name"),
        UniqueConstraint("id", "farmer_user_id", name="uq_farms_id_owner"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    farmer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    farm_name: Mapped[str] = mapped_column(String(150), nullable=False)
    farm_location: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(50), nullable=False)
    local_government_area: Mapped[str] = mapped_column(String(100), nullable=False)
    contact_information: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = _updated_at()

    farmer: Mapped[User] = relationship(foreign_keys=[farmer_user_id], viewonly=True)


# ======================================================================= drivers
class Driver(Base):
    """Driver registry entry. Licence data is private and only shown to administrators."""

    __tablename__ = "drivers"
    __table_args__ = (
        CheckConstraint("vehicle_capacity > 0", name="capacity_positive"),
        CheckConstraint(_sql_in("availability_status", domain.AVAILABILITY_STATUSES), name="valid_availability"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, unique=True)
    driver_license_number: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    vehicle_registration_number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    vehicle_type: Mapped[str] = mapped_column(String(50), nullable=False)
    vehicle_capacity: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    availability_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=domain.AVAILABILITY_AVAILABLE, server_default=domain.AVAILABILITY_AVAILABLE
    )
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = _updated_at()

    user: Mapped[User] = relationship(foreign_keys=[user_id], viewonly=True)


# =============================================================== produce batches
class ProduceBatch(Base):
    __tablename__ = "produce_batches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["farm_id", "farmer_user_id"],
            ["farms.id", "farms.farmer_user_id"],
            name="fk_produce_batches_farm_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "farmer_user_id", name="uq_produce_batches_id_owner"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(_sql_in("batch_status", domain.BATCH_STATUSES), name="valid_batch_status"),
        Index("ix_produce_batches_farmer_status", "farmer_user_id", "batch_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    farmer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    farm_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    produce_name: Mapped[str] = mapped_column(String(100), nullable=False)
    produce_category: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unit_of_measurement: Mapped[str] = mapped_column(String(30), nullable=False)
    harvest_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    pickup_location: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_market: Mapped[str] = mapped_column(String(150), nullable=False)
    destination_location: Mapped[str] = mapped_column(String(255), nullable=False)
    additional_notes: Mapped[str | None] = mapped_column(Text)
    qr_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    qr_revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    batch_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=domain.BATCH_REGISTERED, server_default=domain.BATCH_REGISTERED
    )
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = _updated_at()

    farm: Mapped[Farm] = relationship(primaryjoin="Farm.id == foreign(ProduceBatch.farm_id)", viewonly=True)
    farmer: Mapped[User] = relationship(foreign_keys=[farmer_user_id], viewonly=True)


class RevokedQrToken(Base):
    """QR tokens that were replaced or withdrawn, so scans can explain why they no longer work."""

    __tablename__ = "revoked_qr_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    produce_batch_id: Mapped[int] = mapped_column(
        ForeignKey("produce_batches.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    reason: Mapped[str | None] = mapped_column(String(255))
    revoked_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revoked_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


# ============================================================ transport requests
class TransportRequest(Base):
    __tablename__ = "transport_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["produce_batch_id", "farmer_user_id"],
            ["produce_batches.id", "produce_batches.farmer_user_id"],
            name="fk_transport_requests_batch_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint(_sql_in("status", domain.DELIVERY_STATUSES), name="valid_status"),
        CheckConstraint(
            "NOT (" + _sql_in("status", domain.DRIVER_REQUIRED_STATUSES) + ") OR assigned_driver_id IS NOT NULL",
            name="driver_required",
        ),
        CheckConstraint("transport_cost IS NULL OR transport_cost >= 0", name="cost_non_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_transport_requests_active_batch",
            "produce_batch_id",
            unique=True,
            postgresql_where=text(ACTIVE_REQUEST_CLAUSE),
            sqlite_where=text(ACTIVE_REQUEST_CLAUSE),
        ),
        Index("ix_transport_requests_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    produce_batch_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    farmer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    assigned_driver_id: Mapped[int | None] = mapped_column(ForeignKey("drivers.id", ondelete="RESTRICT"), index=True)
    pickup_location: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_location: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_pickup_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    actual_dispatch_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    actual_delivery_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    estimated_delivery_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    transport_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=domain.STATUS_PENDING, server_default=domain.STATUS_PENDING
    )
    is_delayed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    delay_reason: Mapped[str | None] = mapped_column(String(500))
    delay_reported_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(String(500))
    delivery_notes: Mapped[str | None] = mapped_column(Text)
    #: Optimistic-concurrency counter. Every status change is a conditional UPDATE on it.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = _updated_at()

    batch: Mapped[ProduceBatch] = relationship(
        primaryjoin="ProduceBatch.id == foreign(TransportRequest.produce_batch_id)", viewonly=True
    )
    farmer: Mapped[User] = relationship(foreign_keys=[farmer_user_id], viewonly=True)
    driver: Mapped[Driver | None] = relationship(foreign_keys=[assigned_driver_id], viewonly=True)

    @property
    def display_status(self) -> str:
        return f"{self.status} · {domain.DELAYED_LABEL}" if self.is_delayed else self.status


# ============================================================ tracking history
class TransportHistory(Base):
    """Append-only, hash-linked event log for one transport request.

    ``event_hash`` = SHA-256 of a canonical JSON form of the event fields plus the
    previous event's hash (see ``tracking_service``). The chain is tamper-*evident*:
    it reveals edits, but cannot by itself stop a privileged database administrator
    from rewriting the whole chain.
    """

    __tablename__ = "transport_history"
    __table_args__ = (
        UniqueConstraint("transport_request_id", "sequence_number"),
        CheckConstraint("sequence_number >= 1", name="sequence_positive"),
        Index("ix_transport_history_batch_time", "produce_batch_id", "event_timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transport_request_id: Mapped[int] = mapped_column(
        ForeignKey("transport_requests.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    produce_batch_id: Mapped[int] = mapped_column(ForeignKey("produce_batches.id", ondelete="RESTRICT"), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(20))
    new_status: Mapped[str | None] = mapped_column(String(20))
    event_description: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_location: Mapped[str | None] = mapped_column(String(255))
    location_source: Mapped[str | None] = mapped_column(String(20))
    event_timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous_hash: Mapped[str | None] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[dt.datetime] = _created_at()

    actor: Mapped[User | None] = relationship(foreign_keys=[actor_user_id], viewonly=True)


class AuditLog(Base):
    """Append-only record of significant account and administrative actions."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(40))
    details: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created_at()

    actor: Mapped[User | None] = relationship(foreign_keys=[actor_user_id], viewonly=True)


# ---------------------------------------------------- append-only ORM safeguards
def _reject_change(mapper, connection, target) -> None:  # noqa: ARG001 - SQLAlchemy signature
    raise AppendOnlyViolation()


for _model in (TransportHistory, AuditLog):
    event.listen(_model, "before_update", _reject_change)
    event.listen(_model, "before_delete", _reject_change)
