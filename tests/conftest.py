"""Shared pytest fixtures.

By default the tests run against an isolated in-memory SQLite database (no
credentials needed). To verify PostgreSQL compatibility — including the Alembic
migration and the append-only triggers — point TEST_DATABASE_URL at an EMPTY,
dedicated test database whose name contains "test":

    TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/farmtrace_test pytest

Never point it at a production database: every table is emptied between tests.
"""

from __future__ import annotations

import datetime as dt
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool

from farmtrace import domain
from farmtrace.authentication import auth
from farmtrace.authentication.auth import DriverDetails, RegistrationData
from farmtrace.config.settings import PROJECT_ROOT, normalize_database_url
from farmtrace.database.connection import enable_sqlite_foreign_keys, make_session_factory
from farmtrace.database.models import Base
from farmtrace.services import farmer_service, transport_service
from farmtrace.services.farmer_service import BatchInput, FarmInput
from farmtrace.utils.helpers import local_today

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
PASSWORD = "Harvest2026"


def _migrate_postgres(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    config.attributes["configure_logger"] = False
    command.downgrade(config, "base")
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def engine():
    if TEST_DATABASE_URL:
        url = normalize_database_url(TEST_DATABASE_URL)
        if "test" not in (make_url(url).database or ""):
            pytest.exit("Refusing to run: TEST_DATABASE_URL must name a dedicated database containing 'test'.")
        engine = create_engine(url, connect_args={"prepare_threshold": None})
        _migrate_postgres(url)
    else:
        engine = create_engine(
            "sqlite+pysqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        enable_sqlite_foreign_keys(engine)
        Base.metadata.create_all(engine)  # tests only — the application itself relies on Alembic
    yield engine
    engine.dispose()


@pytest.fixture
def is_postgres(engine) -> bool:
    return engine.dialect.name == "postgresql"


def _empty_all_tables(engine) -> None:
    tables = [t.name for t in reversed(Base.metadata.sorted_tables)]
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
        else:
            for name in tables:
                connection.execute(text(f"DELETE FROM {name}"))


@pytest.fixture
def session(engine):
    factory = make_session_factory(engine)
    db = factory()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
        _empty_all_tables(engine)


# ------------------------------------------------------------------ factories
_counter = {"n": 0}


def _next() -> int:
    _counter["n"] += 1
    return _counter["n"]


def register(session, role: str, *, name: str | None = None, email: str | None = None, driver: bool = True):
    n = _next()
    details = None
    if role == domain.ROLE_DRIVER and driver:
        details = DriverDetails(
            driver_license_number=f"LAG{n:05d}AA01",
            vehicle_registration_number=f"LSD-{n:03d}-AB",
            vehicle_type="Mini truck",
            vehicle_capacity="3000",
        )
    user = auth.register_user(
        session,
        RegistrationData(
            full_name=name or f"Test {role.title()} {n}",
            email=email or f"{role}{n}@example.com",
            password=PASSWORD,
            confirm_password=PASSWORD,
            role=role,
            phone_number=f"0803{n:07d}",
            driver=details,
        ),
    )
    session.commit()
    return user


@pytest.fixture
def farmer(session):
    return register(session, domain.ROLE_FARMER, name="Adebayo Ogunleye")


@pytest.fixture
def other_farmer(session):
    return register(session, domain.ROLE_FARMER, name="Ngozi Eze")


@pytest.fixture
def driver(session):
    return register(session, domain.ROLE_DRIVER, name="Musa Ibrahim")


@pytest.fixture
def other_driver(session):
    return register(session, domain.ROLE_DRIVER, name="Chinedu Okafor")


@pytest.fixture
def consumer(session):
    return register(session, domain.ROLE_CONSUMER, name="Funke Adeyemi")


@pytest.fixture
def admin(session):
    user, _ = auth.bootstrap_administrator(
        session, email=f"admin{_next()}@example.com", full_name="Site Administrator", password=PASSWORD
    )
    session.commit()
    return auth.to_current_user(user)


def make_farm(session, actor, name: str = "Ogbomoso Green Farm"):
    farm = farmer_service.create_farm(
        session, actor,
        FarmInput(farm_name=name, farm_location="Km 5 Ogbomoso-Ilorin Road", state="Oyo",
                  local_government_area="Ogbomoso North", contact_information="Farm office, 08031112222"),
    )
    session.commit()
    return farm


def make_batch(session, actor, farm, produce: str = "Tomatoes"):
    batch = farmer_service.create_batch(
        session, actor,
        BatchInput(
            farm_id=farm.id, produce_name=produce, produce_category="Vegetables", quantity="120",
            unit_of_measurement="crates", harvest_date=local_today() - dt.timedelta(days=1),
            pickup_location="Ogbomoso farm gate", destination_market="Mile 12 Market",
            destination_location="Mile 12, Kosofe, Lagos", additional_notes="Handle with care",
        ),
    )
    session.commit()
    return batch


@pytest.fixture
def farm(session, farmer):
    return make_farm(session, farmer)


@pytest.fixture
def batch(session, farmer, farm):
    return make_batch(session, farmer, farm)


def make_request(session, actor, batch):
    request = transport_service.create_request(
        session, actor, batch_id=batch.id, requested_pickup_date=local_today() + dt.timedelta(days=1)
    )
    session.commit()
    return request


def approve_and_assign(session, admin, request, driver_user):
    from farmtrace.database import repository

    transport_service.approve_request(session, admin, request.id, transport_cost="45000")
    session.commit()
    driver_record = repository.get_driver_by_user(session, driver_user.id)
    assigned = transport_service.assign_driver(session, admin, request.id, driver_record.id)
    session.commit()
    return assigned


@pytest.fixture
def pending_request(session, farmer, batch):
    return make_request(session, farmer, batch)


@pytest.fixture
def assigned_request(session, admin, farmer, batch, driver):
    request = make_request(session, farmer, batch)
    return approve_and_assign(session, admin, request, driver)
