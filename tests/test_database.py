"""Database constraints, relationships and configuration handling."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from farmtrace import domain
from farmtrace.config.settings import load_settings, normalize_database_url, normalize_public_url
from farmtrace.database import repository
from farmtrace.database.connection import check_database_ready
from farmtrace.database.models import ProduceBatch, TransportRequest
from farmtrace.errors import ConfigurationError
from farmtrace.utils.logger import redact

from tests.conftest import make_batch, make_farm


def _batch_kwargs(**overrides):
    values = dict(
        batch_number="FT-B-TEST-000001", produce_name="Yam", produce_category="Tubers & Roots", quantity=50,
        unit_of_measurement="tubers", harvest_date=dt.date.today(), pickup_location="Farm gate",
        destination_market="Bodija Market", destination_location="Ibadan", qr_token="x" * 43,
        batch_status=domain.BATCH_REGISTERED,
    )
    values.update(overrides)
    return values


def test_records_can_be_created_and_retrieved_with_relationships(session, farmer, farm, batch):
    loaded = repository.get_batch(session, batch.id)
    assert loaded.farm.farm_name == "Ogbomoso Green Farm"
    assert loaded.farmer_user_id == farmer.id
    assert repository.get_batch_by_token(session, batch.qr_token).id == batch.id


def test_foreign_key_to_missing_farm_is_rejected(session, farmer):
    session.add(ProduceBatch(farmer_user_id=farmer.id, farm_id=99999, **_batch_kwargs()))
    with pytest.raises(IntegrityError):
        session.flush()


def test_batch_cannot_reference_another_farmers_farm(session, farmer, other_farmer, farm):
    # Composite FK produce_batches(farm_id, farmer_user_id) -> farms(id, farmer_user_id)
    session.add(ProduceBatch(farmer_user_id=other_farmer.id, farm_id=farm.id, **_batch_kwargs()))
    with pytest.raises(IntegrityError):
        session.flush()


def test_unique_batch_numbers_are_enforced(session, farmer, farm, batch):
    session.add(ProduceBatch(farmer_user_id=farmer.id, farm_id=farm.id, **_batch_kwargs(batch_number=batch.batch_number)))
    with pytest.raises(IntegrityError):
        session.flush()


def test_duplicate_verification_tokens_are_rejected(session, farmer, farm, batch):
    session.add(ProduceBatch(farmer_user_id=farmer.id, farm_id=farm.id, **_batch_kwargs(qr_token=batch.qr_token)))
    with pytest.raises(IntegrityError):
        session.flush()


def test_quantity_must_be_positive_at_database_level(session, farmer, farm):
    session.add(ProduceBatch(farmer_user_id=farmer.id, farm_id=farm.id, **_batch_kwargs(quantity=0)))
    with pytest.raises(IntegrityError):
        session.flush()


def test_only_one_active_request_per_batch(session, farmer, batch, pending_request):
    session.add(TransportRequest(
        request_number="FT-R-DUPLICATE", produce_batch_id=batch.id, farmer_user_id=farmer.id,
        pickup_location="A", destination_location="B", requested_pickup_date=dt.date.today(),
        status=domain.STATUS_PENDING,
    ))
    with pytest.raises(IntegrityError):
        session.flush()


def test_assigned_status_requires_a_driver(session, farmer, other_farmer):
    farm = make_farm(session, other_farmer, "Jos Plateau Farm")
    batch = make_batch(session, other_farmer, farm, "Irish potatoes")
    session.add(TransportRequest(
        request_number="FT-R-NODRIVER", produce_batch_id=batch.id, farmer_user_id=other_farmer.id,
        pickup_location="A", destination_location="B", requested_pickup_date=dt.date.today(),
        status=domain.STATUS_ASSIGNED, assigned_driver_id=None,
    ))
    with pytest.raises(IntegrityError):
        session.flush()


# ----------------------------------------------------------------- configuration
def test_missing_database_url_is_reported():
    settings = load_settings(environ={}, secrets={})
    with pytest.raises(ConfigurationError):
        settings.require_database_url()


def test_sqlite_and_other_urls_are_not_accepted_as_production_database():
    with pytest.raises(ConfigurationError):
        normalize_database_url("sqlite:///farmtrace.db")
    assert normalize_database_url("postgres://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"


def test_settings_read_from_secrets_and_validate_values():
    settings = load_settings(
        environ={"APP_ENV": "production"},
        secrets={"DATABASE_URL": "postgresql://u:p@db.example.com/farmtrace", "PUBLIC_APP_URL": "https://ft.streamlit.app/"},
    )
    assert settings.is_production
    assert settings.public_app_url == "https://ft.streamlit.app"
    with pytest.raises(ConfigurationError):
        load_settings(environ={"APP_TIMEZONE": "Mars/Base"}, secrets={})
    with pytest.raises(ConfigurationError):
        normalize_public_url("ftp://example.com")


def test_unreachable_database_reports_safe_error_without_credentials():
    engine = create_engine("postgresql+psycopg://farmuser:SuperSecret123@127.0.0.1:1/farmtrace",
                           connect_args={"connect_timeout": 2})
    status = check_database_ready(engine)
    assert not status.ok and status.code == "unreachable"
    assert "SuperSecret123" not in status.message


def test_unmigrated_database_is_detected():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    status = check_database_ready(engine)
    assert status.code == "not_migrated"


def test_migrated_database_is_ready(engine, is_postgres):
    if not is_postgres:
        pytest.skip("Migration status is verified against PostgreSQL (set TEST_DATABASE_URL).")
    assert check_database_ready(engine).ok


def test_logs_redact_connection_passwords():
    assert redact("connecting to postgresql://farm:pa55word@db.host/ft") == "connecting to postgresql://farm:***@db.host/ft"
