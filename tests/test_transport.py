"""Transport requests, driver assignment and delivery status workflow."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import text

from farmtrace import domain
from farmtrace.database import repository
from farmtrace.errors import ConflictError, InvalidTransitionError, PermissionDenied, ValidationError
from farmtrace.services import driver_service, transport_service
from farmtrace.services.transport_service import RequestFilters
from farmtrace.utils.helpers import local_today

from tests.conftest import approve_and_assign, make_batch, make_farm, make_request


def statuses(session, request_id):
    return [e.new_status for e in repository.history_for_request(session, request_id)]


def test_farmer_creates_request_for_eligible_batch(session, farmer, batch):
    request = make_request(session, farmer, batch)
    assert request.status == domain.STATUS_PENDING
    assert request.request_number.startswith("FT-R-")
    assert repository.get_batch(session, batch.id).batch_status == domain.BATCH_AWAITING_TRANSPORT
    history = repository.history_for_request(session, request.id)
    assert [h.event_type for h in history] == [domain.EVENT_REQUEST_CREATED]
    assert history[0].actor_user_id == farmer.id


def test_farmer_cannot_request_transport_for_someone_elses_batch(session, other_farmer, batch):
    with pytest.raises(PermissionDenied):
        make_request(session, other_farmer, batch)


def test_duplicate_active_request_is_rejected(session, farmer, batch, pending_request):
    with pytest.raises((ConflictError, ValidationError)):
        make_request(session, farmer, batch)


def test_pickup_date_in_the_past_is_rejected(session, farmer, batch):
    with pytest.raises(ValidationError):
        transport_service.create_request(
            session, farmer, batch_id=batch.id, requested_pickup_date=local_today() - dt.timedelta(days=3)
        )


def test_valid_driver_assignment(session, admin, pending_request, driver):
    assigned = approve_and_assign(session, admin, pending_request, driver)
    assert assigned.status == domain.STATUS_ASSIGNED
    assert assigned.driver.user_id == driver.id
    assert statuses(session, pending_request.id) == [domain.STATUS_PENDING, domain.STATUS_APPROVED, domain.STATUS_ASSIGNED]


def test_unavailable_driver_cannot_be_assigned(session, admin, pending_request, driver):
    driver_service.set_availability(session, driver, domain.AVAILABILITY_UNAVAILABLE)
    transport_service.approve_request(session, admin, pending_request.id)
    record = repository.get_driver_by_user(session, driver.id)
    with pytest.raises(ValidationError):
        transport_service.assign_driver(session, admin, pending_request.id, record.id)


def test_only_admins_can_approve_and_assign(session, farmer, driver, pending_request):
    with pytest.raises(PermissionDenied):
        transport_service.approve_request(session, farmer, pending_request.id)
    record = repository.get_driver_by_user(session, driver.id)
    with pytest.raises(PermissionDenied):
        transport_service.assign_driver(session, driver, pending_request.id, record.id)


def test_invalid_status_transitions_are_rejected(session, admin, driver, assigned_request):
    with pytest.raises(InvalidTransitionError):  # skip Dispatched / In-Transit
        transport_service.advance_delivery(session, driver, assigned_request.id, domain.STATUS_DELIVERED)
    with pytest.raises(InvalidTransitionError):  # approving an already-assigned request
        transport_service.approve_request(session, admin, assigned_request.id)


def test_cancelled_request_cannot_be_advanced(session, admin, driver, assigned_request):
    transport_service.cancel_request(session, admin, assigned_request.id, "Market closed for sanitation")
    session.commit()
    with pytest.raises(InvalidTransitionError):
        transport_service.advance_delivery(session, driver, assigned_request.id, domain.STATUS_DISPATCHED)


def test_unassigned_driver_cannot_modify_another_drivers_delivery(session, other_driver, assigned_request):
    with pytest.raises(PermissionDenied):
        transport_service.advance_delivery(session, other_driver, assigned_request.id, domain.STATUS_DISPATCHED)
    with pytest.raises(PermissionDenied):
        transport_service.report_delay(session, other_driver, assigned_request.id, "Not my delivery at all")


def test_farmer_cannot_mark_delivery_completed(session, farmer, assigned_request):
    with pytest.raises(PermissionDenied):
        transport_service.advance_delivery(session, farmer, assigned_request.id, domain.STATUS_DISPATCHED)


def test_full_delivery_workflow_records_times_and_history(session, driver, batch, assigned_request):
    rid = assigned_request.id
    transport_service.advance_delivery(session, driver, rid, domain.STATUS_DISPATCHED, location="Ogbomoso farm gate")
    transport_service.advance_delivery(session, driver, rid, domain.STATUS_IN_TRANSIT, location="Oyo toll gate")
    done = transport_service.advance_delivery(
        session, driver, rid, domain.STATUS_DELIVERED, location="Mile 12 Market", notes="Received by buyer"
    )
    session.commit()
    assert done.status == domain.STATUS_DELIVERED
    assert done.actual_dispatch_time is not None and done.actual_delivery_time is not None
    assert repository.get_batch(session, batch.id).batch_status == domain.BATCH_DELIVERED
    assert statuses(session, rid)[-3:] == [domain.STATUS_DISPATCHED, domain.STATUS_IN_TRANSIT, domain.STATUS_DELIVERED]
    with pytest.raises(InvalidTransitionError):
        transport_service.advance_delivery(session, driver, rid, domain.STATUS_DELIVERED)


def test_delay_is_a_flag_that_preserves_the_stage(session, driver, assigned_request):
    rid = assigned_request.id
    transport_service.advance_delivery(session, driver, rid, domain.STATUS_DISPATCHED)
    delayed = transport_service.report_delay(session, driver, rid, "Heavy traffic at Berger", location="Berger")
    assert delayed.is_delayed and delayed.status == domain.STATUS_DISPATCHED
    with pytest.raises(ConflictError):
        transport_service.report_delay(session, driver, rid, "Second delay report")
    moved = transport_service.advance_delivery(session, driver, rid, domain.STATUS_IN_TRANSIT)
    assert moved.is_delayed and moved.status == domain.STATUS_IN_TRANSIT  # workflow resumes, flag kept
    resumed = transport_service.resolve_delay(session, driver, rid, note="Traffic cleared")
    assert not resumed.is_delayed and resumed.status == domain.STATUS_IN_TRANSIT
    types = [e.event_type for e in repository.history_for_request(session, rid)]
    assert domain.EVENT_DELAY_REPORTED in types and domain.EVENT_DELAY_RESOLVED in types


def test_only_one_driver_can_claim_an_approved_request(session, admin, driver, other_driver, pending_request):
    transport_service.approve_request(session, admin, pending_request.id)
    session.commit()
    transport_service.accept_request(session, driver, pending_request.id)
    session.commit()
    with pytest.raises(ConflictError):
        transport_service.accept_request(session, other_driver, pending_request.id)


def test_stale_update_is_rejected_by_optimistic_locking(session, admin, pending_request):
    stale = repository.get_request(session, pending_request.id)
    # Simulate another user changing the row after we read it.
    session.execute(text("UPDATE transport_requests SET version = version + 1 WHERE id = :id"), {"id": stale.id})
    with pytest.raises(ConflictError):
        transport_service._transition(session, stale, new_status=domain.STATUS_APPROVED)


def test_self_assignment_can_be_disabled(session, admin, driver, pending_request):
    transport_service.approve_request(session, admin, pending_request.id)
    with pytest.raises(PermissionDenied):
        transport_service.accept_request(session, driver, pending_request.id, self_assign_enabled=False)


def test_cancellation_rules(session, admin, farmer, driver, batch, pending_request):
    transport_service.cancel_request(session, farmer, pending_request.id, "Buyer postponed the order")
    session.commit()
    assert repository.get_batch(session, batch.id).batch_status == domain.BATCH_REGISTERED
    second = make_request(session, farmer, batch)  # can request again after cancellation
    approve_and_assign(session, admin, second, driver)
    with pytest.raises(PermissionDenied):
        transport_service.cancel_request(session, farmer, second.id, "Changed my mind again")
    cancelled = transport_service.cancel_request(session, admin, second.id, "Road closed by flooding")
    assert cancelled.status == domain.STATUS_CANCELLED


def test_request_lists_are_scoped_to_the_actor(session, admin, farmer, other_farmer, driver, other_driver, assigned_request):
    farm = make_farm(session, other_farmer, "Ngozi Cassava Farm")
    other_batch = make_batch(session, other_farmer, farm, "Cassava")
    make_request(session, other_farmer, other_batch)
    assert {r.farmer_user_id for r in transport_service.list_requests(session, farmer)} == {farmer.id}
    assert [r.id for r in transport_service.list_requests(session, driver)] == [assigned_request.id]
    assert transport_service.list_requests(session, other_driver) == []
    assert len(transport_service.list_requests(session, admin)) == 2
    found = transport_service.list_requests(session, admin, RequestFilters(search="cassava"))
    assert [r.batch.produce_name for r in found] == ["Cassava"]
