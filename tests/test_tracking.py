"""Chronological, append-only, hash-linked tracking history."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from farmtrace import domain
from farmtrace.database import repository
from farmtrace.errors import AppendOnlyViolation, PermissionDenied
from farmtrace.services import tracking_service, transport_service


def _advance(session, driver, request_id):
    transport_service.advance_delivery(session, driver, request_id, domain.STATUS_DISPATCHED, location="Farm gate")
    transport_service.advance_delivery(session, driver, request_id, domain.STATUS_IN_TRANSIT, location="Ibadan")
    session.commit()


def test_status_changes_append_new_entries_and_keep_previous_ones(session, driver, assigned_request):
    before = repository.history_for_request(session, assigned_request.id)
    snapshot = [(e.id, e.event_hash, e.new_status) for e in before]
    _advance(session, driver, assigned_request.id)
    after = repository.history_for_request(session, assigned_request.id)
    assert len(after) == len(before) + 2
    assert [(e.id, e.event_hash, e.new_status) for e in after[: len(before)]] == snapshot
    assert [e.sequence_number for e in after] == list(range(1, len(after) + 1))
    timestamps = [e.event_timestamp for e in after]
    assert timestamps == sorted(timestamps)
    assert after[-1].actor_user_id == driver.id
    assert after[-1].location_source == domain.LOCATION_SOURCE_MANUAL


def test_intact_chain_verifies(session, admin, driver, assigned_request):
    _advance(session, driver, assigned_request.id)
    result = tracking_service.verify_chain(session, assigned_request.id)
    assert result.valid and result.events_checked == 5
    assert all(r.valid for r in tracking_service.verify_all_chains(session, admin))


def _tamper(session, is_postgres, sql, params):
    if is_postgres:  # a privileged owner must first switch the protection off
        session.execute(text("ALTER TABLE transport_history DISABLE TRIGGER transport_history_append_only"))
    session.execute(text(sql), params)
    if is_postgres:
        session.execute(text("ALTER TABLE transport_history ENABLE TRIGGER transport_history_append_only"))
    session.expire_all()


def test_altered_event_is_detected(session, driver, assigned_request, is_postgres):
    _advance(session, driver, assigned_request.id)
    target = repository.history_for_request(session, assigned_request.id)[2]
    _tamper(session, is_postgres, "UPDATE transport_history SET event_description = 'Edited' WHERE id = :id", {"id": target.id})
    result = tracking_service.verify_chain(session, assigned_request.id)
    assert not result.valid
    assert any("no longer matches" in problem for problem in result.problems)


def test_deleted_event_is_detected(session, driver, assigned_request, is_postgres):
    _advance(session, driver, assigned_request.id)
    target = repository.history_for_request(session, assigned_request.id)[1]
    _tamper(session, is_postgres, "DELETE FROM transport_history WHERE id = :id", {"id": target.id})
    result = tracking_service.verify_chain(session, assigned_request.id)
    assert not result.valid


def test_orm_refuses_to_edit_or_delete_history(session, assigned_request):
    event = repository.history_for_request(session, assigned_request.id)[0]
    event.event_description = "rewritten"
    with pytest.raises(AppendOnlyViolation):
        session.flush()
    session.rollback()
    event = repository.history_for_request(session, assigned_request.id)[0]
    session.delete(event)
    with pytest.raises(AppendOnlyViolation):
        session.flush()


def test_database_trigger_blocks_raw_updates(session, assigned_request, is_postgres):
    if not is_postgres:
        pytest.skip("Append-only triggers exist only in PostgreSQL (set TEST_DATABASE_URL).")
    with pytest.raises(DBAPIError):
        session.execute(text("UPDATE transport_history SET event_description = 'x'"))
    session.rollback()
    with pytest.raises(DBAPIError):
        session.execute(text("DELETE FROM audit_logs"))


def test_history_is_private_to_involved_users(session, farmer, other_farmer, driver, other_driver, admin, assigned_request):
    for allowed in (farmer, driver, admin):
        assert tracking_service.get_request_history(session, allowed, assigned_request.id)
    for denied in (other_farmer, other_driver):
        with pytest.raises(PermissionDenied):
            tracking_service.get_request_history(session, denied, assigned_request.id)
