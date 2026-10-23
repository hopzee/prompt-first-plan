"""QR token generation, verification URLs and public privacy."""

from __future__ import annotations

import dataclasses
import io
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image

from farmtrace import domain
from farmtrace.database import repository
from farmtrace.errors import ConfigurationError, PermissionDenied
from farmtrace.services import farmer_service, qr_service, transport_service

from tests.conftest import make_batch

BASE_URL = "https://farmtrace-demo.streamlit.app"


def test_tokens_are_unique_unpredictable_and_not_ids(session, farmer, farm, batch):
    second = make_batch(session, farmer, farm, "Peppers")
    assert batch.qr_token != second.qr_token
    assert len(batch.qr_token) >= 43  # 32 random bytes, URL-safe base64
    assert str(batch.id) != batch.qr_token and batch.batch_number not in batch.qr_token


def test_verification_url_uses_configured_base_url():
    url = qr_service.build_verification_url(BASE_URL + "/", "TOKEN_abc-1234567890123456")
    assert url == f"{BASE_URL}/?verify=TOKEN_abc-1234567890123456"
    with pytest.raises(ConfigurationError):
        qr_service.build_verification_url(None, "TOKEN_abc-1234567890123456")


def test_generated_qr_contains_expected_verification_url(session, farmer, batch):
    bundle = qr_service.get_batch_qr(session, farmer, batch.id, BASE_URL)
    assert parse_qs(urlsplit(bundle.url).query)["verify"] == [batch.qr_token]
    encoded = qr_service.build_qr(bundle.url).data_list[0].data
    assert encoded == bundle.url.encode()
    image = Image.open(io.BytesIO(bundle.png))
    assert image.format == "PNG" and image.size[0] == image.size[1] >= 200
    assert Image.open(io.BytesIO(bundle.label_png)).format == "PNG"


def test_only_owner_or_admin_can_download_qr(session, other_farmer, admin, batch):
    with pytest.raises(PermissionDenied):
        qr_service.get_batch_qr(session, other_farmer, batch.id, BASE_URL)
    assert qr_service.get_batch_qr(session, admin, batch.id, BASE_URL).url.endswith(batch.qr_token)


def test_valid_token_identifies_the_correct_batch(session, batch, assigned_request):
    result = qr_service.verify_token(session, batch.qr_token)
    assert result.ok
    assert result.view.batch_number == batch.batch_number
    assert result.view.delivery_status == domain.STATUS_ASSIGNED
    assert [e.status for e in result.view.events] == [domain.STATUS_PENDING, domain.STATUS_APPROVED, domain.STATUS_ASSIGNED]


@pytest.mark.parametrize("token", ["", "short", "has spaces in it 1234567890", "' OR 1=1 --"])
def test_malformed_tokens_are_rejected(session, token):
    assert qr_service.verify_token(session, token).status == "invalid"


def test_unknown_token_is_rejected(session, batch):
    assert qr_service.verify_token(session, "A" * 43).status == "unknown"


def test_revoked_and_replaced_tokens_are_explained(session, admin, batch):
    qr_service.revoke_qr(session, admin, batch.id, "Labels were photocopied")
    assert qr_service.verify_token(session, batch.qr_token).status == "revoked"
    old_token = batch.qr_token
    reissued = qr_service.reissue_qr(session, admin, batch.id, "Fresh labels printed")
    assert qr_service.verify_token(session, old_token).status == "revoked"
    assert qr_service.verify_token(session, reissued.qr_token).ok


def test_public_verification_reveals_no_private_information(session, farmer, driver, admin, batch, assigned_request):
    transport_service.advance_delivery(
        session, driver, assigned_request.id, domain.STATUS_DISPATCHED, location="Farm gate",
        notes="Call me on 08099998888",
    )
    transport_service.report_delay(session, driver, assigned_request.id, "Tyre puncture near Ogere")
    result = qr_service.verify_token(session, batch.qr_token)
    view = dataclasses.asdict(result.view)
    assert tuple(view) == qr_service.PUBLIC_FIELDS
    text = repr(view)
    driver_record = repository.get_driver_by_user(session, driver.id)
    private_values = [
        farmer.email, driver.email, farmer.full_name, driver.full_name, "08099998888", "+234",
        driver_record.driver_license_number, driver_record.vehicle_registration_number,
        "Handle with care", "Tyre puncture", "45000", batch.qr_token, "$argon2",
    ]
    for value in private_values:
        assert value not in text, f"public view leaked: {value}"
    assert "id" not in view and all("id" not in dataclasses.asdict(e) for e in result.view.events)
    assert result.view.is_delayed and result.notice


def test_cancelled_batch_is_not_presented_as_delivered(session, farmer, batch):
    farmer_service.cancel_batch(session, farmer, batch.id, "Produce spoiled by rain")
    result = qr_service.verify_token(session, batch.qr_token)
    assert result.ok and result.view.batch_status == domain.BATCH_CANCELLED
    assert "withdrawn" in result.notice
