"""Demo data is clearly labelled, follows the real workflow and cannot be loaded twice."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from farmtrace import domain
from farmtrace.database.models import Farm, ProduceBatch, TransportRequest
from farmtrace.database.seed import load_demo_data
from farmtrace.errors import ConflictError
from tests.conftest import PASSWORD


def test_demo_records_are_flagged_and_follow_the_workflow(session):
    summary = load_demo_data(session, PASSWORD)
    session.commit()

    assert all(email.endswith("@example.com") for email in summary.accounts)
    assert all(f.is_demo for f in session.scalars(select(Farm)))
    assert all(b.is_demo for b in session.scalars(select(ProduceBatch)))
    requests = {r.request_number: r for r in session.scalars(select(TransportRequest))}
    assert all(r.is_demo for r in requests.values())

    delivered, on_road, pending = (requests[n] for n in summary.request_numbers)
    assert delivered.status == domain.STATUS_DELIVERED
    assert on_road.status == domain.STATUS_IN_TRANSIT and on_road.is_delayed is True
    assert pending.status == domain.STATUS_PENDING


def test_demo_data_cannot_be_loaded_twice(session):
    load_demo_data(session, PASSWORD)
    session.commit()
    with pytest.raises(ConflictError):
        load_demo_data(session, PASSWORD)
