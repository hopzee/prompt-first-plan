"""Reusable, parameterised queries. Services add authorisation on top of these."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from farmtrace import domain
from farmtrace.database.models import (
    Driver,
    Farm,
    ProduceBatch,
    RevokedQrToken,
    TransportHistory,
    TransportRequest,
    User,
    UserProfile,
)


def _user_options():
    return (joinedload(User.profile), joinedload(User.credential), selectinload(User.roles))


def get_user(session: Session, user_id: int) -> User | None:
    return session.execute(select(User).options(*_user_options()).where(User.id == user_id)).unique().scalar_one_or_none()


def get_user_by_email(session: Session, email: str) -> User | None:
    stmt = select(User).options(*_user_options()).where(User.email == email.strip().lower())
    return session.execute(stmt).unique().scalar_one_or_none()


def count_admins(session: Session, *, active_only: bool = True) -> int:
    from farmtrace.database.models import UserRole

    stmt = select(func.count(func.distinct(UserRole.user_id))).join(User, User.id == UserRole.user_id)
    stmt = stmt.where(UserRole.role == domain.ROLE_ADMIN)
    if active_only:
        stmt = stmt.where(User.is_active.is_(True))
    return int(session.execute(stmt).scalar_one())


def get_farm(session: Session, farm_id: int) -> Farm | None:
    return session.get(Farm, farm_id)


def get_batch(session: Session, batch_id: int) -> ProduceBatch | None:
    stmt = select(ProduceBatch).options(joinedload(ProduceBatch.farm)).where(ProduceBatch.id == batch_id)
    return session.execute(stmt).scalar_one_or_none()


def get_batch_by_token(session: Session, token: str) -> ProduceBatch | None:
    stmt = select(ProduceBatch).options(joinedload(ProduceBatch.farm)).where(ProduceBatch.qr_token == token)
    return session.execute(stmt).scalar_one_or_none()


def get_revoked_token(session: Session, token: str) -> RevokedQrToken | None:
    return session.execute(select(RevokedQrToken).where(RevokedQrToken.token == token)).scalar_one_or_none()


def request_load_options():
    return (
        joinedload(TransportRequest.batch).joinedload(ProduceBatch.farm),
        joinedload(TransportRequest.farmer).joinedload(User.profile),
        joinedload(TransportRequest.driver).joinedload(Driver.user).joinedload(User.profile),
    )


def get_request(session: Session, request_id: int) -> TransportRequest | None:
    stmt = select(TransportRequest).options(*request_load_options()).where(TransportRequest.id == request_id)
    return session.execute(stmt).unique().scalar_one_or_none()


def get_active_request_for_batch(session: Session, batch_id: int) -> TransportRequest | None:
    stmt = select(TransportRequest).where(
        TransportRequest.produce_batch_id == batch_id, TransportRequest.status.in_(domain.ACTIVE_STATUSES)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_latest_request_for_batch(session: Session, batch_id: int) -> TransportRequest | None:
    stmt = (
        select(TransportRequest)
        .where(TransportRequest.produce_batch_id == batch_id)
        .order_by(TransportRequest.created_at.desc(), TransportRequest.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_driver(session: Session, driver_id: int) -> Driver | None:
    stmt = (
        select(Driver)
        .options(joinedload(Driver.user).joinedload(User.profile), joinedload(Driver.user).selectinload(User.roles))
        .where(Driver.id == driver_id)
    )
    return session.execute(stmt).unique().scalar_one_or_none()


def get_driver_by_user(session: Session, user_id: int) -> Driver | None:
    return session.execute(select(Driver).where(Driver.user_id == user_id)).scalar_one_or_none()


def history_for_request(session: Session, request_id: int) -> list[TransportHistory]:
    stmt = (
        select(TransportHistory)
        .options(joinedload(TransportHistory.actor).joinedload(User.profile))
        .where(TransportHistory.transport_request_id == request_id)
        .order_by(TransportHistory.sequence_number)
    )
    return list(session.execute(stmt).scalars())


def history_for_batch(session: Session, batch_id: int) -> list[TransportHistory]:
    stmt = (
        select(TransportHistory)
        .where(TransportHistory.produce_batch_id == batch_id)
        .order_by(TransportHistory.event_timestamp, TransportHistory.transport_request_id, TransportHistory.sequence_number)
    )
    return list(session.execute(stmt).scalars())


def profile_name(session: Session, user_id: int | None) -> str:
    if user_id is None:
        return "System"
    name = session.execute(select(UserProfile.full_name).where(UserProfile.user_id == user_id)).scalar_one_or_none()
    return name or "Unknown user"
