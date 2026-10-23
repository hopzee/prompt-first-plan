"""Reports, CSV export and dashboard metrics."""

from __future__ import annotations

import io

import pandas as pd
import pytest

from farmtrace import domain
from farmtrace.errors import PermissionDenied
from farmtrace.services import report_service, transport_service

from tests.conftest import make_batch, make_farm, make_request


def test_transport_report_is_scoped_and_exports_csv(session, admin, farmer, other_farmer, assigned_request):
    other = make_batch(session, other_farmer, make_farm(session, other_farmer, "Kano Onion Farm"), "Onions")
    make_request(session, other_farmer, other)
    farmer_report = report_service.transport_report(session, farmer)
    assert list(farmer_report["Request number"]) == [assigned_request.request_number]
    admin_report = report_service.transport_report(session, admin)
    assert len(admin_report) == 2
    csv = report_service.to_csv_bytes(admin_report).decode("utf-8-sig")
    parsed = pd.read_csv(io.StringIO(csv))
    assert list(parsed.columns) == report_service.TRANSPORT_COLUMNS
    assert set(parsed["Status"]) == {domain.STATUS_ASSIGNED, domain.STATUS_PENDING}


def test_csv_export_neutralises_spreadsheet_formulas():
    frame = pd.DataFrame({"Description": ["=HYPERLINK(\"http://evil\")", "normal", "+SUM(A1)"]})
    csv = report_service.to_csv_bytes(frame).decode("utf-8-sig")
    assert "'=HYPERLINK" in csv and "'+SUM" in csv and "\nnormal" in csv


def test_history_report_contains_hashes_in_order(session, farmer, assigned_request):
    report = report_service.history_report(session, farmer)
    assert list(report["Sequence"]) == [1, 2, 3]
    assert report["Event hash"].str.len().eq(64).all()


def test_admin_metrics_count_real_records(session, admin, farmer, driver, assigned_request):
    transport_service.advance_delivery(session, driver, assigned_request.id, domain.STATUS_DISPATCHED)
    transport_service.report_delay(session, admin, assigned_request.id, "Checkpoint inspection delay")
    metrics = report_service.admin_metrics(session, admin)
    assert metrics["farmers"] == 1 and metrics["drivers"] == 1 and metrics["admins"] == 1
    assert metrics["batches"] == 1 and metrics["active"] == 1 and metrics["delayed"] == 1
    assert metrics["completed"] == 0 and metrics["pending"] == 0


def test_metrics_and_performance_require_admin_and_real_data(session, farmer, driver, assigned_request):
    with pytest.raises(PermissionDenied):
        report_service.admin_metrics(session, farmer)
    for status in (domain.STATUS_DISPATCHED, domain.STATUS_IN_TRANSIT, domain.STATUS_DELIVERED):
        transport_service.advance_delivery(session, driver, assigned_request.id, status)
    performance = report_service.delivery_performance(report_service.transport_report(session, farmer))
    assert list(performance["Request number"]) == [assigned_request.request_number]
    assert performance["On time"].iloc[0] == "No ETA set"
    assert report_service.delivery_performance(pd.DataFrame(columns=report_service.TRANSPORT_COLUMNS)).empty
